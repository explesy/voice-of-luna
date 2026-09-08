import pytest
from fastapi.testclient import TestClient

from app.codex import get_base_instructions
from app.main import _get_tts_engine, app, conversations, detect_effective_turn_locale
from app.speak import (
    LocalMacOsSpeaker,
    LocalSpeechError,
    get_default_voice_for_locale,
    get_installed_voices,
    get_voice_for_locale,
    resolve_edge_voice,
    transliterate_latin_for_speech,
)

client = TestClient(app)


def test_default_voice_for_locale() -> None:
    en_voice = get_default_voice_for_locale("en-US")
    assert any(x in en_voice.lower() for x in ("jenny", "samantha", "aria", "guy", "alex"))

    ru_voice = get_default_voice_for_locale("ru-RU")
    assert any(x in ru_voice.lower() for x in ("milena", "svetlana", "dmitry", "ksenia"))

    es_voice = get_default_voice_for_locale("es-ES")
    assert any(x in es_voice.lower() for x in ("elvira", "alvaro", "mónica", "monica", "paulina", "spanish", "spain", "es"))


def test_base_instructions_locale_variation() -> None:
    en_instructions = get_base_instructions("en-US")
    assert "Always reply in English." in en_instructions
    assert "Sources:" in en_instructions
    assert "Источники:" not in en_instructions

    ru_instructions = get_base_instructions("ru-RU")
    assert "Always reply in Russian." in ru_instructions
    assert "Источники:" in ru_instructions
    assert "Sources:" not in ru_instructions


def test_api_locale_endpoint_and_cookie_persistence() -> None:
    response = client.post("/api/locale", json={"locale": "en-US"})
    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert data["active_locale"] == "en-US"
    assert "recommended_voice" in data

    cookie_header = response.headers.get("set-cookie", "")
    assert "voice_of_luna_locale=en-US" in cookie_header

    # Request HTML page with English locale cookie
    page = client.get("/", cookies={"voice_of_luna_locale": "en-US"})
    assert page.status_code == 200
    assert '<html lang="en">' in page.text
    assert 'id="locale-select"' in page.text


