from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from app import main as main_module
from app.codex import CodexAppServer, RuntimeStatus
from app.main import app
from app.plugins import (
    Plugin,
    PluginManager,
    PluginTurnResult,
    ToolCallContext,
    ToolResult,
    ToolSpec,
    TurnContext,
    plugin_manager,
)

client = TestClient(app)


class SlowPlugin(Plugin):
    id = "slow"
    name = "Slow Plugin"

    async def before_turn(self, ctx: TurnContext) -> PluginTurnResult:
        await asyncio.sleep(5.0)
        return PluginTurnResult(prompt_context="Should not reach here")


class BrokenPlugin(Plugin):
    id = "broken"
    name = "Broken Plugin"

    async def system_prompt(self, conversation_id: str) -> str:
        raise RuntimeError("Explosion in system_prompt")

    async def before_turn(self, ctx: TurnContext) -> PluginTurnResult:
        raise RuntimeError("Explosion in before_turn")

    async def after_turn(self, ctx: TurnContext, assistant_response: str) -> None:
        raise RuntimeError("Explosion in after_turn")


class ToolPlugin(Plugin):
    id = "tool_plugin"
    name = "Tool Plugin"

    def tools(self) -> list[ToolSpec]:
        return [
            ToolSpec(
                namespace="memory",
                name="search",
                description="Search test memory",
                input_schema={"type": "object"},
            )
        ]

    async def call_tool(self, name, arguments, ctx: ToolCallContext) -> ToolResult:
        assert name == "search"
        assert ctx.plugin_id == self.id
        return ToolResult(content_items=[{"type": "text", "text": arguments["query"]}])

    async def configure(self, settings):
        return {"owned": str(settings.get("owned", ""))}


@pytest.mark.anyio
async def test_plugin_manager_registration_and_listing():
    mgr = PluginManager()
    plugins = mgr.list_plugins()
    ids = [p["id"] for p in plugins]
    assert "neutral" in ids
    assert "project_room" in ids

    custom = SlowPlugin()
    mgr.register(custom)
    assert mgr.get("slow").name == "Slow Plugin"
    assert mgr.get("non_existent").id == "neutral"


@pytest.mark.anyio
async def test_plugin_manager_dispatches_declared_tools_and_isolates_unknown_tools():
    mgr = PluginManager()
    mgr.register(ToolPlugin())
    ctx = ToolCallContext("conv-1", "tool_plugin", "default")

    assert await mgr.configure("tool_plugin", {"owned": "value", "host": "ignored"}) == {"owned": "value"}
    assert mgr.dynamic_tools("tool_plugin")[0]["name"] == "memory_search"
    result = await mgr.call_tool("tool_plugin", "memory.search", {"query": "decision"}, ctx)
    assert result.content_items == [{"type": "text", "text": "decision"}]
    wire_result = await mgr.call_tool("tool_plugin", "memory_search", {"query": "decision"}, ctx)
    assert wire_result.content_items == [{"type": "text", "text": "decision"}]

    unknown = await mgr.call_tool("tool_plugin", "repo.read", {}, ctx)
    assert unknown.metadata["error"] == "unknown_tool"

    restricted = ToolPlugin()
    restricted.tools = lambda: [ToolSpec(
        namespace="external", name="write", description="write", input_schema={"type": "object"},
        required_permission="external.write",
    )]
    mgr.register(restricted)
    denied = await mgr.call_tool("tool_plugin", "external.write", {}, ctx)
    assert denied.metadata["error"] == "permission_denied"


@pytest.mark.anyio
async def test_plugin_manager_timeout_guard():
    mgr = PluginManager()
    mgr.register(SlowPlugin())
    ctx = TurnContext(
        conversation_id="conv-1",
        user_message="Hello",
        turns_history=[],
    )
    result = await mgr.execute_before_turn("slow", ctx, timeout=0.05)
    assert result.prompt_context == ""
    assert result.mode_label is None


