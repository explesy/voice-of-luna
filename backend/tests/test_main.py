import re

from fastapi.testclient import TestClient

from app import main as main_module
from app.main import app


client = TestClient(app)


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
    clip = tmp_path / "milena.m4a"

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
    assert audio.headers["content-type"].startswith("audio/mp4")
    assert audio.content == b"local speech"
    assert not clip.exists()
