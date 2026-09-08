import asyncio

from app.codex import CodexAppServer, RuntimeStatus


class FakeProcess:
    def __init__(self) -> None:
        self.returncode = None

    def terminate(self) -> None:
        self.returncode = 0

    async def wait(self) -> None:
        return None


def test_reuses_one_process_and_thread_for_multiple_turns(monkeypatch) -> None:
    provider = CodexAppServer()
    process_starts = []
    requests = []

    async def fake_status():
        return RuntimeStatus(True, "Local Codex is connected")

    async def fake_create_process(*_args, **_kwargs):
        process_starts.append(True)
        return FakeProcess()

    async def fake_request(method, params):
        requests.append((method, params))
        if method == "thread/start":
            return {"thread": {"id": "thread-1"}}
        if method == "turn/start":
            return {"turn": {"id": f"turn-{len(requests)}"}}
        return {}

    async def fake_notify(*_args):
        return None

    async def fake_wait_for_answer(thread_id, turn_id):
        return f"{thread_id}:{turn_id}"

    monkeypatch.setattr(provider, "status", fake_status)
    monkeypatch.setattr("app.codex.asyncio.create_subprocess_exec", fake_create_process)
    monkeypatch.setattr(provider, "_request", fake_request)
    monkeypatch.setattr(provider, "_notify", fake_notify)
    monkeypatch.setattr(provider, "_wait_for_answer", fake_wait_for_answer)

    async def exercise() -> None:
        assert await provider.reply("First") == "thread-1:turn-3"
        assert await provider.reply("Second") == "thread-1:turn-4"
        await provider.close()

    asyncio.run(exercise())

    assert len(process_starts) == 1
    assert [method for method, _ in requests].count("thread/start") == 1
    assert [method for method, _ in requests].count("turn/start") == 2


def test_reply_stream_yields_deltas(monkeypatch) -> None:
    provider = CodexAppServer()

    async def fake_status():
        return RuntimeStatus(True, "Local Codex is connected")

    async def fake_create_process(*_args, **_kwargs):
        return FakeProcess()

    async def fake_request(method, params):
        if method == "thread/start":
            return {"thread": {"id": "thread-stream"}}
        if method == "turn/start":
            return {"turn": {"id": "turn-stream"}}
        return {}

    async def fake_notify(*_args):
        return None

    messages = [
        {"method": "item/agentMessage/delta", "params": {"threadId": "thread-stream", "turnId": "turn-stream", "delta": "Привет, "}},
        {"method": "item/agentMessage/delta", "params": {"threadId": "thread-stream", "turnId": "turn-stream", "delta": "мир!"}},
        {"method": "turn/completed", "params": {"threadId": "thread-stream", "turn": {"id": "turn-stream"}}},
    ]
    msg_iter = iter(messages)

    async def fake_read_message():
        return next(msg_iter)

    monkeypatch.setattr(provider, "status", fake_status)
    monkeypatch.setattr("app.codex.asyncio.create_subprocess_exec", fake_create_process)
    monkeypatch.setattr(provider, "_request", fake_request)
    monkeypatch.setattr(provider, "_notify", fake_notify)
    monkeypatch.setattr(provider, "_read_message", fake_read_message)

    async def exercise() -> None:
        chunks = []
        async for chunk in provider.reply_stream("Hello"):
            chunks.append(chunk)
        assert chunks == ["Привет, ", "мир!"]
        await provider.close()

    asyncio.run(exercise())


def test_stream_delivers_events_buffered_before_listener_registration() -> None:
    provider = CodexAppServer()
    provider._buffered_turn_events["turn-early"] = [
        {
            "method": "item/agentMessage/delta",
            "params": {"threadId": "thread-early", "turnId": "turn-early", "delta": "Быстрый "},
        },
        {
            "method": "item/agentMessage/delta",
            "params": {"threadId": "thread-early", "turnId": "turn-early", "delta": "ответ."},
        },
        {
            "method": "turn/completed",
            "params": {"threadId": "thread-early", "turn": {"id": "turn-early"}},
        },
    ]

    async def exercise() -> None:
        chunks = []
        async for chunk in provider._stream_answer("thread-early", "turn-early"):
            chunks.append(chunk)
        assert chunks == ["Быстрый ", "ответ."]
        assert "turn-early" not in provider._buffered_turn_events

    asyncio.run(exercise())


