"""Conversation domain model, lifecycle management, and Codex reply helpers.

Handles:
- Conversation and SpeechClip dataclasses
- In-memory conversation session registry
- Model and voice prewarming
- Idle conversation reaper
- Codex reply and streaming dispatch
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import time
from collections.abc import AsyncIterator, Coroutine
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.codex import CodexAppServer, get_base_instructions
from app.plugin_storage import PluginStorage
import os

CONVERSATION_IDLE_TTL_SECONDS = int(os.environ.get("VOICE_OF_LUNA_CONVERSATION_IDLE_TTL_SECONDS", "900"))
CONVERSATION_REAPER_INTERVAL_SECONDS = 60
from app.plugins import ToolCallContext, plugin_manager
from app.speak import (
    get_active_voice,
    get_default_voice_for_locale,
    prewarm_voice,
    voice_matches_locale,
)
from app.speech_pipeline import (
    detect_effective_turn_locale,
    remove_temporary_audio,
)

logger = logging.getLogger("voice_of_luna")


def _configured_project_root() -> Path | None:
    configured = os.environ.get("VOICE_OF_LUNA_PROJECT_ROOT", "").strip()
    return Path(configured).expanduser().resolve() if configured else None


@dataclass(frozen=True)
class TurnLanguage:
    input_locale: str
    response_locale: str
    voice_locale: str
    speaker_voice: str


@dataclass(frozen=True)
class SttConfig:
    language: str
    prompt: str


def resolve_stt_config(conversation: Conversation) -> SttConfig:
    """Resolve the shared Whisper language and prompt policy for every transport."""
    language = plugin_manager.get_stt_language(conversation.plugin_id)
    prompt = plugin_manager.get_stt_prompt(conversation.plugin_id) or ""
    if not language:
        if conversation.locale.startswith("en"):
            language = "en"
        elif conversation.locale.startswith("ru"):
            language = "ru"
        else:
            language = "auto"
    if conversation.locale.startswith("en") and not plugin_manager.get_stt_prompt(conversation.plugin_id):
        prompt = ""
    return SttConfig(language=language, prompt=prompt)


def resolve_turn_language(
    conversation: Conversation,
    user_text: str = "",
) -> TurnLanguage:
    """Resolve unified turn language across prompt, STT, TTS, and telemetry.

    Considers:
    1. Active plugin overrides (response_locale_override, preferred_voice_locale).
    2. Dynamic user text language detection when session locale is 'auto'.
    3. Session conversation.locale when fixed (e.g. 'ru-RU', 'en-US', 'es-ES').
    4. Speaker voice compatibility with voice_locale.
    """
    plugin = plugin_manager.get(conversation.plugin_id)
    plugin_override = getattr(plugin, "response_locale_override", None)
    pref_voice_locale = plugin_manager.get_preferred_voice_locale(conversation.plugin_id)

    # 1. Resolve user input locale
    if conversation.locale == "auto":
        input_locale = detect_effective_turn_locale(user_text, fallback_locale="ru-RU")
    else:
        input_locale = conversation.locale

    # 2. Resolve target response locale
    if plugin_override:
        response_locale = plugin_override
    else:
        response_locale = input_locale

    # 3. Resolve voice locale
    if pref_voice_locale:
        voice_locale = pref_voice_locale
    elif plugin_override:
        voice_locale = plugin_override
    else:
        voice_locale = response_locale

    # 4. Resolve speaker voice
    selected_voice = conversation.selected_voice or conversation.voice
    if plugin_override or pref_voice_locale or conversation.locale == "auto":
        if selected_voice and voice_matches_locale(selected_voice, voice_locale):
            speaker_voice = selected_voice
        else:
            speaker_voice = get_default_voice_for_locale(voice_locale)
    else:
        # Fixed locale without plugin override: respect user's explicit voice selection if set
        speaker_voice = selected_voice or get_default_voice_for_locale(voice_locale)

    return TurnLanguage(
        input_locale=input_locale,
        response_locale=response_locale,
        voice_locale=voice_locale,
        speaker_voice=speaker_voice,
    )


@dataclass
class Conversation:
    id: str
    turns: list[dict[str, str]] = field(default_factory=list)
    model: CodexAppServer = field(default_factory=CodexAppServer)
    voice: str = field(default_factory=get_active_voice)
    selected_voice: str | None = None
    locale: str = "ru-RU"
    model_name: str | None = None
    reasoning_effort: str = "low"
    plugin_id: str = "neutral"
    plugin_mode: str = "default"
    last_active_at: float = field(default_factory=time.monotonic)
    active_websockets: int = 0
    remote_warmup_key: tuple[str, str, int, int] | None = None
    remote_warmup_task: asyncio.Task[None] | None = field(default=None, repr=False)
    remote_warmup_status: str = "cold"
    thread_generation: int = 0
    binary_audio: bool = False
    project_root: Path | None = field(default_factory=_configured_project_root)

    def set_selected_voice(self, voice: str) -> None:
        """Persist the user's voice choice while keeping legacy ``voice`` callers in sync."""
        self.selected_voice = voice
        self.voice = voice


