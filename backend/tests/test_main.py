import asyncio
import re
from pathlib import Path

from fastapi.testclient import TestClient

from app import main as main_module
from app.main import app
from app.transcribe import run_tone_shadow, tone_shadow_configured, tone_streaming_configured


client = TestClient(app)


def test_tone_shadow_is_disabled_without_local_model(monkeypatch) -> None:
    monkeypatch.delenv("VOICE_OF_LUNA_TONE_MODEL", raising=False)
    monkeypatch.delenv("VOICE_OF_LUNA_TONE_TOKENS", raising=False)
    assert tone_shadow_configured() is False


def test_tone_shadow_reports_missing_local_executable(monkeypatch) -> None:
    monkeypatch.setenv("VOICE_OF_LUNA_TONE_MODEL", "/tmp/t-one.onnx")
    monkeypatch.setenv("VOICE_OF_LUNA_TONE_TOKENS", "/tmp/tokens.txt")
    monkeypatch.setattr("app.transcribe.shutil.which", lambda _: None)
    result = asyncio.run(run_tone_shadow(b"RIFF"))
    assert result == {"status": "unavailable", "reason": "sherpa_executable_not_found"}


def test_tone_streaming_requires_explicit_opt_in(monkeypatch) -> None:
    monkeypatch.setenv("VOICE_OF_LUNA_TONE_MODEL", "/tmp/t-one.onnx")
    monkeypatch.setenv("VOICE_OF_LUNA_TONE_TOKENS", "/tmp/tokens.txt")
    monkeypatch.delenv("VOICE_OF_LUNA_TONE_STREAMING", raising=False)
    assert tone_streaming_configured() is False




def test_creates_and_deletes_conversation() -> None:
    created = client.post("/api/conversations")
    assert created.status_code == 201
    conversation_id = created.json()["id"]

    deleted = client.delete(f"/api/conversations/{conversation_id}")
    assert deleted.status_code == 204


def test_deleting_conversation_closes_its_codex_bridge(monkeypatch) -> None:
    conversation_id = client.post("/api/conversations").json()["id"]
    closed = []

    async def fake_close(bridge):
        closed.append(id(bridge))

    monkeypatch.setattr(main_module.CodexAppServer, "close", fake_close)

    deleted = client.delete(f"/api/conversations/{conversation_id}")

    assert deleted.status_code == 204
    assert len(closed) == 1


def test_idle_reaper_closes_only_disconnected_conversations(monkeypatch) -> None:
    stale = main_module.Conversation(id="stale")
    stale.last_active_at = 10.0
    connected = main_module.Conversation(id="connected", active_websockets=1)
    connected.last_active_at = 10.0
    main_module.conversations[stale.id] = stale
    main_module.conversations[connected.id] = connected
    closed = []

    async def fake_close(conversation_id):
        closed.append(conversation_id)
        return True

    monkeypatch.setattr(main_module, "_close_and_delete_conversation", fake_close)

    import asyncio
    removed = asyncio.run(
        main_module._reap_idle_conversations(
            now=10.0 + main_module.CONVERSATION_IDLE_TTL_SECONDS
        )
    )

    assert removed == 1
    assert closed == ["stale"]
    main_module.conversations.pop(stale.id, None)
    main_module.conversations.pop(connected.id, None)


def test_turn_rejects_unknown_conversation_without_starting_codex() -> None:
    response = client.post("/api/conversations/missing/turns", json={"text": "Hello"})
    assert response.status_code == 404


def test_conversation_reuses_its_codex_bridge_between_turns(monkeypatch) -> None:
    conversation_id = client.post("/api/conversations").json()["id"]
    bridge_ids = []

    async def fake_reply(bridge, text):
        bridge_ids.append(id(bridge))
        return f"Reply to: {text}"

    monkeypatch.setattr(main_module.CodexAppServer, "reply", fake_reply)

    first = client.post(f"/api/conversations/{conversation_id}/turns", json={"text": "First"})
    second = client.post(f"/api/conversations/{conversation_id}/turns", json={"text": "Second"})

    assert first.status_code == 200
    assert second.status_code == 200
    assert bridge_ids[0] == bridge_ids[1]


def test_htmx_shell_creates_a_conversation_form() -> None:
    page = client.get("/")
    assert page.status_code == 200
    assert 'src="/static/vendor/htmx-2.0.4.min.js"' in page.text
    assert client.get("/static/vendor/htmx-2.0.4.min.js").status_code == 200

    fragment = client.post("/conversations")
    assert fragment.status_code == 200
    assert "hx-post" in fragment.text


