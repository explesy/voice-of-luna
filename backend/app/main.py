from __future__ import annotations

import asyncio
import base64
import inspect
import json
import logging
import os
import re
import shutil
import sys
import tempfile
import time
import wave
from collections.abc import AsyncIterator, Coroutine
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any
from uuid import uuid4

from .speech_pipeline import (
    ABBREVIATIONS,
    AUDIO_SUFFIXES,
    FIRST_CHUNK_SPLIT_RE,
    MAX_AUDIO_BYTES,
    SENTENCE_SPLIT_RE,
    SOURCES_SPLIT_RE,
    AudioConversionError,
    VoicePlan,
    convert_to_wav,
    detect_effective_turn_locale,
    extract_speech_sentence,
    is_16k_mono_wav,
    join_speech_clips,
    remove_temporary_audio,
    route_speech,
    write_temporary_audio,
)
from .conversation_service import (
    Conversation,
    ConversationService,
    SpeechClip,
    TurnLanguage,
    call_reply,
    call_reply_stream,
    conversation_service,
    approve_tool_permission,
    pending_tool_approvals,
    approve_pending_tool,
    resolve_stt_config,
    resolve_turn_language,
    resolve_voice_plan,
    plugin_storage,
)
from .i18n import get_ui_text
from . import model_catalog

