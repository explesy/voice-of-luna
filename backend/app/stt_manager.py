"""Local streaming-STT model manager.

On-demand discovery, status tracking, download and extraction of the
sherpa-onnx streaming ASR bundles selected by spike #22 (T-One for Russian,
es-kroko for Spanish, Nemotron 3.5 as an opt-in). Nothing is uploaded and the
models are extracted under a gitignored local directory.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import shutil
import tarfile
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger("voice_of_luna.stt_manager")

_RELEASE_BASE = "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models"


@dataclass(frozen=True)
class StreamingSTTModel:
    id: str
    name: str
    engine: str  # "tone" | "vosk" | "kroko" | "nemotron"
    languages: tuple[str, ...]
    description: str
    directory: str
    required_files: tuple[str, ...]
    archive_url: str
    archive_sha256: str
    archive_size_bytes: int
    auto_download: bool = False
    opt_in: bool = False

    @property
    def size_mb(self) -> float:
        return round(self.archive_size_bytes / (1024 * 1024), 1)

    def to_dict(self, installed: bool = False, status: str = "ready") -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "engine": self.engine,
            "languages": list(self.languages),
            "description": self.description,
            "size_mb": self.size_mb,
            "installed": installed,
            "status": status,
            "auto_download": self.auto_download,
            "opt_in": self.opt_in,
        }


STREAMING_MODEL_CATALOG: dict[str, StreamingSTTModel] = {
    "tone_ru": StreamingSTTModel(
        id="tone_ru",
        name="T-One Russian (streaming)",
        engine="tone",
        languages=("ru",),
        description="Лучшая точность для русского стрима (spike #22); Apache-2.0",
        directory="sherpa-onnx-streaming-t-one-russian-2025-09-08",
        required_files=("model.onnx", "tokens.txt"),
        archive_url=f"{_RELEASE_BASE}/sherpa-onnx-streaming-t-one-russian-2025-09-08.tar.bz2",
        archive_sha256="b9c907450e99a6e5049e279bf18368a17db0bdc5e63b7fa978943138debbe3ae",
        archive_size_bytes=128468156,
        auto_download=True,
    ),
    "vosk_ru": StreamingSTTModel(
        id="vosk_ru",
        name="Vosk zipformer small Russian (streaming)",
        engine="vosk",
        languages=("ru",),
        description="Лёгкая русская streaming-модель (24 МБ), точность ниже T-One",
        directory="sherpa-onnx-streaming-zipformer-small-ru-vosk-int8-2025-08-16",
        required_files=("encoder.int8.onnx", "decoder.onnx", "joiner.int8.onnx", "tokens.txt"),
        archive_url=f"{_RELEASE_BASE}/sherpa-onnx-streaming-zipformer-small-ru-vosk-int8-2025-08-16.tar.bz2",
        archive_sha256="6ba68a01ff3c5445aaf2d61e9b97b026f1149dcc9049d11af3f44f55176341d8",
        archive_size_bytes=24110855,
    ),
    "kroko_es": StreamingSTTModel(
        id="kroko_es",
        name="Kroko Spanish (streaming)",
        engine="kroko",
        languages=("es",),
        description="Лёгкая испанская streaming-модель; лицензия CC-BY-SA (community)",
        directory="sherpa-onnx-streaming-zipformer-es-kroko-2025-08-06",
        required_files=("encoder.onnx", "decoder.onnx", "joiner.onnx", "tokens.txt"),
        archive_url=f"{_RELEASE_BASE}/sherpa-onnx-streaming-zipformer-es-kroko-2025-08-06.tar.bz2",
        archive_sha256="31b2230a95d23290b308b393da930015a4b2105cb3abb9367aed35f7fcf29cf1",
        archive_size_bytes=124394665,
        auto_download=True,
    ),
    "nemotron320": StreamingSTTModel(
        id="nemotron320",
        name="Nemotron 3.5 streaming 0.6B (320 ms)",
        engine="nemotron",
        languages=("ru", "es", "en"),
        description="Мультиязычная модель с per-stream языком; ~1.2 ГБ RAM",
        directory="sherpa-onnx-nemotron-3.5-asr-streaming-0.6b-320ms-int8-2026-06-11",
        required_files=("encoder.int8.onnx", "decoder.int8.onnx", "joiner.int8.onnx", "tokens.txt"),
        archive_url=f"{_RELEASE_BASE}/sherpa-onnx-nemotron-3.5-asr-streaming-0.6b-320ms-int8-2026-06-11.tar.bz2",
        archive_sha256="5f311142337a5c161e92d49f7a3009d8607d3836f39d610bff5307c74d1d2c53",
        archive_size_bytes=475272949,
        opt_in=True,
    ),
    "nemotron560": StreamingSTTModel(
        id="nemotron560",
        name="Nemotron 3.5 streaming 0.6B (560 ms)",
        engine="nemotron",
        languages=("ru", "es", "en"),
        description="Мультиязычная модель с per-stream языком; ~1.2 ГБ RAM",
        directory="sherpa-onnx-nemotron-3.5-asr-streaming-0.6b-560ms-int8-2026-06-11",
        required_files=("encoder.int8.onnx", "decoder.int8.onnx", "joiner.int8.onnx", "tokens.txt"),
        archive_url=f"{_RELEASE_BASE}/sherpa-onnx-nemotron-3.5-asr-streaming-0.6b-560ms-int8-2026-06-11.tar.bz2",
        archive_sha256="c6bf5e0df765f9d5b43bc9e0536d4b4b3e7d40bdf5ecf13e45f134c51c05ae3a",
        archive_size_bytes=475271763,
        opt_in=True,
    ),
}

DEFAULT_MODEL_BY_LOCALE = {"ru": "tone_ru", "es": "kroko_es"}


def _default_model_root() -> Path:
    configured = os.environ.get("VOICE_OF_LUNA_STT_MODEL_DIR")
    if configured:
        return Path(configured).expanduser()
    return Path(__file__).resolve().parents[1] / "models" / "streaming-stt"


def sherpa_available() -> bool:
    """Return whether the optional sherpa-onnx runtime is importable."""

    import importlib.util

    return importlib.util.find_spec("sherpa_onnx") is not None


class StreamingSTTManager:
    """Tracks installation and download state for local streaming-STT bundles."""

    def __init__(self, root: Path | None = None) -> None:
        self.root = (root or _default_model_root()).expanduser()
        self._downloads: dict[str, asyncio.Task[None]] = {}
        self._stats: dict[str, dict[str, Any]] = {}
        self._errors: dict[str, str] = {}

    # -- catalog / resolution -------------------------------------------------

    def get(self, model_id: str) -> StreamingSTTModel | None:
        return STREAMING_MODEL_CATALOG.get(model_id)

    def list_all_models(self) -> list[dict[str, Any]]:
        return [self.get_status(model_id) for model_id in STREAMING_MODEL_CATALOG]

    def model_dir(self, model_id: str) -> Path:
        defn = STREAMING_MODEL_CATALOG[model_id]
        return self.root / defn.directory

    def is_installed(self, model_id: str) -> bool:
        defn = self.get(model_id)
        if defn is None:
            return False
        directory = self.model_dir(model_id)
        return all((directory / name).is_file() and (directory / name).stat().st_size > 0 for name in defn.required_files)

    def default_model_id(self, locale: str) -> str | None:
        prefix = (locale or "").split("-")[0].lower()
        return DEFAULT_MODEL_BY_LOCALE.get(prefix)

    def resolve_model_id(self, locale: str, *, installed_only: bool = False) -> str | None:
        """Pick the streaming model for a locale.

        Prefers the locale default, then any already-installed model that covers
        the language (including opt-in ones). With ``installed_only=False`` the
        default is returned even when it still needs downloading.
        """

        prefix = (locale or "").split("-")[0].lower()
        preferred = self.default_model_id(locale)
        if preferred and (not installed_only or self.is_installed(preferred)):
            return preferred
        installed = [
            model.id
            for model in STREAMING_MODEL_CATALOG.values()
            if prefix in model.languages and self.is_installed(model.id)
        ]
        if installed:
            installed.sort(key=lambda mid: STREAMING_MODEL_CATALOG[mid].archive_size_bytes)
            return installed[0]
        if not installed_only:
            return preferred
        return None

    def download_target(self, locale: str) -> str | None:
        """Return an auto-downloadable model for the locale's default engine."""

        model_id = self.default_model_id(locale)
        defn = self.get(model_id) if model_id else None
        if defn is not None and defn.auto_download:
            return model_id
        return None

    # -- status ---------------------------------------------------------------

    def get_status(self, model_id: str) -> dict[str, Any]:
        defn = self.get(model_id)
        if defn is None:
            return {"id": model_id, "error": "Model not found", "status": "error"}
        installed = self.is_installed(model_id)
        task = self._downloads.get(model_id)
        downloading = task is not None and not task.done()
        error = self._errors.get(model_id)
        status = "ready" if installed else ("downloading" if downloading else ("error" if error else "not_installed"))
        data = defn.to_dict(installed=installed, status=status)
        stats = self._stats.get(model_id, {})
        data["progress_percent"] = stats.get("progress_percent", 100 if installed else 0)
        data["downloaded_mb"] = stats.get("downloaded_mb", defn.size_mb if installed else 0.0)
        data["total_mb"] = stats.get("total_mb", defn.size_mb)
        data["speed_kbps"] = stats.get("speed_kbps", 0.0)
        data["eta_seconds"] = stats.get("eta_seconds", 0)
        data["error"] = error
        return data

    # -- download -------------------------------------------------------------

    def start_download(self, model_id: str) -> asyncio.Task[None]:
        """Register a download task synchronously so status is ``downloading`` immediately."""

        defn = self.get(model_id)
        if defn is None:
            raise ValueError(f"Unknown streaming STT model: {model_id}")
        task = self._downloads.get(model_id)
        if task is not None and not task.done():
            return task
        task = asyncio.get_running_loop().create_task(self._do_download(defn))
        self._downloads[model_id] = task
        return task

    async def download_model(self, model_id: str) -> Path:
        defn = self.get(model_id)
        if defn is None:
            raise ValueError(f"Unknown streaming STT model: {model_id}")
        if self.is_installed(model_id):
            return self.model_dir(model_id)
        task = self.start_download(model_id)
        await task
        return self.model_dir(model_id)

    async def _do_download(self, defn: StreamingSTTModel) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        self._errors.pop(defn.id, None)
        archive_path = self.root / f".{defn.id}.tar.bz2.part"
        self._stats[defn.id] = {
            "progress_percent": 0,
            "downloaded_mb": 0.0,
            "total_mb": defn.size_mb,
            "speed_kbps": 0.0,
            "eta_seconds": 0,
        }
        try:
            await asyncio.to_thread(self._download_archive, defn, archive_path)
            self._stats[defn.id]["progress_percent"] = 95
            await asyncio.to_thread(self._extract_archive, archive_path, defn)
            if not self.is_installed(defn.id):
                raise ValueError(f"Archive for '{defn.id}' did not contain the expected model files")
            self._stats[defn.id] = {
                "progress_percent": 100,
                "downloaded_mb": defn.size_mb,
                "total_mb": defn.size_mb,
                "speed_kbps": 0.0,
                "eta_seconds": 0,
            }
            logger.info("Streaming STT model '%s' is ready", defn.id)
        except Exception as exc:
            logger.exception("Failed to install streaming STT model '%s'", defn.id)
            self._errors[defn.id] = str(exc)
            raise
        finally:
            archive_path.unlink(missing_ok=True)

    def _download_archive(self, defn: StreamingSTTModel, destination: Path) -> None:
        started = time.time()
        hasher = hashlib.sha256()
        request = urllib.request.Request(defn.archive_url, headers={"User-Agent": "Voice-Of-Luna-STT-Downloader"})
        try:
            with urllib.request.urlopen(request, timeout=120) as response, open(destination, "wb") as handle:
                downloaded = 0
                while chunk := response.read(1024 * 256):
                    handle.write(chunk)
                    hasher.update(chunk)
                    downloaded += len(chunk)
                    elapsed = max(0.1, time.time() - started)
                    speed_kbps = round((downloaded / elapsed) / 1024, 1)
                    progress = min(94, int(downloaded / defn.archive_size_bytes * 94)) if defn.archive_size_bytes else 0
                    remaining = max(0, defn.archive_size_bytes - downloaded)
                    self._stats[defn.id] = {
                        "progress_percent": progress,
                        "downloaded_mb": round(downloaded / (1024 * 1024), 1),
                        "total_mb": defn.size_mb,
                        "speed_kbps": speed_kbps,
                        "eta_seconds": int(remaining / (speed_kbps * 1024)) if speed_kbps > 5 else 0,
                    }
        except Exception:
            destination.unlink(missing_ok=True)
            raise
        digest = hasher.hexdigest().lower()
        if defn.archive_sha256 and digest != defn.archive_sha256.lower():
            destination.unlink(missing_ok=True)
            raise ValueError(f"SHA256 mismatch for '{defn.id}': expected {defn.archive_sha256}, got {digest}")

    def _extract_archive(self, archive_path: Path, defn: StreamingSTTModel) -> None:
        """Extract into a staging directory and move it into place atomically."""

        staging = self.root / f".{defn.id}.staging"
        if staging.exists():
            shutil.rmtree(staging)
        staging.mkdir(parents=True, exist_ok=True)
        try:
            with tarfile.open(archive_path, "r:bz2") as archive:
                archive.extractall(staging, filter="data")
            extracted = staging / defn.directory
            missing = [
                name
                for name in defn.required_files
                if not (extracted / name).is_file() or (extracted / name).stat().st_size == 0
            ]
            if missing:
                raise ValueError(
                    f"Archive for '{defn.id}' is missing required files: {', '.join(missing)}"
                )
            target_dir = self.model_dir(defn.id)
            if target_dir.exists():
                shutil.rmtree(target_dir)
            os.replace(extracted, target_dir)
        finally:
            shutil.rmtree(staging, ignore_errors=True)

stt_model_manager = StreamingSTTManager()


__all__ = [
    "StreamingSTTManager",
    "StreamingSTTModel",
    "STREAMING_MODEL_CATALOG",
    "DEFAULT_MODEL_BY_LOCALE",
    "stt_model_manager",
    "sherpa_available",
]