def test_htmx_delete_conversation_resets_shell() -> None:
    conversation_id = client.post("/api/conversations").json()["id"]
    deleted = client.delete(f"/conversations/{conversation_id}")
    assert deleted.status_code == 200
    assert "hx-post" in deleted.text
    assert "data-reset-chat" in deleted.text


def test_htmx_delete_conversation_closes_bridge_and_cleans_speech(monkeypatch, tmp_path) -> None:
    conversation_id = client.post("/api/conversations").json()["id"]
    clip_file = tmp_path / "test_clip.m4a"
    clip_file.write_bytes(b"audio data")
    main_module.speech_clips["clip-1"] = main_module.SpeechClip(
        conversation_id=conversation_id, path=clip_file
    )
    closed = []

    async def fake_close(bridge):
        closed.append(id(bridge))

    monkeypatch.setattr(main_module.CodexAppServer, "close", fake_close)

    deleted = client.delete(f"/conversations/{conversation_id}")
    assert deleted.status_code == 200
    assert len(closed) == 1
    assert not clip_file.exists()
    assert "clip-1" not in main_module.speech_clips


def test_stale_html_conversation_recovers_after_a_local_restart(monkeypatch) -> None:
    async def fake_reply(_, text):
        return f"Reply to: {text}"

    monkeypatch.setattr(main_module.CodexAppServer, "reply", fake_reply)
    response = client.post("/conversations/expired/turns", data={"text": "Second message"})

    assert response.status_code == 200
    assert "Reply to: Second message" in response.text
    assert "/conversations/expired/turns" not in response.text


def test_audio_turn_rejects_non_audio_upload() -> None:
    conversation_id = client.post("/api/conversations").json()["id"]
    response = client.post(
        f"/conversations/{conversation_id}/audio",
        files={"audio": ("note.txt", b"not audio", "text/plain")},
    )
    assert response.status_code == 415


def test_stale_voice_form_recovers_after_a_local_restart(monkeypatch) -> None:
    async def fake_convert(source):
        converted = source.with_suffix(".wav")
        converted.write_bytes(b"converted audio")
        return converted

    async def fake_transcribe(_, __):
        return "Second voice message"

    async def fake_reply(_, text):
        return f"Reply to: {text}"

    monkeypatch.setattr(main_module, "_convert_to_wav", fake_convert)
    monkeypatch.setattr(main_module.LocalWhisperTranscriber, "transcribe", fake_transcribe)
    monkeypatch.setattr(main_module.CodexAppServer, "reply", fake_reply)
    response = client.post(
        "/conversations/expired/audio",
        files={"audio": ("recording.webm", b"fake audio", "audio/webm")},
    )

    assert response.status_code == 200
    assert "Reply to: Second voice message" in response.text
    assert "/conversations/expired/audio" not in response.text


def test_audio_turn_removes_temporary_recording(monkeypatch) -> None:
    conversation_id = client.post("/api/conversations").json()["id"]
    received_paths = []
    source_paths = []

    async def fake_convert(source):
        source_paths.append(source)
        converted = source.with_suffix(".wav")
        converted.write_bytes(b"converted audio")
        return converted

    async def fake_transcribe(_, path):
        received_paths.append(path)
        assert path.exists()
        return "Hello from a local transcription."

    async def fake_reply(_, text):
        assert text == "Hello from a local transcription."
        return "I heard you."

    monkeypatch.setattr(main_module, "_convert_to_wav", fake_convert)
    monkeypatch.setattr(main_module.LocalWhisperTranscriber, "transcribe", fake_transcribe)
    monkeypatch.setattr(main_module.CodexAppServer, "reply", fake_reply)
    response = client.post(
        f"/conversations/{conversation_id}/audio",
        files={"audio": ("recording.webm", b"fake audio", "audio/webm")},
    )

    assert response.status_code == 200
    assert "I heard you." in response.text
    assert received_paths and not received_paths[0].exists()
    assert source_paths and not source_paths[0].exists()


def test_audio_turn_uses_safe_default_temporary_extension(monkeypatch) -> None:
    conversation_id = client.post("/api/conversations").json()["id"]
    source_paths = []

    async def fake_convert(source):
        source_paths.append(source)
        converted = source.with_suffix(".wav")
        converted.write_bytes(b"converted audio")
        return converted

    async def fake_transcribe(_, __):
        return "A transcript."

    async def fake_reply(_, __):
        return "A reply."

    monkeypatch.setattr(main_module, "_convert_to_wav", fake_convert)
    monkeypatch.setattr(main_module.LocalWhisperTranscriber, "transcribe", fake_transcribe)
    monkeypatch.setattr(main_module.CodexAppServer, "reply", fake_reply)
    response = client.post(
        f"/conversations/{conversation_id}/audio",
        files={"audio": ("untrusted.extension", b"fake audio", "audio/unknown")},
    )

    assert response.status_code == 200
    assert source_paths[0].suffix == ".webm"


