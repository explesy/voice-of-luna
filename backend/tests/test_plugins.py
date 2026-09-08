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
    TestContextPlugin,
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


@pytest.mark.anyio
async def test_plugin_manager_registration_and_listing():
    mgr = PluginManager()
    plugins = mgr.list_plugins()
    ids = [p["id"] for p in plugins]
    assert "neutral" in ids
    assert "test_plugin" in ids

    custom = SlowPlugin()
    mgr.register(custom)
    assert mgr.get("slow").name == "Slow Plugin"
    assert mgr.get("non_existent").id == "neutral"


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
async def test_test_context_plugin_lifecycle():
    plugin = TestContextPlugin()
    conv_id = "test-conv-123"

    sys_prompt = await plugin.system_prompt(conv_id)
    assert "Test Plugin active" in sys_prompt

    ctx1 = TurnContext(
        conversation_id=conv_id,
        user_message="First question",
        turns_history=[],
        active_mode="echo_metric",
    )
    res1 = await plugin.before_turn(ctx1)
    assert "Turn #1" in res1.prompt_context
    assert "Mode: echo_metric" in res1.prompt_context
    assert res1.mode_label == "TEST // #1"

    await plugin.after_turn(ctx1, "First answer")
    assert len(plugin.session_logs[conv_id]) == 1
    assert plugin.session_logs[conv_id][0]["user"] == "First question"

    ctx2 = TurnContext(
        conversation_id=conv_id,
        user_message="Second question",
        turns_history=[{"role": "user", "text": "First question"}],
        active_mode="echo_metric",
    )
    res2 = await plugin.before_turn(ctx2)
    assert "Turn #2" in res2.prompt_context
    assert res2.mode_label == "TEST // #2"

    await plugin.on_conversation_reset(conv_id)
    assert conv_id not in plugin.turn_counts
    assert conv_id not in plugin.session_logs


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
    assert "test_plugin" in plugin_ids


def test_api_select_plugin_and_turn_execution(monkeypatch):
    created = client.post("/api/conversations")
    assert created.status_code == 201
    conv_id = created.json()["id"]

    # Select test_plugin
    res = client.post(
        f"/api/conversations/{conv_id}/plugin",
        json={"plugin_id": "test_plugin", "mode": "echo_metric"},
    )
    assert res.status_code == 200
    assert res.json()["plugin_id"] == "test_plugin"
    assert res.json()["mode"] == "echo_metric"

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
    assert captured_context[0] is not None
    assert "TEST CONTEXT // Turn #1" in captured_context[0]
    assert "Mode: echo_metric" in captured_context[0]


