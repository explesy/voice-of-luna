from __future__ import annotations

import json
import time
from typing import Any

from .base import Plugin, PluginTurnResult, ToolCallContext, ToolResult, ToolSpec, TurnContext


def _text(value: Any) -> dict[str, str]:
    return {"type": "text", "text": str(value)}


class TrainingPlugin(Plugin):
    """Small first-party stateful workflow used to exercise the plugin API."""

    id = "training"
    name = "Training"
    description = "A private, stateful practice and reflection workspace"

    async def configure(self, settings: dict[str, Any]) -> dict[str, Any]:
        goal = str(settings.get("goal", "")).strip()[:240]
        skill = str(settings.get("skill", "general")).strip()[:80] or "general"
        return {"skill": skill, "goal": goal}

    def panel_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "fields": [
                {"name": "skill", "type": "text", "label": "Skill", "placeholder": "conversation"},
                {"name": "goal", "type": "text", "label": "Session goal", "placeholder": "Describe what to practise"},
            ],
            "actions": [{"name": "reset", "label": "Reset session"}],
        }

    def get_modes(self) -> list[dict[str, str]]:
        return [
            {"id": "practice", "label": "Practice"},
            {"id": "debrief", "label": "Debrief"},
            {"id": "free", "label": "Free dialogue"},
        ]

    async def before_turn(self, ctx: TurnContext) -> PluginTurnResult:
        settings = ctx.metadata.get("plugin_settings", {})
        skill = str(settings.get("skill", "general"))
        goal = str(settings.get("goal", "")).strip()
        recent = []
        if ctx.state is not None:
            raw = await ctx.state.get("recent_sessions")
            if raw:
                try:
                    recent = json.loads(raw)
                except (TypeError, ValueError):
                    recent = []
        lines = [f"Training mode: {ctx.active_mode}", f"Current skill: {skill}"]
        if goal:
            lines.append(f"Session goal: {goal}")
        if recent:
            lines.append(f"Recent session notes: {recent[-3:]}")
        return PluginTurnResult(prompt_context="\n".join(lines), mode_label=f"TRAINING // {ctx.active_mode.upper()}")

    async def after_turn(self, ctx: TurnContext, assistant_response: str) -> None:
        if ctx.state is None:
            return
        raw = await ctx.state.get("recent_sessions")
        try:
            recent = json.loads(raw) if raw else []
        except (TypeError, ValueError):
            recent = []
        recent = recent if isinstance(recent, list) else []
        recent.append({"at": int(time.time()), "mode": ctx.active_mode, "user": ctx.user_message[:240], "assistant": assistant_response[:240]})
        await ctx.state.set("recent_sessions", json.dumps(recent[-20:], ensure_ascii=False))

    async def action(self, name: str, settings: dict[str, Any], state: Any = None) -> dict[str, Any]:
        if name != "reset" or state is None:
            raise ValueError("Unknown Training action" if name != "reset" else "Training state is unavailable")
        await state.delete("recent_sessions")
        return {"ok": True, "action": name}

    def tools(self) -> list[ToolSpec]:
        return [
            ToolSpec("training", "history", "Read recent training session notes.", {"type": "object", "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 20}}}, "storage.read"),
            ToolSpec("training", "observe", "Record a short training observation.", {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}, "storage.write"),
            ToolSpec("training", "progress", "Summarize current training progress.", {"type": "object"}, "storage.read"),
        ]

    async def call_tool(self, name: str, arguments: dict[str, Any], ctx: ToolCallContext) -> ToolResult:
        storage = ctx.storage
        if storage is None:
            raise RuntimeError("Training storage is unavailable")
        scope = str(ctx.metadata.get("project_scope", "plugin"))
        qualified = str(ctx.metadata.get("tool_qualified_name", name))
        if qualified == "training.history":
            rows = await storage.recent(self.id, scope, int(arguments.get("limit", 8)))
            return ToolResult(content_items=[_text(rows or "No training observations recorded.")])
        if qualified == "training.observe":
            doc_id = await storage.remember(self.id, scope, str(arguments["text"]), "observation", [])
            return ToolResult(content_items=[_text(f"Recorded training observation #{doc_id}.")])
        if qualified == "training.progress":
            rows = await storage.recent(self.id, scope, 20)
            return ToolResult(content_items=[_text(f"Training observations recorded: {len(rows)}")])
        raise ValueError(f"Unknown Training tool: {qualified}")