def test_stream_separates_distinct_message_items() -> None:
    provider = CodexAppServer()
    provider._buffered_turn_events["turn-multi"] = [
        {
            "method": "item/agentMessage/delta",
            "params": {"threadId": "thread-multi", "turnId": "turn-multi", "itemId": "item-1", "delta": "Часть 1."},
        },
        {
            "method": "item/agentMessage/delta",
            "params": {"threadId": "thread-multi", "turnId": "turn-multi", "itemId": "item-2", "delta": "Часть 2."},
        },
        {
            "method": "turn/completed",
            "params": {"threadId": "thread-multi", "turn": {"id": "turn-multi"}},
        },
    ]

    async def exercise() -> None:
        chunks = []
        async for chunk in provider._stream_answer("thread-multi", "turn-multi"):
            chunks.append(chunk)
        assert chunks == ["Часть 1.", "\n\n", "Часть 2."]

    asyncio.run(exercise())


def test_stream_filters_commentary_and_yields_final_answer() -> None:
    provider = CodexAppServer()
    provider._buffered_turn_events["turn-commentary"] = [
        {
            "method": "item/started",
            "params": {
                "threadId": "thread-1",
                "turnId": "turn-commentary",
                "item": {"id": "item-c1", "type": "agentMessage", "phase": "commentary"},
            },
        },
        {
            "method": "item/agentMessage/delta",
            "params": {"threadId": "thread-1", "turnId": "turn-commentary", "itemId": "item-c1", "delta": "Подумаю над подборкой..."},
        },
        {
            "method": "item/completed",
            "params": {
                "threadId": "thread-1",
                "turnId": "turn-commentary",
                "item": {"id": "item-c1", "type": "agentMessage", "phase": "commentary", "text": "Подумаю над подборкой..."},
            },
        },
        {
            "method": "item/started",
            "params": {
                "threadId": "thread-1",
                "turnId": "turn-commentary",
                "item": {"id": "item-f1", "type": "agentMessage", "phase": "final_answer"},
            },
        },
        {
            "method": "item/agentMessage/delta",
            "params": {"threadId": "thread-1", "turnId": "turn-commentary", "itemId": "item-f1", "delta": "Вот отличные репозитории: vscode, immich."},
        },
        {
            "method": "item/completed",
            "params": {
                "threadId": "thread-1",
                "turnId": "turn-commentary",
                "item": {"id": "item-f1", "type": "agentMessage", "phase": "final_answer", "text": "Вот отличные репозитории: vscode, immich."},
            },
        },
        {
            "method": "turn/completed",
            "params": {"threadId": "thread-1", "turn": {"id": "turn-commentary"}},
        },
    ]

    async def exercise() -> None:
        chunks = []
        async for chunk in provider._stream_answer("thread-1", "turn-commentary"):
            chunks.append(chunk)
        assert chunks == ["Вот отличные репозитории: vscode, immich."]

    asyncio.run(exercise())


def test_stream_falls_back_to_commentary_if_no_final_answer() -> None:
    provider = CodexAppServer()
    provider._buffered_turn_events["turn-only-c"] = [
        {
            "method": "item/started",
            "params": {
                "threadId": "thread-1",
                "turnId": "turn-only-c",
                "item": {"id": "item-c1", "type": "agentMessage", "phase": "commentary"},
            },
        },
        {
            "method": "item/agentMessage/delta",
            "params": {"threadId": "thread-1", "turnId": "turn-only-c", "itemId": "item-c1", "delta": "Единственный ответ."},
        },
        {
            "method": "turn/completed",
            "params": {"threadId": "thread-1", "turn": {"id": "turn-only-c"}},
        },
    ]

    async def exercise() -> None:
        chunks = []
        async for chunk in provider._stream_answer("thread-1", "turn-only-c"):
            chunks.append(chunk)
        assert chunks == ["Единственный ответ."]

    asyncio.run(exercise())


