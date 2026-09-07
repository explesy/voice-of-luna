from __future__ import annotations

import asyncio
import logging
from typing import Any

from .base import Plugin, PluginTurnResult, TurnContext
from .focus_sprint import FocusSprintPlugin
from .spanish_buddy import SpanishBuddyPlugin

logger = logging.getLogger("voice_of_luna.plugins")


class LunaCorePlugin(Plugin):
    """Default neutral plugin: preserves standard Luna voice assistant behavior."""

    id = "neutral"
    name = "Lúna (Core)"
    description = "Standard conversational voice assistant"

    def get_modes(self) -> list[dict[str, str]]:
        return [{"id": "default", "label": "Normal"}]


class TestContextPlugin(Plugin):
    """Test and diagnostic plugin for verifying prompt injection and turn telemetry."""

    __test__ = False

    id = "test_plugin"
    name = "Test Plugin"
    description = "Diagnostic plugin verifying context injection and turn metadata"

    def __init__(self) -> None:
        self.turn_counts: dict[str, int] = {}
        self.session_logs: dict[str, list[dict[str, str]]] = {}

    def get_modes(self) -> list[dict[str, str]]:
        return [
            {"id": "default", "label": "Standard"},
            {"id": "echo_metric", "label": "Echo Metric"},
        ]

    async def system_prompt(self, conversation_id: str) -> str:
        return (
            "You are running with Test Plugin active. "
            "Reply naturally and concisely in 1 short spoken sentence. "
            "Acknowledge test mode if the user asks about it."
        )

    async def before_turn(self, ctx: TurnContext) -> PluginTurnResult:
        count = self.turn_counts.get(ctx.conversation_id, 0) + 1
        self.turn_counts[ctx.conversation_id] = count
        prompt_context = f"[TEST CONTEXT // Turn #{count} | Mode: {ctx.active_mode}]"
        mode_label = f"TEST // #{count}"
        return PluginTurnResult(
            prompt_context=prompt_context,
            mode_label=mode_label,
            metadata={"turn_count": count},
        )

    async def after_turn(self, ctx: TurnContext, assistant_response: str) -> None:
        if ctx.conversation_id not in self.session_logs:
            self.session_logs[ctx.conversation_id] = []
        self.session_logs[ctx.conversation_id].append({
            "user": ctx.user_message,
            "assistant": assistant_response,
        })

    async def on_conversation_reset(self, conversation_id: str) -> None:
        self.turn_counts.pop(conversation_id, None)
        self.session_logs.pop(conversation_id, None)


class PluginManager:
    """Manages plugin registration, lifecycle execution, and safety boundaries."""

    def __init__(self) -> None:
        self._plugins: dict[str, Plugin] = {}
        self.register(LunaCorePlugin())
        self.register(FocusSprintPlugin())
        self.register(SpanishBuddyPlugin())
        self.register(TestContextPlugin())

    def register(self, plugin: Plugin) -> None:
        if not plugin.id:
            raise ValueError("Plugin must have a non-empty id")
        self._plugins[plugin.id] = plugin
        logger.info("Registered plugin: %s (%s)", plugin.name, plugin.id)

    def get(self, plugin_id: str) -> Plugin:
        return self._plugins.get(plugin_id, self._plugins["neutral"])

    def list_plugins(self) -> list[dict[str, Any]]:
        return [
            {
                "id": p.id,
                "name": p.name,
                "description": p.description,
                "modes": p.get_modes(),
            }
            for p in self._plugins.values()
        ]

    def get_stt_language(self, plugin_id: str) -> str | None:
        plugin = self.get(plugin_id)
        return getattr(plugin, "stt_language", None)

    def get_stt_prompt(self, plugin_id: str) -> str | None:
        plugin = self.get(plugin_id)
        return getattr(plugin, "stt_prompt", None)

    def get_preferred_voice_locale(self, plugin_id: str) -> str | None:
        plugin = self.get(plugin_id)
        return getattr(plugin, "preferred_voice_locale", None)

    async def get_system_prompt(
        self, plugin_id: str, conversation_id: str, timeout: float = 1.5
    ) -> str:
        plugin = self.get(plugin_id)
        try:
            return await asyncio.wait_for(
                plugin.system_prompt(conversation_id), timeout=timeout
            )
        except asyncio.TimeoutError:
            logger.warning(
                "Plugin %s.system_prompt timed out after %.1fs for %s",
                plugin_id,
                timeout,
                conversation_id,
            )
            return ""
        except Exception as exc:
            logger.error(
                "Plugin %s.system_prompt failed for %s: %s",
                plugin_id,
                conversation_id,
                exc,
            )
            return ""

    async def execute_before_turn(
        self, plugin_id: str, ctx: TurnContext, timeout: float = 1.5
    ) -> PluginTurnResult:
        plugin = self.get(plugin_id)
        try:
            return await asyncio.wait_for(plugin.before_turn(ctx), timeout=timeout)
        except asyncio.TimeoutError:
            logger.warning(
                "Plugin %s.before_turn timed out after %.1fs in %s",
                plugin_id,
                timeout,
                ctx.conversation_id,
            )
            return PluginTurnResult()
        except Exception as exc:
            logger.error(
                "Plugin %s.before_turn failed in %s: %s",
                plugin_id,
                ctx.conversation_id,
                exc,
            )
            return PluginTurnResult()

    async def execute_after_turn(
        self,
        plugin_id: str,
        ctx: TurnContext,
        assistant_response: str,
        timeout: float = 2.0,
    ) -> None:
        plugin = self.get(plugin_id)
        try:
            await asyncio.wait_for(
                plugin.after_turn(ctx, assistant_response), timeout=timeout
            )
        except asyncio.TimeoutError:
            logger.warning(
                "Plugin %s.after_turn timed out after %.1fs in %s",
                plugin_id,
                timeout,
                ctx.conversation_id,
            )
        except Exception as exc:
            logger.error(
                "Plugin %s.after_turn failed in %s: %s",
                plugin_id,
                ctx.conversation_id,
                exc,
            )

    async def on_conversation_reset(self, plugin_id: str, conversation_id: str) -> None:
        plugin = self.get(plugin_id)
        try:
            await plugin.on_conversation_reset(conversation_id)
        except Exception as exc:
            logger.error(
                "Plugin %s.on_conversation_reset failed for %s: %s",
                plugin_id,
                conversation_id,
                exc,
            )


plugin_manager = PluginManager()
