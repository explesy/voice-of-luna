"""Unit tests for speech pipeline and conversation service modularization."""

import asyncio
from pathlib import Path
import pytest

from app.conversation_service import Conversation, ConversationService, SpeechClip, call_reply, call_reply_stream, resolve_stt_config
from app.speech_pipeline import (
    AudioConversionError,
    extract_speech_sentence,
    is_16k_mono_wav,
    remove_temporary_audio,
    write_temporary_audio,
)


def test_call_reply_does_not_retry_internal_type_error() -> None:
    calls = 0

    class Model:
        async def reply(self, text: str, **kwargs) -> str:
            nonlocal calls
            calls += 1
            raise TypeError("provider failure")

    with pytest.raises(TypeError, match="provider failure"):
        asyncio.run(call_reply(Model(), "hello", model_name="gpt-test"))
    assert calls == 1


def test_call_reply_stream_does_not_retry_after_partial_output() -> None:
    calls = 0

    class Model:
        async def reply_stream(self, text: str, **kwargs):
            nonlocal calls
            calls += 1
            yield "hello"
            raise TypeError("stream provider failure")

    async def exercise() -> None:
        chunks = []
        with pytest.raises(TypeError, match="stream provider failure"):
            async for chunk in call_reply_stream(Model(), "hello", effort="low"):
                chunks.append(chunk)
        assert chunks == ["hello"]

    asyncio.run(exercise())
    assert calls == 1


def test_warmup_is_invalidated_when_base_instructions_change(monkeypatch) -> None:
    service = ConversationService()
    conversation = Conversation(id="warmup-generation")

    async def fake_set_base_instructions(instructions: str) -> None:
        conversation.model.base_instructions = instructions

    async def fake_get_system_prompt(*_args) -> str:
        return ""

    monkeypatch.setattr(conversation.model, "set_base_instructions", fake_set_base_instructions)
    monkeypatch.setattr("app.conversation_service.plugin_manager.get_system_prompt", fake_get_system_prompt)

    async def exercise() -> None:
        await service.refresh_base_instructions(conversation, "en-US")
        service.schedule_warmup(conversation, "gpt-test", "low")
        await service.refresh_base_instructions(conversation, "es-ES")

    asyncio.run(exercise())
    assert conversation.thread_generation == 2
    assert conversation.remote_warmup_key is None
    assert conversation.remote_warmup_task is None


def test_resolve_stt_config_matches_locale_and_plugin_policy() -> None:
    russian = Conversation(id="stt-ru", locale="ru-RU")
    assert resolve_stt_config(russian).language == "ru"

    english = Conversation(id="stt-en", locale="en-US")
    assert resolve_stt_config(english).language == "en"
    assert resolve_stt_config(english).prompt == ""

    spanish = Conversation(id="stt-es", locale="auto", plugin_id="spanish_buddy")
    config = resolve_stt_config(spanish)
    assert config.language == "auto"
    assert "español" in config.prompt


def test_write_and_remove_temporary_audio(tmp_path: Path) -> None:
    data = b"test audio bytes for pipeline"
    temp_path = write_temporary_audio(data, ".bin")
    try:
        assert temp_path.exists()
        assert temp_path.read_bytes() == data
    finally:
        remove_temporary_audio(temp_path)
    assert not temp_path.exists()


def test_is_16k_mono_wav_non_wav(tmp_path: Path) -> None:
    dummy = tmp_path / "not_a_wav.wav"
    dummy.write_bytes(b"RIFF....WAVEinvalid")
    assert is_16k_mono_wav(dummy) is False
    assert is_16k_mono_wav(tmp_path / "non_existent.wav") is False


def test_extract_speech_sentence_basic() -> None:
    text = "Hello world. How are you today? This is another sentence."
    sentence, remainder = extract_speech_sentence(text, is_first_chunk=False)
    assert sentence == "Hello world."
    assert remainder == "How are you today? This is another sentence."

    sentence2, remainder2 = extract_speech_sentence(remainder, is_first_chunk=False)
    assert sentence2 == "How are you today?"
    assert remainder2 == "This is another sentence."


