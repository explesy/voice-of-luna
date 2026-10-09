from pathlib import Path
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.speak import (
    get_installed_voices,
    is_piper_voice,
    is_silero_voice,
    resolve_piper_model,
    LocalMacOsSpeaker,
    prewarm_voice,
)
from app.tts_manager import (
    tts_model_manager,
    TTSModelDefinition,
    MODEL_CATALOG,
)


client = TestClient(app)


def test_model_catalog_contains_expected_models() -> None:
    assert "piper_ru_dmitri" in MODEL_CATALOG
    assert "piper_ru_irina" in MODEL_CATALOG
    assert "piper_ru_denis" in MODEL_CATALOG
    assert "piper_ru_ruslan" in MODEL_CATALOG
    assert "silero_v4_ru" in MODEL_CATALOG
    assert "silero_v5_ru" in MODEL_CATALOG
    assert "whisper_large_v3_turbo" in MODEL_CATALOG
    assert "whisper_small" in MODEL_CATALOG
    assert "edge_tts_cloud" in MODEL_CATALOG
    assert "macos_system" in MODEL_CATALOG

    dmitri = MODEL_CATALOG["piper_ru_dmitri"]
    assert dmitri.engine == "piper"
    assert "Dmitri (Piper Neural · Offline)" in dmitri.voices
    assert dmitri.size_mb == 60.0

    silero = MODEL_CATALOG["silero_v4_ru"]
    assert silero.engine == "silero"
    assert "Eugene (Silero Neural · Offline)" in silero.voices


def test_get_installed_voices_contains_piper_and_eugene() -> None:
    voices = get_installed_voices(force_refresh=True)
    names = [v.name for v in voices]

    assert "Eugene (Silero Neural · Offline)" in names
    assert "Raya (Silero Neural · Offline)" in names
    assert "Ksenia v5 (Silero Neural · Offline)" in names
    assert "Dmitri (Piper Neural · Offline)" in names
    assert "Irina (Piper Neural · Offline)" in names
    assert "Denis (Piper Neural · Offline)" in names
    assert "Ruslan (Piper Neural · Offline)" in names
    assert "Lessac (Piper Neural · Offline)" in names

    piper_voices = [v for v in voices if v.engine == "piper"]
    assert len(piper_voices) == 5
    ru_piper = [v for v in piper_voices if v.is_russian]
    assert len(ru_piper) == 4

    dmitri_voice = next(v for v in piper_voices if "Dmitri" in v.name)
    assert dmitri_voice.model_id == "piper_ru_dmitri"


def test_is_piper_voice_detection() -> None:
    assert is_piper_voice("Dmitri (Piper Neural · Offline)") is True
    assert is_piper_voice("Irina (Piper Neural · Offline)") is True
    assert is_piper_voice("Denis (Piper Neural · Offline)") is True
    assert is_piper_voice("Ruslan (Piper Neural · Offline)") is True
    assert is_piper_voice("piper-voice-custom") is True
    assert is_piper_voice("Milena") is False
    assert is_piper_voice("Svetlana (Neural · Edge)") is False
    assert is_piper_voice("Ksenia (Silero Neural · Offline)") is False


def test_resolve_piper_model() -> None:
    assert resolve_piper_model("Dmitri (Piper Neural · Offline)") == "ru_RU-dmitri-medium"
    assert resolve_piper_model("Irina (Piper Neural · Offline)") == "ru_RU-irina-medium"
    assert resolve_piper_model("Denis (Piper Neural · Offline)") == "ru_RU-denis-medium"
    assert resolve_piper_model("Ruslan (Piper Neural · Offline)") == "ru_RU-ruslan-medium"
    assert resolve_piper_model("unknown_irina_voice") == "ru_RU-irina-medium"
    assert resolve_piper_model("unknown_other") == "ru_RU-dmitri-medium"


def test_api_list_tts_models() -> None:
    resp = client.get("/api/tts/models")
    assert resp.status_code == 200
    data = resp.json()
    assert "models" in data
    model_ids = [m["id"] for m in data["models"]]
    assert "piper_ru_dmitri" in model_ids
    assert "piper_ru_irina" in model_ids
    assert "piper_ru_denis" in model_ids
    assert "piper_ru_ruslan" in model_ids
    assert "silero_v4_ru" in model_ids
    assert "silero_v5_ru" in model_ids
    assert "whisper_large_v3_turbo" in model_ids
    assert "whisper_small" in model_ids