@pytest.mark.anyio
async def test_plugin_manager_error_isolation():
    mgr = PluginManager()
    mgr.register(BrokenPlugin())
    ctx = TurnContext(
        conversation_id="conv-1",
        user_message="Hello",
        turns_history=[],
    )

    sys_prompt = await mgr.get_system_prompt("broken", "conv-1")
    assert sys_prompt == ""

    result = await mgr.execute_before_turn("broken", ctx)
    assert result.prompt_context == ""

    # after_turn should catch and not raise
    await mgr.execute_after_turn("broken", ctx, "some answer")


@pytest.mark.anyio
async def test_codex_app_server_context_prompt(monkeypatch):
    server = CodexAppServer(base_instructions="Custom Base")
    recorded_inputs = []

    async def fake_status():
        return RuntimeStatus(True, "Local Codex is connected")

    async def fake_ensure_thread():
        return "thread-ctx-1"

    async def fake_request(method, params):
        if method == "turn/start":
            recorded_inputs.append(params["input"])
            return {"turn": {"id": "turn-ctx-1"}}
        return {}

    async def fake_wait_for_answer(thread_id, turn_id):
        return "Acknowledged"

    monkeypatch.setattr(server, "status", fake_status)
    monkeypatch.setattr(server, "_ensure_thread", fake_ensure_thread)
    monkeypatch.setattr(server, "_request", fake_request)
    monkeypatch.setattr(server, "_wait_for_answer", fake_wait_for_answer)

    reply = await server.reply(
        "User question", context_prompt="[EXTRA CONTEXT DATA]"
    )
    assert reply == "Acknowledged"
    assert len(recorded_inputs) == 1
    text_sent = recorded_inputs[0][0]["text"]
    assert "[CONTEXT]\n[EXTRA CONTEXT DATA]\n\n[USER MESSAGE]\nUser question" == text_sent


def test_api_list_plugins():
    res = client.get("/api/plugins")
    assert res.status_code == 200
    data = res.json()
    assert "plugins" in data
    plugin_ids = [p["id"] for p in data["plugins"]]
    assert "neutral" in plugin_ids
    assert "project_room" in plugin_ids


def test_api_select_project_room_and_turn_execution(monkeypatch):
    created = client.post("/api/conversations")
    assert created.status_code == 201
    conv_id = created.json()["id"]

    # Select the first-party tool plugin
    res = client.post(
        f"/api/conversations/{conv_id}/plugin",
        json={"plugin_id": "project_room", "mode": "default"},
    )
    assert res.status_code == 200
    assert res.json()["plugin_id"] == "project_room"
    assert res.json()["mode"] == "default"

    # Verify conversation model reply called with context
    captured_context = []

    async def fake_reply(model, text, context_prompt=None, **kwargs):
        captured_context.append(context_prompt)
        return f"Echo: {text}"

    monkeypatch.setattr(main_module, "_call_reply", fake_reply)

    turn_res = client.post(
        f"/api/conversations/{conv_id}/turns", json={"text": "Check plugin"}
    )
    assert turn_res.status_code == 200
    assert turn_res.json()["text"] == "Echo: Check plugin"
    assert len(captured_context) == 1
    assert captured_context[0] == ""


def test_api_select_project_room_reports_invalid_root(tmp_path):
    created = client.post("/api/conversations")
    assert created.status_code == 201
    conv_id = created.json()["id"]

    res = client.post(
        f"/api/conversations/{conv_id}/plugin",
        json={"plugin_id": "project_room", "settings": {"root": str(tmp_path)}},
    )

    assert res.status_code == 400
    assert "Git repository" in res.json()["detail"]


def test_htmx_turn_with_plugin(monkeypatch):
    created = client.post("/api/conversations")
    conv_id = created.json()["id"]

    client.post(
        f"/api/conversations/{conv_id}/plugin",
        json={"plugin_id": "project_room", "mode": "default"},
    )

    captured_context = []

    async def fake_reply(model, text, context_prompt=None, **kwargs):
        captured_context.append(context_prompt)
        return "Fragment reply"

    monkeypatch.setattr(main_module, "_call_reply", fake_reply)

    form_res = client.post(
        f"/conversations/{conv_id}/turns", data={"text": "Hello HTMX"}
    )
    assert form_res.status_code == 200
    assert "Fragment reply" in form_res.text
    assert len(captured_context) == 1