def test_extract_speech_sentence_first_chunk_clause_boundary() -> None:
    # First chunk allows clause boundary (comma) when words >= 3 and chars >= 12
    text = "Sure thing my friend, I can definitely assist you with that right away."
    sentence, remainder = extract_speech_sentence(text, is_first_chunk=True)
    assert sentence == "Sure thing my friend,"
    assert remainder == "I can definitely assist you with that right away."

    # But subsequent chunk does NOT split on comma even if longer
    sentence_sub, remainder_sub = extract_speech_sentence(remainder, is_first_chunk=False)
    assert sentence_sub is None
    assert remainder_sub == "I can definitely assist you with that right away."


def test_extract_speech_sentence_preserves_abbreviations() -> None:
    # Abbreviations like e.g., т.д., mr. shouldn't trigger split
    text = "Please consult Mr. Smith for more details on this topic. The second sentence starts here."
    sentence, remainder = extract_speech_sentence(text, is_first_chunk=False)
    assert sentence == "Please consult Mr. Smith for more details on this topic."
    assert remainder == "The second sentence starts here."


def test_conversation_service_lifecycle() -> None:
    service = ConversationService()
    conv = Conversation(id="test-conv-1")
    service.conversations[conv.id] = conv
    service.touch(conv)
    assert conv.last_active_at > 0

    # Test html recovery
    recovered = service.recover_html_conversation(conv.id)
    assert recovered is conv

    new_conv = service.recover_html_conversation("non-existent-id")
    assert new_conv.id != "non-existent-id"
    assert new_conv.id in service.conversations


def test_conversation_service_idle_reaper() -> None:
    service = ConversationService()
    conv_active = Conversation(id="active", active_websockets=1)
    conv_stale = Conversation(id="stale", active_websockets=0)
    conv_stale.last_active_at = 100.0

    service.conversations["active"] = conv_active
    service.conversations["stale"] = conv_stale

    closed_ids = []

    async def fake_close(cid: str) -> bool:
        closed_ids.append(cid)
        return True

    count = asyncio.run(service.reap_idle_conversations(now=100.0 + 900 + 1, closer=fake_close))
    assert count == 1
    assert closed_ids == ["stale"]


def test_call_reply_and_stream_duck_typing() -> None:
    class DummyModel:
        async def reply(self, text: str, context_prompt: str | None = None, model: str | None = None, effort: str | None = None) -> str:
            return f"echo: {text} (model={model}, effort={effort})"

        async def reply_stream(self, text: str, **kwargs):
            yield "chunk1 "
            yield text

    dummy = DummyModel()
    res = asyncio.run(call_reply(dummy, "hello", model_name="test-m", effort="low"))
    assert res == "echo: hello (model=test-m, effort=low)"

    async def collect_stream():
        chunks = []
        async for c in call_reply_stream(dummy, "world"):
            chunks.append(c)
        return "".join(chunks)

    assert asyncio.run(collect_stream()) == "chunk1 world"


def test_is_silero_available_detection() -> None:
    from app.speak import is_silero_available
    # In dev environment with torch installed, it should be True
    assert is_silero_available() is True


def test_silero_without_torch_raises_friendly_error(monkeypatch) -> None:
    from app.speak import LocalMacOsSpeaker, LocalSpeechError

    # Simulate missing torch via is_silero_available
    monkeypatch.setattr("app.speak.is_silero_available", lambda: False)

    speaker = LocalMacOsSpeaker(voice="Ksenia (Silero Neural · Offline)")

    with pytest.raises(LocalSpeechError) as exc_info:
        asyncio.run(speaker._synthesize_silero("Тестовый текст", "Ksenia (Silero Neural · Offline)"))

    assert "PyTorch (torch) is not installed" in str(exc_info.value)
    assert "setup-silero" in str(exc_info.value)