from fastapi import (
    FastAPI,
    File,
    Form,
    HTTPException,
    Request,
    Response,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from markupsafe import Markup, escape
from pydantic import BaseModel, Field
from starlette.background import BackgroundTask
from . import __version__
from .codex import (
    DEFAULT_BASE_INSTRUCTIONS,
    DEFAULT_MODEL,
    CodexAppServer,
    CodexUnavailable,
    get_base_instructions,
)
from .plugins import OutputEvent, PluginTurnResult, ResponseCandidate, TurnContext, plugin_manager
from .speak import (
    LocalMacOsSpeaker,
    LocalSpeechError,
    SpeechSynthesisResult,
    get_active_voice,
    get_default_voice,
    get_default_voice_for_locale,
    get_installed_voices,
    get_ready_voice_for_locale,
    get_voice_for_locale,
    is_edge_voice,
    is_piper_voice,
    is_silero_voice,
    prewarm_voice,
    sanitize_for_speech,
)
from .transcribe import (
    LocalTranscriptionError,
    LocalWhisperTranscriber,
    SpeechToTextProvider,
    run_tone_shadow,
    create_streaming_session,
    tone_shadow_configured,
)
from .stt_manager import (
    get_batch_stt_model,
    is_batch_stt_model,
    list_batch_stt_models,
    resolve_batch_stt_model_id,
    sherpa_available,
    stt_model_manager,
)
from .whisper_server import WhisperServerManager

whisper_server = WhisperServerManager()
CONVERSATION_IDLE_TTL_SECONDS = int(os.environ.get("VOICE_OF_LUNA_CONVERSATION_IDLE_TTL_SECONDS", "900"))
CONVERSATION_REAPER_INTERVAL_SECONDS = 60
_DEFAULT_SPEAKER_SYNTHESIZE = LocalMacOsSpeaker.synthesize


def _build_stt_provider(
    *, language: str | None, prompt: str | None, stt_model: str | None = None
) -> SpeechToTextProvider:
    """Build the configured batch STT capability for a completed audio turn.

    When a specific batch Whisper model is selected, transcription must use that
    exact asset. The resident ``whisper-server`` may have loaded a different
    model, so HTTP is only used when the server is known to serve this file;
    otherwise the CLI path runs the selected model explicitly.
    """

    if not stt_model:
        return LocalWhisperTranscriber(language=language, prompt=prompt)
    definition = get_batch_stt_model(stt_model)
    if definition is None or not definition.files:
        return LocalWhisperTranscriber(language=language, prompt=prompt)
    from app.tts_manager import find_model_file

    model_path = find_model_file(definition.files[0].filename)
    if model_path is None or not model_path.is_file():
        raise LocalTranscriptionError(
            f"Selected speech-to-text model '{stt_model}' is not installed locally"
        )
    return LocalWhisperTranscriber(
        model_path=model_path,
        language=language,
        prompt=prompt,
        prefer_server=whisper_server.serves_model(model_path),
    )


def _resolve_conversation_stt_model(conversation: Conversation) -> tuple[str | None, str | None]:
    """Return the effective batch STT model id and a fallback reason, if any."""

    return resolve_batch_stt_model_id(conversation.stt_model)


def _require_batch_stt_model(conversation: Conversation) -> str | None:
    """Resolve the batch STT model for a turn, failing on an unusable explicit choice.

    An explicit selection that cannot be used must never silently fall back to
    the automatic model, otherwise the transcript would be attributed to the
    wrong model. ``None`` (automatic) is still allowed to resolve to nothing and
    use the default transcriber.
    """

    resolved, reason = resolve_batch_stt_model_id(conversation.stt_model)
    if conversation.stt_model and resolved is None:
        raise LocalTranscriptionError(
            reason or f"Selected speech-to-text model '{conversation.stt_model}' is not available"
        )
    return resolved


def _stt_catalog_errors() -> list[str]:
    """Sanitized user-model configuration errors for the UI."""

    from app.tts_manager import tts_model_manager

    return tts_model_manager.user_model_errors


def _stt_model_context(conversation: Conversation) -> dict[str, object]:
    """Describe the selected batch STT model for UI/WS messages."""

    resolved, fallback_reason = _resolve_conversation_stt_model(conversation)
    definition = get_batch_stt_model(resolved) if resolved else None
    return {
        "stt_model": conversation.stt_model,
        "stt_model_resolved": resolved,
        "stt_model_name": definition.name if definition else None,
        "stt_model_fallback": fallback_reason,
    }


async def _record_tone_shadow_observation(conversation_id: str, wav_bytes: bytes) -> None:
    """Record opt-in T-One health/latency without retaining speech text."""

    result = await run_tone_shadow(wav_bytes)
    logger.info(
        "T-One shadow observation conversation=%s status=%s elapsed_ms=%s reason=%s",
        conversation_id,
        result.get("status"),
        result.get("elapsed_ms"),
        result.get("reason"),
    )


TRAILING_SOURCES_PLACEHOLDER_RE = re.compile(
    r"""(?xi)
    (?:
        # Case 1: Explicit header
        (?:\n|\A)\s*(?:[#/*_~-]+\s*)?(?:источники|ссылки|источник|sources|references|source|fuentes|referencias|fuente)\b\s*:?[\s\S]*$
        |
        # Case 2: Trailing block with link placeholders after sentence end or newline
        (?<=[.!?…\n])\s*
        (?:
            (?:[#/*_~-]+\s*)?(?:источники|ссылки|источник|sources|references|source|fuentes|referencias|fuente)\b\s*:?\s*
        )?
        (?:
            (?:[-*•·]|\d+\.)?\s*
            [\(\[]?\s*
            @@@LINK_\d+@@@
            [\)\]]?
            \s*[,;•·–—\-/\n\s]*
        )+
        $
    )
    """
)


def format_terminal_text(text: str) -> Markup:
    """Format terminal log text with safe clickable links, markdown formatting, and highlighted sources."""
    if not text:
        return Markup("")

    escaped = str(escape(text))
    links: list[str] = []

    def save_md_link(match: re.Match[str]) -> str:
        title = match.group(1)
        url = match.group(2)
        if url.startswith("http://") or url.startswith("https://"):
            idx = len(links)
            links.append(
                f'<a href="{url}" target="_blank" rel="noopener noreferrer" class="term-link">'
                f'{title}&nbsp;<span class="ext-glyph">↗</span></a>'
            )
            return f"@@@LINK_{idx}@@@"
        return match.group(0)

    formatted = re.sub(r"\[([^\]]+)\]\(((?:https?://)[^)\s]+)\)", save_md_link, escaped)

    def save_bare_url(match: re.Match[str]) -> str:
        url = match.group(0)
        idx = len(links)
        links.append(
            f'<a href="{url}" target="_blank" rel="noopener noreferrer" class="term-link">'
            f'{url}&nbsp;<span class="ext-glyph">↗</span></a>'
        )
        return f"@@@LINK_{idx}@@@"

    formatted = re.sub(r'(?<!href=")(?!(?:">|@@@LINK_))(https?://[^\s<)]+)', save_bare_url, formatted)

    sources_part = ""
    sources_match = TRAILING_SOURCES_PLACEHOLDER_RE.search(formatted)
    if sources_match:
        before = formatted[: sources_match.start()].strip()
        raw_sources = formatted[sources_match.start():].strip()
        header_match = re.match(
            r"(?i)\s*(?:источники|ссылки|источник|sources|references|source|fuentes|referencias|fuente)\s*:?",
            raw_sources,
        )
        if header_match:
            header = header_match.group(0).strip().rstrip(":")
            body = raw_sources[header_match.end():].strip()
        else:
            header = "источники"
            body = raw_sources

        if body.startswith("(") and body.endswith(")"):
            body = body[1:-1].strip()

        sources_part = (
            f'<div class="log-sources">'
            f'<div class="sources-tag">// {header.upper()}:</div>'
            f'<div class="sources-body">{body}</div>'
            f'</div>'
        )
        formatted = before

    # 1. Unescape escaped markdown characters if present
    formatted = re.sub(r"\\([*_`~[\]])", r"\1", formatted)

    # 2. Extract inline code
    code_snippets: list[str] = []

    def save_code(m: re.Match[str]) -> str:
        idx = len(code_snippets)
        code_snippets.append(f"<code>{m.group(1)}</code>")
        return f"@@@CODE_{idx}@@@"

    formatted = re.sub(r"`([^`\n]+)`", save_code, formatted)

    # 3. Bold (**...** or __...__)
    formatted = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", formatted)
    formatted = re.sub(r"__(.+?)__", r"<strong>\1</strong>", formatted)

    # 4. Strikethrough (~~...~~)
    formatted = re.sub(r"~~(.+?)~~", r"<del>\1</del>", formatted)

    # 5. Italics (*...* or _..._)
    formatted = re.sub(r"(?<!\*)\*(?!\s)(.+?)(?<!\s)\*(?!\*)", r"<em>\1</em>", formatted)
    formatted = re.sub(r"(?:\b|(?<=\s)|^)_(?!\s)(.+?)(?<!\s)_(?:\b|(?=\s)|$)", r"<em>\1</em>", formatted)

    # 6. Clean any remaining stray backslashes
    formatted = re.sub(r"\\+", "", formatted)

    # 7. Restore code snippets
    for idx, html in enumerate(code_snippets):
        formatted = formatted.replace(f"@@@CODE_{idx}@@@", html)

    # 8. Restore links
    for idx, html in enumerate(links):
        formatted = formatted.replace(f"@@@LINK_{idx}@@@", html)
        if sources_part:
            sources_part = sources_part.replace(f"@@@LINK_{idx}@@@", html)

    if sources_part:
        formatted = formatted + sources_part

    return Markup(formatted)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    from app.tts_manager import tts_model_manager

    # Register user-defined models before any voice cache or STT resolution.
    tts_model_manager.register_user_models()
    get_installed_voices(force_refresh=True)
    await whisper_server.start()
    reaper_task = asyncio.create_task(_conversation_reaper())
    try:
        yield
    finally:
        reaper_task.cancel()
        await asyncio.gather(reaper_task, return_exceptions=True)
        await asyncio.gather(
            whisper_server.close(),
            LocalWhisperTranscriber.close_shared_http_client(),
            *(conversation.model.close() for conversation in conversations.values()),
            return_exceptions=True,
        )


app = FastAPI(title="Voice of Luna", version=__version__, lifespan=lifespan)
app.mount("/static", StaticFiles(directory="app/static"), name="static")
templates = Jinja2Templates(directory="app/templates")
templates.env.globals["version"] = __version__
templates.env.globals["app_version"] = __version__
templates.env.filters["format_terminal_text"] = format_terminal_text


logger = logging.getLogger("voice_of_luna")

conversations: dict[str, Conversation] = conversation_service.conversations
speech_clips: dict[str, SpeechClip] = conversation_service.speech_clips


def _safe_background_task(
    coro: Coroutine[Any, Any, Any], name: str | None = None
) -> asyncio.Task[Any]:
    return conversation_service.safe_background_task(coro, name=name)


def _prewarm_conversation(conversation: Conversation) -> None:
    conversation_service.prewarm_conversation(conversation)


def _touch_conversation(conversation: Conversation) -> None:
    conversation_service.touch(conversation)


async def _reap_idle_conversations(now: float | None = None) -> int:
    closer = getattr(sys.modules[__name__], "_close_and_delete_conversation", conversation_service.close_and_delete)
    return await conversation_service.reap_idle_conversations(now=now, closer=closer)


async def _conversation_reaper() -> None:
    while True:
        await asyncio.sleep(CONVERSATION_REAPER_INTERVAL_SECONDS)
        try:
            reaper_fn = getattr(sys.modules[__name__], "_reap_idle_conversations", conversation_service.reap_idle_conversations)
            await reaper_fn()
        except Exception:
            logger.exception("Failed to reap idle conversations")


async def _run_conversation_warmup(
    conversation: Conversation, model_name: str, effort: str
) -> None:
    await conversation_service.run_warmup(conversation, model_name, effort)


def _schedule_conversation_warmup(
    conversation: Conversation, model_name: str, effort: str
) -> str:
    return conversation_service.schedule_warmup(conversation, model_name, effort)


async def _await_conversation_warmup(conversation: Conversation) -> None:
    await conversation_service.await_warmup(conversation)


def detect_effective_turn_locale(text: str, fallback_locale: str = "ru-RU") -> str:
    """Detect turn locale based on text content (Cyrillic -> ru-RU, else en-US)."""
    if re.search(r"[\u0400-\u04FF]", text):
        return "ru-RU"
    # If no Cyrillic and contains Latin letters, prefer English
    if re.search(r"[a-zA-Z]", text):
        return "en-US"
    return fallback_locale if fallback_locale != "auto" else "ru-RU"


def _get_tts_engine(voice_name: str) -> str:
    """Return canonical uppercase engine name for dynamic footer status."""
    if is_edge_voice(voice_name):
        return "EDGE_TTS"
    if is_piper_voice(voice_name):
        return "PIPER_OFFLINE"
    if is_silero_voice(voice_name):
        return "SILERO_OFFLINE"
    return "MACOS_SAY"


async def _refresh_conversation_base_instructions(
    conversation: Conversation, override_locale: str | None = None
) -> str:
    return await conversation_service.refresh_base_instructions(conversation, override_locale=override_locale)


async def _apply_plugin_to_conversation(
    conversation: Conversation,
    plugin_id: str,
    mode: str = "default",
    settings: dict[str, Any] | None = None,
) -> None:
    await conversation_service.apply_plugin(
        conversation, plugin_id, mode=mode, settings=settings
    )


async def _get_view_context(request: Request, conversation: Conversation | None = None) -> dict[str, object]:
    cookie_locale = request.cookies.get("voice_of_luna_locale")
    if cookie_locale:
        cookie_locale = cookie_locale.strip('"')
        if conversation:
            conversation.locale = cookie_locale
    active_locale = (conversation.locale if conversation and conversation.locale else None) or (cookie_locale if cookie_locale else "ru-RU")
    if conversation:
        conversation.locale = active_locale

    cookie_voice = request.cookies.get("voice_of_luna_voice")
    if cookie_voice:
        cookie_voice = cookie_voice.strip('"')
        if conversation:
            conversation.set_selected_voice(cookie_voice)
    elif cookie_locale and conversation:
        conversation.set_selected_voice(get_default_voice_for_locale(active_locale))
    active_voice = (conversation.voice if conversation and conversation.voice else None) or (get_default_voice_for_locale(active_locale) if cookie_locale else get_active_voice())
    if conversation:
        conversation.set_selected_voice(active_voice)

    cookie_model = request.cookies.get("voice_of_luna_model")
    if cookie_model and conversation and not conversation.model_name:
        conversation.model_name = cookie_model.strip('"')

    cookie_effort = request.cookies.get("voice_of_luna_effort")
    if cookie_effort and conversation:
        conversation.reasoning_effort = cookie_effort.strip('"')

    remote_warmup_enabled = request.cookies.get("voice_of_luna_remote_warmup") != "false"

    cookie_plugin = request.cookies.get("voice_of_luna_plugin")
    if cookie_plugin and conversation and conversation.plugin_id == "neutral":
        active_p = cookie_plugin.strip('"')
        cookie_p_mode = (request.cookies.get("voice_of_luna_plugin_mode") or "default").strip('"')
        await _apply_plugin_to_conversation(conversation, active_p, cookie_p_mode)

    all_voices = [v.to_dict() for v in get_installed_voices()]
    deprecated_voices = [v for v in all_voices if v.get("catalog_status") == "deprecated"]
    # Deprecated voices live in their own opt-in group, not in the everyday lists.
    active_voices = [v for v in all_voices if v.get("catalog_status") != "deprecated"]
    russian_voices = [v for v in active_voices if v["is_russian"]]
    other_voices = [v for v in active_voices if not v["is_russian"]]
    # Keep the everyday list focused on Russian voices.  Previously this used
    # every Edge voice, which made English and Spanish voices appear in the
    # primary group without a locale marker.
    edge_voices = [v for v in russian_voices if v.get("engine") == "edge"]
    piper_voices = [v for v in russian_voices if v.get("engine") == "piper"]
    silero_voices = [v for v in russian_voices if v.get("engine") == "silero"]
    local_russian_voices = [v for v in russian_voices if v.get("engine") not in ("edge", "silero", "piper")]

    models: list[dict[str, object]] = []
    if conversation:
        models = await conversation.model.list_models()
    else:
        models = await CodexAppServer().list_models()

    cookie_stt_model = request.cookies.get("voice_of_luna_stt_model")
    if cookie_stt_model and conversation and conversation.stt_model is None:
        conversation.stt_model = cookie_stt_model.strip('"') or None

    requested_model = (
        (conversation.requested_model if conversation else None)
        or (conversation.model_name if conversation else None)
        or (cookie_model.strip('"') if cookie_model else None)
    )
    available_model_ids = {str(model.get("id")) for model in models if model.get("id")}
    model_fallback_reason: str | None = None
    if requested_model and requested_model in available_model_ids:
        active_model = requested_model
    elif DEFAULT_MODEL in available_model_ids:
        active_model = DEFAULT_MODEL
        if requested_model:
            model_fallback_reason = f"Requested model '{requested_model}' is not available; using '{DEFAULT_MODEL}'."
    elif models:
        active_model = str(models[0]["id"])
        if requested_model:
            model_fallback_reason = f"Requested model '{requested_model}' is not available; using '{active_model}'."
    else:
        active_model = DEFAULT_MODEL
        if requested_model:
            model_fallback_reason = f"Requested model '{requested_model}' is not available; using '{DEFAULT_MODEL}'."
    if conversation:
        if requested_model:
            conversation.requested_model = requested_model
        conversation.model_name = active_model
    active_effort = (conversation.reasoning_effort if conversation else None) or (cookie_effort.strip('"') if cookie_effort else "low")
    active_model_entry = next((m for m in models if str(m.get("id")) == active_model), None)
    supported_efforts = [
        str(item.get("reasoningEffort"))
        for item in (active_model_entry or {}).get("supportedReasoningEfforts", [])
        if item.get("reasoningEffort")
    ] or ["low", "medium", "high", "xhigh"]
    if active_effort not in supported_efforts:
        active_effort = supported_efforts[0]
    if conversation:
        conversation.reasoning_effort = active_effort
    stt_context = _stt_model_context(conversation) if conversation else {
        "stt_model": None,
        "stt_model_resolved": None,
        "stt_model_name": None,
        "stt_model_fallback": None,
    }
    if remote_warmup_enabled and conversation:
        _schedule_conversation_warmup(
            conversation, str(active_model), str(active_effort)
        )

    all_plugins = plugin_manager.list_plugins()
    active_plugin = conversation.plugin_id if conversation else (cookie_plugin.strip('"') if cookie_plugin else "neutral")
    active_plugin_mode = conversation.plugin_mode if conversation else "default"

    return {
        "active_locale": active_locale,
        "active_voice": active_voice,
        "russian_voice": active_voice,
        "russian_voices": russian_voices,
        "edge_voices": edge_voices,
        "piper_voices": piper_voices,
        "silero_voices": silero_voices,
        "local_russian_voices": local_russian_voices,
        "other_voices": other_voices,
        "all_voices": all_voices,
        "deprecated_voices": deprecated_voices,
        "models": models,
        "models_source": str(models[0].get("source")) if models else "preset",
        "active_model": active_model,
        "requested_model": requested_model,
        "model_fallback_reason": model_fallback_reason,
        "active_effort": active_effort,
        "supported_efforts": supported_efforts,
        "stt_models": list_batch_stt_models(),
        **stt_context,
        "stt_errors": _stt_catalog_errors(),
        "remote_warmup_enabled": remote_warmup_enabled,
        "plugins": all_plugins,
        "active_plugin": active_plugin,
        "active_plugin_mode": active_plugin_mode,
        "version": __version__,
        "app_version": __version__,
        "ui": get_ui_text(active_locale),
    }


def _get_voice_context(request: Request, conversation: Conversation | None = None) -> dict[str, object]:
    cookie_voice = request.cookies.get("voice_of_luna_voice")
    if cookie_voice:
        cookie_voice = cookie_voice.strip('"')
        if conversation:
            conversation.set_selected_voice(cookie_voice)
    active_voice = (conversation.voice if conversation and conversation.voice else None) or get_active_voice()
    if conversation:
        conversation.set_selected_voice(active_voice)
    all_voices = [v.to_dict() for v in get_installed_voices()]
    deprecated_voices = [v for v in all_voices if v.get("catalog_status") == "deprecated"]
    active_voices = [v for v in all_voices if v.get("catalog_status") != "deprecated"]
    russian_voices = [v for v in active_voices if v["is_russian"]]
    edge_voices = [v for v in russian_voices if v.get("engine") == "edge"]
    piper_voices = [v for v in russian_voices if v.get("engine") == "piper"]
    silero_voices = [v for v in russian_voices if v.get("engine") == "silero"]
    local_russian_voices = [v for v in russian_voices if v.get("engine") not in ("edge", "silero", "piper")]
    return {
        "active_voice": active_voice,
        "russian_voice": active_voice,
        "russian_voices": russian_voices,
        "edge_voices": edge_voices,
        "piper_voices": piper_voices,
        "silero_voices": silero_voices,
        "local_russian_voices": local_russian_voices,
        "other_voices": [v for v in active_voices if not v["is_russian"]],
        "all_voices": all_voices,
        "deprecated_voices": deprecated_voices,
        "models": [],
        "active_model": DEFAULT_MODEL,
        "active_effort": "low",
        "supported_efforts": ["low", "medium", "high", "xhigh"],
        "plugins": plugin_manager.list_plugins(),
        "active_plugin": conversation.plugin_id if conversation else "neutral",
        "active_plugin_mode": conversation.plugin_mode if conversation else "default",
    }


async def _close_and_delete_conversation(conversation_id: str) -> bool:
    return await conversation_service.close_and_delete(conversation_id)


class TurnInput(BaseModel):
    text: str = Field(min_length=1, max_length=8_000)


async def _call_reply(
    model: CodexAppServer,
    text: str,
    context_prompt: str | None = None,
    model_name: str | None = None,
    effort: str | None = None,
) -> str:
    return await call_reply(
        model=model,
        text=text,
        context_prompt=context_prompt,
        model_name=model_name,
        effort=effort,
    )


async def _call_reply_stream(
    model: CodexAppServer,
    text: str,
    context_prompt: str | None = None,
    model_name: str | None = None,
    effort: str | None = None,
) -> AsyncIterator[str]:
    async for chunk in call_reply_stream(
        model=model,
        text=text,
        context_prompt=context_prompt,
        model_name=model_name,
        effort=effort,
    ):
        yield chunk


@app.get("/favicon.ico", include_in_schema=False)
async def favicon() -> FileResponse:
    favicon_path = Path(__file__).parent / "static" / "favicon.ico"
    return FileResponse(favicon_path)


@app.get("/healthz")
async def healthz() -> dict[str, object]:
    runtime = await CodexAppServer().status()
    return {"ok": True, "codex": asdict(runtime)}


@app.get("/", response_class=HTMLResponse)
async def home(request: Request) -> HTMLResponse:
    runtime = await CodexAppServer().status()
    conversation = Conversation(id=str(uuid4()))
    conversations[conversation.id] = conversation
    _prewarm_conversation(conversation)
    view_ctx = await _get_view_context(request, conversation)
    return templates.TemplateResponse(
        request,
        "index.html",
        {"conversation": conversation, "runtime": runtime, **view_ctx},
    )


@app.get("/conversations/{conversation_id}", response_class=HTMLResponse)
async def view_conversation(request: Request, conversation_id: str) -> HTMLResponse:
    conversation = _recover_html_conversation(conversation_id)
    runtime = await CodexAppServer().status()
    _prewarm_conversation(conversation)
    view_ctx = await _get_view_context(request, conversation)
    return templates.TemplateResponse(
        request,
        "index.html",
        {"conversation": conversation, "runtime": runtime, **view_ctx},
    )


@app.post("/conversations", response_class=HTMLResponse)
async def create_conversation_fragment(request: Request) -> HTMLResponse:
    conversation = Conversation(id=str(uuid4()))
    conversations[conversation.id] = conversation
    _prewarm_conversation(conversation)
    view_ctx = await _get_view_context(request, conversation)
    return templates.TemplateResponse(
        request, "conversation.html", {"conversation": conversation, **view_ctx}
    )


@app.delete("/conversations/{conversation_id}", response_class=HTMLResponse)
async def delete_conversation_fragment(request: Request, conversation_id: str) -> HTMLResponse:
    conversation = conversations.pop(conversation_id, None)
    if conversation is not None:
        await plugin_manager.on_conversation_reset(conversation.plugin_id, conversation.id)
        try:
            await asyncio.wait_for(conversation.model.close(), timeout=2.0)
        except Exception as exc:
            logger.warning("Error closing conversation model on reset: %s", exc)
    for clip_id, clip in list(speech_clips.items()):
        if clip.conversation_id == conversation_id:
            _remove_temporary_audio(clip.path)
            del speech_clips[clip_id]
    new_conversation = Conversation(id=str(uuid4()))
    conversations[new_conversation.id] = new_conversation
    _prewarm_conversation(new_conversation)
    view_ctx = await _get_view_context(request, new_conversation)
    return templates.TemplateResponse(
        request, "conversation.html", {"conversation": new_conversation, **view_ctx}
    )


@app.post("/conversations/{conversation_id}/turns", response_class=HTMLResponse)
async def create_turn_fragment(
    request: Request, conversation_id: str, text: str = Form(min_length=1, max_length=8_000)
) -> HTMLResponse:
    conversation = _recover_html_conversation(conversation_id)
    conversation.turns.append({"role": "user", "text": text})
    conversation.turn_evidence = []
    turn_ctx = TurnContext(
        conversation_id=conversation.id,
        user_message=text,
        turns_history=conversation.turns,
        active_mode=conversation.plugin_mode,
        metadata={"plugin_settings": conversation.plugin_settings},
        state=plugin_storage.for_plugin(conversation.plugin_id, conversation.id),
    )
    plugin_res = await plugin_manager.execute_before_turn(conversation.plugin_id, turn_ctx)
    t_start = time.perf_counter()
    error = None
    try:
        reply = await _call_reply(
            conversation.model,
            text,
            context_prompt=plugin_res.prompt_context,
            model_name=conversation.model_name,
            effort=conversation.reasoning_effort,
        )
        t_llm = time.perf_counter()
        if plugin_manager.delivery_mode(conversation.plugin_id) == "gated":
            decision = await plugin_manager.execute_validate_response(
                conversation.plugin_id, turn_ctx,
                ResponseCandidate(conversation.id, text, reply),
            )
            if decision.action == "reject":
                raise CodexUnavailable("Plugin rejected the model response")
            reply = str(decision.text or reply)
        error = await _append_assistant_turn(conversation, reply)
        t_tts = time.perf_counter()
        logger.info(
            "Text turn latency: llm=%.3fs, tts=%.3fs, total=%.3fs",
            t_llm - t_start,
            t_tts - t_llm,
            t_tts - t_start,
        )
        await plugin_manager.execute_after_turn(conversation.plugin_id, turn_ctx, reply)
    except CodexUnavailable as exception:
        error = str(exception)
    view_ctx = await _get_view_context(request, conversation)
    return templates.TemplateResponse(
        request,
        "conversation.html",
        {"conversation": conversation, "error": error, **view_ctx},
    )


@app.post("/conversations/{conversation_id}/audio", response_class=HTMLResponse)
async def create_audio_turn_fragment(
    request: Request, conversation_id: str, audio: UploadFile = File(...)
) -> HTMLResponse:
    conversation = _recover_html_conversation(conversation_id)
    if not (audio.content_type or "").startswith("audio/"):
        raise HTTPException(status_code=415, detail="Expected an audio recording")

    recording = await audio.read(MAX_AUDIO_BYTES + 1)
    if not recording or len(recording) > MAX_AUDIO_BYTES:
        raise HTTPException(status_code=413, detail="Audio must be between 1 byte and 12 MB")

    suffix = AUDIO_SUFFIXES.get(audio.content_type or "", ".webm")
    temporary_path = _write_temporary_audio(recording, suffix)
    wav_path: Path | None = None
    t_start = time.perf_counter()
    error = None
    try:
        if suffix == ".wav" and _is_16k_mono_wav(temporary_path):
            wav_path = temporary_path
        else:
            wav_path = await _convert_to_wav(temporary_path)
        t_wav = time.perf_counter()
        stt_config = resolve_stt_config(conversation)
        batch_model_id = _require_batch_stt_model(conversation)
        transcript = await _build_stt_provider(
            language=stt_config.language, prompt=stt_config.prompt, stt_model=batch_model_id
        ).transcribe(wav_path)
        t_stt = time.perf_counter()
        conversation.turns.append({"role": "user", "text": transcript})
        conversation.turn_evidence = []
        turn_ctx = TurnContext(
            conversation_id=conversation.id,
            user_message=transcript,
            turns_history=conversation.turns,
            active_mode=conversation.plugin_mode,
            metadata={"plugin_settings": conversation.plugin_settings},
            state=plugin_storage.for_plugin(conversation.plugin_id, conversation.id),
        )
        plugin_res = await plugin_manager.execute_before_turn(conversation.plugin_id, turn_ctx)
        reply = await _call_reply(
            conversation.model,
            transcript,
            context_prompt=plugin_res.prompt_context,
            model_name=conversation.model_name,
            effort=conversation.reasoning_effort,
        )
        t_llm = time.perf_counter()
        if plugin_manager.delivery_mode(conversation.plugin_id) == "gated":
            decision = await plugin_manager.execute_validate_response(
                conversation.plugin_id, turn_ctx,
                ResponseCandidate(conversation.id, transcript, reply),
            )
            if decision.action == "reject":
                raise CodexUnavailable("Plugin rejected the model response")
            reply = str(decision.text or reply)
        error = await _append_assistant_turn(conversation, reply)
        t_tts = time.perf_counter()
        logger.info(
            "Audio turn latency: audio_prep=%.3fs, stt=%.3fs, llm=%.3fs, tts=%.3fs, total=%.3fs",
            t_wav - t_start,
            t_stt - t_wav,
            t_llm - t_stt,
            t_tts - t_llm,
            t_tts - t_start,
        )
        await plugin_manager.execute_after_turn(conversation.plugin_id, turn_ctx, reply)
    except AudioConversionError as exception:
        error = str(exception)
    except LocalTranscriptionError as exception:
        error = str(exception)
    except CodexUnavailable as exception:
        error = str(exception)
    finally:
        await asyncio.to_thread(_remove_temporary_audio, temporary_path)
        if wav_path is not None and wav_path != temporary_path:
            await asyncio.to_thread(_remove_temporary_audio, wav_path)
    view_ctx = await _get_view_context(request, conversation)
    return templates.TemplateResponse(
        request,
        "conversation.html",
        {"conversation": conversation, "error": error, **view_ctx},
    )


@app.get("/api/runtime")
async def runtime() -> dict[str, object]:
    result = asdict(await CodexAppServer().status())
    result["version"] = __version__
    return result


@app.get("/api/voices")
async def list_available_voices(request: Request) -> dict[str, object]:
    all_voices = [v.to_dict() for v in get_installed_voices()]
    active_voice = (request.cookies.get("voice_of_luna_voice") or get_active_voice()).strip('"')
    return {
        "active_voice": active_voice,
        "voices": all_voices,
        "russian_voices": [v for v in all_voices if v["is_russian"]],
        "other_voices": [v for v in all_voices if not v["is_russian"]],
    }


@app.get("/api/tts/models")
async def list_tts_models(kind: str | None = None) -> dict[str, object]:
    from app.tts_manager import tts_model_manager

    # Default keeps the historical full list (including Whisper assets) for
    # backwards compatibility; the TTS picker requests ?kind=tts.
    if kind in (None, ""):
        return {"models": tts_model_manager.list_all_models()}
    if kind not in ("tts", "stt"):
        raise HTTPException(status_code=400, detail="kind must be 'tts' or 'stt'")
    return {"models": tts_model_manager.list_all_models(kind=kind)}


@app.post("/api/tts/models/{model_id}/download")
async def download_tts_model(model_id: str) -> dict[str, object]:
    from app.speak import get_installed_voices
    from app.tts_manager import tts_model_manager

    status = tts_model_manager.get_status(model_id)
    if "error" in status and status.get("status") == "error" and status.get("error") == "Model not found":
        raise HTTPException(status_code=404, detail="Model not found")

    async def _bg() -> None:
        try:
            await tts_model_manager.download_model(model_id)
            get_installed_voices(force_refresh=True)
        except Exception as exc:
            logger.error("Download failed for TTS model '%s': %s", model_id, exc)

    _safe_background_task(_bg(), name=f"download-tts-model-{model_id}")
    return {"ok": True, "model_id": model_id, "status": "downloading"}


@app.get("/api/tts/models/{model_id}/status")
async def get_tts_model_status(model_id: str) -> dict[str, object]:
    from app.tts_manager import tts_model_manager

    status = tts_model_manager.get_status(model_id)
    if "error" in status and status.get("status") == "error" and status.get("error") == "Model not found":
        raise HTTPException(status_code=404, detail="Model not found")
    return status


@app.get("/api/stt/models")
async def list_stt_models() -> dict[str, object]:
    return {
        "models": [*stt_model_manager.list_all_models(), *list_batch_stt_models()],
        "streaming": stt_model_manager.list_all_models(),
        "batch": list_batch_stt_models(),
        "runtime_available": sherpa_available(),
        "errors": _stt_catalog_errors(),
    }


@app.post("/api/stt/models/{model_id}/download")
async def download_stt_model(model_id: str) -> dict[str, object]:
    from app.speak import get_installed_voices
    from app.tts_manager import tts_model_manager

    if is_batch_stt_model(model_id):
        # Whisper does not need the optional sherpa-onnx runtime.
        async def _bg_batch() -> None:
            try:
                await tts_model_manager.download_model(model_id)
            except Exception as exc:
                logger.error("Download failed for batch STT model '%s': %s", model_id, exc)

        _safe_background_task(_bg_batch(), name=f"download-stt-model-{model_id}")
        return {"ok": True, "model_id": model_id, "status": "downloading"}
    if stt_model_manager.get(model_id) is None:
        raise HTTPException(status_code=404, detail="Model not found")
    if not sherpa_available():
        raise HTTPException(status_code=409, detail="sherpa-onnx runtime is not installed")
    stt_model_manager.start_download(model_id)
    return {"ok": True, "model_id": model_id, "status": "downloading"}


@app.get("/api/stt/models/{model_id}/status")
async def get_stt_model_status(model_id: str) -> dict[str, object]:
    from app.tts_manager import tts_model_manager

    if is_batch_stt_model(model_id):
        status = tts_model_manager.get_status(model_id)
        status["batch"] = True
        return status
    if stt_model_manager.get(model_id) is None:
        raise HTTPException(status_code=404, detail="Model not found")
    return stt_model_manager.get_status(model_id)


@app.get("/api/models")
async def list_available_models(request: Request) -> dict[str, object]:
    provider = CodexAppServer()
    try:
        models = await provider.list_models()
    finally:
        await provider.close()
    cookie_model = request.cookies.get("voice_of_luna_model")
    cookie_effort = request.cookies.get("voice_of_luna_effort")
    requested_model = cookie_model.strip('"') if cookie_model else None
    available_model_ids = {str(model.get("id")) for model in models if model.get("id")}
    model_fallback_reason: str | None = None
    if requested_model and requested_model in available_model_ids:
        active_model = requested_model
    elif DEFAULT_MODEL in available_model_ids:
        active_model = DEFAULT_MODEL
        if requested_model:
            model_fallback_reason = f"Requested model '{requested_model}' is not available; using '{DEFAULT_MODEL}'."
    elif models:
        active_model = str(models[0]["id"])
        if requested_model:
            model_fallback_reason = f"Requested model '{requested_model}' is not available; using '{active_model}'."
    else:
        active_model = DEFAULT_MODEL
    return {
        "models": models,
        "models_source": str(models[0].get("source")) if models else "preset",
        "active_model": active_model,
        "requested_model": requested_model,
        "model_fallback_reason": model_fallback_reason,
        "active_effort": cookie_effort.strip('"') if cookie_effort else "low",
    }


@app.get("/api/models/catalog")
async def list_model_catalog() -> dict[str, object]:
    """Read-only unified catalog grouped by kind/engine/locale (issue #14)."""
    from app.tts_manager import tts_model_manager

    tts_entries = tts_model_manager.list_all_models(kind="tts")
    batch_entries = list_batch_stt_models()
    streaming_entries = stt_model_manager.list_all_models()
    models: list[dict[str, object]] = []
    for entry in tts_entries:
        models.append(entry)
    for entry in [*batch_entries, *streaming_entries]:
        models.append(entry)
    try:
        provider = CodexAppServer()
        try:
            llm_models = await provider.list_models()
        finally:
            await provider.close()
        for entry in llm_models:
            models.append({
                "id": entry.get("id"),
                "name": entry.get("displayName") or entry.get("id"),
                "kind": "llm",
                "engine": "codex",
                "locale": "multi",
                "languages": [],
                "multilingual": None,
                "code_switching": None,
                "speech": False,
                "catalog_status": "recommended",
                "deprecation_reason": None,
                "status": "ready" if entry.get("source") == "live" else "preset",
                "installed": entry.get("source") == "live",
                "source": entry.get("source", "preset"),
                "supportedReasoningEfforts": entry.get("supportedReasoningEfforts", []),
            })
    except Exception as exc:
        logger.debug("LLM catalog discovery failed: %s", exc)
    grouped = model_catalog.group_catalog(models)
    return {"models": models, "errors": _stt_catalog_errors(), **grouped}


class SettingsInput(BaseModel):
    model: str | None = None
    effort: str | None = None
    voice: str | None = None
    locale: str | None = None
    remote_warmup: bool | None = None
    stt_model: str | None = None


@app.post("/api/settings")
async def update_settings(body: SettingsInput, response: Response) -> dict[str, object]:
    if body.voice:
        response.set_cookie(
            key="voice_of_luna_voice",
            value=body.voice,
            max_age=365 * 24 * 3600,
            httponly=False,
            samesite="lax",
        )
    if body.locale:
        response.set_cookie(
            key="voice_of_luna_locale",
            value=body.locale,
            max_age=365 * 24 * 3600,
            httponly=False,
            samesite="lax",
        )
    if body.model:
        response.set_cookie(
            key="voice_of_luna_model",
            value=body.model,
            max_age=365 * 24 * 3600,
            httponly=False,
            samesite="lax",
        )
    if body.remote_warmup is not None:
        response.set_cookie(
            key="voice_of_luna_remote_warmup",
            value="true" if body.remote_warmup else "false",
            max_age=365 * 24 * 3600,
            httponly=False,
            samesite="lax",
        )
    if body.effort:
        response.set_cookie(
            key="voice_of_luna_effort",
            value=body.effort,
            max_age=365 * 24 * 3600,
            httponly=False,
            samesite="lax",
        )
    stt_model = body.stt_model
    if "stt_model" in body.model_fields_set:
        # Explicit null means "automatic" and must clear the persisted choice.
        if stt_model and not is_batch_stt_model(stt_model):
            raise HTTPException(status_code=400, detail="Unknown batch speech-to-text model")
        if stt_model:
            response.set_cookie(
                key="voice_of_luna_stt_model",
                value=stt_model,
                max_age=365 * 24 * 3600,
                httponly=False,
                samesite="lax",
            )
        else:
            response.delete_cookie("voice_of_luna_stt_model")
    active_model = body.model or DEFAULT_MODEL
    active_effort = body.effort or "low"
    remote_warmup_enabled = body.remote_warmup is not False
    warmup_status = "enabled" if remote_warmup_enabled else "off"
    return {
        "ok": True,
        "model": active_model,
        "effort": body.effort,
        "voice": body.voice or get_active_voice(),
        "locale": body.locale,
        "stt_model": stt_model,
        "remote_warmup": remote_warmup_enabled,
        "remote_warmup_status": warmup_status,
    }


class LocaleSelectInput(BaseModel):
    locale: str = Field(min_length=2)


@app.post("/api/locale")
async def select_locale(body: LocaleSelectInput, response: Response) -> dict[str, object]:
    response.set_cookie(
        key="voice_of_luna_locale",
        value=body.locale,
        max_age=365 * 24 * 3600,
        httponly=False,
        samesite="lax",
    )
    recommended_voice = get_default_voice_for_locale(body.locale)
    return {"ok": True, "active_locale": body.locale, "recommended_voice": recommended_voice}


class VoiceSelectInput(BaseModel):
    voice: str = Field(min_length=1)


@app.post("/api/voice")
async def select_voice(body: VoiceSelectInput, response: Response) -> dict[str, object]:
    from app.speak import get_installed_voices
    from app.tts_manager import tts_model_manager

    response.set_cookie(
        key="voice_of_luna_voice",
        value=body.voice,
        max_age=365 * 24 * 3600,
        httponly=False,
        samesite="lax",
    )

    model = tts_model_manager.get_model_for_voice(body.voice)
    auto_downloading = False
    if model and not tts_model_manager.is_ready(model.id):
        auto_downloading = True

        async def _bg() -> None:
            try:
                await tts_model_manager.download_model(model.id)
                get_installed_voices(force_refresh=True)
            except Exception as e:
                logger.error("Auto-download failed for '%s': %s", model.id, e)

        _safe_background_task(_bg(), name=f"auto-download-tts-{model.id}")

    res: dict[str, object] = {
        "ok": True,
        "active_voice": body.voice,
    }
    if auto_downloading:
        res["auto_downloading"] = True
        res["model_id"] = model.id if model else None
    return res


@app.post("/api/conversations", status_code=status.HTTP_201_CREATED)
async def create_conversation(request: Request) -> dict[str, str]:
    conversation = Conversation(id=str(uuid4()))
    cookie_locale = request.cookies.get("voice_of_luna_locale")
    if cookie_locale:
        conversation.locale = cookie_locale.strip('"')
    cookie_voice = request.cookies.get("voice_of_luna_voice")
    if cookie_voice:
        conversation.set_selected_voice(cookie_voice.strip('"'))
    elif cookie_locale:
        conversation.set_selected_voice(get_default_voice_for_locale(conversation.locale))
    cookie_stt_model = request.cookies.get("voice_of_luna_stt_model")
    if cookie_stt_model:
        conversation.stt_model = cookie_stt_model.strip('"') or None
    conversations[conversation.id] = conversation
    await _refresh_conversation_base_instructions(conversation)
    _prewarm_conversation(conversation)
    return {"id": conversation.id}


@app.get("/api/plugins")
async def list_plugins() -> dict[str, object]:
    return {"plugins": plugin_manager.list_plugins()}


class PluginSelectInput(BaseModel):
    plugin_id: str = Field(min_length=1)
    mode: str | None = Field(default="default")
    settings: dict[str, Any] = Field(default_factory=dict)
    project_root: str | None = Field(default=None, max_length=1000)


class ToolApprovalInput(BaseModel):
    request_id: str = Field(min_length=1, max_length=100)
    args_hash: str = Field(min_length=64, max_length=64)


class PluginActionInput(BaseModel):
    action: str = Field(min_length=1, max_length=80)


class ApprovedSpeechInput(BaseModel):
    text: str = Field(min_length=1, max_length=4_000)
    turn_id: str = Field(default="plugin", min_length=1, max_length=100)


@app.post("/api/conversations/{conversation_id}/plugin")
async def select_plugin(
    conversation_id: str, body: PluginSelectInput, response: Response
) -> dict[str, object]:
    conversation = conversations.get(conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    settings = dict(body.settings)
    # Backward-compatible input while clients migrate to plugin-owned settings.
    if body.project_root is not None and "root" not in settings:
        settings["root"] = body.project_root
    try:
        await _apply_plugin_to_conversation(
            conversation,
            body.plugin_id,
            body.mode or "default",
            settings=settings,
        )
    except ValueError as exc:
        # Configuration errors must be visible to the client (for example an
        # invalid/non-repository Project Room root), rather than becoming an
        # opaque 500 that looks like Apply simply did nothing.
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    response.set_cookie(
        key="voice_of_luna_plugin",
        value=conversation.plugin_id,
        max_age=365 * 24 * 3600,
        httponly=False,
        samesite="lax",
    )
    response.set_cookie(
        key="voice_of_luna_plugin_mode",
        value=conversation.plugin_mode,
        max_age=365 * 24 * 3600,
        httponly=False,
        samesite="lax",
    )
    if conversation.voice:
        response.set_cookie(
            key="voice_of_luna_voice",
            value=conversation.selected_voice or conversation.voice,
            max_age=365 * 24 * 3600,
            httponly=False,
            samesite="lax",
        )
    effective_voice = resolve_turn_language(conversation).speaker_voice
    return {
        "ok": True,
        "plugin_id": conversation.plugin_id,
        "mode": conversation.plugin_mode,
        "voice": effective_voice,
        "settings": conversation.plugin_settings,
        "panel": plugin_manager.panel_schema(conversation.plugin_id),
    }


@app.post("/api/conversations/{conversation_id}/plugin/action")
async def plugin_action(conversation_id: str, body: PluginActionInput) -> dict[str, Any]:
    conversation = conversations.get(conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    try:
        result = await plugin_manager.action(
            conversation.plugin_id,
            body.action,
            conversation.plugin_settings,
            plugin_storage.for_plugin(conversation.plugin_id, conversation.id),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "plugin_id": conversation.plugin_id, **result}


@app.post("/api/conversations/{conversation_id}/plugin/speak")
async def plugin_speak_approved(conversation_id: str, body: ApprovedSpeechInput) -> dict[str, Any]:
    """Synthesize plugin-approved text without creating an LLM conversation turn."""
    conversation = conversations.get(conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    turn_lang = resolve_turn_language(conversation)
    voice = turn_lang.speaker_voice
    speaker = LocalMacOsSpeaker(voice=voice)
    try:
        result = await _synthesize_routed_single_clip(speaker, body.text.strip(), resolve_voice_plan(conversation, turn_lang))
    except LocalSpeechError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    if result is None or not result.path.is_file():
        raise HTTPException(status_code=400, detail="Cannot synthesize approved text")
    clip_id = str(uuid4())
    speech_clips[clip_id] = SpeechClip(conversation_id=conversation.id, path=result.path)
    return {
        "ok": True,
        "clip_id": clip_id,
        "turn_id": body.turn_id,
        "audio_url": f"/speech/{clip_id}",
        "text": body.text.strip(),
        "tts_engine": result.actual_engine,
    }


class ResynthesizeInput(BaseModel):
    voice: str | None = Field(default=None)
    set_default: bool = False
    turn_id: str | None = Field(default=None)


@app.post("/api/conversations/{conversation_id}/turns/{turn_index}/resynthesize")
async def resynthesize_turn(
    conversation_id: str, turn_index: int, body: ResynthesizeInput
) -> dict[str, Any]:
    """Re-synthesize an existing assistant turn without creating an LLM turn.

    The message text is taken from the authoritative server-side turn, so the
    browser cannot substitute arbitrary text. The result is ephemeral: the
    audio is returned inline and the temporary clip is deleted immediately.
    """

    conversation = conversations.get(conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    if body.turn_id:
        # The browser-supplied turn id is the stable key; it survives any DOM
        # reordering that could otherwise shift positional indices.
        turn = next((item for item in conversation.turns if item.get("turn_id") == body.turn_id), None)
        if turn is None:
            raise HTTPException(status_code=404, detail="Turn not found")
    elif 0 <= turn_index < len(conversation.turns):
        turn = conversation.turns[turn_index]
    else:
        raise HTTPException(status_code=404, detail="Turn not found")
    text = str(turn.get("text") or "").strip()
    if turn.get("role") != "assistant" or not text:
        raise HTTPException(status_code=400, detail="Only assistant turns with text can be re-synthesized")

    requested_voice = (body.voice or "").strip() or conversation.voice
    explicit_voice = bool((body.voice or "").strip())
    turn_lang = resolve_turn_language(conversation)
    turn_plan = resolve_voice_plan(conversation, turn_lang)
    # A plain replay must keep the resolved turn voices (including a plugin's
    # target/explanation pair); only an explicit re-voice applies one voice to
    # the whole text.
    if not explicit_voice:
        requested_voice = turn_lang.speaker_voice
    # An explicit re-voice applies one voice to the whole text; a plain replay
    # still routes embedded/explanation language runs to their own voices.
    plan = VoicePlan(
        primary_locale=turn_plan.primary_locale,
        primary_voice=requested_voice,
        explanation_locale=turn_plan.explanation_locale,
        explanation_voice=turn_plan.explanation_voice,
        enabled=turn_plan.enabled and not explicit_voice,
    )
    speaker = LocalMacOsSpeaker(voice=requested_voice)
    try:
        synthesis = await _synthesize_routed_single_clip(speaker, text, plan)
    except LocalSpeechError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    if synthesis is None or not synthesis.path.is_file():
        raise HTTPException(status_code=400, detail="Cannot synthesize this message")

    try:
        audio_bytes = await asyncio.to_thread(synthesis.path.read_bytes)
        mime_type = "audio/mpeg" if synthesis.path.suffix == ".mp3" else "audio/wav"
    finally:
        await asyncio.to_thread(_remove_temporary_audio, synthesis.path)

    actual_voice = synthesis.actual_voice or requested_voice
    fallback = actual_voice != requested_voice
    # Never silently change the session default to a voice the user did not pick.
    applied_default = bool(body.set_default and not fallback)
    if applied_default:
        conversation.set_selected_voice(actual_voice)

    engine_label = {
        "edge": "EDGE_TTS",
        "piper": "PIPER_OFFLINE",
        "silero": "SILERO_OFFLINE",
        "macos": "MACOS_SAY",
    }.get(synthesis.actual_engine, _get_tts_engine(actual_voice))
    return {
        "ok": True,
        "clip_id": str(uuid4()),
        "turn_id": turn.get("turn_id") or str(uuid4()),
        "audio_base64": base64.b64encode(audio_bytes).decode("ascii"),
        "mime_type": mime_type,
        "text": text,
        "voice": actual_voice,
        "requested_voice": requested_voice,
        "fallback": fallback,
        "fallback_reason": synthesis.fallback_reason,
        "requested_tts_engine": _get_tts_engine(requested_voice),
        "tts_engine": engine_label,
        "set_default": applied_default,
        "replay": True,
    }


@app.post("/api/conversations/{conversation_id}/tool-approval")
async def approve_tool(conversation_id: str, body: ToolApprovalInput) -> dict[str, object]:
    if conversation_id not in conversations:
        raise HTTPException(status_code=404, detail="Conversation not found")
    if not approve_pending_tool(body.request_id, body.args_hash):
        raise HTTPException(status_code=409, detail="Approval request is unknown, expired, or changed")
    return {"ok": True, "request_id": body.request_id}


@app.get("/api/conversations/{conversation_id}/tool-approval")
async def list_pending_tool_approvals(conversation_id: str) -> dict[str, object]:
    if conversation_id not in conversations:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return {"requests": pending_tool_approvals(conversation_id)}


@app.post("/api/conversations/{conversation_id}/turns")
async def create_turn(conversation_id: str, body: TurnInput) -> dict[str, Any]:
    conversation = conversations.get(conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    conversation.turns.append({"role": "user", "text": body.text})
    conversation.turn_evidence = []
    turn_ctx = TurnContext(
        conversation_id=conversation.id,
        user_message=body.text,
        turns_history=conversation.turns,
        active_mode=conversation.plugin_mode,
        metadata={"plugin_settings": conversation.plugin_settings},
        state=plugin_storage.for_plugin(conversation.plugin_id, conversation.id),
    )
    plugin_res = await plugin_manager.execute_before_turn(conversation.plugin_id, turn_ctx)
    try:
        reply = await _call_reply(
            conversation.model,
            body.text,
            context_prompt=plugin_res.prompt_context,
            model_name=conversation.model_name,
            effort=conversation.reasoning_effort,
        )
    except CodexUnavailable as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    if plugin_manager.delivery_mode(conversation.plugin_id) == "gated":
        decision = await plugin_manager.execute_validate_response(
            conversation.plugin_id,
            turn_ctx,
            ResponseCandidate(conversation.id, body.text, reply),
        )
        if decision.action == "reject":
            raise HTTPException(status_code=502, detail="Plugin rejected the model response")
        reply = str(decision.text or reply)
    await _append_assistant_turn(conversation, reply)
    await plugin_manager.execute_after_turn(conversation.plugin_id, turn_ctx, reply)
    return {"text": reply, "evidence": list(conversation.turn_evidence)}


@app.delete("/api/conversations/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_conversation(conversation_id: str) -> None:
    if not await _close_and_delete_conversation(conversation_id):
        raise HTTPException(status_code=404, detail="Conversation not found")


@app.get("/speech/{clip_id}")
async def get_speech(clip_id: str) -> FileResponse:
    clip = speech_clips.pop(clip_id, None)
    if clip is None or not clip.path.is_file():
        raise HTTPException(status_code=404, detail="Speech clip not found")
    conversation = conversations.get(clip.conversation_id)
    if conversation is not None:
        for turn in conversation.turns:
            if turn.get("audio_url") == f"/speech/{clip_id}":
                turn.pop("audio_url", None)
    if clip.path.suffix == ".wav":
        media_type = "audio/wav"
    elif clip.path.suffix == ".mp3":
        media_type = "audio/mpeg"
    else:
        media_type = "audio/mp4"
    return FileResponse(
        clip.path,
        media_type=media_type,
        background=BackgroundTask(_remove_temporary_audio, clip.path),
    )


class SynthesizeInput(BaseModel):
    text: str = Field(min_length=1, max_length=8_000)
    voice: str | None = Field(default=None)


@app.post("/api/speech/synthesize")
async def synthesize_speech(body: SynthesizeInput) -> FileResponse:
    speaker = LocalMacOsSpeaker(voice=body.voice)
    try:
        clip_path = await speaker.synthesize(body.text)
    except LocalSpeechError as exception:
        raise HTTPException(status_code=500, detail=str(exception)) from exception
    if clip_path is None or not clip_path.is_file():
        raise HTTPException(status_code=400, detail="Cannot synthesize non-Cyrillic text")
    if clip_path.suffix == ".wav":
        media_type = "audio/wav"
    elif clip_path.suffix == ".mp3":
        media_type = "audio/mpeg"
    else:
        media_type = "audio/mp4"
    return FileResponse(
        clip_path,
        media_type=media_type,
        background=BackgroundTask(_remove_temporary_audio, clip_path),
    )


ABBREVIATIONS = {
    "т.д.", "т.п.", "т.е.", "руб.", "коп.", "г.", "ул.", "стр.", "рис.",
    "e.g.", "i.e.", "vs.", "etc.", "bros.", "mr.", "mrs.", "dr.", "corp.", "inc.",
}

SENTENCE_SPLIT_RE = re.compile(
    r"""(?x)
    (?:
        (?<=[.!?…])(?:\s+|\n)
        |
        \n+
    )
    """
)

# For the very first chunk of a turn, also allow natural clause boundaries
# (comma, colon, semicolon, dash) if sufficient words have accumulated,
# dramatically reducing time-to-first-audio (TTFA).
FIRST_CHUNK_SPLIT_RE = re.compile(
    r"""(?x)
    (?:
        (?<=[.!?…])(?:\s+|\n)
        |
        \n+
        |
        (?<=[,;:—–])(?:\s+)
    )
    """
)

def _extract_speech_sentence(buffer: str, is_first_chunk: bool = False) -> tuple[str | None, str]:
    return extract_speech_sentence(buffer, is_first_chunk=is_first_chunk)


async def _synthesize_speech_chunk(
    speaker: LocalMacOsSpeaker,
    speaker_voice: str,
    text: str,
    voice: str | None = None,
) -> SpeechSynthesisResult | None:
    """Synthesize one routed language run with its own voice.

    Keeps compatibility with integrations/tests that override the legacy
    path-only ``synthesize`` method: a run for another voice gets its own
    speaker instance, while the primary run keeps the caller's speaker.
    """

    run_voice = voice or speaker_voice
    if getattr(speaker.synthesize, "__func__", None) is not _DEFAULT_SPEAKER_SYNTHESIZE:
        active = speaker if run_voice == speaker_voice else LocalMacOsSpeaker(voice=run_voice)
        path = await active.synthesize(text)
        if path is None:
            return None
        engine_label = _get_tts_engine(run_voice)
        actual_engine = {
            "EDGE_TTS": "edge",
            "PIPER_OFFLINE": "piper",
            "SILERO_OFFLINE": "silero",
            "MACOS_SAY": "macos",
        }.get(engine_label, "macos")
        return SpeechSynthesisResult(
            path=path,
            requested_engine=actual_engine,
            actual_engine=actual_engine,
            actual_voice=run_voice,
        )
    return await speaker.synthesize_with_metadata(text, voice=run_voice)


async def _synthesize_routed_single_clip(
    speaker: LocalMacOsSpeaker,
    text: str,
    plan: VoicePlan,
) -> SpeechSynthesisResult | None:
    """Synthesize routed speech as ONE clip for the single-clip endpoints.

    Single-language text keeps the existing one-call path. Mixed text is
    synthesized per run and joined locally; if the join is impossible the call
    fails instead of silently romanizing the mixed text with one voice.
    """

    chunks = route_speech(sanitize_for_speech(text), plan, resolve_voice=get_ready_voice_for_locale)
    if not chunks:
        return None
    if len(chunks) == 1:
        return await _synthesize_speech_chunk(speaker, plan.primary_voice, chunks[0].text, chunks[0].voice)

    results: list[SpeechSynthesisResult] = []
    joined: Path | None = None
    try:
        for chunk in chunks:
            result = await _synthesize_speech_chunk(speaker, plan.primary_voice, chunk.text, chunk.voice)
            if result is None or result.path is None:
                raise LocalSpeechError(
                    f"Could not synthesize the {chunk.language or chunk.role} segment of a mixed-language reply"
                )
            results.append(result)
        descriptor, raw_joined = tempfile.mkstemp(prefix="voice-of-luna-mixed-", suffix=".wav")
        os.close(descriptor)
        joined = Path(raw_joined)
        joined.unlink(missing_ok=True)
        if not await join_speech_clips([result.path for result in results], joined):
            raise LocalSpeechError(
                "Could not join mixed-language speech locally (ffmpeg is unavailable or the join failed)"
            )
        primary = next(
            (result for result in results if result.actual_voice == plan.primary_voice),
            results[0],
        )
        return SpeechSynthesisResult(
            path=joined,
            requested_engine=primary.requested_engine,
            actual_engine=primary.actual_engine,
            fallback_reason="; ".join(
                result.fallback_reason for result in results if result.fallback_reason
            ) or None,
            actual_voice=primary.actual_voice,
        )
    except BaseException:
        if joined is not None:
            joined.unlink(missing_ok=True)
        raise
    finally:
        for result in results:
            result.path.unlink(missing_ok=True)


async def _gated_turn_and_synthesize(
    websocket: WebSocket,
    conversation: Conversation,
    prompt_text: str,
    *,
    t_start: float | None = None,
) -> None:
    """Run an opted-in plugin turn only after its complete response is approved."""
    await conversation_service.stop_warmup(conversation)
    turn_id = str(uuid4())
    turn_ctx = TurnContext(
        conversation_id=conversation.id,
        user_message=prompt_text,
        turns_history=conversation.turns,
        active_mode=conversation.plugin_mode,
        metadata={"plugin_settings": conversation.plugin_settings},
        state=plugin_storage.for_plugin(conversation.plugin_id, conversation.id),
    )
    plugin_res = await plugin_manager.execute_before_turn(conversation.plugin_id, turn_ctx)
    await websocket.send_json({"type": "status", "state": "thinking", "message": "Luna thinking...", "mode_label": plugin_res.mode_label or "THINKING // CODEX", "turn_taking_profile": plugin_res.metadata.get("turn_taking_profile", "normal")})
    try:
        generated = await _call_reply(
            conversation.model,
            prompt_text,
            context_prompt=plugin_res.prompt_context,
            model_name=conversation.model_name,
            effort=conversation.reasoning_effort,
        )
        decision = await plugin_manager.execute_validate_response(
            conversation.plugin_id,
            turn_ctx,
            ResponseCandidate(conversation.id, prompt_text, generated),
        )
        if decision.action == "reject":
            await websocket.send_json({"type": "error", "message": "Plugin rejected the model response"})
            return
        approved = str(decision.text or generated).strip()
        await websocket.send_json({"type": "delta", "delta": approved})
        turn_lang = resolve_turn_language(conversation, user_text=prompt_text)
        speaker_voice = turn_lang.speaker_voice
        voice_plan = resolve_voice_plan(conversation, turn_lang)
        speaker = LocalMacOsSpeaker(voice=speaker_voice)
        chunks = route_speech(approved, voice_plan, resolve_voice=get_ready_voice_for_locale)
        if not chunks:
            raise CodexUnavailable("Unable to synthesize approved response")
        for chunk in chunks:
            synthesis = await _synthesize_speech_chunk(speaker, speaker_voice, chunk.text, chunk.voice)
            if synthesis is None:
                raise CodexUnavailable("Unable to synthesize approved response")
            clip_id = str(uuid4())
            speech_clips[clip_id] = SpeechClip(conversation_id=conversation.id, path=synthesis.path)
            audio_bytes = await asyncio.to_thread(synthesis.path.read_bytes)
            mime_type = "audio/mpeg" if synthesis.path.suffix == ".mp3" else "audio/wav"
            payload = {"type": "audio_chunk", "clip_id": clip_id, "turn_id": turn_id, "audio_url": f"/speech/{clip_id}", "mime_type": mime_type, "text": chunk.text}
            if conversation.binary_audio:
                header = json.dumps({k: v for k, v in payload.items() if k != "type"}).encode("utf-8")
                await websocket.send_bytes(b"\x01" + len(header).to_bytes(2, "big") + header + audio_bytes)
            else:
                payload["audio_base64"] = base64.b64encode(audio_bytes).decode("ascii")
                await websocket.send_json(payload)
        turn = {"role": "assistant", "text": approved, "turn_id": turn_id}
        conversation.turns.append(turn)
        await plugin_manager.execute_after_turn(conversation.plugin_id, turn_ctx, approved)
        await websocket.send_json({"type": "turn_completed", "turn_id": turn_id, "turn": turn, "timing": {"backend_total_ms": round((time.perf_counter() - t_start) * 1000) if t_start else None}})
    except (CodexUnavailable, LocalSpeechError) as exc:
        await websocket.send_json({"type": "error", "message": str(exc)})


async def _stream_and_synthesize(
    websocket: WebSocket,
    conversation: Conversation,
    prompt_text: str,
    t_start: float | None = None,
    t_audio_prepared: float | None = None,
    t_stt: float | None = None,
    client_timing: dict[str, int] | None = None,
) -> None:
    if plugin_manager.delivery_mode(conversation.plugin_id) == "gated":
        await _gated_turn_and_synthesize(websocket, conversation, prompt_text, t_start=t_start)
        return
    # A technical remote warmup is opportunistic. It must never delay the
    # user's turn, and cancellation must interrupt the remote inference first.
    await conversation_service.stop_warmup(conversation)

    turn_lang = resolve_turn_language(conversation, user_text=prompt_text)
    effective_locale = turn_lang.response_locale
    speaker_voice = turn_lang.speaker_voice
    voice_plan = resolve_voice_plan(conversation, turn_lang)

    speaker = LocalMacOsSpeaker(voice=speaker_voice)
    turn_id = str(uuid4())
    full_reply_parts: list[str] = []
    current_sentence = ""
    in_sources_mode = False
    is_first_chunk = True
    t_first_delta: float | None = None
    t_first_sentence_queued: float | None = None
    t_first_audio: float | None = None

    # Pipelined TTS: pre-synthesize sentence n+1 concurrently while delivering sentence n
    synthesis_jobs: asyncio.Queue[tuple[str, asyncio.Task[SpeechSynthesisResult | None]] | None] = asyncio.Queue()
    synthesis_results: list[SpeechSynthesisResult] = []

    async def _send_audio(clean_text: str, result: SpeechSynthesisResult | None) -> None:
        nonlocal t_first_audio
        if result is None:
            return
        clip_path = result.path
        synthesis_results.append(result)
        try:
            if t_first_audio is None:
                t_first_audio = time.perf_counter()
            clip_id = str(uuid4())
            speech_clips[clip_id] = SpeechClip(conversation_id=conversation.id, path=clip_path)
            audio_bytes = await asyncio.to_thread(clip_path.read_bytes)
            mime_type = "audio/mpeg" if clip_path.suffix == ".mp3" else "audio/wav"

            if conversation.binary_audio:
                # Binary frame: [0x01][2-byte big-endian header len L][JSON UTF-8][Raw audio bytes]
                header_data = json.dumps({
                    "clip_id": clip_id,
                    "turn_id": turn_id,
                    "audio_url": f"/speech/{clip_id}",
                    "mime_type": mime_type,
                    "text": clean_text,
                }).encode("utf-8")
                header_len = len(header_data)
                binary_frame = b"\x01" + header_len.to_bytes(2, byteorder="big") + header_data + audio_bytes
                await websocket.send_bytes(binary_frame)
            else:
                audio_b64 = base64.b64encode(audio_bytes).decode("ascii")
                await websocket.send_json({
                    "type": "audio_chunk",
                    "clip_id": clip_id,
                    "turn_id": turn_id,
                    "audio_url": f"/speech/{clip_id}",
                    "audio_base64": audio_b64,
                    "mime_type": mime_type,
                    "text": clean_text,
                })
        except Exception as exc:
            logger.warning("Speech delivery error during streaming: %s", exc, exc_info=True)

    async def _synthesis_consumer() -> None:
        while True:
            job = await synthesis_jobs.get()
            try:
                if job is None:
                    return
                clean_text, synth_task = job
                try:
                    clip_path = await synth_task
                except Exception as exc:
                    logger.warning("Speech synthesis task error: %s", exc, exc_info=True)
                    clip_path = None
                await _send_audio(clean_text, clip_path)
            finally:
                synthesis_jobs.task_done()

    consumer_task = asyncio.create_task(_synthesis_consumer())
    synth_semaphore = asyncio.Semaphore(2)

    async def _bounded_synthesize(text: str, voice: str | None = None) -> SpeechSynthesisResult | None:
        async with synth_semaphore:
            return await _synthesize_speech_chunk(speaker, speaker_voice, text, voice)

    async def _queue_sentence(sentence_to_deliver: str) -> None:
        nonlocal t_first_sentence_queued
        clean_text = sanitize_for_speech(sentence_to_deliver).strip()
        if not clean_text:
            return
        if t_first_sentence_queued is None:
            t_first_sentence_queued = time.perf_counter()
        for chunk in route_speech(clean_text, voice_plan, resolve_voice=get_ready_voice_for_locale):
            synth_task = asyncio.create_task(_bounded_synthesize(chunk.text, chunk.voice))
            await synthesis_jobs.put((chunk.text, synth_task))

    turn_ctx = TurnContext(
        conversation_id=conversation.id,
        user_message=prompt_text,
        turns_history=conversation.turns,
        active_mode=conversation.plugin_mode,
        metadata={"plugin_settings": conversation.plugin_settings},
        state=plugin_storage.for_plugin(conversation.plugin_id, conversation.id),
    )
    plugin_res = await plugin_manager.execute_before_turn(conversation.plugin_id, turn_ctx)
    mode_label = plugin_res.mode_label or "THINKING // CODEX"

    await websocket.send_json({
        "type": "status",
        "state": "thinking",
        "message": "Luna thinking...",
        "mode_label": mode_label,
        "turn_taking_profile": plugin_res.metadata.get("turn_taking_profile", "normal"),
    })

    try:
        async for delta in _call_reply_stream(
            conversation.model,
            prompt_text,
            context_prompt=plugin_res.prompt_context,
            model_name=conversation.model_name,
            effort=conversation.reasoning_effort,
        ):
            if t_first_delta is None:
                t_first_delta = time.perf_counter()
            full_reply_parts.append(delta)
            await websocket.send_json({
                "type": "delta",
                "delta": delta,
            })
            if in_sources_mode:
                continue

            current_sentence += delta
            sources_match = SOURCES_SPLIT_RE.search(current_sentence)
            if sources_match:
                in_sources_mode = True
                before_sources = current_sentence[: sources_match.start()]
                if before_sources.strip():
                    await _queue_sentence(before_sources)
                current_sentence = ""
                continue

            sentence, current_sentence = _extract_speech_sentence(current_sentence, is_first_chunk=is_first_chunk)
            while sentence:
                is_first_chunk = False
                await _queue_sentence(sentence)
                sentence, current_sentence = _extract_speech_sentence(current_sentence, is_first_chunk=False)

        if not in_sources_mode and current_sentence.strip():
            await _queue_sentence(current_sentence)

        await synthesis_jobs.put(None)
        await consumer_task

        full_reply = "".join(full_reply_parts).strip()
        if not full_reply:
            raise CodexUnavailable("Codex finished without a spoken response")

        turn = {"role": "assistant", "text": full_reply, "turn_id": turn_id}
        if conversation.turn_evidence:
            turn["evidence"] = list(conversation.turn_evidence)
        conversation.turns.append(turn)
        _safe_background_task(
            plugin_manager.execute_after_turn(
                conversation.plugin_id, turn_ctx, full_reply
            ),
            name=f"after-turn-{conversation.id}",
        )

        t_turn_completed = time.perf_counter()
        timing: dict[str, float | None] = {}
        if t_start:
            if t_audio_prepared:
                timing["server_audio_prep_ms"] = round((t_audio_prepared - t_start) * 1000)
            if t_stt:
                timing["stt_ms"] = round((t_stt - (t_audio_prepared or t_start)) * 1000)
            if t_first_delta:
                base_llm = t_stt or t_start
                timing["llm_first_delta_ms"] = round((t_first_delta - base_llm) * 1000)
            if t_first_sentence_queued and t_first_delta:
                timing["first_speech_segment_wait_ms"] = round((t_first_sentence_queued - t_first_delta) * 1000)
            if t_first_audio and t_first_sentence_queued:
                timing["tts_synthesis_first_chunk_ms"] = round((t_first_audio - t_first_sentence_queued) * 1000)
            if t_first_audio:
                timing["backend_first_audio_ms"] = round((t_first_audio - t_start) * 1000)
            timing["backend_total_ms"] = round((t_turn_completed - t_start) * 1000)
        if client_timing and client_timing.get("endpoint_delay_ms") is not None:
            timing["client_endpoint_delay_ms"] = client_timing["endpoint_delay_ms"]
        if client_timing and client_timing.get("audio_encode_ms") is not None:
            timing["client_audio_encode_ms"] = client_timing["audio_encode_ms"]

        await websocket.send_json({
            "type": "turn_completed",
            "turn_id": turn_id,
            "turn": turn,
            "timing": timing,
            "effective_locale": effective_locale,
            "input_locale": turn_lang.input_locale,
            "response_locale": turn_lang.response_locale,
            "voice_locale": turn_lang.voice_locale,
            "voice": speaker_voice,
            "requested_tts_engine": _get_tts_engine(speaker_voice),
            "tts_engine": (
                {"edge": "EDGE_TTS", "piper": "PIPER_OFFLINE", "silero": "SILERO_OFFLINE", "macos": "MACOS_SAY"}.get(
                    synthesis_results[0].actual_engine,
                    _get_tts_engine(speaker_voice),
                )
                if synthesis_results and len({result.actual_engine for result in synthesis_results}) == 1
                else ("MIXED" if synthesis_results else _get_tts_engine(speaker_voice))
            ),
            "tts_fallback_reason": "; ".join(
                result.fallback_reason for result in synthesis_results if result.fallback_reason
            ) or None,
        })
        await websocket.send_json({
            "type": "status",
            "state": "idle",
            "message": "Press [Space] or click radar to speak",
            "mode_label": "IDLE // READY",
        })
    except CodexUnavailable as error:
        await websocket.send_json({
            "type": "error",
            "message": str(error),
        })
        await websocket.send_json({
            "type": "status",
            "state": "idle",
            "message": f"Error: {error}",
            "mode_label": "ERR // CODEX",
        })
    finally:
        if not consumer_task.done():
            consumer_task.cancel()
            await asyncio.gather(consumer_task, return_exceptions=True)
        # Cancel any pending pre-synthesis tasks remaining in the queue, await
        # them, and delete clips that were produced but never delivered. Without
        # this, a barge-in leaks temporary artifacts (``to_thread`` work already
        # running cannot be interrupted, so the file may still appear).
        abandoned: list[asyncio.Task[SpeechSynthesisResult | None]] = []
        while not synthesis_jobs.empty():
            try:
                item = synthesis_jobs.get_nowait()
            except Exception:
                break
            if item is not None:
                _, task = item
                if not task.done():
                    task.cancel()
                abandoned.append(task)
            synthesis_jobs.task_done()
        if abandoned:
            for result in await asyncio.gather(*abandoned, return_exceptions=True):
                if isinstance(result, SpeechSynthesisResult) and result.path is not None:
                    result.path.unlink(missing_ok=True)


def _streaming_enabled_by_env() -> bool:
    """Global streaming kill switches (new name + legacy T-One flag)."""

    return (
        os.environ.get("VOICE_OF_LUNA_STREAMING_STT") != "0"
        and os.environ.get("VOICE_OF_LUNA_TONE_STREAMING") != "0"
    )


def _streaming_language(conversation: Conversation) -> str:
    prefix = (conversation.locale or "").split("-")[0].lower()
    return prefix if prefix in {"ru", "es", "en"} else "auto"


def _streaming_engine_model_id(conversation: Conversation) -> str | None:
    """Resolve the installed streaming model for the conversation locale."""

    if not _streaming_enabled_by_env():
        return None
    return stt_model_manager.resolve_model_id(conversation.locale, installed_only=True)


def _live_transcript_enabled(conversation: Conversation) -> bool:
    """Resolve the effective live-transcript state for one conversation.

    A locally installed streaming model turns streaming on by default (D8).
    Global kill switches and the per-conversation UI toggle can disable it.
    When disabled, the batch Whisper path is authoritative.
    """

    if conversation.live_transcript is False:
        return False
    return _streaming_engine_model_id(conversation) is not None


def _streaming_state(conversation: Conversation) -> dict[str, object]:
    """Describe streaming availability and the relevant model for the UI."""

    if not _streaming_enabled_by_env():
        return {"available": False, "model": None}
    installed_id = _streaming_engine_model_id(conversation)
    if installed_id:
        return {"available": True, "model": stt_model_manager.get_status(installed_id)}
    target_id = stt_model_manager.download_target(conversation.locale)
    if target_id:
        return {"available": False, "model": stt_model_manager.get_status(target_id)}
    return {"available": False, "model": None}


def _maybe_autodownload_streaming(conversation: Conversation) -> None:
    """Start a background download of the locale's default streaming model."""

    if not _streaming_enabled_by_env():
        return
    if os.environ.get("VOICE_OF_LUNA_STT_AUTODOWNLOAD", "1") == "0":
        return
    if not sherpa_available():
        return
    target_id = stt_model_manager.download_target(conversation.locale)
    if not target_id or stt_model_manager.is_installed(target_id):
        return
    # Registered synchronously so /status reports "downloading" right away.
    stt_model_manager.start_download(target_id)


@app.websocket("/ws/conversations/{conversation_id}")
async def conversation_websocket(websocket: WebSocket, conversation_id: str):
    await websocket.accept()
    conversation = _recover_html_conversation(conversation_id)
    conversation.active_websockets += 1
    _touch_conversation(conversation)
    _prewarm_conversation(conversation)
    _maybe_autodownload_streaming(conversation)
    streaming_state = _streaming_state(conversation)
    await websocket.send_json({
        "type": "ready",
        "conversation_id": conversation.id,
        "model": conversation.model_name,
        "requested_model": conversation.requested_model,
        "effort": conversation.reasoning_effort,
        "voice": conversation.voice,
        "locale": conversation.locale,
        "tts_engine": _get_tts_engine(conversation.voice),
        "live_transcript_available": streaming_state["available"],
        "live_transcript": _live_transcript_enabled(conversation),
        "live_transcript_model": streaming_state["model"],
        "streaming_runtime_available": sherpa_available(),
        **_stt_model_context(conversation),
    })

    active_turn_task: asyncio.Task[None] | None = None
    pending_audio_timing: dict[str, int] | None = None
    streaming_pcm: bytearray | None = None
    streaming_sample_rate = 16_000
    stream_session = None
    stream_last_partial = ""
    stream_provider = "stt"

    try:
        while True:
            message = await websocket.receive()
            if message.get("type") == "websocket.disconnect":
                break
            if "bytes" in message and message["bytes"]:
                _touch_conversation(conversation)
                raw_bytes = message["bytes"]
                client_timing = pending_audio_timing
                pending_audio_timing = None
                if active_turn_task and not active_turn_task.done():
                    active_turn_task.cancel()
                    await conversation.model.interrupt()

                async def handle_audio(data: bytes, *, pcm_sample_rate: int | None = None):
                    t_recv = time.perf_counter()
                    await websocket.send_json({
                        "type": "status",
                        "state": "transcribing",
                        "message": "Transcribing audio...",
                        "mode_label": "PROCESSING // STT",
                    })
                    temporary_path = _write_temporary_audio(data, ".wav")
                    wav_path = None
                    try:
                        if pcm_sample_rate is not None:
                            pcm_path = temporary_path
                            with wave.open(str(pcm_path), "wb") as pcm_file:
                                pcm_file.setnchannels(1)
                                pcm_file.setsampwidth(2)
                                pcm_file.setframerate(pcm_sample_rate)
                                pcm_file.writeframes(data)
                        if _is_16k_mono_wav(temporary_path):
                            wav_path = temporary_path
                        else:
                            wav_path = await _convert_to_wav(temporary_path)
                        t_audio_prepared = time.perf_counter()
                        stt_config = resolve_stt_config(conversation)
                        batch_model_id = _require_batch_stt_model(conversation)
                        transcript = await _build_stt_provider(
                            language=stt_config.language,
                            prompt=stt_config.prompt,
                            stt_model=batch_model_id,
                        ).transcribe(wav_path)
                        t_stt = time.perf_counter()
                        if tone_shadow_configured():
                            shadow_bytes = await asyncio.to_thread(wav_path.read_bytes)
                            _safe_background_task(
                                _record_tone_shadow_observation(conversation.id, shadow_bytes),
                                name=f"tone-shadow-{conversation.id}",
                            )
                        conversation.turns.append({"role": "user", "text": transcript})
                        await websocket.send_json({
                            "type": "transcript",
                            "role": "user",
                            "text": transcript,
                        })
                        await _stream_and_synthesize(
                            websocket,
                            conversation,
                            transcript,
                            t_start=t_recv,
                            t_audio_prepared=t_audio_prepared,
                            t_stt=t_stt,
                            client_timing=client_timing,
                        )
                    except (AudioConversionError, LocalTranscriptionError, CodexUnavailable) as exc:
                        await websocket.send_json({"type": "error", "message": str(exc)})
                        await websocket.send_json({
                            "type": "status",
                            "state": "idle",
                            "message": str(exc),
                            "mode_label": "ERR // STT",
                        })
                    finally:
                        await asyncio.to_thread(_remove_temporary_audio, temporary_path)
                        if wav_path is not None and wav_path != temporary_path:
                            await asyncio.to_thread(_remove_temporary_audio, wav_path)

                if streaming_pcm is not None:
                    streaming_pcm.extend(raw_bytes)
                    if stream_session is not None:
                        try:
                            partial = await stream_session.push_pcm(raw_bytes, streaming_sample_rate)
                        except Exception as exc:
                            logger.warning("Live transcript push failed, falling back to batch: %s", exc)
                            try:
                                await stream_session.cancel()
                            except Exception:
                                pass
                            stream_session = None
                            partial = ""
                        if partial and partial != stream_last_partial:
                            stream_last_partial = partial
                            await websocket.send_json({
                                "type": "stt_partial",
                                "provider": stream_provider,
                                "text": partial,
                                "interim": True,
                                "final": False,
                            })
                    continue
                active_turn_task = asyncio.create_task(handle_audio(raw_bytes))

            elif "text" in message and message["text"]:
                import json

                try:
                    payload = json.loads(message["text"])
                except json.JSONDecodeError:
                    continue

                msg_type = payload.get("type")
                if msg_type == "audio_stream_start":
                    if streaming_pcm is not None:
                        await websocket.send_json({"type": "error", "message": "Audio stream is already active"})
                        continue
                    sample_rate = payload.get("sample_rate", 16_000)
                    if not isinstance(sample_rate, int) or not 8_000 <= sample_rate <= 48_000:
                        await websocket.send_json({"type": "error", "message": "Invalid PCM sample rate"})
                        continue
                    streaming_sample_rate = sample_rate
                    streaming_pcm = bytearray()
                    stream_last_partial = ""
                    stream_session = None
                    stream_provider = "stt"
                    streaming_model_id = _streaming_engine_model_id(conversation)
                    if _live_transcript_enabled(conversation) and streaming_model_id:
                        model_def = stt_model_manager.get(streaming_model_id)
                        try:
                            stream_session = await asyncio.to_thread(
                                create_streaming_session,
                                model_def.engine,
                                stt_model_manager.model_dir(streaming_model_id),
                                sample_rate,
                                _streaming_language(conversation),
                            )
                            stream_provider = model_def.engine
                            await websocket.send_json({
                                "type": "status",
                                "state": "transcribing",
                                "message": "Live transcript streaming is active...",
                                "mode_label": f"STREAM // {model_def.engine.upper()}",
                            })
                        except LocalTranscriptionError as exc:
                            await websocket.send_json({
                                "type": "status",
                                "state": "transcribing",
                                "message": str(exc),
                                "mode_label": "STREAM // WHISPER",
                            })
                        except Exception as exc:
                            logger.warning("Live transcript session failed, falling back to batch: %s", exc)
                            stream_session = None
                            await websocket.send_json({
                                "type": "status",
                                "state": "transcribing",
                                "message": "Live transcript unavailable; using batch transcription.",
                                "mode_label": "STREAM // WHISPER",
                            })
                    pending_audio_timing = None
                    await websocket.send_json({
                        "type": "status",
                        "state": "transcribing",
                        "message": "Receiving microphone stream...",
                        "mode_label": "STREAM // PCM",
                    })
                elif msg_type == "audio_stream_end":
                    if streaming_pcm is None:
                        await websocket.send_json({"type": "error", "message": "No audio stream is active"})
                        continue
                    raw_pcm = bytes(streaming_pcm)
                    streaming_pcm = None
                    if not raw_pcm:
                        if stream_session is not None:
                            await stream_session.cancel()
                            stream_session = None
                        await websocket.send_json({"type": "error", "message": "Audio stream was empty"})
                        continue
                    if stream_session is not None:
                        try:
                            partial = await stream_session.finalize()
                        except Exception as exc:
                            logger.warning("Live transcript finalize failed, using batch: %s", exc)
                            partial = ""
                        if partial and partial != stream_last_partial:
                            await websocket.send_json({
                                "type": "stt_partial",
                                "provider": stream_provider,
                                "text": partial,
                                "interim": True,
                                "final": True,
                            })
                        try:
                            await stream_session.cancel()
                        except Exception:
                            pass
                        stream_session = None
                    client_timing = pending_audio_timing
                    pending_audio_timing = None
                    if active_turn_task and not active_turn_task.done():
                        active_turn_task.cancel()
                        await conversation.model.interrupt()
                    active_turn_task = asyncio.create_task(
                        handle_audio(raw_pcm, pcm_sample_rate=streaming_sample_rate)
                    )
                elif msg_type == "set_voice":
                    new_voice = payload.get("voice", "").strip()
                    if new_voice:
                        conversation.set_selected_voice(new_voice)
                        _safe_background_task(prewarm_voice(new_voice), name=f"prewarm-voice-{conversation.id}")
                        await websocket.send_json({
                            "type": "voice_updated",
                            "voice": new_voice,
                            "tts_engine": _get_tts_engine(new_voice),
                        })
                elif msg_type == "set_locale":
                    new_locale = payload.get("locale", "").strip()
                    if new_locale:
                        conversation.locale = new_locale
                        new_voice = get_default_voice_for_locale(new_locale)
                        conversation.set_selected_voice(new_voice)
                        _safe_background_task(prewarm_voice(new_voice), name=f"prewarm-voice-{conversation.id}")
                        base_instructions = await _refresh_conversation_base_instructions(conversation, override_locale=new_locale)
                        _maybe_autodownload_streaming(conversation)
                        streaming_state = _streaming_state(conversation)
                        await websocket.send_json({
                            "type": "locale_updated",
                            "locale": new_locale,
                            "voice": new_voice,
                            "tts_engine": _get_tts_engine(new_voice),
                            "live_transcript_available": streaming_state["available"],
                            "live_transcript": _live_transcript_enabled(conversation),
                            "live_transcript_model": streaming_state["model"],
                        })
                elif msg_type == "set_settings":
                    if "model" in payload and payload["model"]:
                        conversation.model_name = payload["model"]
                        conversation.requested_model = payload["model"]
                    if "effort" in payload and payload["effort"]:
                        conversation.reasoning_effort = payload["effort"]
                    if "voice" in payload and payload["voice"]:
                        conversation.set_selected_voice(payload["voice"])
                        _safe_background_task(prewarm_voice(conversation.voice), name=f"prewarm-voice-{conversation.id}")
                    if "locale" in payload and payload["locale"]:
                        conversation.locale = payload["locale"]
                        await _refresh_conversation_base_instructions(conversation)
                        _maybe_autodownload_streaming(conversation)
                    if "binary_audio" in payload:
                        conversation.binary_audio = bool(payload["binary_audio"])
                    if "stt_model" in payload:
                        requested_stt = payload.get("stt_model") or None
                        if requested_stt and not is_batch_stt_model(requested_stt):
                            await websocket.send_json({
                                "type": "error",
                                "message": "Unknown batch speech-to-text model",
                            })
                        else:
                            conversation.stt_model = requested_stt
                    if "live_transcript" in payload:
                        conversation.live_transcript = bool(payload["live_transcript"])
                    warmup_status = "off"
                    if payload.get("remote_warmup") is True:
                        warmup_status = _schedule_conversation_warmup(
                            conversation,
                            conversation.model_name or DEFAULT_MODEL,
                            conversation.reasoning_effort,
                        )
                    elif payload.get("remote_warmup") is False:
                        if conversation.remote_warmup_task and not conversation.remote_warmup_task.done():
                            conversation.remote_warmup_task.cancel()
                        conversation.remote_warmup_key = None
                        conversation.remote_warmup_status = "cold"
                    await websocket.send_json({
                        "type": "settings_updated",
                        "model": conversation.model_name,
                        "effort": conversation.reasoning_effort,
                        "voice": conversation.voice,
                        "locale": conversation.locale,
                        "tts_engine": _get_tts_engine(conversation.voice),
                        "live_transcript": _live_transcript_enabled(conversation),
                        "live_transcript_model": _streaming_state(conversation)["model"],
                        "remote_warmup_status": warmup_status,
                        **_stt_model_context(conversation),
                    })
                elif msg_type == "set_plugin":
                    new_plugin = payload.get("plugin_id", "neutral").strip()
                    new_mode = payload.get("mode", "default").strip()
                    raw_settings = payload.get("settings")
                    plugin_settings = raw_settings if isinstance(raw_settings, dict) else None
                    try:
                        await _apply_plugin_to_conversation(
                            conversation, new_plugin, new_mode, settings=plugin_settings
                        )
                    except ValueError as exc:
                        await websocket.send_json({"type": "error", "message": str(exc)})
                        continue
                    effective_voice = resolve_turn_language(conversation).speaker_voice
                    await websocket.send_json({
                        "type": "plugin_updated",
                        "plugin_id": conversation.plugin_id,
                        "mode": conversation.plugin_mode,
                        "panel": plugin_manager.panel_schema(conversation.plugin_id),
                        "settings": conversation.plugin_settings,
                        "voice": effective_voice,
                        "tts_engine": _get_tts_engine(effective_voice),
                    })
                elif msg_type == "stop_speaking":
                    if active_turn_task and not active_turn_task.done():
                        active_turn_task.cancel()
                    await conversation.model.interrupt()
                    await websocket.send_json({
                        "type": "status",
                        "state": "idle",
                        "message": "Playback stopped",
                        "mode_label": "IDLE // READY",
                    })
                elif msg_type == "audio_timing":
                    endpoint_delay = payload.get("endpoint_delay_ms")
                    audio_encode = payload.get("audio_encode_ms")
                    if isinstance(endpoint_delay, (int, float)) and 0 <= endpoint_delay <= 10_000:
                        pending_audio_timing = {"endpoint_delay_ms": round(endpoint_delay)}
                        if isinstance(audio_encode, (int, float)) and 0 <= audio_encode <= 10_000:
                            pending_audio_timing["audio_encode_ms"] = round(audio_encode)
                elif msg_type == "output_event":
                    state = payload.get("state")
                    turn_id = payload.get("turn_id")
                    clip_id = payload.get("clip_id")
                    if state not in {"started", "completed", "interrupted", "failed"} or not isinstance(turn_id, str) or not isinstance(clip_id, str):
                        await websocket.send_json({"type": "error", "message": "Invalid output event"})
                        continue
                    await plugin_manager.execute_output_event(
                        conversation.plugin_id,
                        OutputEvent(
                            conversation_id=conversation.id,
                            turn_id=turn_id[:100],
                            clip_id=clip_id[:100],
                            state=state,
                            delivered_text=(payload.get("delivered_text")[:4000] if isinstance(payload.get("delivered_text"), str) else None),
                            metadata={"source": "browser", "replay": bool(payload.get("replay"))},
                        ),
                    )
                elif msg_type == "text":
                    text = payload.get("text", "").strip()
                    if not text:
                        continue
                    _touch_conversation(conversation)
                    if active_turn_task and not active_turn_task.done():
                        active_turn_task.cancel()
                        await conversation.model.interrupt()

                    async def handle_text(prompt: str):
                        t_start = time.perf_counter()
                        conversation.turns.append({"role": "user", "text": prompt})
                        await websocket.send_json({
                            "type": "transcript",
                            "role": "user",
                            "text": prompt,
                        })
                        await _stream_and_synthesize(
                            websocket,
                            conversation,
                            prompt,
                            t_start=t_start,
                        )

                    active_turn_task = asyncio.create_task(handle_text(text))

    except WebSocketDisconnect:
        pass
    finally:
        if active_turn_task and not active_turn_task.done():
            active_turn_task.cancel()
        streaming_pcm = None
        if stream_session is not None:
            try:
                await stream_session.cancel()
            except Exception:
                pass
        conversation.active_websockets = max(0, conversation.active_websockets - 1)
        _touch_conversation(conversation)


async def _append_assistant_turn(conversation: Conversation, text: str) -> str | None:
    turn = {"role": "assistant", "text": text}
    if conversation.turn_evidence:
        turn["evidence"] = list(conversation.turn_evidence)
    source_user_text = next(
        (
            previous_turn.get("text", "")
            for previous_turn in reversed(conversation.turns)
            if previous_turn.get("role") == "user"
        ),
        "",
    )
    turn_lang = resolve_turn_language(conversation, user_text=source_user_text)
    voice_to_use = turn_lang.speaker_voice
    try:
        synthesis = await _synthesize_routed_single_clip(
            LocalMacOsSpeaker(voice=voice_to_use), text, resolve_voice_plan(conversation, turn_lang)
        )
        speech_path = synthesis.path if synthesis is not None else None
    except LocalSpeechError as exception:
        conversation.turns.append(turn)
        return str(exception)
    if speech_path is not None:
        clip_id = str(uuid4())
        speech_clips[clip_id] = SpeechClip(conversation_id=conversation.id, path=speech_path)
        turn["audio_url"] = f"/speech/{clip_id}"
    conversation.turns.append(turn)
    return None


def _recover_html_conversation(conversation_id: str) -> Conversation:
    return conversation_service.recover_html_conversation(conversation_id)


def _write_temporary_audio(recording: bytes, suffix: str) -> Path:
    return write_temporary_audio(recording, suffix)


def _remove_temporary_audio(path: Path) -> None:
    remove_temporary_audio(path)


def _is_16k_mono_wav(path: Path) -> bool:
    return is_16k_mono_wav(path)


async def _convert_to_wav(source: Path) -> Path:
    return await convert_to_wav(source)