def test_russian_reply_uses_a_one_time_local_speech_clip(monkeypatch, tmp_path) -> None:
    conversation_id = client.post("/api/conversations").json()["id"]
    clip = tmp_path / "milena.wav"

    async def fake_reply(_, __):
        return "Привет, я говорю по-русски."

    async def fake_synthesize(_, text):
        assert text == "Привет, я говорю по-русски."
        clip.write_bytes(b"local speech")
        return clip

    monkeypatch.setattr(main_module.CodexAppServer, "reply", fake_reply)
    monkeypatch.setattr(main_module.LocalMacOsSpeaker, "synthesize", fake_synthesize)
    response = client.post(
        f"/conversations/{conversation_id}/turns",
        data={"text": "Привет"},
    )

    assert response.status_code == 200
    match = re.search(r'src="(/speech/[^"]+)"', response.text)
    assert match
    audio = client.get(match.group(1))
    assert audio.status_code == 200
    assert audio.headers["content-type"].startswith("audio/wav")
    assert audio.content == b"local speech"
    assert not clip.exists()


def test_whisper_transcriber_passes_configured_language_and_threads(monkeypatch, tmp_path) -> None:
    captured_args = []

    async def fake_create_subprocess_exec(*args, **kwargs):
        captured_args.extend(args)
        # Create the fake output json that whisper-cli would generate
        for i, arg in enumerate(args):
            if arg == "-of":
                out_json = Path(f"{args[i + 1]}.json")
                out_json.write_text('{"transcription": [{"text": "тестовый текст"}]}', encoding="utf-8")
        class FakeProc:
            async def wait(self):
                return 0
        return FakeProc()

    model_file = tmp_path / "model.bin"
    model_file.write_bytes(b"model")
    audio_file = tmp_path / "audio.wav"
    audio_file.write_bytes(b"audio")

    monkeypatch.setattr("app.transcribe.shutil.which", lambda cmd: "/usr/bin/" + cmd)
    monkeypatch.setattr("app.transcribe.asyncio.create_subprocess_exec", fake_create_subprocess_exec)

    transcriber = main_module.LocalWhisperTranscriber(model_path=model_file, language="ru")
    import asyncio
    result = asyncio.run(transcriber.transcribe(audio_file))

    assert result == "тестовый текст"
    assert "-l" in captured_args
    idx_l = captured_args.index("-l")
    assert captured_args[idx_l + 1] == "ru"
    assert "-t" in captured_args


def test_macos_speaker_renders_wav_directly(monkeypatch, tmp_path) -> None:
    captured_args = []

    async def fake_create_subprocess_exec(*args, **kwargs):
        captured_args.extend(args)
        for i, arg in enumerate(args):
            if arg == "-o":
                wav_dest = Path(args[i + 1])
                wav_dest.write_bytes(b"RIFFwavdata")
        class FakeProc:
            async def wait(self):
                return 0
        return FakeProc()

    monkeypatch.setattr("app.speak.shutil.which", lambda cmd: "/usr/bin/" + cmd)
    monkeypatch.setattr("app.speak.asyncio.create_subprocess_exec", fake_create_subprocess_exec)

    # Exercise the macOS engine explicitly; the CI host may be Linux and has
    # no system `say` voice inventory to select from.
    speaker = main_module.LocalMacOsSpeaker(voice="Milena")
    import asyncio
    output_path = asyncio.run(speaker.synthesize("Привет мир"))

    assert output_path is not None
    assert output_path.suffix == ".wav"
    assert captured_args[0] == "say"
    assert "--data-format=LEI16@22050" in captured_args
    output_path.unlink(missing_ok=True)


def test_whisper_transcriber_uses_http_server_when_available(monkeypatch, tmp_path) -> None:
    audio_file = tmp_path / "audio.wav"
    audio_file.write_bytes(b"RIFFwavdata")

    async def fake_post(*args, **kwargs):
        class FakeResponse:
            status_code = 200
            def json(self):
                return {"text": "распознано через сервер"}
        return FakeResponse()

    monkeypatch.setattr("httpx.AsyncClient.post", fake_post)

    transcriber = main_module.LocalWhisperTranscriber(
        server_url="http://127.0.0.1:8089", language="ru"
    )
    import asyncio
    result = asyncio.run(transcriber.transcribe(audio_file))
    assert result == "распознано через сервер"


