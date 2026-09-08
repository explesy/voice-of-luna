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
    convert_to_wav,
    detect_effective_turn_locale,
    extract_speech_sentence,
    is_16k_mono_wav,
    remove_temporary_audio,
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
    resolve_stt_config,
    resolve_turn_language,
)
from .i18n import get_ui_text

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
    CodexAppServer,
    CodexUnavailable,
    get_base_instructions,
)
from .plugins import PluginTurnResult, TurnContext, plugin_manager
from .speak import (
    LocalMacOsSpeaker,
    LocalSpeechError,
    SpeechSynthesisResult,
    get_active_voice,
    get_default_voice,
    get_default_voice_for_locale,
    get_installed_voices,
    get_voice_for_locale,
    is_edge_voice,
    is_piper_voice,
    is_silero_voice,
    prewarm_voice,
    sanitize_for_speech,
)
from .transcribe import LocalTranscriptionError, LocalWhisperTranscriber
from .whisper_server import WhisperServerManager

whisper_server = WhisperServerManager()
CONVERSATION_IDLE_TTL_SECONDS = int(os.environ.get("VOICE_OF_LUNA_CONVERSATION_IDLE_TTL_SECONDS", "900"))
CONVERSATION_REAPER_INTERVAL_SECONDS = 60
_DEFAULT_SPEAKER_SYNTHESIZE = LocalMacOsSpeaker.synthesize


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
    conversation: Conversation, plugin_id: str, mode: str = "default"
) -> None:
    await conversation_service.apply_plugin(conversation, plugin_id, mode=mode)


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
    russian_voices = [v for v in all_voices if v["is_russian"]]
    other_voices = [v for v in all_voices if not v["is_russian"]]
    edge_voices = [v for v in all_voices if v.get("engine") == "edge"]
    piper_voices = [v for v in russian_voices if v.get("engine") == "piper"]
    silero_voices = [v for v in russian_voices if v.get("engine") == "silero"]
    local_russian_voices = [v for v in russian_voices if v.get("engine") not in ("edge", "silero", "piper")]

    models: list[dict[str, object]] = []
    if conversation:
        models = await conversation.model.list_models()
    else:
        models = await CodexAppServer().list_models()

    active_model = (conversation.model_name if conversation else None) or (cookie_model.strip('"') if cookie_model else None) or (models[0]["id"] if models else "gpt-5.4-mini")
    active_effort = (conversation.reasoning_effort if conversation else None) or (cookie_effort.strip('"') if cookie_effort else "low")
    if active_model and not any(m.get("id") == active_model for m in models):
        models = list(models) + [{
            "id": active_model,
            "displayName": active_model,
            "description": "",
            "supportedReasoningEfforts": [],
        }]
    if conversation:
        conversation.model_name = active_model
        conversation.reasoning_effort = active_effort
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
        "models": models,
        "active_model": active_model,
        "active_effort": active_effort,
        "remote_warmup_enabled": remote_warmup_enabled,
        "supported_efforts": ["low", "medium", "high", "xhigh"],
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
    russian_voices = [v for v in all_voices if v["is_russian"]]
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
        "other_voices": [v for v in all_voices if not v["is_russian"]],
        "all_voices": all_voices,
        "models": [],
        "active_model": "gpt-5.4-mini",
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
    turn_ctx = TurnContext(
        conversation_id=conversation.id,
        user_message=text,
        turns_history=conversation.turns,
        active_mode=conversation.plugin_mode,
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
        transcript = await LocalWhisperTranscriber(
            language=stt_config.language, prompt=stt_config.prompt
        ).transcribe(wav_path)
        t_stt = time.perf_counter()
        conversation.turns.append({"role": "user", "text": transcript})
        turn_ctx = TurnContext(
            conversation_id=conversation.id,
            user_message=transcript,
            turns_history=conversation.turns,
            active_mode=conversation.plugin_mode,
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
async def list_tts_models() -> dict[str, object]:
    from app.tts_manager import tts_model_manager

    return {"models": tts_model_manager.list_all_models()}


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


@app.get("/api/models")
async def list_available_models(request: Request) -> dict[str, object]:
    provider = CodexAppServer()
    try:
        models = await provider.list_models()
    finally:
        await provider.close()
    cookie_model = request.cookies.get("voice_of_luna_model")
    cookie_effort = request.cookies.get("voice_of_luna_effort")
    active_model = (
        cookie_model.strip('"')
        if cookie_model
        else (models[0]["id"] if models else "gpt-5.4-mini")
    )
    return {
        "models": models,
        "active_model": active_model,
        "active_effort": cookie_effort.strip('"') if cookie_effort else "low",
    }


class SettingsInput(BaseModel):
    model: str | None = None
    effort: str | None = None
    voice: str | None = None
    locale: str | None = None
    remote_warmup: bool | None = None


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
    active_model = body.model or "gpt-5.4-mini"
    active_effort = body.effort or "low"
    remote_warmup_enabled = body.remote_warmup is not False
    warmup_status = "enabled" if remote_warmup_enabled else "off"
    return {
        "ok": True,
        "model": body.model,
        "effort": body.effort,
        "voice": body.voice or get_active_voice(),
        "locale": body.locale,
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


@app.post("/api/conversations/{conversation_id}/plugin")
async def select_plugin(
    conversation_id: str, body: PluginSelectInput, response: Response
) -> dict[str, object]:
    conversation = conversations.get(conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    await _apply_plugin_to_conversation(
        conversation, body.plugin_id, body.mode or "default"
    )
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
    }


@app.post("/api/conversations/{conversation_id}/turns")
async def create_turn(conversation_id: str, body: TurnInput) -> dict[str, str]:
    conversation = conversations.get(conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    conversation.turns.append({"role": "user", "text": body.text})
    turn_ctx = TurnContext(
        conversation_id=conversation.id,
        user_message=body.text,
        turns_history=conversation.turns,
        active_mode=conversation.plugin_mode,
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
    await _append_assistant_turn(conversation, reply)
    await plugin_manager.execute_after_turn(conversation.plugin_id, turn_ctx, reply)
    return {"text": reply}


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


async def _stream_and_synthesize(
    websocket: WebSocket,
    conversation: Conversation,
    prompt_text: str,
    t_start: float | None = None,
    t_audio_prepared: float | None = None,
    t_stt: float | None = None,
    client_timing: dict[str, int] | None = None,
) -> None:
    await _await_conversation_warmup(conversation)

    turn_lang = resolve_turn_language(conversation, user_text=prompt_text)
    effective_locale = turn_lang.response_locale
    speaker_voice = turn_lang.speaker_voice

    speaker = LocalMacOsSpeaker(voice=speaker_voice)
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

    async def _bounded_synthesize(text: str) -> SpeechSynthesisResult | None:
        async with synth_semaphore:
            # Keep compatibility with integrations/tests that override the
            # legacy path-only ``synthesize`` method.
            if getattr(speaker.synthesize, "__func__", None) is not _DEFAULT_SPEAKER_SYNTHESIZE:
                path = await speaker.synthesize(text)
                if path is None:
                    return None
                engine_label = _get_tts_engine(speaker_voice)
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
                )
            return await speaker.synthesize_with_metadata(text)

    async def _queue_sentence(sentence_to_deliver: str) -> None:
        nonlocal t_first_sentence_queued
        clean_text = sanitize_for_speech(sentence_to_deliver).strip()
        if clean_text:
            if t_first_sentence_queued is None:
                t_first_sentence_queued = time.perf_counter()
            synth_task = asyncio.create_task(_bounded_synthesize(clean_text))
            await synthesis_jobs.put((clean_text, synth_task))

    turn_ctx = TurnContext(
        conversation_id=conversation.id,
        user_message=prompt_text,
        turns_history=conversation.turns,
        active_mode=conversation.plugin_mode,
    )
    plugin_res = await plugin_manager.execute_before_turn(conversation.plugin_id, turn_ctx)
    mode_label = plugin_res.mode_label or "THINKING // CODEX"

    await websocket.send_json({
        "type": "status",
        "state": "thinking",
        "message": "Luna thinking...",
        "mode_label": mode_label,
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

        turn = {"role": "assistant", "text": full_reply}
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
        # Cancel any pending pre-synthesis tasks remaining in the queue
        while not synthesis_jobs.empty():
            try:
                item = synthesis_jobs.get_nowait()
                if item is not None:
                    _, task = item
                    if not task.done():
                        task.cancel()
                synthesis_jobs.task_done()
            except Exception:
                break


@app.websocket("/ws/conversations/{conversation_id}")
async def conversation_websocket(websocket: WebSocket, conversation_id: str):
    await websocket.accept()
    conversation = _recover_html_conversation(conversation_id)
    conversation.active_websockets += 1
    _touch_conversation(conversation)
    _prewarm_conversation(conversation)
    await websocket.send_json({
        "type": "ready",
        "conversation_id": conversation.id,
        "model": conversation.model_name,
        "effort": conversation.reasoning_effort,
        "voice": conversation.voice,
        "locale": conversation.locale,
        "tts_engine": _get_tts_engine(conversation.voice),
    })

    active_turn_task: asyncio.Task[None] | None = None
    pending_audio_timing: dict[str, int] | None = None

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

                async def handle_audio(data: bytes):
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
                        if _is_16k_mono_wav(temporary_path):
                            wav_path = temporary_path
                        else:
                            wav_path = await _convert_to_wav(temporary_path)
                        t_audio_prepared = time.perf_counter()
                        stt_config = resolve_stt_config(conversation)
                        transcript = await LocalWhisperTranscriber(
                            language=stt_config.language, prompt=stt_config.prompt
                        ).transcribe(wav_path)
                        t_stt = time.perf_counter()
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

                active_turn_task = asyncio.create_task(handle_audio(raw_bytes))

            elif "text" in message and message["text"]:
                import json

                try:
                    payload = json.loads(message["text"])
                except json.JSONDecodeError:
                    continue

                msg_type = payload.get("type")
                if msg_type == "set_voice":
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
                        await websocket.send_json({
                            "type": "locale_updated",
                            "locale": new_locale,
                            "voice": new_voice,
                            "tts_engine": _get_tts_engine(new_voice),
                        })
                elif msg_type == "set_settings":
                    if "model" in payload and payload["model"]:
                        conversation.model_name = payload["model"]
                    if "effort" in payload and payload["effort"]:
                        conversation.reasoning_effort = payload["effort"]
                    if "voice" in payload and payload["voice"]:
                        conversation.set_selected_voice(payload["voice"])
                        _safe_background_task(prewarm_voice(conversation.voice), name=f"prewarm-voice-{conversation.id}")
                    if "locale" in payload and payload["locale"]:
                        conversation.locale = payload["locale"]
                        await _refresh_conversation_base_instructions(conversation)
                    if "binary_audio" in payload:
                        conversation.binary_audio = bool(payload["binary_audio"])
                    warmup_status = "off"
                    if payload.get("remote_warmup") is True:
                        warmup_status = _schedule_conversation_warmup(
                            conversation,
                            conversation.model_name or "gpt-5.4-mini",
                            conversation.reasoning_effort,
                        )
                    elif payload.get("remote_warmup") is False:
                        if conversation.remote_warmup_task and not conversation.remote_warmup_task.done():
                            conversation.remote_warmup_task.cancel()
                        conversation.remote_warmup_key = None
                    await websocket.send_json({
                        "type": "settings_updated",
                        "model": conversation.model_name,
                        "effort": conversation.reasoning_effort,
                        "voice": conversation.voice,
                        "locale": conversation.locale,
                        "tts_engine": _get_tts_engine(conversation.voice),
                        "remote_warmup_status": warmup_status,
                    })
                elif msg_type == "set_plugin":
                    new_plugin = payload.get("plugin_id", "neutral").strip()
                    new_mode = payload.get("mode", "default").strip()
                    await _apply_plugin_to_conversation(conversation, new_plugin, new_mode)
                    effective_voice = resolve_turn_language(conversation).speaker_voice
                    await websocket.send_json({
                        "type": "plugin_updated",
                        "plugin_id": conversation.plugin_id,
                        "mode": conversation.plugin_mode,
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
        conversation.active_websockets = max(0, conversation.active_websockets - 1)
        _touch_conversation(conversation)


async def _append_assistant_turn(conversation: Conversation, text: str) -> str | None:
    turn = {"role": "assistant", "text": text}
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
        speech_path = await LocalMacOsSpeaker(voice=voice_to_use).synthesize(text)
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
