"""Unit tests for the streaming-STT model manager (issue #23)."""

import hashlib
import io
import tarfile
from pathlib import Path

import pytest

from app.stt_manager import (
    STREAMING_MODEL_CATALOG,
    StreamingSTTManager,
    StreamingSTTModel,
)


def test_locale_defaults_and_download_targets() -> None:
    manager = StreamingSTTManager()
    assert manager.default_model_id("ru-RU") == "tone_ru"
    assert manager.default_model_id("es-ES") == "kroko_es"
    assert manager.default_model_id("en-US") is None
    assert manager.download_target("ru-RU") == "tone_ru"
    assert manager.download_target("es-ES") == "kroko_es"
    # English requires the opt-in Nemotron, which is never auto-downloaded.
    assert manager.download_target("en-US") is None
    assert manager.download_target("de-DE") is None


def test_resolve_prefers_default_and_honours_installed_only(tmp_path: Path) -> None:
    manager = StreamingSTTManager(root=tmp_path)
    assert manager.resolve_model_id("ru-RU", installed_only=False) == "tone_ru"
    assert manager.resolve_model_id("ru-RU", installed_only=True) is None
    assert manager.resolve_model_id("de-DE", installed_only=False) is None


def test_extract_archive_installs_required_files(tmp_path: Path) -> None:
    manager = StreamingSTTManager(root=tmp_path)
    defn = STREAMING_MODEL_CATALOG["tone_ru"]
    assert manager.is_installed("tone_ru") is False

    archive = tmp_path / "tone.tar.bz2"
    with tarfile.open(archive, "w:bz2") as tar:
        for name in defn.required_files:
            payload = b"model-bytes"
            info = tarfile.TarInfo(f"{defn.directory}/{name}")
            info.size = len(payload)
            tar.addfile(info, io.BytesIO(payload))

    manager._extract_archive(archive, defn)
    assert manager.is_installed("tone_ru") is True
    assert manager.get_status("tone_ru")["status"] == "ready"


def test_download_archive_verifies_sha256(tmp_path: Path) -> None:
    manager = StreamingSTTManager(root=tmp_path)
    source = tmp_path / "payload.bin"
    source.write_bytes(b"streaming-bytes")
    good = hashlib.sha256(b"streaming-bytes").hexdigest()

    mismatched = StreamingSTTModel(
        id="broken",
        name="broken",
        engine="tone",
        languages=("ru",),
        description="",
        directory="broken",
        required_files=("tokens.txt",),
        archive_url=source.as_uri(),
        archive_sha256="0" * 64,
        archive_size_bytes=source.stat().st_size,
    )
    with pytest.raises(ValueError):
        manager._download_archive(mismatched, tmp_path / "out.bin")
    assert not (tmp_path / "out.bin").exists()

    valid = StreamingSTTModel(
        id="valid",
        name="valid",
        engine="tone",
        languages=("ru",),
        description="",
        directory="valid",
        required_files=("tokens.txt",),
        archive_url=source.as_uri(),
        archive_sha256=good,
        archive_size_bytes=source.stat().st_size,
    )
    manager._download_archive(valid, tmp_path / "valid.bin")
    assert (tmp_path / "valid.bin").read_bytes() == b"streaming-bytes"


def _make_archive(path: Path, root_name: str, files: tuple[str, ...]) -> None:
    with tarfile.open(path, "w:bz2") as tar:
        for name in files:
            payload = b"model-bytes"
            info = tarfile.TarInfo(f"{root_name}/{name}")
            info.size = len(payload)
            tar.addfile(info, io.BytesIO(payload))


def test_extract_rejects_wrong_directory(tmp_path: Path) -> None:
    manager = StreamingSTTManager(root=tmp_path)
    defn = STREAMING_MODEL_CATALOG["tone_ru"]
    archive = tmp_path / "wrong.tar.bz2"
    _make_archive(archive, "totally-different-dir", defn.required_files)
    with pytest.raises(ValueError):
        manager._extract_archive(archive, defn)
    assert manager.is_installed("tone_ru") is False


def test_extract_rejects_path_traversal(tmp_path: Path) -> None:
    manager = StreamingSTTManager(root=tmp_path)
    defn = STREAMING_MODEL_CATALOG["tone_ru"]
    archive = tmp_path / "evil.tar.bz2"
    with tarfile.open(archive, "w:bz2") as tar:
        payload = b"owned"
        info = tarfile.TarInfo("../escaped.txt")
        info.size = len(payload)
        tar.addfile(info, io.BytesIO(payload))
    with pytest.raises(Exception):
        manager._extract_archive(archive, defn)
    assert not (tmp_path.parent / "escaped.txt").exists()


def test_download_model_installs_and_dedupes(tmp_path: Path) -> None:
    import asyncio

    manager = StreamingSTTManager(root=tmp_path)
    defn = STREAMING_MODEL_CATALOG["tone_ru"]
    prepared = tmp_path / "prepared.tar.bz2"
    _make_archive(prepared, defn.directory, defn.required_files)
    calls = {"count": 0}

    def fake_download(definition, destination):
        calls["count"] += 1
        destination.write_bytes(prepared.read_bytes())

    manager._download_archive = fake_download  # type: ignore[method-assign]

    async def run() -> None:
        await asyncio.gather(
            manager.download_model("tone_ru"),
            manager.download_model("tone_ru"),
        )

    asyncio.run(run())
    assert manager.is_installed("tone_ru") is True
    assert manager.get_status("tone_ru")["status"] == "ready"
    assert calls["count"] == 1
    assert not list(tmp_path.glob(".*.part"))


def test_catalog_models_are_pinned() -> None:
    for model in STREAMING_MODEL_CATALOG.values():
        assert model.archive_url.startswith("https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/")
        assert len(model.archive_sha256) == 64
        assert model.required_files
