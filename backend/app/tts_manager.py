"""Modular TTS model manager for Voice of Luna.

Provides on-demand discovery, status tracking, downloading, and verification
for local neural speech synthesis models (Silero, Piper, etc.).
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
import urllib.request
import urllib.error

logger = logging.getLogger("voice_of_luna.tts_manager")


@dataclass
class ModelFileSpec:
    filename: str
    url: str
    size_bytes: int = 0


@dataclass
class TTSModelDefinition:
    id: str
    name: str
    engine: str  # "piper" | "silero" | "edge" | "macos"
    locale: str
    description: str
    size_mb: float
    voices: list[str]
    files: list[ModelFileSpec] = field(default_factory=list)
    is_cloud: bool = False
    is_builtin: bool = False

    def to_dict(self, installed: bool = False, status: str = "ready") -> dict[str, object]:
        return {
            "id": self.id,
            "name": self.name,
            "engine": self.engine,
            "locale": self.locale,
            "description": self.description,
            "size_mb": self.size_mb,
            "voices": self.voices,
            "installed": installed,
            "status": status,
            "is_cloud": self.is_cloud,
            "is_builtin": self.is_builtin,
        }


# Catalog of supported TTS models
MODEL_CATALOG: dict[str, TTSModelDefinition] = {
    "piper_ru_dmitri": TTSModelDefinition(
        id="piper_ru_dmitri",
        name="Piper Dmitri (Medium)",
        engine="piper",
        locale="ru_RU",
        description="Высококачественный мужской оффлайн-голос ONNX для русского языка",
        size_mb=60.0,
        voices=["Dmitri (Piper Neural · Offline)"],
        files=[
            ModelFileSpec(
                filename="ru_RU-dmitri-medium.onnx",
                url="https://huggingface.co/rhasspy/piper-voices/resolve/main/ru/ru_RU/dmitri/medium/ru_RU-dmitri-medium.onnx",
            ),
            ModelFileSpec(
                filename="ru_RU-dmitri-medium.onnx.json",
                url="https://huggingface.co/rhasspy/piper-voices/resolve/main/ru/ru_RU/dmitri/medium/ru_RU-dmitri-medium.onnx.json",
            ),
        ],
    ),
    "piper_ru_irina": TTSModelDefinition(
        id="piper_ru_irina",
        name="Piper Irina (Medium)",
        engine="piper",
        locale="ru_RU",
        description="Высококачественный женский оффлайн-голос ONNX для русского языка",
        size_mb=60.0,
        voices=["Irina (Piper Neural · Offline)"],
        files=[
            ModelFileSpec(
                filename="ru_RU-irina-medium.onnx",
                url="https://huggingface.co/rhasspy/piper-voices/resolve/main/ru/ru_RU/irina/medium/ru_RU-irina-medium.onnx",
            ),
            ModelFileSpec(
                filename="ru_RU-irina-medium.onnx.json",
                url="https://huggingface.co/rhasspy/piper-voices/resolve/main/ru/ru_RU/irina/medium/ru_RU-irina-medium.onnx.json",
            ),
        ],
    ),
    "silero_v4_ru": TTSModelDefinition(
        id="silero_v4_ru",
        name="Silero v4 Russian",
        engine="silero",
        locale="ru_RU",
        description="Оффлайн нейросеть PyTorch с 5 голосами: Ksenia, Baya, Aidar, Eugene, Raya",
        size_mb=40.0,
        voices=[
            "Ksenia (Silero Neural · Offline)",
            "Baya (Silero Neural · Offline)",
            "Aidar (Silero Neural · Offline)",
            "Eugene (Silero Neural · Offline)",
            "Raya (Silero Neural · Offline)",
        ],
        files=[
            ModelFileSpec(
                filename="silero_v4_ru.pt",
                url="https://models.silero.ai/models/tts/ru/v4_ru.pt",
            ),
        ],
    ),
    "edge_tts_cloud": TTSModelDefinition(
        id="edge_tts_cloud",
        name="Microsoft Edge TTS",
        engine="edge",
        locale="multi",
        description="Облачные нейросетевые голоса без локальных моделей (Svetlana, Dmitry, Jenny...)",
        size_mb=0.0,
        voices=[
            "Svetlana (Neural · Edge)",
            "Dmitry (Neural · Edge)",
            "Jenny (Neural · Edge)",
            "Guy (Neural · Edge)",
            "Aria (Neural · Edge)",
        ],
        is_cloud=True,
    ),
    "macos_system": TTSModelDefinition(
        id="macos_system",
        name="macOS System Voices",
        engine="macos",
        locale="system",
        description="Системный синтез речи macOS (Milena, Samantha...)",
        size_mb=0.0,
        voices=[],
        is_builtin=True,
    ),
}


def get_model_storage_dir() -> Path:
    """Return the primary directory used to store TTS models."""
    custom = os.environ.get("VOICE_OF_LUNA_MODELS_DIR")
    if custom:
        target = Path(custom)
        target.mkdir(parents=True, exist_ok=True)
        return target

    backend_models = Path(__file__).resolve().parents[1] / "models"
    try:
        backend_models.mkdir(parents=True, exist_ok=True)
        test_file = backend_models / ".write_test"
        test_file.touch()
        test_file.unlink()
        return backend_models
    except OSError:
        pass

    user_cache = Path.home() / ".cache" / "voice-of-luna" / "models"
    user_cache.mkdir(parents=True, exist_ok=True)
    return user_cache


def get_model_search_dirs() -> list[Path]:
    """Return all directories where models may be searched."""
    dirs: list[Path] = []
    custom = os.environ.get("VOICE_OF_LUNA_MODELS_DIR")
    if custom:
        dirs.append(Path(custom))
    dirs.append(Path(__file__).resolve().parents[1] / "models")
    dirs.append(Path("models"))
    dirs.append(Path.home() / ".cache" / "voice-of-luna" / "models")
    # Also data/models for whisper / shared weights
    dirs.append(Path(__file__).resolve().parents[2] / "data" / "models")
    return dirs


def find_model_file(filename: str) -> Path | None:
    """Find a model file across known search directories."""
    for base in get_model_search_dirs():
        candidate = base / filename
        if candidate.is_file() and candidate.stat().st_size > 0:
            return candidate
    return None


class TTSModelManager:
    """Registry and async download manager for TTS models."""

    def __init__(self) -> None:
        self._downloads: dict[str, asyncio.Task[None]] = {}
        self._progress: dict[str, int] = {}
        self._errors: dict[str, str] = {}
        self._lock = asyncio.Lock()

    def is_installed(self, model_id: str) -> bool:
        """Return True if all files required by model_id are present."""
        defn = MODEL_CATALOG.get(model_id)
        if not defn:
            return False
        if defn.is_cloud or defn.is_builtin:
            return True
        if not defn.files:
            return False
        return all(find_model_file(spec.filename) is not None for spec in defn.files)

    def is_voice_installed(self, voice_name: str) -> bool:
        """Check if a specific voice has its required model installed."""
        for model in MODEL_CATALOG.values():
            if voice_name in model.voices:
                return self.is_installed(model.id)
        return True

    def get_model_for_voice(self, voice_name: str) -> TTSModelDefinition | None:
        """Find model definition for a given voice name."""
        for model in MODEL_CATALOG.values():
            if voice_name in model.voices:
                return model
            for v in model.voices:
                if voice_name.lower() in v.lower() or v.lower() in voice_name.lower():
                    return model
        return None

    def get_status(self, model_id: str) -> dict[str, object]:
        """Return status dictionary for a model."""
        defn = MODEL_CATALOG.get(model_id)
        if not defn:
            return {"id": model_id, "error": "Model not found", "status": "error"}

        installed = self.is_installed(model_id)
        is_downloading = model_id in self._downloads and not self._downloads[model_id].done()
        err = self._errors.get(model_id)

        status = "ready" if installed else ("downloading" if is_downloading else ("error" if err else "not_installed"))
        data = defn.to_dict(installed=installed, status=status)
        data["progress_percent"] = self._progress.get(model_id, 100 if installed else 0)
        data["error"] = err
        return data

    def list_all_models(self) -> list[dict[str, object]]:
        """Return full list of models with current statuses."""
        return [self.get_status(m_id) for m_id in MODEL_CATALOG]

    async def download_model(self, model_id: str) -> Path:
        """Download model files asynchronously with concurrency protection."""
        defn = MODEL_CATALOG.get(model_id)
        if not defn:
            raise ValueError(f"Unknown TTS model: {model_id}")

        if defn.is_cloud or defn.is_builtin:
            return get_model_storage_dir()

        if self.is_installed(model_id):
            first_file = find_model_file(defn.files[0].filename)
            return first_file.parent if first_file else get_model_storage_dir()

        async with self._lock:
            task = self._downloads.get(model_id)
            if task is not None and not task.done():
                pass
            else:
                task = asyncio.create_task(self._do_download(defn))
                self._downloads[model_id] = task

        await task
        storage_dir = get_model_storage_dir()
        return storage_dir

    async def _do_download(self, defn: TTSModelDefinition) -> None:
        target_dir = get_model_storage_dir()
        self._progress[defn.id] = 0
        self._errors.pop(defn.id, None)

        def _sync_download_file(url: str, dest_path: Path, current_idx: int, total_files: int) -> None:
            part_path = dest_path.with_suffix(dest_path.suffix + ".part")
            try:
                req = urllib.request.Request(
                    url,
                    headers={"User-Agent": "Voice-Of-Luna-Model-Downloader"},
                )
                with urllib.request.urlopen(req, timeout=120) as resp, open(part_path, "wb") as f:
                    content_length = resp.headers.get("Content-Length")
                    total_bytes = int(content_length) if content_length and content_length.isdigit() else 0
                    downloaded_bytes = 0

                    while chunk := resp.read(1024 * 256):
                        f.write(chunk)
                        downloaded_bytes += len(chunk)
                        if total_bytes > 0:
                            file_pct = downloaded_bytes / total_bytes
                            overall_pct = int(((current_idx + file_pct) / total_files) * 100)
                            self._progress[defn.id] = min(99, max(1, overall_pct))

                part_path.replace(dest_path)
            except Exception as e:
                part_path.unlink(missing_ok=True)
                raise e

        try:
            total_files = len(defn.files)
            for idx, file_spec in enumerate(defn.files):
                dest = target_dir / file_spec.filename
                if dest.is_file() and dest.stat().st_size > 0:
                    continue
                logger.info("Downloading TTS model file '%s' from %s", file_spec.filename, file_spec.url)
                await asyncio.to_thread(_sync_download_file, file_spec.url, dest, idx, total_files)

            self._progress[defn.id] = 100
            logger.info("Successfully downloaded all files for model '%s'", defn.id)
        except Exception as exc:
            logger.exception("Failed to download model '%s'", defn.id)
            self._errors[defn.id] = str(exc)
            raise


# Global singleton instance
tts_model_manager = TTSModelManager()


# ---------------------------------------------------------------------------
# CLI Helper
# ---------------------------------------------------------------------------

async def _cli_main() -> None:
    if len(sys.argv) < 2 or sys.argv[1] == "list":
        print(f"{'ID':<18} {'ENGINE':<8} {'INSTALLED':<10} {'SIZE':<8} {'NAME'}")
        print("-" * 65)
        for m in tts_model_manager.list_all_models():
            status_str = "YES" if m["installed"] else ("CLOUD" if m.get("is_cloud") else "NO")
            print(f"{m['id']:<18} {m['engine']:<8} {status_str:<10} {m['size_mb']}MB{'':<3} {m['name']}")
    elif sys.argv[1] == "download" and len(sys.argv) > 2:
        target_id = sys.argv[2]
        print(f"Starting download for {target_id}...")
        try:
            dest = await tts_model_manager.download_model(target_id)
            print(f"Downloaded successfully to {dest}")
        except Exception as err:
            print(f"Download failed: {err}", file=sys.stderr)
            sys.exit(1)
    else:
        print("Usage: python -m app.tts_manager [list | download <model_id>]")


if __name__ == "__main__":
    asyncio.run(_cli_main())