def test_htmx_turn_with_plugin(monkeypatch):
    created = client.post("/api/conversations")
    conv_id = created.json()["id"]

    client.post(
        f"/api/conversations/{conv_id}/plugin",
        json={"plugin_id": "test_plugin", "mode": "default"},
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
    assert "TEST CONTEXT // Turn #1" in captured_context[0]


@pytest.mark.anyio
async def test_focus_sprint_plugin():
    from app.plugins.focus_sprint import FocusSprintPlugin

    plugin = FocusSprintPlugin()
    conv_id = "sprint-conv-1"

    sys_prompt = await plugin.system_prompt(conv_id)
    assert "voice coach for a focused work sprint" in sys_prompt

    modes = plugin.get_modes()
    mode_ids = [m["id"] for m in modes]
    assert "15m" in mode_ids
    assert "25m" in mode_ids
    assert "debrief" in mode_ids

    ctx_15m = TurnContext(
        conversation_id=conv_id,
        user_message="I want to finish the parser",
        turns_history=[],
        active_mode="15m",
    )
    res_15m = await plugin.before_turn(ctx_15m)
    assert "FOCUS SPRINT:" in res_15m.prompt_context
    assert "15:00 elapsed" in res_15m.prompt_context
    assert "Do NOT conclude the sprint early" in res_15m.prompt_context
    assert "SPRINT // 00:0" in res_15m.mode_label

    await plugin.after_turn(ctx_15m, "Stay focused!")
    assert plugin.sessions[conv_id].checkins == 1

    ctx_debrief = TurnContext(
        conversation_id=conv_id,
        user_message="Done with parser",
        turns_history=[],
        active_mode="debrief",
    )
    res_debrief = await plugin.before_turn(ctx_debrief)
    assert "DEBRIEF MODE:" in res_debrief.prompt_context
    assert "SPRINT // DEBRIEF" in res_debrief.mode_label

    await plugin.on_conversation_reset(conv_id)
    assert conv_id not in plugin.sessions


@pytest.mark.anyio
async def test_spanish_buddy_plugin():
    from app.plugins.spanish_buddy import SpanishBuddyPlugin

    plugin = SpanishBuddyPlugin()
    conv_id = "es-conv-1"

    sys_prompt = await plugin.system_prompt(conv_id)
    assert "español" in sys_prompt.lower()

    ctx_casual = TurnContext(
        conversation_id=conv_id,
        user_message="Hola, ¿cómo estás?",
        turns_history=[],
        active_mode="casual",
    )
    res_casual = await plugin.before_turn(ctx_casual)
    assert "Conversación fluida" in res_casual.prompt_context
    assert res_casual.mode_label == "ES // CONVERSACIÓN"

    ctx_corr = TurnContext(
        conversation_id=conv_id,
        user_message="Yo estar cansado",
        turns_history=[],
        active_mode="correction",
    )
    res_corr = await plugin.before_turn(ctx_corr)
    assert "corrección muy breve" in res_corr.prompt_context
    assert res_corr.mode_label == "ES // CORRECCIÓN"

    ctx_vocab = TurnContext(
        conversation_id=conv_id,
        user_message="Cuéntame algo",
        turns_history=[],
        active_mode="vocabulary",
    )
    res_vocab = await plugin.before_turn(ctx_vocab)
    assert "expresión idiomática" in res_vocab.prompt_context
    assert res_vocab.mode_label == "ES // VOCABULARIO"


def test_api_select_focus_sprint_and_spanish_plugins():
    created = client.post("/api/conversations")
    conv_id = created.json()["id"]

    # Select focus sprint
    res_sprint = client.post(
        f"/api/conversations/{conv_id}/plugin",
        json={"plugin_id": "focus_sprint", "mode": "15m"},
    )
    assert res_sprint.status_code == 200
    assert res_sprint.json()["plugin_id"] == "focus_sprint"
    assert res_sprint.json()["mode"] == "15m"

    # Select spanish buddy
    res_es = client.post(
        f"/api/conversations/{conv_id}/plugin",
        json={"plugin_id": "spanish_buddy", "mode": "correction"},
    )
    assert res_es.status_code == 200
    assert res_es.json()["plugin_id"] == "spanish_buddy"
    assert res_es.json()["mode"] == "correction"

    # Verify both exist in list_plugins
    plugins = client.get("/api/plugins").json()["plugins"]
    p_ids = [p["id"] for p in plugins]
    assert "focus_sprint" in p_ids
    assert "spanish_buddy" in p_ids


def test_spanish_buddy_stt_language_and_voice():
    assert plugin_manager.get_stt_language("spanish_buddy") == "auto"
    assert "español y ruso" in (plugin_manager.get_stt_prompt("spanish_buddy") or "")
    assert plugin_manager.get_preferred_voice_locale("spanish_buddy") == "es"

    created = client.post("/api/conversations")
    conv_id = created.json()["id"]

    res_es = client.post(
        f"/api/conversations/{conv_id}/plugin",
        json={"plugin_id": "spanish_buddy", "mode": "casual"},
    )
    assert res_es.status_code == 200
    data = res_es.json()
    assert data["plugin_id"] == "spanish_buddy"
    if "voice" in data and data["voice"]:
        # Should be a Spanish voice (e.g. Elvira, Alvaro, Mónica, Paulina)
        assert any(x in data["voice"].lower() for x in ("elvira", "alvaro", "mónica", "monica", "paulina", "es"))


@pytest.mark.anyio
async def test_spanish_buddy_instructions_enforce_spanish():
    from app.plugins.spanish_buddy import SpanishBuddyPlugin

    plugin = SpanishBuddyPlugin()
    sys_prompt = await plugin.system_prompt("conv-es")
    assert "tutor de conversación" in sys_prompt
    assert "SOPORTE BILINGÜE" in sys_prompt

    ctx = TurnContext(
        conversation_id="conv-es",
        user_message="Как сказать хлеб?",
        turns_history=[],
        active_mode="casual",
    )
    res = await plugin.before_turn(ctx)
    assert "INSTRUCCIÓN DE TUTOR" in res.prompt_context
    assert "El diálogo principal es en español" in res.prompt_context


@pytest.mark.anyio
async def test_spanish_buddy_response_locale_override_sets_spanish_base_instructions():
    from app.main import Conversation, _refresh_conversation_base_instructions

    conv = Conversation(id="conv-es-override", locale="ru-RU", plugin_id="spanish_buddy")
    instructions = await _refresh_conversation_base_instructions(conv)

    assert "Always reply in Spanish." in instructions
    assert "Fuentes:" in instructions
    assert "Always reply in Russian." not in instructions
    assert "Always reply in English." not in instructions