def test_whisper_transcriber_falls_back_to_cli_on_http_error(monkeypatch, tmp_path) -> None:
    audio_file = tmp_path / "audio.wav"
    audio_file.write_bytes(b"RIFFwavdata")
    model_file = tmp_path / "model.bin"
    model_file.write_bytes(b"model")

    async def fake_post(*args, **kwargs):
        raise RuntimeError("Connection refused")

    async def fake_create_subprocess_exec(*args, **kwargs):
        for i, arg in enumerate(args):
            if arg == "-of":
                out_json = Path(f"{args[i + 1]}.json")
                out_json.write_text('{"transcription": [{"text": "распознано через cli fallback"}]}', encoding="utf-8")
        class FakeProc:
            async def wait(self):
                return 0
        return FakeProc()

    monkeypatch.setattr("httpx.AsyncClient.post", fake_post)
    monkeypatch.setattr("app.transcribe.shutil.which", lambda cmd: "/usr/bin/" + cmd)
    monkeypatch.setattr("app.transcribe.asyncio.create_subprocess_exec", fake_create_subprocess_exec)

    transcriber = main_module.LocalWhisperTranscriber(
        model_path=model_file, server_url="http://127.0.0.1:8089", language="ru"
    )
    import asyncio
    result = asyncio.run(transcriber.transcribe(audio_file))
    assert result == "распознано через cli fallback"


def test_whisper_server_manager_properties(tmp_path) -> None:
    from app.whisper_server import WhisperServerManager

    model_file = tmp_path / "model.bin"
    model_file.write_bytes(b"model")

    manager = WhisperServerManager(port=8099, model_path=model_file)
    assert manager.server_url == "http://127.0.0.1:8099"
    assert manager.inference_url == "http://127.0.0.1:8099/inference"
    assert manager.threads >= 1


