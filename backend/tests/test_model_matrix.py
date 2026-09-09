"""Comprehensive test matrix across AI models and Audio (TTS & STT) models.

Covers:
1. AI Model Matrix (Codex LLM models x reasoning efforts x streaming turns)
2. TTS Model Matrix (Piper, Silero, Edge TTS, macOS System)
3. STT Model Matrix (Whisper HTTP server, Whisper CLI fallback, multi-locale)
4. Cross-Model Integration Matrix (AI model + TTS engine in WebSocket end-to-end turns)
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, patch
import pytest
from fastapi.testclient import TestClient

from app import main as main_module
from app.codex import CodexAppServer, FALLBACK_MODELS
from app.main import app, _get_tts_engine
from app.speak import (
    LocalMacOsSpeaker,
    is_edge_voice,
    is_piper_voice,
    is_silero_voice,
    transliterate_latin_for_speech,
)
from app.tts_manager import MODEL_CATALOG, tts_model_manager
from app.transcribe import LocalWhisperTranscriber

client = TestClient(app)

# ---------------------------------------------------------------------------
# Dimension 1: AI Models Matrix (Codex LLM)
# ---------------------------------------------------------------------------

AI_MODELS_MATRIX = [
    ("gpt-5.6-luna", ["low", "medium", "high", "xhigh"]),
    ("gpt-5.6-sol", ["low", "medium", "high", "xhigh"]),
    ("gpt-5.6-terra", ["low", "medium", "high", "xhigh"]),
    ("gpt-6-astra", ["low", "medium", "high", "xhigh"]),
    ("gpt-5.5", ["low", "medium", "high"]),
]


@pytest.mark.parametrize("model_id,supported_efforts", AI_MODELS_MATRIX)
def test_ai_model_metadata_and_catalog(model_id: str, supported_efforts: list[str]) -> None:
    """Verify that every AI model in the catalog specifies correct identifiers and effort levels."""
    model_entry = next((m for m in FALLBACK_MODELS if m["id"] == model_id), None)
    assert model_entry is not None, f"Model {model_id} missing from FALLBACK_MODELS"
    assert model_entry["displayName"]
    assert model_entry["description"]

    efforts = [e["reasoningEffort"] for e in model_entry["supportedReasoningEfforts"]]
    assert efforts == supported_efforts


@pytest.mark.parametrize("model_id,supported_efforts", AI_MODELS_MATRIX)
@pytest.mark.parametrize("effort", ["low", "medium", "high"])
@pytest.mark.anyio
async def test_ai_model_turn_execution_matrix(
    monkeypatch, model_id: str, supported_efforts: list[str], effort: str
) -> None:
    """Verify that all AI models correctly receive model name, reasoning effort, and stream answers."""
    if effort not in supported_efforts:
        pytest.skip(f"Effort {effort} not supported by {model_id}")

    server = CodexAppServer()
    sent_requests: list[tuple[str, dict]] = []
    thread_id = "test-thread"
    turn_id = f"turn-{model_id}-{effort}"

    async def fake_status():
        from app.codex import RuntimeStatus
        return RuntimeStatus(available=True, detail="Ready")

    async def fake_request(method, params):
        sent_requests.append((method, params))
        if method == "turn/start":
            return {"turn": {"id": turn_id}}
        return {}

    async def fake_notify(method, params):
        return None

    # Pre-buffer turn events with correct threadId and turnId
    server._buffered_turn_events[turn_id] = [
        {
            "method": "item/agentMessage/delta",
            "params": {"threadId": thread_id, "turnId": turn_id, "delta": f"Ответ от {model_id} ({effort}). "},
        },
        {
            "method": "item/agentMessage/delta",
            "params": {"threadId": thread_id, "turnId": turn_id, "delta": "Всё работает штатно."},
        },
        {
            "method": "turn/completed",
            "params": {"threadId": thread_id, "turn": {"id": turn_id}},
        },
    ]

    monkeypatch.setattr(server, "status", fake_status)
    monkeypatch.setattr(server, "_ensure_thread", AsyncMock(return_value=thread_id))
    monkeypatch.setattr(server, "_request", fake_request)
    monkeypatch.setattr(server, "_notify", fake_notify)

    chunks = []
    async for chunk in server.reply_stream("Тестовый запрос", model=model_id, effort=effort):
        chunks.append(chunk)

    await server.close()

    # Verify that turn/start received correct model and effort
    turn_starts = [p for m, p in sent_requests if m == "turn/start"]
    assert len(turn_starts) == 1
    assert turn_starts[0]["model"] == model_id
    assert turn_starts[0]["effort"] == effort
    assert "".join(chunks) == f"Ответ от {model_id} ({effort}). Всё работает штатно."


# ---------------------------------------------------------------------------
# Dimension 2: TTS Audio Models Matrix (Piper, Silero, Edge, macOS)
# ---------------------------------------------------------------------------

TTS_VOICES_MATRIX = [
    # Engine, Voice Name, Expected Engine Category, Suffix
    ("piper", "Dmitri (Piper Neural · Offline)", "piper", ".wav"),
    ("piper", "Irina (Piper Neural · Offline)", "piper", ".wav"),
    ("silero", "Ksenia (Silero Neural · Offline)", "silero", ".wav"),
    ("silero", "Baya (Silero Neural · Offline)", "silero", ".wav"),
    ("silero", "Eugene (Silero Neural · Offline)", "silero", ".wav"),
    ("edge", "Svetlana (Neural · Edge)", "edge", ".mp3"),
    ("edge", "Dmitry (Neural · Edge)", "edge", ".mp3"),
    ("edge", "Jenny (Neural · Edge)", "edge", ".mp3"),
    ("macos", "Milena", "macos", ".wav"),
    ("macos", "Samantha", "macos", ".wav"),
]


@pytest.mark.parametrize("engine,voice_name,expected_engine,expected_suffix", TTS_VOICES_MATRIX)
def test_tts_voice_classification_matrix(
    engine: str, voice_name: str, expected_engine: str, expected_suffix: str
) -> None:
    """Verify voice classifier flags for all supported TTS engines."""
    if expected_engine == "piper":
        assert is_piper_voice(voice_name) is True
        assert is_silero_voice(voice_name) is False
        assert is_edge_voice(voice_name) is False
    elif expected_engine == "silero":
        assert is_silero_voice(voice_name) is True
        assert is_piper_voice(voice_name) is False
        assert is_edge_voice(voice_name) is False
    elif expected_engine == "edge":
        assert is_edge_voice(voice_name) is True
        assert is_piper_voice(voice_name) is False
        assert is_silero_voice(voice_name) is False
    else:
        assert is_piper_voice(voice_name) is False
        assert is_silero_voice(voice_name) is False
        assert is_edge_voice(voice_name) is False


@pytest.mark.parametrize("engine,voice_name,expected_engine,expected_suffix", TTS_VOICES_MATRIX)
@pytest.mark.anyio
async def test_tts_synthesis_matrix(
    monkeypatch, tmp_path: Path, engine: str, voice_name: str, expected_engine: str, expected_suffix: str
) -> None:
    """Verify synthesis execution across all TTS models with format and mime validation."""
    speaker = LocalMacOsSpeaker(voice=voice_name)

    if expected_engine == "piper":
        fake_wav = tmp_path / f"fake_piper_{voice_name[:5]}.wav"
        fake_wav.write_bytes(b"RIFFpiperwavdata")

        async def fake_piper(clean_text, v_name):
            dest = tmp_path / f"piper_out_{v_name[:5]}.wav"
            dest.write_bytes(fake_wav.read_bytes())
            return dest

        monkeypatch.setattr(speaker, "_synthesize_piper", fake_piper)

    elif expected_engine == "silero":
        fake_wav = tmp_path / f"fake_silero_{voice_name[:5]}.wav"
        fake_wav.write_bytes(b"RIFFsilerowavdata")

        async def fake_silero(clean_text, v_name):
            dest = tmp_path / f"silero_out_{v_name[:5]}.wav"
            dest.write_bytes(fake_wav.read_bytes())
            return dest

        monkeypatch.setattr(speaker, "_synthesize_silero", fake_silero)

    elif expected_engine == "edge":
        class FakeCommunicate:
            def __init__(self, text, voice, rate="+5%"):
                self.text = text
                self.voice = voice

            async def save(self, path):
                Path(path).write_bytes(b"ID3edgemp3data")

        import edge_tts
        monkeypatch.setattr(edge_tts, "Communicate", FakeCommunicate)

    elif expected_engine == "macos":
        fake_wav = tmp_path / f"fake_macos_{voice_name[:5]}.wav"
        fake_wav.write_bytes(b"RIFFmacoswavdata")

        async def fake_macos(clean_text, v_name):
            dest = tmp_path / f"macos_out_{v_name[:5]}.wav"
            dest.write_bytes(fake_wav.read_bytes())
            return dest

        monkeypatch.setattr(speaker, "_synthesize_macos", fake_macos)

    clip = await speaker.synthesize("Привет, это матричный тест синтеза!")
    assert clip is not None
    assert clip.is_file()
    assert clip.suffix == expected_suffix
    clip.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Dimension 3: STT Audio Models Matrix (Whisper HTTP & CLI across Locales)
# ---------------------------------------------------------------------------

STT_MATRIX = [
    ("ru", "Здравствуйте, проверка микрофона."),
    ("en", "Hello, microphone check."),
    ("es", "Hola, prueba de microfono."),
    ("auto", "Multilingual test audio."),
]


@pytest.mark.parametrize("lang,expected_transcript", STT_MATRIX)
@pytest.mark.anyio
async def test_stt_transcription_http_mode_matrix(
    monkeypatch, tmp_path: Path, lang: str, expected_transcript: str
) -> None:
    """Verify Whisper HTTP inference handling across languages."""
    fake_audio = tmp_path / "test_input.wav"
    fake_audio.write_bytes(b"RIFFdummywavdata")

    class FakeHttpResponse:
        status_code = 200

        def json(self):
            return {"text": expected_transcript}

    class FakeClient:
        async def post(self, url, files=None, data=None):
            assert data["language"] == lang
            return FakeHttpResponse()

    transcriber = LocalWhisperTranscriber(language=lang)
    monkeypatch.setattr(transcriber, "_get_http_client", lambda: FakeClient())

    result = await transcriber.transcribe(fake_audio, language=lang)
    assert result == expected_transcript


@pytest.mark.parametrize("lang,expected_transcript", STT_MATRIX)
@pytest.mark.anyio
async def test_stt_transcription_cli_fallback_matrix(
    monkeypatch, tmp_path: Path, lang: str, expected_transcript: str
) -> None:
    """Verify Whisper CLI fallback mode when HTTP server is unreachable."""
    fake_audio = tmp_path / "test_input.wav"
    fake_audio.write_bytes(b"RIFFdummywavdata")

    transcriber = LocalWhisperTranscriber(language=lang)

    # Force HTTP to return None (simulating unavailable server)
    monkeypatch.setattr(transcriber, "_transcribe_http", AsyncMock(return_value=None))

    async def fake_cli(audio_path, language, prompt):
        assert language == lang
        return expected_transcript

    monkeypatch.setattr(transcriber, "_transcribe_cli", fake_cli)

    result = await transcriber.transcribe(fake_audio, language=lang)
    assert result == expected_transcript


# ---------------------------------------------------------------------------
# Dimension 4: Cross-Model End-to-End WebSocket Matrix
# ---------------------------------------------------------------------------

CROSS_MATRIX_CASES = [
    # (Model ID, Effort, Voice Name, Binary Audio, User Input)
    ("gpt-5.6-luna", "low", "Svetlana (Neural · Edge)", False, "Тест скорости Луны с Edge"),
    ("gpt-5.6-sol", "medium", "Ksenia (Silero Neural · Offline)", False, "Тест Сола с Силеро"),
    ("gpt-6-astra", "high", "Dmitri (Piper Neural · Offline)", True, "Тест Астры с Пайпером и бинарным аудио"),
    ("gpt-5.5", "medium", "Milena", False, "Тест GPT-5.5 с macOS Say"),
]


@pytest.mark.parametrize("model_id,effort,voice_name,binary_audio,prompt_text", CROSS_MATRIX_CASES)
def test_cross_model_websocket_matrix(
    monkeypatch,
    tmp_path: Path,
    model_id: str,
    effort: str,
    voice_name: str,
    binary_audio: bool,
    prompt_text: str,
) -> None:
    """Verify end-to-end WebSocket turn execution for cross combinations of AI model + TTS voice."""
    conv_id = f"matrix-{model_id}-{effort}-{voice_name[:4]}-bin{binary_audio}"

    # Setup fake model reply stream
    async def fake_reply_stream(*args, **kwargs):
        yield f"Привет! Я работаю на {model_id}. "
        yield f"Использую голос {voice_name}."

    # Setup fake speaker
    fake_audio_clip = tmp_path / f"clip_{conv_id}.{'mp3' if 'Edge' in voice_name else 'wav'}"
    if "Edge" in voice_name:
        fake_audio_clip.write_bytes(b"ID3fakemp3content")
    else:
        fake_audio_clip.write_bytes(b"RIFF\x24\x00\x00\x00WAVEfmt \x10\x00\x00\x00\x01\x00\x01\x00\x80>\x00\x00\x00}\x00\x00\x02\x00\x10\x00data\x00\x00\x00\x00")

    async def fake_synthesize(self, text: str):
        return fake_audio_clip

    monkeypatch.setattr(main_module, "_call_reply_stream", fake_reply_stream)
    monkeypatch.setattr(LocalMacOsSpeaker, "synthesize", fake_synthesize)

    with client.websocket_connect(f"/ws/conversations/{conv_id}") as ws:
        ready = ws.receive_json()
        assert ready["type"] == "ready"

        # Apply settings from matrix
        ws.send_json({
            "type": "set_settings",
            "model": model_id,
            "effort": effort,
            "voice": voice_name,
            "binary_audio": binary_audio,
        })
        settings_ack = ws.receive_json()
        assert settings_ack["type"] == "settings_updated"
        assert settings_ack["model"] == model_id
        assert settings_ack["effort"] == effort
        assert settings_ack["voice"] == voice_name
        assert settings_ack["tts_engine"] == _get_tts_engine(voice_name)

        # Send turn prompt
        ws.send_json({"type": "text", "text": prompt_text})

        transcript = ws.receive_json()
        assert transcript["type"] == "transcript"
        assert transcript["text"] == prompt_text

        status_thinking = ws.receive_json()
        assert status_thinking["type"] == "status"
        assert status_thinking["state"] == "thinking"

        # Collect turn artifacts
        received_audio_chunks = []
        received_binary_frames = []
        turn_completed = False

        for _ in range(60):
            raw_msg = ws.receive()
            if "bytes" in raw_msg and raw_msg["bytes"]:
                received_binary_frames.append(raw_msg["bytes"])
            elif "text" in raw_msg and raw_msg["text"]:
                data = json.loads(raw_msg["text"])
                if data.get("type") == "audio_chunk":
                    received_audio_chunks.append(data)
                elif data.get("type") == "turn_completed":
                    turn_completed = True
                    assert data.get("voice") == voice_name
                    assert data.get("tts_engine") == _get_tts_engine(voice_name)
                    assert "timing" in data
                    timing = data["timing"]
                    assert "backend_total_ms" in timing
                    assert timing["backend_total_ms"] >= 0
                    if "backend_first_audio_ms" in timing:
                        assert timing["backend_first_audio_ms"] <= timing["backend_total_ms"]
                    break

        assert turn_completed is True

        if binary_audio:
            assert len(received_binary_frames) >= 1
            magic = received_binary_frames[0][0]
            assert magic == 0x01
        else:
            assert len(received_audio_chunks) >= 1
            assert "/speech/" in received_audio_chunks[0]["audio_url"]
