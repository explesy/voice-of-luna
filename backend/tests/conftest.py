"""Shared test safeguards for the local voice bridge."""

import pytest


@pytest.fixture(autouse=True)
def prevent_live_remote_warmups(monkeypatch):
    """Warmup is quota-consuming and must never run against a real account in tests."""
    from app import main

    monkeypatch.setattr(main, "_schedule_conversation_warmup", lambda *_: "warm")