def test_is_16k_mono_wav_validation(tmp_path) -> None:
    import wave

    valid_wav = tmp_path / "valid_16k.wav"
    with wave.open(str(valid_wav), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"\x00\x00" * 1600)
    assert main_module._is_16k_mono_wav(valid_wav) is True

    stereo_wav = tmp_path / "stereo.wav"
    with wave.open(str(stereo_wav), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"\x00\x00\x00\x00" * 1600)
    assert main_module._is_16k_mono_wav(stereo_wav) is False

    wrong_rate = tmp_path / "rate_44k.wav"
    with wave.open(str(wrong_rate), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(44100)
        w.writeframes(b"\x00\x00" * 4410)
    assert main_module._is_16k_mono_wav(wrong_rate) is False


def test_audio_turn_bypasses_convert_to_wav_for_16k_mono_wav(monkeypatch, tmp_path) -> None:
    import wave

    valid_wav = tmp_path / "mic_recording.wav"
    with wave.open(str(valid_wav), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"\x00\x00" * 1600)
    audio_bytes = valid_wav.read_bytes()

    conversation_id = client.post("/api/conversations").json()["id"]

    convert_called = []

    async def fake_convert(source):
        convert_called.append(source)
        return source

    async def fake_transcribe(_, path):
        assert path.exists()
        return "Bypassed conversion successfully."

    async def fake_reply(_, text):
        return f"Echo: {text}"

    monkeypatch.setattr(main_module, "_convert_to_wav", fake_convert)
    monkeypatch.setattr(main_module.LocalWhisperTranscriber, "transcribe", fake_transcribe)
    monkeypatch.setattr(main_module.CodexAppServer, "reply", fake_reply)

    response = client.post(
        f"/conversations/{conversation_id}/audio",
        files={"audio": ("recording.wav", audio_bytes, "audio/wav")},
    )

    assert response.status_code == 200
    assert "Echo: Bypassed conversion successfully." in response.text
    assert len(convert_called) == 0


def test_websocket_connect_and_receive_ready() -> None:
    with client.websocket_connect("/ws/conversations/test-ws-conv") as ws:
        ready = ws.receive_json()
        assert ready["type"] == "ready"
        assert ready["conversation_id"] is not None


def test_websocket_text_turn_streams_and_completes(monkeypatch, tmp_path) -> None:
    async def fake_reply_stream(self, text):
        yield "Привет, "
        yield "я Luna!"

    async def fake_synthesize(self, text):
        clip = tmp_path / "stream.wav"
        clip.write_bytes(b"RIFFwavdata")
        return clip

    monkeypatch.setattr(main_module.CodexAppServer, "reply_stream", fake_reply_stream)
    monkeypatch.setattr(main_module.LocalMacOsSpeaker, "synthesize", fake_synthesize)

    with client.websocket_connect("/ws/conversations/test-ws-stream") as ws:
        ready = ws.receive_json()
        assert ready["type"] == "ready"

        ws.send_json({"type": "text", "text": "Привет"})

        transcript = ws.receive_json()
        assert transcript["type"] == "transcript"
        assert transcript["text"] == "Привет"

        status_thinking = ws.receive_json()
        assert status_thinking["type"] == "status"
        assert status_thinking["state"] == "thinking"

        delta1 = ws.receive_json()
        assert delta1["type"] == "delta"
        assert delta1["delta"] == "Привет, "

        delta2 = ws.receive_json()
        assert delta2["type"] == "delta"
        assert delta2["delta"] == "я Luna!"

        audio_chunk = ws.receive_json()
        assert audio_chunk["type"] == "audio_chunk"
        assert "/speech/" in audio_chunk["audio_url"]

        turn_done = ws.receive_json()
        assert turn_done["type"] == "turn_completed"
        assert turn_done["turn"]["text"] == "Привет, я Luna!"

        status_idle = ws.receive_json()
        if status_idle["type"] == "metrics":
            status_idle = ws.receive_json()
        assert status_idle["type"] == "status"
        assert status_idle["state"] == "idle"


def test_websocket_stop_speaking_resets_state() -> None:
    with client.websocket_connect("/ws/conversations/test-ws-stop") as ws:
        ws.receive_json()  # ready
        ws.send_json({"type": "stop_speaking"})
        response = ws.receive_json()
        assert response["type"] == "status"
        assert response["state"] == "idle"


def test_websocket_binary_audio_turn_transcribes_and_streams(monkeypatch, tmp_path) -> None:
    import wave

    valid_wav = tmp_path / "mic_recording.wav"
    with wave.open(str(valid_wav), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"\x00\x00" * 1600)
    audio_bytes = valid_wav.read_bytes()

    async def fake_transcribe(_, path):
        assert path.exists()
        return "Голосовой запрос"

    async def fake_reply_stream(self, text):
        yield "Ответ "
        yield "на голос."

    async def fake_synthesize(self, text):
        clip = tmp_path / "voice_reply.wav"
        clip.write_bytes(b"RIFFwavdata")
        return clip

    monkeypatch.setattr(main_module.LocalWhisperTranscriber, "transcribe", fake_transcribe)
    monkeypatch.setattr(main_module.CodexAppServer, "reply_stream", fake_reply_stream)
    monkeypatch.setattr(main_module.LocalMacOsSpeaker, "synthesize", fake_synthesize)

    with client.websocket_connect("/ws/conversations/test-ws-audio") as ws:
        ready = ws.receive_json()
        assert ready["type"] == "ready"

        ws.send_json({"type": "audio_timing", "endpoint_delay_ms": 450, "audio_encode_ms": 32})
        ws.send_bytes(audio_bytes)

        status_transcribing = ws.receive_json()
        assert status_transcribing["type"] == "status"
        assert status_transcribing["state"] == "transcribing"

        transcript = ws.receive_json()
        assert transcript["type"] == "transcript"
        assert transcript["text"] == "Голосовой запрос"

        status_thinking = ws.receive_json()
        assert status_thinking["type"] == "status"
        assert status_thinking["state"] == "thinking"

        delta1 = ws.receive_json()
        assert delta1["type"] == "delta"
        assert delta1["delta"] == "Ответ "

        delta2 = ws.receive_json()
        assert delta2["type"] == "delta"
        assert delta2["delta"] == "на голос."

        audio_chunk = ws.receive_json()
        assert audio_chunk["type"] == "audio_chunk"

        turn_done = ws.receive_json()
        assert turn_done["type"] == "turn_completed"
        assert turn_done["turn"]["text"] == "Ответ на голос."
        assert turn_done["timing"]["client_endpoint_delay_ms"] == 450
        assert turn_done["timing"]["client_audio_encode_ms"] == 32
        assert "server_audio_prep_ms" in turn_done["timing"]
        assert "stt_ms" in turn_done["timing"]
        assert "first_speech_segment_wait_ms" in turn_done["timing"]
        assert "tts_synthesis_first_chunk_ms" in turn_done["timing"]

        status_idle = ws.receive_json()
        if status_idle["type"] == "metrics":
            status_idle = ws.receive_json()
        assert status_idle["type"] == "status"
        assert status_idle["state"] == "idle"


def test_websocket_pcm_stream_buffers_until_end_and_transcribes(monkeypatch, tmp_path) -> None:
    async def fake_transcribe(_, path):
        assert path.exists()
        return "Потоковый запрос"

    async def fake_reply_stream(self, text):
        yield "Потоковый ответ."

    async def fake_synthesize(self, text):
        clip = tmp_path / "stream_reply.wav"
        clip.write_bytes(b"RIFFwavdata")
        return clip

    monkeypatch.setattr(main_module.LocalWhisperTranscriber, "transcribe", fake_transcribe)
    monkeypatch.setattr(main_module.CodexAppServer, "reply_stream", fake_reply_stream)
    monkeypatch.setattr(main_module.LocalMacOsSpeaker, "synthesize", fake_synthesize)

    with client.websocket_connect("/ws/conversations/test-ws-pcm-stream") as ws:
        assert ws.receive_json()["type"] == "ready"
        ws.send_json({"type": "audio_stream_start", "sample_rate": 16_000})
        stream_status = ws.receive_json()
        assert stream_status["type"] == "status"
        assert stream_status["mode_label"] == "STREAM // PCM"

        ws.send_bytes(b"\x00\x00" * 800)
        ws.send_bytes(b"\x01\x00" * 800)
        ws.send_json({"type": "audio_stream_end"})

        transcribing = ws.receive_json()
        assert transcribing["type"] == "status"
        assert transcribing["state"] == "transcribing"
        transcript = ws.receive_json()
        assert transcript["type"] == "transcript"
        assert transcript["text"] == "Потоковый запрос"


def test_api_speech_synthesize_success(monkeypatch, tmp_path) -> None:
    clip = tmp_path / "synthesize_test.wav"
    clip.write_bytes(b"RIFFwavsynthesized")

    async def fake_synthesize(_, text):
        assert "Привет" in text
        return clip

    monkeypatch.setattr(main_module.LocalMacOsSpeaker, "synthesize", fake_synthesize)
    response = client.post("/api/speech/synthesize", json={"text": "Привет, Luna!"})

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("audio/wav")
    assert response.content == b"RIFFwavsynthesized"
    assert not clip.exists()


def test_api_speech_synthesize_accepts_supported_english(monkeypatch, tmp_path) -> None:
    clip = tmp_path / "english_synthesis.wav"
    clip.write_bytes(b"RIFFenglish")

    async def fake_synthesize(_, text):
        assert text == "Hello, this is pure English."
        return clip

    monkeypatch.setattr(main_module.LocalMacOsSpeaker, "synthesize", fake_synthesize)
    response = client.post("/api/speech/synthesize", json={"text": "Hello, this is pure English."})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("audio/wav")
    assert not clip.exists()


def test_api_speech_synthesize_validates_input() -> None:
    response = client.post("/api/speech/synthesize", json={"text": ""})
    assert response.status_code == 422


def test_get_installed_voices_and_default() -> None:
    voices = main_module.get_installed_voices()
    assert len(voices) > 0
    ru_voices = [v for v in voices if v.is_russian]
    assert any(any(n in v.name for n in ("Milena", "Svetlana", "Dmitri")) for v in ru_voices)
    default_voice = main_module.get_default_voice()
    assert default_voice in [v.name for v in voices]


def test_api_voices_endpoint() -> None:
    response = client.get("/api/voices")
    assert response.status_code == 200
    data = response.json()
    assert "active_voice" in data
    assert "voices" in data
    assert "russian_voices" in data
    assert "other_voices" in data
    assert any(any(n in v["name"] for n in ("Milena", "Svetlana", "Dmitri")) for v in data["russian_voices"])


def test_api_voice_selection_and_cookie_persistence() -> None:
    # Edge voices are available on every supported CI host.
    response = client.post("/api/voice", json={"voice": "Svetlana (Neural · Edge)"})
    assert response.status_code == 200
    assert response.json() == {"ok": True, "active_voice": "Svetlana (Neural · Edge)"}
    cookie_header = response.headers.get("set-cookie", "")
    assert "voice_of_luna_voice=" in cookie_header
    assert "Svetlana (Neural" in cookie_header

    # The TestClient keeps the response cookie; passing a Unicode cookie via
    # httpx's per-request cookies is not portable across Python versions.
    page = client.get("/")
    assert page.status_code == 200
    assert 'value="Svetlana (Neural · Edge)" selected' in page.text


def test_htmx_shell_renders_voice_selector_with_options() -> None:
    page = client.get("/")
    assert page.status_code == 200
    assert 'id="voice-select"' in page.text
    assert "Svetlana (Neural" in page.text
    assert "voice-selector-chip" in page.text
    # Other voices should be collapsed by default into template + expand option
    assert 'value="__expand_other__"' in page.text
    assert 'id="other-voices-template"' in page.text
    # Plugin selector should NOT be duplicated in terminal-header
    assert 'id="plugin-select"' not in page.text
    # Plugin selector should be in session-toolbar
    assert 'id="session-plugin-select"' in page.text
    assert 'id="mode-chip"' in page.text


def test_htmx_shell_keeps_other_language_voice_compact_when_active() -> None:
    # Edge English is available independently of macOS system voices.
    selected = client.post("/api/voice", json={"voice": "Jenny (Neural · Edge)"})
    assert selected.status_code == 200
    page = client.get("/")
    assert page.status_code == 200
    assert 'id="voice-select"' in page.text
    # Selecting a foreign-language voice must not re-open the whole catalog.
    assert 'id="active-other-voice"' in page.text
    select_markup = page.text.split('<select id="voice-select"', 1)[1].split("</select>", 1)[0]
    assert 'id="other-voices-group"' not in select_markup
    assert 'value="__expand_other__"' in page.text
    assert "Jenny (Neural · Edge) — en-US" in page.text


def test_voice_selector_labels_primary_edge_voices_with_locale() -> None:
    page = client.get("/")
    assert page.status_code == 200
    assert "Svetlana (Neural · Edge) — ru-RU" in page.text
    # English Edge voices belong to the collapsed other-languages catalog.
    primary_edge_group = page.text.split('label="Нейросеть (Edge Cloud · Бесплатно)"', 1)[1].split("</optgroup>", 1)[0]
    assert "Jenny (Neural · Edge)" not in primary_edge_group


def test_websocket_set_voice_updates_state() -> None:
    conv_id = client.post("/api/conversations").json()["id"]
    with client.websocket_connect(f"/ws/conversations/{conv_id}") as ws:
        ready = ws.receive_json()
        assert ready["type"] == "ready"

        ws.send_json({"type": "set_voice", "voice": "Milena (Enhanced)"})
        reply = ws.receive_json()
        assert reply["type"] == "voice_updated"
        assert reply["voice"] == "Milena (Enhanced)"

        conv = main_module.conversations.get(conv_id)
        assert conv is not None
        assert conv.voice == "Milena (Enhanced)"


def test_api_models_endpoint() -> None:
    response = client.get("/api/models")
    assert response.status_code == 200
    data = response.json()
    assert "models" in data
    assert "active_model" in data
    assert "active_effort" in data
    assert isinstance(data["models"], list)
    assert len(data["models"]) > 0


def test_api_models_closes_temporary_discovery_client(monkeypatch) -> None:
    closed = []

    async def fake_list_models(_):
        return [{"id": "test-model"}]

    async def fake_close(client):
        closed.append(id(client))

    monkeypatch.setattr(main_module.CodexAppServer, "list_models", fake_list_models)
    monkeypatch.setattr(main_module.CodexAppServer, "close", fake_close)

    response = client.get("/api/models")

    assert response.status_code == 200
    assert response.json()["models"] == [{"id": "test-model"}]
    assert len(closed) == 1


def test_api_models_does_not_reinsert_an_unavailable_saved_model(monkeypatch) -> None:
    async def fake_list_models(_):
        return [{"id": "gpt-5.6-luna"}]

    async def fake_close(_):
        return None

    monkeypatch.setattr(main_module.CodexAppServer, "list_models", fake_list_models)
    monkeypatch.setattr(main_module.CodexAppServer, "close", fake_close)

    response = client.get("/api/models", cookies={"voice_of_luna_model": "gpt-5.4-mini"})

    assert response.status_code == 200
    assert response.json()["models"] == [{"id": "gpt-5.6-luna"}]
    assert response.json()["active_model"] == "gpt-5.6-luna"


def test_voice_selection_does_not_change_another_conversations_default_voice() -> None:
    from app.speak import get_default_voice, reset_active_voice

    reset_active_voice()
    selected_voice = "Dmitry (Neural · Edge)"
    response = client.post("/api/voice", json={"voice": selected_voice})
    assert response.status_code == 200

    other_client = TestClient(app)
    conversation_id = other_client.post("/api/conversations").json()["id"]
    conversation = main_module.conversations[conversation_id]

    assert conversation.voice == get_default_voice()
    assert conversation.voice != selected_voice


def test_api_settings_endpoint_and_cookie_persistence() -> None:
    response = client.post(
        "/api/settings",
        json={"model": "gpt-5.6-luna", "effort": "high", "voice": "Milena"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert data["model"] == "gpt-5.6-luna"
    assert data["effort"] == "high"
    assert data["voice"] == "Milena"

    cookie_header = response.headers.get("set-cookie", "")
    assert "voice_of_luna_model=gpt-5.6-luna" in cookie_header
    assert "voice_of_luna_effort=high" in cookie_header

    # GET / with cookies should select the configured model and effort
    page = client.get(
        "/",
        cookies={
            "voice_of_luna_model": "gpt-5.6-luna",
            "voice_of_luna_effort": "high",
        },
    )
    assert page.status_code == 200
    assert 'value="gpt-5.6-luna" selected' in page.text
    assert 'value="high" selected' in page.text


def test_remote_warmup_setting_is_enabled_by_default_and_can_be_disabled(monkeypatch) -> None:
    scheduled = []
    monkeypatch.setattr(
        main_module,
        "_schedule_conversation_warmup",
        lambda conversation, model, effort: scheduled.append((conversation.id, model, effort)) or "warming",
    )

    enabled = client.post(
        "/api/settings",
        json={"model": "gpt-5.6-luna", "effort": "low", "remote_warmup": True},
    )
    assert enabled.status_code == 200
    assert enabled.json()["remote_warmup"] is True
    assert enabled.json()["remote_warmup_status"] == "enabled"
    assert scheduled == []
    assert "voice_of_luna_remote_warmup=true" in enabled.headers["set-cookie"]

    disabled = client.post("/api/settings", json={"remote_warmup": False})
    assert disabled.status_code == 200
    assert disabled.json()["remote_warmup"] is False
    assert disabled.json()["remote_warmup_status"] == "off"
    assert "voice_of_luna_remote_warmup=false" in disabled.headers["set-cookie"]

    conv_id = client.post("/api/conversations").json()["id"]
    with client.websocket_connect(f"/ws/conversations/{conv_id}") as ws:
        ws.receive_json()
        ws.send_json({
            "type": "set_settings",
            "model": "gpt-5.6-luna",
            "effort": "low",
            "remote_warmup": True,
        })
        response = ws.receive_json()
    assert response["remote_warmup_status"] == "warming"
    assert scheduled[-1] == (conv_id, "gpt-5.6-luna", "low")


def test_htmx_shell_renders_model_and_effort_selectors() -> None:
    page = client.get("/")
    assert page.status_code == 200
    assert 'id="model-select"' in page.text
    assert 'id="effort-select"' in page.text
    assert "model-selector-chip" in page.text
    assert "effort-selector-chip" in page.text


def test_conversation_html_renders_latency_badge() -> None:
    conv_id = client.post("/api/conversations").json()["id"]
    page = client.get(f"/conversations/{conv_id}")
    assert page.status_code == 200
    assert 'id="latency-metrics"' in page.text
    assert "data-latency-hud" in page.text


def test_websocket_set_settings_updates_model_and_effort() -> None:
    conv_id = client.post("/api/conversations").json()["id"]
    with client.websocket_connect(f"/ws/conversations/{conv_id}") as ws:
        ready = ws.receive_json()
        assert ready["type"] == "ready"
        assert "model" in ready
        assert "effort" in ready

        ws.send_json({
            "type": "set_settings",
            "model": "gpt-5.6-luna",
            "effort": "xhigh",
            "voice": "Milena",
        })
        reply = ws.receive_json()
        assert reply["type"] == "settings_updated"
        assert reply["model"] == "gpt-5.6-luna"
        assert reply["effort"] == "xhigh"
        assert reply["voice"] == "Milena"

        conv = main_module.conversations.get(conv_id)
        assert conv is not None
        assert conv.model_name == "gpt-5.6-luna"
        assert conv.reasoning_effort == "xhigh"
        assert conv.voice == "Milena"


def test_htmx_shell_renders_vad_toggle() -> None:
    page = client.get("/")
    assert page.status_code == 200
    assert 'id="vad-toggle"' in page.text
    assert "vad-toggle-chip" in page.text
    assert "VAD:" in page.text


def test_favicon_endpoint_and_markup() -> None:
    page = client.get("/")
    assert page.status_code == 200
    assert '<link rel="icon" type="image/svg+xml" href="/static/favicon.svg"' in page.text
    assert '<link rel="alternate icon" type="image/x-icon" href="/static/favicon.ico"' in page.text
    assert '<link rel="apple-touch-icon"' in page.text

    res_ico = client.get("/favicon.ico")
    assert res_ico.status_code == 200
    assert res_ico.headers["content-type"] in ["image/x-icon", "image/vnd.microsoft.icon"]

    res_svg = client.get("/static/favicon.svg")
    assert res_svg.status_code == 200
    assert "image/svg+xml" in res_svg.headers["content-type"]