@dataclass
class SpeechClip:
    conversation_id: str
    path: Path


class ConversationService:
    """Encapsulates stateful conversation sessions and background lifecycle."""

    def __init__(self) -> None:
        self.conversations: dict[str, Conversation] = {}
        self.speech_clips: dict[str, SpeechClip] = {}
        self._background_tasks: set[asyncio.Task[Any]] = set()

    def safe_background_task(
        self, coro: Coroutine[Any, Any, Any], name: str | None = None
    ) -> asyncio.Task[Any]:
        """Schedule a background task, retaining a strong reference and logging unexpected errors."""
        task = asyncio.create_task(coro, name=name)
        self._background_tasks.add(task)

        def _on_done(t: asyncio.Task[Any]) -> None:
            self._background_tasks.discard(t)
            try:
                exc = t.exception()
                if exc and not isinstance(exc, asyncio.CancelledError):
                    logger.debug(
                        "Background task %s finished with exception: %s",
                        t.get_name(),
                        exc,
                    )
            except asyncio.CancelledError:
                pass
            except Exception as ex:
                logger.debug(
                    "Error checking background task %s exception: %s",
                    t.get_name(),
                    ex,
                )

        task.add_done_callback(_on_done)
        return task

    def prewarm_conversation(self, conversation: Conversation) -> None:
        """Start optional local runtimes before the first user turn."""
        async def _safe_model_prewarm() -> None:
            try:
                await conversation.model.prewarm()
            except Exception as exc:
                logger.debug("Model prewarm skipped or failed for %s: %s", conversation.id, exc)

        async def _safe_voice_prewarm() -> None:
            try:
                await prewarm_voice(conversation.voice)
            except Exception as exc:
                logger.debug("Voice prewarm skipped or failed for %s: %s", conversation.id, exc)

        self.safe_background_task(_safe_model_prewarm(), name=f"prewarm-model-{conversation.id}")
        self.safe_background_task(_safe_voice_prewarm(), name=f"prewarm-voice-{conversation.id}")

    def touch(self, conversation: Conversation) -> None:
        conversation.last_active_at = time.monotonic()

    async def close_and_delete(self, conversation_id: str) -> bool:
        conversation = self.conversations.pop(conversation_id, None)
        if conversation is not None:
            await plugin_manager.on_conversation_reset(conversation.plugin_id, conversation.id)
            try:
                await asyncio.wait_for(conversation.model.close(), timeout=2.0)
            except Exception as exc:
                logger.warning("Error closing conversation model: %s", exc)
        for clip_id, clip in list(self.speech_clips.items()):
            if clip.conversation_id == conversation_id:
                remove_temporary_audio(clip.path)
                del self.speech_clips[clip_id]
        return conversation is not None

    async def reap_idle_conversations(
        self, now: float | None = None, closer: Any = None
    ) -> int:
        """Close local Codex processes for conversations abandoned by every client."""
        current_time = now if now is not None else time.monotonic()
        stale_ids = [
            conversation_id
            for conversation_id, conversation in self.conversations.items()
            if conversation.active_websockets == 0
            and current_time - conversation.last_active_at >= CONVERSATION_IDLE_TTL_SECONDS
        ]
        close_fn = closer if closer is not None else self.close_and_delete
        for conversation_id in stale_ids:
            await close_fn(conversation_id)
        if stale_ids:
            logger.info("Closed %d idle local conversations", len(stale_ids))
        return len(stale_ids)

    async def reaper_loop(self) -> None:
        while True:
            await asyncio.sleep(CONVERSATION_REAPER_INTERVAL_SECONDS)
            try:
                await self.reap_idle_conversations()
            except Exception:
                logger.exception("Failed to reap idle conversations")

    async def run_warmup(
        self, conversation: Conversation, model_name: str, effort: str
    ) -> None:
        """Warm the exact Codex thread that will handle the user's first turn."""
        try:
            await conversation.model.reply(
                "Technical warmup. Reply with exactly one word: ready.",
                model=model_name,
                effort=effort,
            )
            conversation.remote_warmup_status = "warm"
            logger.info("Conversation warmup completed for %s (%s)", model_name, effort)
        except asyncio.CancelledError:
            conversation.remote_warmup_status = "cold"
            raise
        except Exception as exc:
            conversation.remote_warmup_status = "failed"
            logger.warning("Conversation warmup failed for %s (%s): %s", model_name, effort, exc)

    def schedule_warmup(
        self, conversation: Conversation, model_name: str, effort: str
    ) -> str:
        """Run at most one invisible warmup per model and effort in a conversation."""
        key = (
            model_name,
            effort,
            conversation.thread_generation,
            getattr(conversation.model, "thread_generation", 0),
        )
        if conversation.remote_warmup_key == key:
            task = conversation.remote_warmup_task
            return "warming" if task and not task.done() else conversation.remote_warmup_status
        if conversation.remote_warmup_task and not conversation.remote_warmup_task.done():
            conversation.remote_warmup_task.cancel()
        conversation.remote_warmup_key = key
        conversation.remote_warmup_status = "warming"
        conversation.remote_warmup_task = asyncio.create_task(
            self.run_warmup(conversation, model_name, effort)
        )
        return "warming"

    async def await_warmup(self, conversation: Conversation) -> None:
        task = conversation.remote_warmup_task
        if task and not task.done():
            await asyncio.gather(task, return_exceptions=True)

    def recover_html_conversation(self, conversation_id: str) -> Conversation:
        """Keep a stale browser form usable after a local --reload restart."""
        conversation = self.conversations.get(conversation_id)
        if conversation is not None:
            self.touch(conversation)
            return conversation
        conversation = Conversation(id=str(uuid4()))
        self.conversations[conversation.id] = conversation
        self.touch(conversation)
        return conversation

    async def refresh_base_instructions(
        self, conversation: Conversation, override_locale: str | None = None
    ) -> str:
        plugin = plugin_manager.get(conversation.plugin_id)
        plugin_override = getattr(plugin, "response_locale_override", None)
        target_locale = plugin_override or override_locale or conversation.locale
        if target_locale == "auto":
            base_instructions = get_base_instructions("auto")
        else:
            base_instructions = get_base_instructions(target_locale)
        plugin_sys = await plugin_manager.get_system_prompt(conversation.plugin_id, conversation.id)
        if plugin_sys.strip():
            base_instructions = f"{base_instructions}\n\n{plugin_sys.strip()}"
        previous_instructions = conversation.model.base_instructions
        await conversation.model.set_base_instructions(base_instructions)
        if previous_instructions != base_instructions:
            conversation.thread_generation += 1
            conversation.remote_warmup_key = None
            conversation.remote_warmup_status = "cold"
            if conversation.remote_warmup_task and not conversation.remote_warmup_task.done():
                conversation.remote_warmup_task.cancel()
            conversation.remote_warmup_task = None
        return base_instructions

    async def apply_plugin(
        self, conversation: Conversation, plugin_id: str, mode: str = "default"
    ) -> None:
        if conversation.plugin_id != plugin_id:
            conversation.plugin_id = plugin_id
            conversation.plugin_mode = mode
            await self.refresh_base_instructions(conversation)

            selected_plugin = plugin_id

            async def handle_server_request(method: str, params: dict[str, Any]) -> dict[str, Any]:
                if method != "item/tool/call":
                    raise ValueError(f"Unsupported app-server request: {method}")
                tool_name = str(params.get("tool", ""))
                arguments = params.get("arguments", {})
                if not isinstance(arguments, dict):
                    raise ValueError("Tool arguments must be an object")
                result = await plugin_manager.call_tool(
                    selected_plugin,
                    tool_name,
                    arguments,
                    ToolCallContext(
                        conversation_id=conversation.id,
                        plugin_id=selected_plugin,
                        active_mode=conversation.plugin_mode,
                        metadata={
                            "project_root": str(conversation.project_root)
                            if conversation.project_root
                            else None
                        },
                        storage=plugin_storage,
                    ),
                )
                return result.as_rpc_result()

            await conversation.model.set_dynamic_tools(
                plugin_manager.dynamic_tools(plugin_id), handle_server_request
            )
        else:
            conversation.plugin_mode = mode