def test_api_settings_endpoint_supports_locale() -> None:
    response = client.post(
        "/api/settings",
        json={"locale": "en-US", "model": "gpt-5.6-luna", "effort": "low"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["locale"] == "en-US"
    cookie_header = response.headers.get("set-cookie", "")
    assert "voice_of_luna_locale=en-US" in cookie_header


def test_create_conversation_inherits_locale_and_sets_english_voice() -> None:
    response = client.post("/api/conversations", cookies={"voice_of_luna_locale": "en-US"})
    assert response.status_code == 201
    conv_id = response.json()["id"]

    conv = conversations.get(conv_id)
    assert conv is not None
    assert conv.locale == "en-US"
    assert any(x in conv.voice.lower() for x in ("jenny", "samantha", "aria", "guy", "alex"))


def test_websocket_locale_update() -> None:
    created = client.post("/api/conversations")
    conv_id = created.json()["id"]

    with client.websocket_connect(f"/ws/conversations/{conv_id}") as ws:
        ready = ws.receive_json()
        assert ready["type"] == "ready"
        assert "locale" in ready

        ws.send_json({"type": "set_locale", "locale": "en-US"})
        reply = ws.receive_json()
        assert reply["type"] == "locale_updated"
        assert reply["locale"] == "en-US"
        assert any(x in reply["voice"].lower() for x in ("jenny", "samantha", "aria", "guy", "alex"))

        conv = conversations.get(conv_id)
        assert conv.locale == "en-US"


def test_edge_english_voices_registered() -> None:
    voices = get_installed_voices()
    names = [v.name for v in voices]
    assert "Jenny (Neural · Edge)" in names
    assert "Guy (Neural · Edge)" in names
    assert "Aria (Neural · Edge)" in names

    assert resolve_edge_voice("Jenny (Neural · Edge)") == "en-US-JennyNeural"
    assert resolve_edge_voice("Guy (Neural · Edge)") == "en-US-GuyNeural"


def test_transliterate_only_when_cyrillic_present() -> None:
    pure_english = "The quick brown fox jumps over the lazy dog."
    assert transliterate_latin_for_speech(pure_english) == pure_english

    mixed = "Отличная игра на Nintendo Switch"
    trans = transliterate_latin_for_speech(mixed)
    assert "Switch" not in trans
    assert "Свитч" in trans or "свитч" in trans.lower()


@pytest.mark.anyio
async def test_silero_rejects_pure_english_text() -> None:
    speaker = LocalMacOsSpeaker(voice="Ksenia (Silero Neural · Offline)")
    with pytest.raises(LocalSpeechError, match="Silero TTS only supports Cyrillic"):
        await speaker._synthesize_silero("This is a pure English utterance that cannot be spoken by Silero.", "Ksenia")


def test_get_voice_for_locale_engine_filtering() -> None:
    # When excluding edge, Jenny should not be returned
    local_en_voice = get_voice_for_locale("en", excluded_engines={"edge"})
    if local_en_voice:
        assert "Edge" not in local_en_voice

    # When allowing only edge, an Edge voice should be returned
    edge_en_voice = get_voice_for_locale("en", allowed_engines={"edge"})
    assert edge_en_voice is not None
    assert "Edge" in edge_en_voice


@pytest.mark.anyio
async def test_edge_tts_fallback_to_local_english_voice(monkeypatch) -> None:
    speaker = LocalMacOsSpeaker(voice="Jenny (Neural · Edge)")

    async def fake_synthesize_edge(text, voice):
        raise RuntimeError("Edge service disconnected")

    synth_calls = []
    async def fake_synthesize_macos(text, voice):
        synth_calls.append(voice)
        from pathlib import Path
        return Path("/tmp/fake_fallback.wav")

    monkeypatch.setattr(speaker, "_synthesize_edge", fake_synthesize_edge)
    monkeypatch.setattr(speaker, "_synthesize_macos", fake_synthesize_macos)

    res = await speaker.synthesize("Hello world, this is a test fallback.")
    assert len(synth_calls) == 1
    # Voice used for macOS say must NOT be the Edge voice name
    assert "Edge" not in synth_calls[0]
    assert synth_calls[0] == "Samantha" or "Samantha" in synth_calls[0] or "en" in synth_calls[0].lower()


def test_detect_effective_turn_locale() -> None:
    assert detect_effective_turn_locale("Привет, как дела?") == "ru-RU"
    assert detect_effective_turn_locale("Hello, how are you?") == "en-US"
    assert detect_effective_turn_locale("Mixed текст с кириллицей") == "ru-RU"
    assert detect_effective_turn_locale("12345 !?", fallback_locale="ru-RU") == "ru-RU"
    assert detect_effective_turn_locale("12345 !?", fallback_locale="en-US") == "en-US"


def test_get_tts_engine_helper() -> None:
    assert _get_tts_engine("Jenny (Neural · Edge)") == "EDGE_TTS"
    assert _get_tts_engine("Svetlana (Neural · Edge)") == "EDGE_TTS"
    assert _get_tts_engine("Ksenia (Silero Neural · Offline)") == "SILERO_OFFLINE"
    assert _get_tts_engine("Dmitri (Piper Neural · Offline)") == "PIPER_OFFLINE"
    assert _get_tts_engine("Milena") == "MACOS_SAY"
    assert _get_tts_engine("Samantha") == "MACOS_SAY"


def test_websocket_auto_locale_and_tts_engine(monkeypatch) -> None:
    import asyncio
    from app.main import _call_reply_stream

    async def fake_reply_stream(model, prompt, **kwargs):
        yield "Hello there! This is a test response."

    monkeypatch.setattr("app.main._call_reply_stream", fake_reply_stream)
    monkeypatch.setattr("app.main.LocalMacOsSpeaker.synthesize", lambda self, text: asyncio.sleep(0, result=None))

    with client.websocket_connect("/ws/conversations/test-auto-conv") as ws:
        ready = ws.receive_json()
        assert ready["type"] == "ready"
        assert "tts_engine" in ready

        # Switch to AUTO locale
        ws.send_json({"type": "set_locale", "locale": "auto"})
        loc_reply = ws.receive_json()
        assert loc_reply["type"] == "locale_updated"
        assert loc_reply["locale"] == "auto"
        assert "tts_engine" in loc_reply

        # Send an English message and verify effective turn locale & engine
        ws.send_json({"type": "text", "text": "What is the weather today?"})

        msgs = []
        while True:
            msg = ws.receive_json()
            msgs.append(msg)
            if msg.get("type") == "turn_completed":
                break

        completed = msgs[-1]
        assert completed["type"] == "turn_completed"
        assert completed["effective_locale"] == "en-US"
        assert any(x in completed["voice"].lower() for x in ("jenny", "samantha", "aria", "guy", "alex"))
        assert completed["tts_engine"] in ("EDGE_TTS", "MACOS_SAY")


def test_resolve_turn_language_auto_english() -> None:
    from app.conversation_service import Conversation, resolve_turn_language

    conv = Conversation(id="test-conv-en", locale="auto", plugin_id="neutral")
    conv.voice = "Milena"

    lang = resolve_turn_language(conv, user_text="Can you explain quantum computing?")
    assert lang.input_locale == "en-US"
    assert lang.response_locale == "en-US"
    assert any(en in lang.speaker_voice.lower() for en in ("jenny", "samantha", "aria", "guy", "lessac"))


def test_spanish_fuentes_source_pipeline() -> None:
    from app.speech_pipeline import SOURCES_SPLIT_RE
    from app.speak import sanitize_for_speech
    from app.main import format_terminal_text

    text = (
        "Madrid es la capital y ciudad más grande de España.\n\n"
        "Fuentes:\n"
        "- [Wikipedia](https://es.wikipedia.org/wiki/Madrid)\n"
        "- [Portal](https://madrid.es)"
    )

    # 1. SOURCES_SPLIT_RE must detect the Fuentes: boundary
    match = SOURCES_SPLIT_RE.search(text)
    assert match is not None
    assert "Fuentes" in text[match.start():match.end()]

    # 2. sanitize_for_speech must completely strip the Fuentes section and URLs
    clean = sanitize_for_speech(text)
    assert "Madrid es la capital" in clean
    assert "Fuentes" not in clean
    assert "wikipedia" not in clean.lower()
    assert "https" not in clean

    # 3. format_terminal_text must create the formatted log-sources card with FUENTES: tag
    html = str(format_terminal_text(text))
    assert '<div class="sources-tag">// FUENTES:</div>' in html
    assert 'class="term-link"' in html
    assert "https://es.wikipedia.org/wiki/Madrid" in html
