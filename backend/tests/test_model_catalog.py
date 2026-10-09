"""Catalog status, capability metadata and user-defined model tests (issue #14).

All tests are offline: user models reference local temp files (or are validated
without any download), and no model is fetched from the network.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import model_catalog
from app.conversation_service import Conversation
from app.main import app
from app.speak import get_default_voice, get_default_voice_for_locale, get_installed_voices, resolve_piper_model
from app.stt_manager import (
    get_batch_stt_model,
    is_batch_stt_model,
    list_batch_stt_models,
    resolve_batch_stt_model_id,
)
from app.tts_manager import MODEL_CATALOG, tts_model_manager
from app.whisper_server import resolve_whisper_model_path


client = TestClient(app)


@pytest.fixture
def clean_catalog(monkeypatch):
    """Isolate global catalog/voice mutation performed by user-model tests."""

    import app.speak as speak

    before_models = dict(MODEL_CATALOG)
    yield
    MODEL_CATALOG.clear()
    MODEL_CATALOG.update(before_models)
    tts_model_manager._user_model_errors = []
    speak._cached_installed_voices = None
    monkeypatch.delenv("VOICE_OF_LUNA_MODELS_CONFIG", raising=False)


def _write_config(tmp_path: Path, payload: dict) -> Path:
    config = tmp_path / "catalog.json"
    config.write_text(json.dumps(payload), encoding="utf-8")
    return config


def _piper_entry(model_id: str = "piper_de_voice", stem: str = "de_DE-voice-medium") -> dict:
    return {
        "id": model_id,
        "kind": "tts",
        "engine": "piper",
        "name": "Piper German Voice",
        "locale": "de_DE",
        "size_mb": 65,
        "catalog_status": "recommended",
        "voices": [
            {
                "name": "Thorsten (Piper Neural · Offline)",
                "languages": ["de"],
                "multilingual": False,
                "code_switching": "segment_only",
            }
        ],
        "assets": [
            {"filename": f"{stem}.onnx"},
            {"filename": f"{stem}.onnx.json"},
        ],
    }


def _whisper_entry(model_id: str = "whisper_custom", filename: str = "ggml-custom.bin") -> dict:
    return {
        "id": model_id,
        "kind": "stt",
        "engine": "whisper",
        "name": "Whisper Custom German",
        "locale": "multi",
        "languages": ["de"],
        "size_mb": 400,
        "catalog_status": "recommended",
        "assets": [{"filename": filename}],
    }


def test_builtin_catalog_exposes_status_and_capabilities() -> None:
    assert MODEL_CATALOG["silero_v4_ru"].catalog_status == "deprecated"
    assert "v5" in MODEL_CATALOG["silero_v4_ru"].deprecation_reason
    assert MODEL_CATALOG["silero_v5_ru"].catalog_status == "recommended"
    assert MODEL_CATALOG["whisper_small"].catalog_status == "legacy"
    assert MODEL_CATALOG["whisper_large_v3_turbo"].kind == "stt"

    status = tts_model_manager.get_status("piper_ru_dmitri")
    assert status["catalog_status"] == "recommended"
    voice_caps = status["voice_capabilities"]
    assert voice_caps and voice_caps[0]["languages"] == ["ru"]
    assert voice_caps[0]["code_switching"] == "segment_only"
    assert voice_caps[0]["multilingual"] is False


def test_voice_capability_falls_back_conservatively() -> None:
    capability = model_catalog.voice_capability("Unknown Custom Voice", "de_DE")
    assert capability.languages == ("de",)
    assert capability.multilingual is False
    assert capability.code_switching == model_catalog.CODE_SWITCHING_SEGMENT_ONLY
    # Sentinel locales carry no single language.
    assert model_catalog.languages_for_locale("multi") == ()
    assert model_catalog.languages_for_locale("system") == ()


def test_deprecated_voices_excluded_from_defaults() -> None:
    assert MODEL_CATALOG["silero_v4_ru"].catalog_status == "deprecated"
    get_installed_voices(force_refresh=True)
    default_ru = get_default_voice()
    assert default_ru not in {
        "Ksenia (Silero Neural · Offline)",
        "Baya (Silero Neural · Offline)",
        "Eugene (Silero Neural · Offline)",
    }
    assert get_default_voice_for_locale("ru-RU") != "Ksenia (Silero Neural · Offline)"


def test_deprecated_status_requires_reason(tmp_path: Path, clean_catalog) -> None:
    load = model_catalog.load_user_model_config(
        _write_config(tmp_path, {"schema_version": 1, "models": [
            {
                "id": "bad_deprecated",
                "kind": "tts",
                "engine": "piper",
                "name": "Bad",
                "locale": "de_DE",
                "catalog_status": "deprecated",
                "voices": [{"name": "Bad", "languages": ["de"]}],
                "assets": [{"filename": "bad.onnx"}, {"filename": "bad.onnx.json"}],
            }
        ]})
    )
    assert load.models == []
    assert any("deprecation_reason" in error for error in load.errors)


def test_load_user_catalog_accepts_local_piper_and_whisper(tmp_path: Path, clean_catalog) -> None:
    config = _write_config(
        tmp_path,
        {"schema_version": 1, "models": [_piper_entry(), _whisper_entry()]},
    )
    load = model_catalog.load_user_model_config(config, reserved_ids=set(MODEL_CATALOG))
    assert load.errors == []
    assert {spec.id for spec in load.models} == {"piper_de_voice", "whisper_custom"}
    piper = next(spec for spec in load.models if spec.kind == "tts")
    assert piper.model_key == "de_DE-voice-medium"
    assert piper.voices[0].languages == ("de",)


def test_load_user_catalog_rejects_duplicate_ids(tmp_path: Path, clean_catalog) -> None:
    config = _write_config(
        tmp_path,
        {"schema_version": 1, "models": [_piper_entry("dup_id"), _whisper_entry("dup_id", "ggml-dup.bin")]},
    )
    load = model_catalog.load_user_model_config(config, reserved_ids=set(MODEL_CATALOG))
    assert [spec.id for spec in load.models] == ["dup_id"]
    assert any("already exists" in error for error in load.errors)


def test_load_user_catalog_rejects_builtin_collision(tmp_path: Path, clean_catalog) -> None:
    config = _write_config(tmp_path, {"schema_version": 1, "models": [_piper_entry("piper_ru_dmitri")]})
    load = model_catalog.load_user_model_config(config, reserved_ids=set(MODEL_CATALOG))
    assert load.models == []
    assert any("already exists" in error for error in load.errors)


def test_load_user_catalog_rejects_unsafe_filenames(tmp_path: Path, clean_catalog) -> None:
    entry = _piper_entry("unsafe", "safe-stem")
    entry["assets"] = [{"filename": "../escape.onnx"}, {"filename": "safe-stem.onnx.json"}]
    load = model_catalog.load_user_model_config(
        _write_config(tmp_path, {"schema_version": 1, "models": [entry]}),
        reserved_ids=set(MODEL_CATALOG),
    )
    assert load.models == []
    assert any("unsafe filename" in error for error in load.errors)


def test_load_user_catalog_requires_https_and_sha256(tmp_path: Path, clean_catalog) -> None:
    entry = _piper_entry("no_sha", "net-stem")
    entry["assets"] = [
        {"filename": "net-stem.onnx", "url": "http://example.com/a.onnx"},
        {"filename": "net-stem.onnx.json", "url": "https://example.com/a.onnx.json", "sha256": "abc"},
    ]
    load = model_catalog.load_user_model_config(
        _write_config(tmp_path, {"schema_version": 1, "models": [entry]}),
        reserved_ids=set(MODEL_CATALOG),
    )
    assert load.models == []
    assert any("https" in error for error in load.errors)
    assert any("sha256" in error for error in load.errors)


def test_load_user_catalog_rejects_unknown_engine_and_bad_capabilities(tmp_path: Path, clean_catalog) -> None:
    wrong_engine = _piper_entry("wrong_engine")
    wrong_engine["engine"] = "edge"
    bad_caps = _whisper_entry("bad_caps", "ggml-badcaps.bin")
    bad_caps["multilingual"] = False
    bad_caps["code_switching"] = "native"
    bad_multiple = _piper_entry("bad_multiple", "multi-stem")
    bad_multiple["voices"][0]["languages"] = ["de", "en"]

    load = model_catalog.load_user_model_config(
        _write_config(tmp_path, {"schema_version": 1, "models": [wrong_engine, bad_caps, bad_multiple]}),
        reserved_ids=set(MODEL_CATALOG),
    )
    assert load.models == []
    assert any("only tts/piper and stt/whisper" in error for error in load.errors)
    assert any("native" in error for error in load.errors)


def test_load_user_catalog_rejects_mismatched_piper_pair(tmp_path: Path, clean_catalog) -> None:
    entry = _piper_entry("mismatch", "one-stem")
    entry["assets"] = [{"filename": "one-stem.onnx"}, {"filename": "other-stem.onnx.json"}]
    load = model_catalog.load_user_model_config(
        _write_config(tmp_path, {"schema_version": 1, "models": [entry]}),
        reserved_ids=set(MODEL_CATALOG),
    )
    assert load.models == []
    assert any("matching" in error for error in load.errors)


def test_user_piper_model_registers_and_is_selectable(tmp_path: Path, monkeypatch, clean_catalog) -> None:
    monkeypatch.setenv("VOICE_OF_LUNA_MODELS_DIR", str(tmp_path))
    stem = "de_DE-voice-medium"
    (tmp_path / f"{stem}.onnx").write_bytes(b"onnx-bytes")
    (tmp_path / f"{stem}.onnx.json").write_text("{}", encoding="utf-8")
    config = _write_config(tmp_path, {"schema_version": 1, "models": [_piper_entry(stem=stem)]})
    monkeypatch.setenv("VOICE_OF_LUNA_MODELS_CONFIG", str(config))

    errors = tts_model_manager.register_user_models()
    assert errors == []
    assert MODEL_CATALOG["piper_de_voice"].user_defined is True

    import app.speak as speak

    speak._cached_installed_voices = None
    voices = get_installed_voices(force_refresh=True)
    voice = next(v for v in voices if v.name == "Thorsten (Piper Neural · Offline)")
    assert voice.model_id == "piper_de_voice"
    assert voice.languages == ("de",)
    assert voice.is_downloaded is True
    assert resolve_piper_model(voice.name) == stem


def test_user_whisper_model_resolves_path_and_is_batch(tmp_path: Path, monkeypatch, clean_catalog) -> None:
    monkeypatch.setenv("VOICE_OF_LUNA_MODELS_DIR", str(tmp_path))
    (tmp_path / "ggml-custom.bin").write_bytes(b"ggml-bytes")
    config = _write_config(tmp_path, {"schema_version": 1, "models": [_whisper_entry()]})
    monkeypatch.setenv("VOICE_OF_LUNA_MODELS_CONFIG", str(config))

    tts_model_manager.register_user_models()
    assert is_batch_stt_model("whisper_custom") is True
    resolved = resolve_whisper_model_path("whisper_custom")
    assert resolved == tmp_path / "ggml-custom.bin"

    model_id, reason = resolve_batch_stt_model_id("whisper_custom")
    assert model_id == "whisper_custom"
    assert reason is None
    assert get_batch_stt_model("whisper_custom").kind == "stt"


def test_user_model_registration_is_idempotent(tmp_path: Path, monkeypatch, clean_catalog) -> None:
    monkeypatch.setenv("VOICE_OF_LUNA_MODELS_DIR", str(tmp_path))
    (tmp_path / "de_DE-voice-medium.onnx").write_bytes(b"x")
    (tmp_path / "de_DE-voice-medium.onnx.json").write_text("{}", encoding="utf-8")
    config = _write_config(tmp_path, {"schema_version": 1, "models": [_piper_entry()]})
    monkeypatch.setenv("VOICE_OF_LUNA_MODELS_CONFIG", str(config))
    tts_model_manager.register_user_models()
    tts_model_manager.register_user_models()
    assert sum(1 for model in MODEL_CATALOG.values() if model.user_defined) == 1


def test_local_only_user_model_reports_manual_install(tmp_path: Path, monkeypatch, clean_catalog) -> None:
    monkeypatch.setenv("VOICE_OF_LUNA_MODELS_DIR", str(tmp_path))
    config = _write_config(tmp_path, {"schema_version": 1, "models": [_piper_entry()]})
    monkeypatch.setenv("VOICE_OF_LUNA_MODELS_CONFIG", str(config))
    tts_model_manager.register_user_models()

    status = tts_model_manager.get_status("piper_de_voice")
    assert status["installed"] is False
    assert status["downloadable"] is False
    assert "install_hint" in status
    assert any("manually" in error for error in tts_model_manager.user_model_errors) is False


@pytest.mark.anyio
async def test_local_only_download_is_refused(clean_catalog) -> None:
    entry = _piper_entry("local_only", "local-stem")
    MODEL_CATALOG["local_only"] = type(MODEL_CATALOG["piper_ru_dmitri"])(
        id="local_only",
        name="Local Only",
        engine="piper",
        locale="de_DE",
        description="",
        size_mb=1.0,
        voices=["Local Only"],
        files=[],
        user_defined=True,
    )
    with pytest.raises(ValueError):
        await tts_model_manager.download_model("local_only")


def test_api_tts_models_kind_filter() -> None:
    all_ids = {m["id"] for m in client.get("/api/tts/models").json()["models"]}
    assert "whisper_small" in all_ids
    tts_ids = {m["id"] for m in client.get("/api/tts/models?kind=tts").json()["models"]}
    assert "whisper_small" not in tts_ids
    assert "piper_ru_dmitri" in tts_ids
    assert client.get("/api/tts/models?kind=bogus").status_code == 400


def test_api_stt_models_lists_batch_and_streaming() -> None:
    payload = client.get("/api/stt/models").json()
    ids = {model["id"] for model in payload["models"]}
    assert "tone_ru" in ids
    assert "whisper_large_v3_turbo" in ids
    assert any(model.get("batch") for model in payload["batch"])
    assert payload["errors"] == []
    # Batch status is available without the sherpa runtime.
    status = client.get("/api/stt/models/whisper_large_v3_turbo/status")
    assert status.status_code == 200
    assert status.json()["batch"] is True


def test_api_stt_batch_download_does_not_require_sherpa(monkeypatch) -> None:
    from app import tts_manager

    async def fake_download(model_id: str):
        return None

    monkeypatch.setattr(tts_manager.tts_model_manager, "download_model", fake_download)
    monkeypatch.setattr("app.main.sherpa_available", lambda: False)
    response = client.post("/api/stt/models/whisper_large_v3_turbo/download")
    assert response.status_code == 200
    assert response.json()["status"] == "downloading"
    assert client.post("/api/stt/models/does-not-exist/download").status_code == 404


def test_api_models_catalog_groups_and_status_counts() -> None:
    payload = client.get("/api/models/catalog").json()
    kinds = {model["kind"] for model in payload["models"]}
    assert {"tts", "stt"}.issubset(kinds)
    assert payload["status_counts"]["deprecated"] >= 1
    assert payload["status_counts"]["recommended"] >= 1
    group_keys = {group["key"] for group in payload["groups"]}
    assert any(key.startswith("tts/piper/") for key in group_keys)
    assert "stt/whisper/multi" in group_keys


def test_api_voices_expose_capability_metadata() -> None:
    voices = client.get("/api/voices").json()["voices"]
    dmitri = next(v for v in voices if v["name"] == "Dmitri (Piper Neural · Offline)")
    assert dmitri["languages"] == ["ru"]
    assert dmitri["multilingual"] is False
    assert dmitri["code_switching"] == "segment_only"
    assert dmitri["catalog_status"] == "recommended"


def test_home_page_hides_deprecated_voices_behind_toggle() -> None:
    page = client.get("/")
    assert page.status_code == 200
    assert 'id="deprecated-voices-group"' in page.text
    assert 'id="multilingual-filter"' in page.text
    assert 'id="legacy-voices-toggle"' in page.text
    assert 'id="stt-model-select"' in page.text
    assert "[DEPRECATED" in page.text


def test_stt_selection_reflected_in_conversation_context(tmp_path: Path, monkeypatch, clean_catalog) -> None:
    # A registered but not-installed batch model reports a reason instead of
    # being silently swapped for a different one.
    monkeypatch.setenv("VOICE_OF_LUNA_MODELS_DIR", str(tmp_path))
    config = _write_config(
        tmp_path,
        {"schema_version": 1, "models": [_whisper_entry("whisper_missing", "ggml-missing.bin")]},
    )
    monkeypatch.setenv("VOICE_OF_LUNA_MODELS_CONFIG", str(config))
    tts_model_manager.register_user_models()

    conversation = Conversation(id="stt-context")
    conversation.stt_model = "whisper_missing"
    resolved, reason = resolve_batch_stt_model_id(conversation.stt_model)
    assert resolved != "whisper_missing"
    assert reason and "whisper_missing" in reason

    conversation.stt_model = "not-a-model"
    resolved, reason = resolve_batch_stt_model_id(conversation.stt_model)
    assert resolved is None
    assert reason and "not-a-model" in reason


def test_batch_model_list_prefers_recommended_default() -> None:
    ids = [model["id"] for model in list_batch_stt_models()]
    assert ids[0] == "whisper_large_v3_turbo"
    assert "whisper_small" in ids


def test_streaming_resolution_reports_mismatch_instead_of_swapping() -> None:
    from app.stt_manager import stt_model_manager

    # A caller-requested model that cannot be used yields None (no silent swap).
    assert stt_model_manager.resolve_model_id("ru-RU", installed_only=True, preferred_id="vosk_ru") is None
    assert stt_model_manager.resolve_model_id("ru-RU", installed_only=True, preferred_id="tone_ru") is None
    # An unknown preferred id is not substituted either.
    assert stt_model_manager.resolve_model_id("ru-RU", preferred_id="nope") is None


def test_streaming_models_expose_catalog_status_and_capability() -> None:
    from app.stt_manager import STREAMING_MODEL_CATALOG, stt_model_manager

    assert STREAMING_MODEL_CATALOG["vosk_ru"].catalog_status == "legacy"
    assert STREAMING_MODEL_CATALOG["tone_ru"].catalog_status == "recommended"
    assert STREAMING_MODEL_CATALOG["nemotron320"].multilingual is True
    status = stt_model_manager.get_status("tone_ru")
    assert status["kind"] == "stt"
    assert status["streaming"] is True
    assert status["catalog_status"] == "recommended"
    assert status["languages"] == ["ru"]


def test_codex_model_provenance_tags() -> None:
    from app.codex import FALLBACK_MODELS, _tag_models

    preset = _tag_models(FALLBACK_MODELS, "preset")
    assert all(entry["source"] == "preset" for entry in preset)
    assert all(entry["available"] is False for entry in preset)
    # The shared fallback constant is not mutated in place.
    assert all("source" not in entry for entry in FALLBACK_MODELS)

    live = _tag_models([{"id": "x", "supportedReasoningEfforts": []}], "live")
    assert live[0]["source"] == "live"
    assert live[0]["available"] is True


def test_websocket_stt_selection_is_reflected(tmp_path: Path, monkeypatch, clean_catalog) -> None:
    monkeypatch.setenv("VOICE_OF_LUNA_MODELS_DIR", str(tmp_path))
    config = _write_config(
        tmp_path,
        {"schema_version": 1, "models": [_whisper_entry("whisper_ws", "ggml-ws.bin")]},
    )
    monkeypatch.setenv("VOICE_OF_LUNA_MODELS_CONFIG", str(config))
    tts_model_manager.register_user_models()

    with client.websocket_connect("/ws/conversations/test-ws-stt") as ws:
        ready = ws.receive_json()
        assert ready["type"] == "ready"
        assert "stt_model" in ready
        ws.send_json({"type": "set_settings", "stt_model": "whisper_ws"})
        response = ws.receive_json()
        assert response["type"] == "settings_updated"
        assert response["stt_model"] == "whisper_ws"
        # Not installed locally: the mismatch is surfaced, not hidden.
        assert response["stt_model_resolved"] != "whisper_ws"
        assert response["stt_model_fallback"]


def test_websocket_rejects_unknown_stt_model() -> None:
    with client.websocket_connect("/ws/conversations/test-ws-stt-bad") as ws:
        ws.receive_json()  # ready
        ws.send_json({"type": "set_settings", "stt_model": "does-not-exist"})
        response = ws.receive_json()
        assert response["type"] == "error"
        assert "Unknown batch" in response["message"]


def test_api_settings_persists_stt_model_cookie() -> None:
    response = client.post("/api/settings", json={"stt_model": "whisper_large_v3_turbo"})
    assert response.status_code == 200
    assert "voice_of_luna_stt_model=whisper_large_v3_turbo" in response.headers.get("set-cookie", "")
    bad = client.post("/api/settings", json={"stt_model": "not-a-model"})
    assert bad.status_code == 400


def test_api_settings_auto_clears_stt_cookie() -> None:
    response = client.post("/api/settings", json={"stt_model": None})
    assert response.status_code == 200
    set_cookie = response.headers.get("set-cookie", "")
    assert "voice_of_luna_stt_model=" in set_cookie
    assert "Max-Age=0" in set_cookie or 'voice_of_luna_stt_model=""' in set_cookie


def test_explicit_uninstalled_stt_selection_fails_instead_of_falling_back(
    tmp_path: Path, monkeypatch, clean_catalog
) -> None:
    from app import main as main_module
    from app.transcribe import LocalTranscriptionError

    monkeypatch.setenv("VOICE_OF_LUNA_MODELS_DIR", str(tmp_path))
    config = _write_config(
        tmp_path,
        {"schema_version": 1, "models": [_whisper_entry("whisper_gone", "ggml-gone.bin")]},
    )
    monkeypatch.setenv("VOICE_OF_LUNA_MODELS_CONFIG", str(config))
    tts_model_manager.register_user_models()

    conversation = Conversation(id="stt-required")
    conversation.stt_model = "whisper_gone"
    with pytest.raises(LocalTranscriptionError):
        main_module._require_batch_stt_model(conversation)

    # Automatic selection is still allowed to resolve to nothing.
    conversation.stt_model = None
    assert main_module._require_batch_stt_model(conversation) != "whisper_gone"


def test_user_multilingual_capability_is_preserved(tmp_path: Path, monkeypatch, clean_catalog) -> None:
    import app.speak as speak

    monkeypatch.setenv("VOICE_OF_LUNA_MODELS_DIR", str(tmp_path))
    entry = _piper_entry("piper_multi", "multi-voice")
    entry["voices"] = [
        {
            "name": "Polyglot (Piper Neural · Offline)",
            "languages": ["de", "en"],
            "multilingual": True,
            "code_switching": "native",
        }
    ]
    config = _write_config(tmp_path, {"schema_version": 1, "models": [entry]})
    monkeypatch.setenv("VOICE_OF_LUNA_MODELS_CONFIG", str(config))
    tts_model_manager.register_user_models()

    status = tts_model_manager.get_status("piper_multi")
    capability = status["voice_capabilities"][0]
    assert capability["languages"] == ["de", "en"]
    assert capability["multilingual"] is True
    assert capability["code_switching"] == "native"
    assert status["multilingual"] is True

    speak._cached_installed_voices = None
    voice = next(v for v in get_installed_voices(force_refresh=True) if v.name == "Polyglot (Piper Neural · Offline)")
    assert voice.languages == ("de", "en")
    assert voice.multilingual is True
    assert voice.code_switching == "native"


def test_malformed_user_config_does_not_raise(tmp_path: Path, clean_catalog) -> None:
    bad_kind = _piper_entry("bad_kind")
    bad_kind["kind"] = []
    bad_languages = _whisper_entry("bad_languages", "ggml-badlang.bin")
    bad_languages["languages"] = 42
    string_languages = _whisper_entry("string_languages", "ggml-strlang.bin")
    string_languages["languages"] = "de"

    load = model_catalog.load_user_model_config(
        _write_config(tmp_path, {"schema_version": 1, "models": [bad_kind, bad_languages, string_languages]}),
        reserved_ids=set(MODEL_CATALOG),
    )
    assert load.models == []
    assert any("only tts/piper" in error for error in load.errors)
    assert any("languages must be a list of strings" in error for error in load.errors)


def test_user_id_collision_is_case_insensitive(tmp_path: Path, clean_catalog) -> None:
    first = _piper_entry("AlphaVoice", "alpha-stem")
    second = _piper_entry("alphavoice", "beta-stem")
    second["assets"] = [{"filename": "beta-stem.onnx"}, {"filename": "beta-stem.onnx.json"}]
    load = model_catalog.load_user_model_config(
        _write_config(tmp_path, {"schema_version": 1, "models": [first, second]}),
        reserved_ids=set(MODEL_CATALOG),
    )
    assert [spec.id for spec in load.models] == ["AlphaVoice"]
    assert any("already exists" in error for error in load.errors)


def test_user_model_cannot_shadow_streaming_id(tmp_path: Path, monkeypatch, clean_catalog) -> None:
    monkeypatch.setenv("VOICE_OF_LUNA_MODELS_DIR", str(tmp_path))
    entry = _whisper_entry("tone_ru", "ggml-tone.bin")
    config = _write_config(tmp_path, {"schema_version": 1, "models": [entry]})
    monkeypatch.setenv("VOICE_OF_LUNA_MODELS_CONFIG", str(config))
    errors = tts_model_manager.register_user_models()
    assert any("already exists" in error for error in errors)
    assert "tone_ru" not in MODEL_CATALOG


def test_deprecated_user_voice_keeps_status_and_reason(tmp_path: Path, monkeypatch, clean_catalog) -> None:
    import re as _re

    import app.speak as speak

    monkeypatch.setenv("VOICE_OF_LUNA_MODELS_DIR", str(tmp_path))
    entry = _piper_entry("piper_old", "old-stem")
    entry["locale"] = "fr_FR"
    entry["catalog_status"] = "deprecated"
    entry["deprecation_reason"] = "Superseded by a newer voice"
    entry["voices"] = [
        {
            "name": "Old French (Piper Neural · Offline)",
            "languages": ["fr"],
            "multilingual": False,
            "code_switching": "segment_only",
        }
    ]
    config = _write_config(tmp_path, {"schema_version": 1, "models": [entry]})
    monkeypatch.setenv("VOICE_OF_LUNA_MODELS_CONFIG", str(config))
    tts_model_manager.register_user_models()

    status = tts_model_manager.get_status("piper_old")
    assert status["catalog_status"] == "deprecated"
    capability = status["voice_capabilities"][0]
    assert capability["catalog_status"] == "deprecated"
    assert capability["deprecation_reason"] == "Superseded by a newer voice"

    speak._cached_installed_voices = None
    voice = next(v for v in get_installed_voices(force_refresh=True) if v.name == "Old French (Piper Neural · Offline)")
    assert voice.catalog_status == "deprecated"
    assert voice.deprecation_reason == "Superseded by a newer voice"

    page = client.get("/")
    deprecated_block = _re.search(r'id="deprecated-voices-group".*?</optgroup>', page.text, _re.S)
    assert deprecated_block and "Old French (Piper Neural · Offline)" in deprecated_block.group(0)
    other_block = _re.search(r'id="other-voices-template".*?</template>', page.text, _re.S)
    if other_block:
        assert "Old French (Piper Neural · Offline)" not in other_block.group(0)


def test_rejected_entry_does_not_reserve_assets(tmp_path: Path, clean_catalog) -> None:
    # The first entry is invalid (deprecated without a reason) and must not
    # reserve its filenames/voices against the following valid entry.
    invalid = _piper_entry("invalid_deprecated", "shared-stem")
    invalid["catalog_status"] = "deprecated"
    invalid["voices"][0]["name"] = "Shared Voice (Piper Neural · Offline)"
    valid = _piper_entry("valid_after_invalid", "shared-stem")
    valid["voices"][0]["name"] = "Shared Voice (Piper Neural · Offline)"

    load = model_catalog.load_user_model_config(
        _write_config(tmp_path, {"schema_version": 1, "models": [invalid, valid]}),
        reserved_ids=set(MODEL_CATALOG),
    )
    assert [spec.id for spec in load.models] == ["valid_after_invalid"]
    assert any("deprecation_reason" in error for error in load.errors)