conversation_service = ConversationService()
plugin_storage = PluginStorage()


async def call_reply(
    model: CodexAppServer,
    text: str,
    context_prompt: str | None = None,
    model_name: str | None = None,
    effort: str | None = None,
) -> str:
    """Invoke model.reply with dynamically inspected argument support."""
    try:
        sig = inspect.signature(model.reply)
    except (TypeError, ValueError):
        return await model.reply(text)

    kwargs: dict[str, object] = {}
    accepts_kwargs = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values())
    if ("context_prompt" in sig.parameters or accepts_kwargs) and context_prompt is not None:
        kwargs["context_prompt"] = context_prompt
    if ("model" in sig.parameters or accepts_kwargs) and model_name is not None:
        kwargs["model"] = model_name
    if ("effort" in sig.parameters or accepts_kwargs) and effort is not None:
        kwargs["effort"] = effort
    return await model.reply(text, **kwargs)


async def call_reply_stream(
    model: CodexAppServer,
    text: str,
    context_prompt: str | None = None,
    model_name: str | None = None,
    effort: str | None = None,
) -> AsyncIterator[str]:
    """Stream replies from model with dynamically inspected argument support."""
    try:
        sig = inspect.signature(model.reply_stream)
    except (TypeError, ValueError):
        async for chunk in model.reply_stream(text):
            yield chunk
        return

    kwargs: dict[str, object] = {}
    accepts_kwargs = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values())
    if ("context_prompt" in sig.parameters or accepts_kwargs) and context_prompt is not None:
        kwargs["context_prompt"] = context_prompt
    if ("model" in sig.parameters or accepts_kwargs) and model_name is not None:
        kwargs["model"] = model_name
    if ("effort" in sig.parameters or accepts_kwargs) and effort is not None:
        kwargs["effort"] = effort
    async for chunk in model.reply_stream(text, **kwargs):
        yield chunk
