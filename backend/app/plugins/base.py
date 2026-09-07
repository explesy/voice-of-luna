from __future__ import annotations

from abc import ABC
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class TurnContext:
    """Normalized turn context passed to plugin hooks."""

    conversation_id: str
    user_message: str
    turns_history: list[dict[str, str]]
    active_mode: str = "default"
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class PluginTurnResult:
    """Result returned by plugin.before_turn."""

    prompt_context: str = ""
    mode_label: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class Plugin(ABC):
    """Abstract base class for Voice of Luna plugins.

    Plugins have capability-limited access: they receive only normalized text
    and conversation metadata. They never receive raw audio, process handles,
    or authentication tokens.
    """

    id: str = ""
    name: str = ""
    description: str = ""
    stt_language: str | None = None
    stt_prompt: str | None = None
    preferred_voice_locale: str | None = None

    async def system_prompt(self, conversation_id: str) -> str:
        """Static instructions to append to the base LLM prompt when initializing a thread."""
        return ""

    async def before_turn(self, ctx: TurnContext) -> PluginTurnResult:
        """Hook executed before sending the user's turn to the model.

        Can inject state, relevant facts, or instructions into prompt_context.
        Must complete within the manager's timeout window (default 1.5s).
        """
        return PluginTurnResult()

    async def after_turn(self, ctx: TurnContext, assistant_response: str) -> None:
        """Hook executed after the model finishes answering.

        Used for recording notes, updating training metrics, or debrief logs.
        """
        pass

    async def on_conversation_reset(self, conversation_id: str) -> None:
        """Hook executed when a conversation is deleted or reset."""
        pass

    def get_modes(self) -> list[dict[str, str]]:
        """List of selectable modes/sub-modes for this plugin in the UI."""
        return [{"id": "default", "label": "Default"}]