def test_api_get_tts_model_status() -> None:
    resp = client.get("/api/tts/models/piper_ru_dmitri/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == "piper_ru_dmitri"
    assert data["engine"] == "piper"
    assert "installed" in data
    assert "status" in data
    assert "downloaded_mb" in data
    assert "total_mb" in data
    assert "speed_kbps" in data
    assert "eta_seconds" in data

    bad_resp = client.get("/api/tts/models/nonexistent_model/status")
    assert bad_resp.status_code == 404


def test_api_download_tts_model_endpoint(monkeypatch) -> None:
    downloaded = []

    async def fake_download(model_id: str):
        downloaded.append(model_id)

    monkeypatch.setattr(tts_model_manager, "download_model", fake_download)

    resp = client.post("/api/tts/models/piper_ru_irina/download")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["model_id"] == "piper_ru_irina"
    assert data["status"] == "downloading"


def test_api_voice_auto_download(monkeypatch) -> None:
    monkeypatch.setattr(tts_model_manager, "is_installed", lambda mid: False)

    downloaded = []

    async def fake_download(model_id: str):
        downloaded.append(model_id)

    monkeypatch.setattr(tts_model_manager, "download_model", fake_download)

    resp = client.post(
        "/api/voice",
        json={"voice": "Irina (Piper Neural · Offline)"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["active_voice"] == "Irina (Piper Neural · Offline)"
    assert data["auto_downloading"] is True
    assert data["model_id"] == "piper_ru_irina"


@pytest.mark.anyio
async def test_piper_synthesizer_mock(monkeypatch, tmp_path) -> None:
    fake_wav = tmp_path / "fake_piper.wav"
    fake_wav.write_bytes(b"RIFFpiper_audio_data")

    async def fake_piper(self, text, voice):
        return fake_wav

    monkeypatch.setattr(LocalMacOsSpeaker, "_synthesize_piper", fake_piper)

    speaker = LocalMacOsSpeaker(voice="Dmitri (Piper Neural · Offline)")
    result = await speaker.synthesize("Привет, это проверка синтезатора Piper!")
    assert result == fake_wav
    assert result.read_bytes() == b"RIFFpiper_audio_data"


@pytest.mark.anyio
async def test_piper_synthesizer_fallback_on_error(monkeypatch, tmp_path) -> None:
    async def failing_piper(self, text, voice):
        raise RuntimeError("Piper engine failure")

    monkeypatch.setattr(LocalMacOsSpeaker, "_synthesize_piper", failing_piper)
    monkeypatch.setattr(LocalMacOsSpeaker, "_fallback_candidates", lambda *args: ["Milena"])

    fallback_wav = tmp_path / "fallback.wav"
    fallback_wav.write_bytes(b"RIFFfallback_from_piper")

    async def fake_macos(self, text, voice):
        return fallback_wav

    monkeypatch.setattr(LocalMacOsSpeaker, "_synthesize_macos", fake_macos)

    speaker = LocalMacOsSpeaker(voice="Dmitri (Piper Neural · Offline)")
    result = await speaker.synthesize("Текст для синтеза.")
    assert result == fallback_wav


@pytest.mark.anyio
async def test_synthesis_metadata_reports_fallback_engine(monkeypatch, tmp_path) -> None:
    async def failing_piper(self, text, voice):
        raise RuntimeError("Piper engine failure")

    fallback_wav = tmp_path / "fallback-metadata.wav"
    fallback_wav.write_bytes(b"RIFFfallback")

    async def fake_macos(self, text, voice):
        return fallback_wav

    monkeypatch.setattr(LocalMacOsSpeaker, "_synthesize_piper", failing_piper)
    monkeypatch.setattr(LocalMacOsSpeaker, "_synthesize_macos", fake_macos)
    monkeypatch.setattr(LocalMacOsSpeaker, "_fallback_candidates", lambda *args: ["Milena"])

    result = await LocalMacOsSpeaker(
        voice="Dmitri (Piper Neural · Offline)"
    ).synthesize_with_metadata("Текст для синтеза.")

    assert result is not None
    assert result.requested_engine == "piper"
    assert result.actual_engine == "macos"
    assert "Piper engine failure" in (result.fallback_reason or "")


@pytest.mark.anyio
async def test_prewarm_voice_handles_piper_and_silero(monkeypatch) -> None:
    prewarmed = []

    monkeypatch.setattr("app.tts_manager.find_model_file", lambda f: Path(f))
    monkeypatch.setattr("app.tts_manager.TTSModelManager.is_ready", lambda self, model_id: True)
    monkeypatch.setattr("app.speak._get_piper_voice", lambda k: prewarmed.append(f"piper:{k}"))
    monkeypatch.setattr("app.speak._get_silero_model", lambda *args, **kwargs: prewarmed.append("silero"))

    await prewarm_voice("Dmitri (Piper Neural · Offline)")
    assert "piper:ru_RU-dmitri-medium" in prewarmed

    await prewarm_voice("Eugene (Silero Neural · Offline)")
    assert "silero" in prewarmed


def test_compute_file_sha256(tmp_path: Path) -> None:
    from app.tts_manager import compute_file_sha256
    test_file = tmp_path / "sample.bin"
    test_file.write_bytes(b"Voice of Luna Test Bytes")
    import hashlib
    expected_hash = hashlib.sha256(b"Voice of Luna Test Bytes").hexdigest()
    assert compute_file_sha256(test_file) == expected_hash


def test_verify_checksums_logic(monkeypatch, tmp_path: Path) -> None:
    from app.tts_manager import MODEL_CATALOG, ModelFileSpec, TTSModelDefinition, compute_file_sha256, tts_model_manager
    test_file = tmp_path / "test-model.onnx"
    test_file.write_bytes(b"valid-model-content")
    valid_sha = compute_file_sha256(test_file)

    test_model = TTSModelDefinition(
        id="test_model_sha",
        name="Test Model SHA",
        engine="piper",
        locale="en_US",
        description="Test",
        size_mb=1.0,
        voices=["TestVoice"],
        files=[ModelFileSpec(filename="test-model.onnx", url="http://example.com", sha256=valid_sha)],
    )
    monkeypatch.setitem(MODEL_CATALOG, "test_model_sha", test_model)
    monkeypatch.setattr("app.tts_manager.find_model_file", lambda f: test_file if f == "test-model.onnx" else None)

    assert tts_model_manager.verify_checksums("test_model_sha") is True
    assert tts_model_manager.is_ready("test_model_sha") is True

    # Tamper with file
    test_file.write_bytes(b"corrupted-tampered-content")
    assert tts_model_manager.verify_checksums("test_model_sha") is False
    assert tts_model_manager.is_ready("test_model_sha") is False


def test_resolve_whisper_model_path_presets(monkeypatch, tmp_path: Path) -> None:
    from app.whisper_server import resolve_whisper_model_path

    turbo_mock = tmp_path / "ggml-large-v3-turbo-q5_0.bin"
    turbo_mock.write_bytes(b"mock-turbo")

    monkeypatch.setenv("VOICE_OF_LUNA_WHISPER_MODEL", str(turbo_mock))
    resolved = resolve_whisper_model_path()
    assert resolved == turbo_mock

    # Test preset keyword 'turbo'
    fake_models_dir = tmp_path / "models"
    fake_models_dir.mkdir()
    fake_turbo = fake_models_dir / "ggml-large-v3-turbo-q5_0.bin"
    fake_turbo.write_bytes(b"mock-turbo-file")

    monkeypatch.setattr("app.whisper_server.Path.home", lambda: tmp_path)
    # Put fake file in ~/.cache/voice-of-luna/models
    cache_dir = tmp_path / ".cache/voice-of-luna/models"
    cache_dir.mkdir(parents=True)
    (cache_dir / "ggml-large-v3-turbo-q5_0.bin").write_bytes(b"cached-turbo")

    monkeypatch.setenv("VOICE_OF_LUNA_WHISPER_MODEL", "turbo")
    resolved_preset = resolve_whisper_model_path()
    assert resolved_preset.name == "ggml-large-v3-turbo-q5_0.bin"


def test_model_download_ui_elements_present_in_html() -> None:
    """Verify that the explicit model download banner and badge are rendered in index.html."""
    response = client.get("/")
    assert response.status_code == 200
    html = response.text
    assert 'id="model-download-banner"' in html
    assert 'id="voice-download-badge"' in html
    assert 'id="download-actions"' in html
    assert 'id="download-retry-btn"' in html
    assert 'id="download-close-btn"' in html


def test_model_download_trigger_and_status() -> None:
    """Verify that /api/tts/models/{id}/download and /status respond correctly."""
    status_res = client.get("/api/tts/models/piper_ru_denis/status")
    assert status_res.status_code == 200
    data = status_res.json()
    assert data["id"] == "piper_ru_denis"
    assert "status" in data
    assert "progress_percent" in data

    # Trigger download with non-existent model returns 404
    missing_res = client.get("/api/tts/models/non_existent_model/status")
    assert missing_res.status_code == 404


