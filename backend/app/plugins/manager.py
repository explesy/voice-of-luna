from __future__ import annotations

import asyncio
import logging
from importlib import metadata as importlib_metadata
from typing import Any

from .base import Plugin, PluginTurnResult, ToolCallContext, ToolResult, ToolSpec, TurnContext
from .project_room import ProjectRoomPlugin

logger = logging.getLogger("voice_of_luna.plugins")


class LunaCorePlugin(Plugin):
    """Default neutral plugin: preserves standard Luna voice assistant behavior."""

    id = "neutral"
    name = "Lúna (Core)"
    description = "Standard conversational voice assistant"

    def get_modes(self) -> list[dict[str, str]]:
        return [{"id": "default", "label": "Normal"}]


class PluginManager:
    """Manages plugin registration, lifecycle execution, and safety boundaries."""

    def __init__(self) -> None:
        self._plugins: dict[str, Plugin] = {}
        self.register(LunaCorePlugin())
        self.register(ProjectRoomPlugin())
        self._load_external_plugins()

    def _load_external_plugins(self) -> None:
        """Discover trusted, installed plugins through the Python package API."""
        try:
            entries = importlib_metadata.entry_points(group="voice_of_luna.plugins")
        except TypeError:  # pragma: no cover - compatibility with older metadata
            entries = importlib_metadata.entry_points().get("voice_of_luna.plugins", ())
        for entry in entries:
            try:
                loaded = entry.load()
                plugin = loaded() if isinstance(loaded, type) else loaded() if callable(loaded) else loaded
                if not isinstance(plugin, Plugin):
                    raise TypeError("entry point did not return a Plugin")
                self.register(plugin, external=True)
            except Exception as exc:
                logger.error("Failed to load external plugin %s: %s", entry.name, exc)

    def register(self, plugin: Plugin, *, external: bool = False) -> None:
        if not plugin.id:
            raise ValueError("Plugin must have a non-empty id")
        if external and plugin.id in self._plugins:
            raise ValueError(f"Plugin id already registered: {plugin.id}")
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
                "tools": [
                    {
                        "name": tool.qualified_name,
                        "permission": tool.required_permission,
                    }
                    for tool in p.tools()
                ],
            }
            for p in self._plugins.values()
        ]

    def get_tools(self, plugin_id: str) -> list[ToolSpec]:
        return self.get(plugin_id).tools()

    def dynamic_tools(self, plugin_id: str) -> list[dict[str, Any]]:
        return [tool.as_dynamic_tool() for tool in self.get_tools(plugin_id)]

    async def call_tool(
        self,
        plugin_id: str,
        name: str,
        arguments: dict[str, Any],
        ctx: ToolCallContext,
        timeout: float = 5.0,
    ) -> ToolResult:
        """Dispatch one model-requested tool with isolation and a bounded wait."""
        plugin = self.get(plugin_id)
        declared = {tool.name: tool for tool in plugin.tools()}
        tool = declared.get(name) or next(
            (item for item in plugin.tools() if item.qualified_name == name), None
        )
        if tool is None:
            return ToolResult(
                content_items=[
                    {"type": "text", "text": f"Unknown tool: {name}"}
                ],
                metadata={"ok": False, "error": "unknown_tool"},
            )
        granted = set(ctx.metadata.get("permissions", ()))
        checker = ctx.metadata.get("permission_checker")
        if tool.required_permission and callable(checker) and checker(tool.required_permission):
            granted.add(tool.required_permission)
        if tool.required_permission and tool.required_permission not in granted:
            return ToolResult(
                content_items=[
                    {"type": "text", "text": "Permission required for this tool."}
                ],
                metadata={"ok": False, "error": "permission_denied", "permission": tool.required_permission},
            )
        try:
            qualified = name if "." in name else tool.qualified_name
            call_ctx = ToolCallContext(
                conversation_id=ctx.conversation_id,
                plugin_id=ctx.plugin_id,
                active_mode=ctx.active_mode,
                metadata={**ctx.metadata, "tool_qualified_name": qualified},
                storage=ctx.storage,
                github=ctx.github,
            )
            result = await asyncio.wait_for(plugin.call_tool(tool.name, arguments, call_ctx), timeout)
            return self._bound_result(result)
        except asyncio.TimeoutError:
            logger.warning("Plugin %s tool %s timed out after %.1fs", plugin_id, name, timeout)
            return ToolResult(
                content_items=[{"type": "text", "text": "Tool timed out."}],
                metadata={"ok": False, "error": "timeout"},
            )
        except Exception as exc:
            logger.warning("Plugin %s tool %s failed: %s", plugin_id, name, exc)
            return ToolResult(
                content_items=[{"type": "text", "text": "Tool failed safely."}],
                metadata={"ok": False, "error": "tool_failed"},
            )

    @staticmethod
    def _bound_result(result: ToolResult) -> ToolResult:
        items = []
        budget = 16_000
        for item in result.content_items[:8]:
            bounded = dict(item)
            if isinstance(bounded.get("text"), str):
                bounded["text"] = bounded["text"][: min(8_000, budget)]
                budget -= len(bounded["text"])
            items.append(bounded)
            if budget <= 0:
                break
        return ToolResult(content_items=items, metadata=result.metadata)

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
