from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import app, conversations, Conversation
from app.transcribe import LocalWhisperTranscriber
from app.whisper_server import WhisperServerManager


client = TestClient(app)


def test_binary_audio_frame_transport_via_websocket() -> None:
    conv_id = "test-ws-binary-audio"
    with client.websocket_connect(f"/ws/conversations/{conv_id}") as ws:
        ready = ws.receive_json()
        assert ready["type"] == "ready"

        # Enable binary audio transport
        ws.send_json({
            "type": "set_settings",
            "binary_audio": True,
        })
        settings = ws.receive_json()
        assert settings["type"] == "settings_updated"

        async def fake_reply_stream(*args, **kwargs):
            yield "Тестовый ответ модели. "
            yield "Проверка бинарного аудио."

        # Mock speaker synthesize to return a dummy wav file and mock model reply stream
        with patch("app.main.LocalMacOsSpeaker.synthesize") as mock_synth, \
             patch("app.main._call_reply_stream", side_effect=fake_reply_stream):
            dummy_wav = Path(__file__).parent / "dummy_test_audio.wav"
            dummy_wav.write_bytes(b"RIFF\x24\x00\x00\x00WAVEfmt \x10\x00\x00\x00\x01\x00\x01\x00\x80>\x00\x00\x00}\x00\x00\x02\x00\x10\x00data\x00\x00\x00\x00")
            try:
                mock_synth.return_value = dummy_wav

                # Send user prompt
                ws.send_json({
                    "type": "text",
                    "text": "Тест бинарного аудио",
                })

                # Receive transcript
                transcript = ws.receive_json()
                assert transcript["type"] == "transcript"

                # Receive status
                status = ws.receive_json()
                assert status["type"] == "status"

                # Receive delta(s) and binary audio frames
                binary_frames = []
                json_messages = []

                for _ in range(50):
                    raw_msg = ws.receive()
                    if "bytes" in raw_msg and raw_msg["bytes"]:
                        binary_frames.append(raw_msg["bytes"])
                    elif "text" in raw_msg and raw_msg["text"]:
                        data = json.loads(raw_msg["text"])
                        json_messages.append(data)
                        if data.get("type") == "turn_completed":
                            break
                else:
                    pytest.fail("WebSocket did not receive turn_completed within 50 messages")

                assert len(binary_frames) >= 1
                frame = binary_frames[0]
                assert frame[0] == 0x01  # Magic byte for audio chunk
                header_len = int.from_bytes(frame[1:3], byteorder="big")
                header_json = frame[3 : 3 + header_len].decode("utf-8")
                header = json.loads(header_json)
                assert "clip_id" in header
                assert "mime_type" in header
                assert "/speech/" in header["audio_url"]
                raw_audio = frame[3 + header_len :]
                assert raw_audio.startswith(b"RIFF")
            finally:
                dummy_wav.unlink(missing_ok=True)


@pytest.mark.anyio
async def test_whisper_transcriber_reuses_http_client(tmp_path: Path) -> None:
    audio_file = tmp_path / "sample.wav"
    audio_file.write_bytes(b"RIFFdummywavbytes")

    client1 = LocalWhisperTranscriber(server_url="http://127.0.0.1:8089")
    http_instance1 = client1._get_http_client()
    assert isinstance(http_instance1, httpx.AsyncClient)

    client2 = LocalWhisperTranscriber(server_url="http://127.0.0.1:8089")
    http_instance2 = client2._get_http_client()
    # Verifies singleton connection pooling across instances
    assert http_instance1 is http_instance2

    # Clean shutdown
    await LocalWhisperTranscriber.close_shared_http_client()
    assert LocalWhisperTranscriber._shared_http_client is None


@pytest.mark.anyio
async def test_whisper_server_manager_persistent_client() -> None:
    manager = WhisperServerManager()
    client1 = manager._get_http_client()
    assert isinstance(client1, httpx.AsyncClient)
    client2 = manager._get_http_client()
    assert client1 is client2

    await manager.close()
    assert manager._http_client is None


@pytest.mark.anyio
async def test_pipelined_synthesis_prewarms_next_sentence() -> None:
    # Verify that pipelining pre-schedules synthesis concurrently
    synthesized_calls = []

    async def fake_synthesize(text: str) -> Path | None:
        synthesized_calls.append((text, asyncio.get_running_loop().time()))
        await asyncio.sleep(0.02)
        return None

    with patch("app.main.LocalMacOsSpeaker.synthesize", side_effect=fake_synthesize):
        conv_id = "test-ws-pipeline-synth"
        with client.websocket_connect(f"/ws/conversations/{conv_id}") as ws:
            ws.receive_json()  # ready

            async def mock_reply_stream(*args, **kwargs):
                yield "Первое предложение! "
                await asyncio.sleep(0.05)
                yield "Второе предложение! "
                yield "Третье предложение завершено."

            with patch("app.main._call_reply_stream", side_effect=mock_reply_stream):
                ws.send_json({"type": "text", "text": "тест пайплайна"})

                while True:
                    msg = ws.receive()
                    if "text" in msg and msg["text"]:
                        data = json.loads(msg["text"])
                        if data.get("type") == "turn_completed":
                            break

            # Must have synthesized all 3 sentences
            assert len(synthesized_calls) == 3
            texts = [c[0] for c in synthesized_calls]
            assert "Первое предложение!" in texts
            assert "Второе предложение!" in texts
            assert "Третье предложение завершено." in texts


@pytest.mark.anyio
async def test_bounded_synthesis_concurrency_limit() -> None:
    current_concurrent = 0
    max_concurrent = 0

    async def tracking_synthesize(text: str) -> Path | None:
        nonlocal current_concurrent, max_concurrent
        current_concurrent += 1
        max_concurrent = max(max_concurrent, current_concurrent)
        await asyncio.sleep(0.04)
        current_concurrent -= 1
        return None

    with patch("app.main.LocalMacOsSpeaker.synthesize", side_effect=tracking_synthesize):
        conv_id = "test-ws-bounded-concurrency"
        with client.websocket_connect(f"/ws/conversations/{conv_id}") as ws:
            ws.receive_json()  # ready

            async def fast_multi_sentence_stream(*args, **kwargs):
                for i in range(6):
                    yield f"Предложение номер {i + 1}! "

            with patch("app.main._call_reply_stream", side_effect=fast_multi_sentence_stream):
                ws.send_json({"type": "text", "text": "тест лимита параллелизма"})

                for _ in range(50):
                    msg = ws.receive()
                    if "text" in msg and msg["text"]:
                        data = json.loads(msg["text"])
                        if data.get("type") == "turn_completed":
                            break
                else:
                    pytest.fail("WebSocket did not receive turn_completed within 50 messages")

            assert max_concurrent <= 2
            assert max_concurrent >= 1

