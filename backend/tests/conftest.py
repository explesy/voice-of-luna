"""Shared test safeguards for the local voice bridge."""

import pytest


@pytest.fixture(autouse=True)
def prevent_live_remote_warmups(monkeypatch, request):
    """Warmup is quota-consuming and must never run against a real account in tests."""
    from app import main

    monkeypatch.setattr(main, "_schedule_conversation_warmup", lambda *_: "warm")

    # Safeguard against unmocked live Codex subprocess calls outside test_codex
    if request.module and "test_codex" not in request.module.__name__:
        import asyncio

        orig_exec = asyncio.create_subprocess_exec

        async def guarded_subprocess_exec(program, *args, **kwargs):
            if "codex" in str(program).lower():
                raise RuntimeError(
                    f"Live Codex subprocess call ({program}) prevented in test! Mock Codex in your test."
                )
            return await orig_exec(program, *args, **kwargs)

        monkeypatch.setattr(asyncio, "create_subprocess_exec", guarded_subprocess_exec)
