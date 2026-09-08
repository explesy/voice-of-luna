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
    assert "silero_v4_ru" in MODEL_CATALOG
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
    assert "Dmitri (Piper Neural · Offline)" in names
    assert "Irina (Piper Neural · Offline)" in names

    piper_voices = [v for v in voices if v.engine == "piper"]
    assert len(piper_voices) == 2
    assert all(v.is_russian for v in piper_voices)

    dmitri_voice = next(v for v in piper_voices if "Dmitri" in v.name)
    assert dmitri_voice.model_id == "piper_ru_dmitri"


def test_is_piper_voice_detection() -> None:
    assert is_piper_voice("Dmitri (Piper Neural · Offline)") is True
    assert is_piper_voice("Irina (Piper Neural · Offline)") is True
    assert is_piper_voice("piper-voice-custom") is True
    assert is_piper_voice("Milena") is False
    assert is_piper_voice("Svetlana (Neural · Edge)") is False
    assert is_piper_voice("Ksenia (Silero Neural · Offline)") is False


def test_resolve_piper_model() -> None:
    assert resolve_piper_model("Dmitri (Piper Neural · Offline)") == "ru_RU-dmitri-medium"
    assert resolve_piper_model("Irina (Piper Neural · Offline)") == "ru_RU-irina-medium"
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
    assert "silero_v4_ru" in model_ids


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
    monkeypatch.setattr("app.speak._get_piper_voice", lambda k: prewarmed.append(f"piper:{k}"))
    monkeypatch.setattr("app.speak._get_silero_model", lambda: prewarmed.append("silero"))

    await prewarm_voice("Dmitri (Piper Neural · Offline)")
    assert "piper:ru_RU-dmitri-medium" in prewarmed

    await prewarm_voice("Eugene (Silero Neural · Offline)")
    assert "silero" in prewarmed
