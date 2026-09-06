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