def test_turn_start_passes_model_and_effort(monkeypatch) -> None:
    provider = CodexAppServer()
    turn_params = []

    async def fake_status():
        return RuntimeStatus(True, "Local Codex is connected")

    async def fake_create_process(*_args, **_kwargs):
        return FakeProcess()

    async def fake_request(method, params):
        if method == "thread/start":
            return {"thread": {"id": "thread-cfg"}}
        if method == "turn/start":
            turn_params.append(params)
            return {"turn": {"id": "turn-cfg"}}
        return {}

    async def fake_notify(*_args):
        return None

    async def fake_wait_for_answer(*_args):
        return "Ответ"

    monkeypatch.setattr(provider, "status", fake_status)
    monkeypatch.setattr("app.codex.asyncio.create_subprocess_exec", fake_create_process)
    monkeypatch.setattr(provider, "_request", fake_request)
    monkeypatch.setattr(provider, "_notify", fake_notify)
    monkeypatch.setattr(provider, "_wait_for_answer", fake_wait_for_answer)

    async def exercise() -> None:
        await provider.reply("Привет", model="gpt-5.4-mini", effort="low")
        await provider.close()

    asyncio.run(exercise())

    assert len(turn_params) == 1
    assert turn_params[0]["model"] == "gpt-5.4-mini"
    assert turn_params[0]["effort"] == "low"


def test_turn_interrupt(monkeypatch) -> None:
    provider = CodexAppServer()
    interrupted = []

    async def fake_status():
        return RuntimeStatus(True, "Local Codex is connected")

    async def fake_create_process(*_args, **_kwargs):
        return FakeProcess()

    async def fake_request(method, params, *args, **kwargs):
        if method == "thread/start":
            return {"thread": {"id": "thread-int"}}
        if method == "turn/start":
            return {"turn": {"id": "turn-int"}}
        if method == "turn/interrupt":
            interrupted.append(params)
            return {}
        return {}

    async def fake_notify(*_args):
        return None

    monkeypatch.setattr(provider, "status", fake_status)
    monkeypatch.setattr("app.codex.asyncio.create_subprocess_exec", fake_create_process)
    monkeypatch.setattr(provider, "_request", fake_request)
    monkeypatch.setattr(provider, "_notify", fake_notify)

    async def exercise() -> None:
        await provider._ensure_thread()
        provider._active_turn_id = "turn-int"
        ok = await provider.interrupt()
        assert ok is True
        await provider.close()

    asyncio.run(exercise())

    assert len(interrupted) == 1
    assert interrupted[0] == {"threadId": "thread-int", "turnId": "turn-int"}


def test_list_models(monkeypatch) -> None:
    provider = CodexAppServer()

    async def fake_status():
        return RuntimeStatus(True, "Local Codex is connected")

    async def fake_create_process(*_args, **_kwargs):
        return FakeProcess()

    async def fake_request(method, params, *args, **kwargs):
        if method == "thread/start":
            return {"thread": {"id": "thread-m"}}
        if method == "model/list":
            return {
                "data": [
                    {
                        "id": "gpt-5.4-mini",
                        "displayName": "GPT-5.4-Mini",
                        "supportedReasoningEfforts": [{"reasoningEffort": "low", "description": "Fast"}],
                    }
                ]
            }
        return {}

    async def fake_notify(*_args):
        return None

    monkeypatch.setattr(provider, "status", fake_status)
    monkeypatch.setattr("app.codex.asyncio.create_subprocess_exec", fake_create_process)
    monkeypatch.setattr(provider, "_request", fake_request)
    monkeypatch.setattr(provider, "_notify", fake_notify)

    async def exercise() -> None:
        models = await provider.list_models()
        assert len(models) == 1
        assert models[0]["id"] == "gpt-5.4-mini"
        await provider.close()

    asyncio.run(exercise())
