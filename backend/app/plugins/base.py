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


@dataclass(frozen=True)
class ToolSpec:
    """A model-visible, host-executed plugin tool declaration."""

    namespace: str
    name: str
    description: str
    input_schema: dict[str, Any]
    required_permission: str = ""

    @property
    def qualified_name(self) -> str:
        return f"{self.namespace}.{self.name}"

    def as_dynamic_tool(self) -> dict[str, Any]:
        """Return the app-server dynamicTools wire shape."""
        return {
            "name": self.qualified_name,
            "description": self.description,
            "inputSchema": self.input_schema,
        }


@dataclass
class ToolResult:
    """Safe structured result returned to the app-server."""

    content_items: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def as_rpc_result(self) -> dict[str, Any]:
        return {"contentItems": self.content_items}


@dataclass(frozen=True)
class ToolCallContext:
    conversation_id: str
    plugin_id: str
    active_mode: str
    metadata: dict[str, Any] = field(default_factory=dict)
    storage: Any = None
    github: Any = None


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
    response_locale_override: str | None = None

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

    def tools(self) -> list[ToolSpec]:
        """Declare model-callable tools. Hooks remain valid for old plugins."""
        return []

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, Any],
        ctx: ToolCallContext,
    ) -> ToolResult:
        raise ValueError(f"Plugin {self.id!r} does not provide tool {name!r}")

    def get_modes(self) -> list[dict[str, str]]:
        """List of selectable modes/sub-modes for this plugin in the UI."""
        return [{"id": "default", "label": "Default"}]
