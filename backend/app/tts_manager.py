"""Modular TTS model manager for Voice of Luna.

Provides on-demand discovery, status tracking, downloading, and verification
for local neural speech synthesis models (Silero, Piper, etc.).
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import shutil
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
import time
import urllib.request
import urllib.error

from . import model_catalog

logger = logging.getLogger("voice_of_luna.tts_manager")


@dataclass
class ModelFileSpec:
    filename: str
    url: str
    size_bytes: int = 0
    sha256: str = ""


@dataclass
class TTSModelDefinition:
    id: str
    name: str
    engine: str  # "piper" | "silero" | "edge" | "macos" | "whisper"
    locale: str
    description: str
    size_mb: float
    voices: list[str]
    files: list[ModelFileSpec] = field(default_factory=list)
    is_cloud: bool = False
    is_builtin: bool = False
    kind: str = model_catalog.MODEL_KIND_TTS
    catalog_status: str = model_catalog.CATALOG_STATUS_RECOMMENDED
    deprecation_reason: str = ""
    languages: tuple[str, ...] = ()
    multilingual: bool = False
    code_switching: str = model_catalog.CODE_SWITCHING_NONE
    streaming: bool = False
    user_defined: bool = False
    voice_model_keys: dict[str, str] = field(default_factory=dict)
    # Validated per-voice capability declarations for user-defined models.
    # Built-in voices fall back to the neutral capability table instead.
    voice_capability_overrides: dict[str, model_catalog.VoiceCapability] = field(default_factory=dict)

    @property
    def is_deprecated(self) -> bool:
        return model_catalog.is_deprecated(self.catalog_status)

    @property
    def is_downloadable(self) -> bool:
        return any(spec.url for spec in self.files)

    def voice_capabilities(self) -> list[dict[str, object]]:
        """Return per-voice language capability entries (issue #14 / #13)."""

        entries: list[dict[str, object]] = []
        for voice in self.voices:
            capability = self.voice_capability_overrides.get(voice) or model_catalog.voice_capability(
                voice,
                self.locale,
                status=self.catalog_status,
                deprecation_reason=self.deprecation_reason,
            )
            entry = capability.to_dict()
            entry["name"] = voice
            entry["model_id"] = self.id
            entries.append(entry)
        return entries

    def resolved_languages(self) -> tuple[str, ...]:
        if self.languages:
            return self.languages
        if self.voices:
            override = self.voice_capability_overrides.get(self.voices[0])
            if override is not None:
                return override.languages
            return model_catalog.voice_capability(
                self.voices[0], self.locale
            ).languages
        return model_catalog.languages_for_locale(self.locale)

    def to_dict(self, installed: bool = False, status: str = "ready") -> dict[str, object]:
        voice_capabilities = self.voice_capabilities()
        multilingual = self.multilingual or any(
            bool(entry.get("multilingual")) for entry in voice_capabilities
        )
        code_switching = self.code_switching
        if code_switching == model_catalog.CODE_SWITCHING_NONE and voice_capabilities:
            code_switching = str(voice_capabilities[0].get("code_switching") or model_catalog.CODE_SWITCHING_NONE)
        languages = list(self.resolved_languages())
        return {
            "id": self.id,
            "name": self.name,
            "engine": self.engine,
            "kind": self.kind,
            "locale": self.locale,
            "languages": languages,
            "description": self.description,
            "size_mb": self.size_mb,
            "voices": self.voices,
            "voice_capabilities": voice_capabilities,
            "multilingual": multilingual,
            "code_switching": code_switching,
            "streaming": self.streaming,
            "installed": installed,
            "status": status,
            "catalog_status": self.catalog_status,
            "deprecation_reason": self.deprecation_reason or None,
            "is_cloud": self.is_cloud,
            "is_builtin": self.is_builtin,
            "user_defined": self.user_defined,
            "downloadable": self.is_downloadable,
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
                size_bytes=63510526,
                sha256="f073356ebc4bd0f80c5af58df2953a5988bd5bdab1eb38635ce960b071fbefcb",
            ),
            ModelFileSpec(
                filename="ru_RU-dmitri-medium.onnx.json",
                url="https://huggingface.co/rhasspy/piper-voices/resolve/main/ru/ru_RU/dmitri/medium/ru_RU-dmitri-medium.onnx.json",
                size_bytes=4842,
                sha256="667ef3117bc642c2892dff7690d8bdc8ca4228aeaa783b2dc1416df632855e0d",
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
                size_bytes=63340030,
                sha256="8ff38212d23da300bbe3705c645e6e5b9475f0bfde01558eb17813e22acaaaaa",
            ),
            ModelFileSpec(
                filename="ru_RU-irina-medium.onnx.json",
                url="https://huggingface.co/rhasspy/piper-voices/resolve/main/ru/ru_RU/irina/medium/ru_RU-irina-medium.onnx.json",
                size_bytes=4842,
                sha256="c2ec28bb38e2b59e93b959b3e40348c1afebbd272f30fed5d41205d08e98a9d7",
            ),
        ],
    ),
    "piper_ru_denis": TTSModelDefinition(
        id="piper_ru_denis",
        name="Piper Denis (Medium)",
        engine="piper",
        locale="ru_RU",
        description="Высококачественный мужской голос ONNX (Денис) для русского языка",
        size_mb=60.0,
        voices=["Denis (Piper Neural · Offline)"],
        files=[
            ModelFileSpec(
                filename="ru_RU-denis-medium.onnx",
                url="https://huggingface.co/rhasspy/piper-voices/resolve/main/ru/ru_RU/denis/medium/ru_RU-denis-medium.onnx",
                size_bytes=63201294,
                sha256="15fab56e11a097858ee115545d0f697fc2a316c41a291a5362349fb870411b0a",
            ),
            ModelFileSpec(
                filename="ru_RU-denis-medium.onnx.json",
                url="https://huggingface.co/rhasspy/piper-voices/resolve/main/ru/ru_RU/denis/medium/ru_RU-denis-medium.onnx.json",
                size_bytes=4823,
                sha256="831c860dac0b5073eaa81610a0a638ec23d90a6cf8e5f871b4485c2cec3767c8",
            ),
        ],
    ),
    "piper_ru_ruslan": TTSModelDefinition(
        id="piper_ru_ruslan",
        name="Piper Ruslan (Medium)",
        engine="piper",
        locale="ru_RU",
        description="Высококачественный выразительный мужской голос ONNX (Руслан) для русского языка",
        size_mb=60.0,
        voices=["Ruslan (Piper Neural · Offline)"],
        files=[
            ModelFileSpec(
                filename="ru_RU-ruslan-medium.onnx",
                url="https://huggingface.co/rhasspy/piper-voices/resolve/main/ru/ru_RU/ruslan/medium/ru_RU-ruslan-medium.onnx",
                size_bytes=63201294,
                sha256="72a5f88e0b20928064eb45d88e1daa21f8af62d18613580d32cbb4aed48dcf7f",
            ),
            ModelFileSpec(
                filename="ru_RU-ruslan-medium.onnx.json",
                url="https://huggingface.co/rhasspy/piper-voices/resolve/main/ru/ru_RU/ruslan/medium/ru_RU-ruslan-medium.onnx.json",
                size_bytes=4882,
                sha256="706a4fb17bc304abd07809b552deae615e64dcbffbfbd09854ba37ca59e88117",
            ),
        ],
    ),
    "piper_en_lessac": TTSModelDefinition(
        id="piper_en_lessac",
        name="Piper Lessac (Medium)",
        engine="piper",
        locale="en_US",
        description="Высококачественный оффлайн-голос ONNX для английского языка",
        size_mb=63.0,
        voices=["Lessac (Piper Neural · Offline)"],
        files=[
            ModelFileSpec(
                filename="en_US-lessac-medium.onnx",
                url="https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/medium/en_US-lessac-medium.onnx",
                size_bytes=63201294,
                sha256="5efe09e69902187827af646e1a6e9d269dee769f9877d17b16b1b46eeaaf019f",
            ),
            ModelFileSpec(
                filename="en_US-lessac-medium.onnx.json",
                url="https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/medium/en_US-lessac-medium.onnx.json",
                size_bytes=4889,
                sha256="efe19c417bed055f2d69908248c6ba650fa135bc868b0e6abb3da181dab690a0",
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
                size_bytes=41870817,
                sha256="896ab96347d5bd781ab97959d4fd6885620e5aab52405d3445626eb7c1414b00",
            ),
        ],
    ),
    "silero_v5_ru": TTSModelDefinition(
        id="silero_v5_ru",
        name="Silero v5 Russian",
        engine="silero",
        locale="ru_RU",
        description="Обновленная нейросеть Silero v5 (24/48 kHz, чистое произношение) с 5 голосами",
        size_mb=139.0,
        voices=[
            "Ksenia v5 (Silero Neural · Offline)",
            "Baya v5 (Silero Neural · Offline)",
            "Aidar v5 (Silero Neural · Offline)",
            "Eugene v5 (Silero Neural · Offline)",
            "Raya v5 (Silero Neural · Offline)",
        ],
        files=[
            ModelFileSpec(
                filename="silero_v5_ru.pt",
                url="https://models.silero.ai/models/tts/ru/v5_ru.pt",
                size_bytes=145382211,
            ),
        ],
    ),
    "whisper_large_v3_turbo": TTSModelDefinition(
        id="whisper_large_v3_turbo",
        name="Whisper Large-v3-Turbo (q5_0)",
        engine="whisper",
        locale="multi",
        description="Флагманская модель распознавания речи OpenAI (809M params, 4 decoder layers, высокая точность при низкой задержке)",
        size_mb=547.0,
        voices=[],
        files=[
            ModelFileSpec(
                filename="ggml-large-v3-turbo-q5_0.bin",
                url="https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-large-v3-turbo-q5_0.bin",
                size_bytes=574041195,
                sha256="394221709cd5ad1f40c46e6031ca61bce88931e6e088c188294c6d5a55ffa7e2",
            ),
        ],
    ),
    "whisper_small": TTSModelDefinition(
        id="whisper_small",
        name="Whisper Small",
        engine="whisper",
        locale="multi",
        description="Компактная быстрая модель распознавания речи OpenAI (244M params)",
        size_mb=465.0,
        voices=[],
        files=[
            ModelFileSpec(
                filename="ggml-small.bin",
                url="https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-small.bin",
                size_bytes=487826477,
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
            "Elvira (Neural · Edge)",
            "Alvaro (Neural · Edge)",
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

# Whisper ggml models are speech-to-text assets. They stay in this catalog so the
# existing file readiness / checksum / download machinery keeps working, but
# they are tagged with ``kind="stt"`` and surfaced through the STT endpoints.
for _whisper_id in ("whisper_large_v3_turbo", "whisper_small"):
    _model = MODEL_CATALOG[_whisper_id]
    _model.kind = model_catalog.MODEL_KIND_STT
    _model.multilingual = True
    _model.code_switching = model_catalog.CODE_SWITCHING_SEGMENT_ONLY

# Explicit decisions for stale entries (issue #14).
MODEL_CATALOG["silero_v4_ru"].catalog_status = model_catalog.CATALOG_STATUS_DEPRECATED
MODEL_CATALOG["silero_v4_ru"].deprecation_reason = "Заменена Silero v5 (чище произношение, 24/48 кГц)"
MODEL_CATALOG["whisper_small"].catalog_status = model_catalog.CATALOG_STATUS_LEGACY
MODEL_CATALOG["whisper_small"].deprecation_reason = "Заменена Whisper Large-v3-Turbo; оставлена для машин с малым объёмом RAM"

# Batch (non-streaming) speech-to-text model ids that ship with the app.
BUILTIN_BATCH_STT_MODEL_IDS = ("whisper_large_v3_turbo", "whisper_small")
DEFAULT_BATCH_STT_MODEL_ID = "whisper_large_v3_turbo"


def batch_stt_models() -> list[TTSModelDefinition]:
    """Return Whisper ggml definitions, newest first, for the STT selector."""

    return [
        MODEL_CATALOG[model_id]
        for model_id in (*BUILTIN_BATCH_STT_MODEL_IDS,)
        if model_id in MODEL_CATALOG
    ] + [
        model
        for model in MODEL_CATALOG.values()
        if model.kind == model_catalog.MODEL_KIND_STT
        and model.user_defined
    ]


def is_batch_stt_model(model_id: str) -> bool:
    model = MODEL_CATALOG.get(model_id)
    return bool(model and model.kind == model_catalog.MODEL_KIND_STT)


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


def compute_file_sha256(path: Path) -> str:
    """Compute sha256 hex digest for a file."""
    hasher = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(1024 * 128):
            hasher.update(chunk)
    return hasher.hexdigest().lower()


class TTSModelManager:
    """Registry and async download manager for TTS models."""

    def __init__(self) -> None:
        self._downloads: dict[str, asyncio.Task[None]] = {}
        self._progress: dict[str, int] = {}
        self._stats: dict[str, dict[str, object]] = {}
        self._errors: dict[str, str] = {}
        self._checksum_cache: dict[tuple[str, int, int, str], bool] = {}
        self._user_model_errors: list[str] = []
        self._lock = asyncio.Lock()

    # -- user-defined models ---------------------------------------------------

    @property
    def user_model_errors(self) -> list[str]:
        """Sanitized validation errors from the last user catalog load."""

        return list(self._user_model_errors)

    def register_user_models(self) -> list[str]:
        """(Re)load user-defined models from ``VOICE_OF_LUNA_MODELS_CONFIG``.

        Idempotent: previously registered user entries are removed first so a
        reload replaces the snapshot instead of accumulating duplicates.
        Invalid entries are skipped and reported; built-ins are never replaced.
        """

        for model_id in [mid for mid, model in MODEL_CATALOG.items() if model.user_defined]:
            MODEL_CATALOG.pop(model_id, None)
            self._errors.pop(model_id, None)
            self._stats.pop(model_id, None)
            self._downloads.pop(model_id, None)

        reserved_ids = set(MODEL_CATALOG)
        # Reserve streaming-STT ids too: a user model must not shadow e.g. tone_ru,
        # which would otherwise be routed to the wrong manager by the STT endpoints.
        try:
            from .stt_manager import STREAMING_MODEL_CATALOG

            reserved_ids.update(STREAMING_MODEL_CATALOG)
        except Exception:  # pragma: no cover - defensive only
            pass
        reserved_filenames = {
            spec.filename.casefold() for model in MODEL_CATALOG.values() for spec in model.files
        }
        reserved_voices = {
            voice.casefold() for model in MODEL_CATALOG.values() for voice in model.voices
        }
        load = model_catalog.load_user_model_config(
            reserved_ids=reserved_ids,
            reserved_filenames=reserved_filenames,
            reserved_voices=reserved_voices,
        )
        for spec in load.models:
            MODEL_CATALOG[spec.id] = TTSModelDefinition(
                id=spec.id,
                name=spec.name,
                engine=spec.engine,
                locale=spec.locale,
                description=spec.description,
                size_mb=spec.size_mb,
                voices=[voice.name for voice in spec.voices],
                files=[
                    ModelFileSpec(
                        filename=asset.filename,
                        url=asset.url,
                        size_bytes=asset.size_bytes,
                        sha256=asset.sha256,
                    )
                    for asset in spec.assets
                ],
                kind=spec.kind,
                catalog_status=spec.catalog_status,
                deprecation_reason=spec.deprecation_reason,
                languages=spec.languages,
                multilingual=spec.multilingual,
                code_switching=spec.code_switching,
                streaming=False,
                user_defined=True,
                voice_model_keys=(
                    {spec.voices[0].name: spec.model_key} if spec.voices and spec.model_key else {}
                ),
                voice_capability_overrides={
                    voice.name: model_catalog.VoiceCapability(
                        languages=voice.languages,
                        multilingual=voice.multilingual,
                        code_switching=voice.code_switching,
                        status=spec.catalog_status,
                        deprecation_reason=spec.deprecation_reason,
                    )
                    for voice in spec.voices
                },
            )
        self._invalidate_checksum_cache()
        self._user_model_errors = list(load.errors)
        if load.models:
            logger.info("Registered %d user-defined model(s) from %s", len(load.models), load.path)
        for error in load.errors:
            logger.warning("User model config problem: %s", error)
        return list(load.errors)

    def _invalidate_checksum_cache(self, model_id: str | None = None) -> None:
        if model_id is None:
            self._checksum_cache.clear()
            return
        defn = MODEL_CATALOG.get(model_id)
        filenames = {spec.filename for spec in defn.files} if defn else set()
        self._checksum_cache = {
            key: value for key, value in self._checksum_cache.items()
            if Path(key[0]).name not in filenames
        }

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

    def is_ready(self, model_id: str) -> bool:
        """Return whether all model files exist and match their catalog checksums."""
        defn = MODEL_CATALOG.get(model_id)
        if not defn:
            return False
        if defn.is_cloud or defn.is_builtin:
            return True
        return self.is_installed(model_id) and self.verify_checksums(model_id)

    def is_voice_installed(self, voice_name: str) -> bool:
        """Check if a specific voice has its required model installed."""
        for model in MODEL_CATALOG.values():
            if voice_name in model.voices:
                return self.is_ready(model.id)
        return True

    def get_model_for_voice(self, voice_name: str) -> TTSModelDefinition | None:
        """Find model definition for a given voice name (exact match first)."""
        for model in MODEL_CATALOG.values():
            if voice_name in model.voices:
                return model
        for model in MODEL_CATALOG.values():
            for v in model.voices:
                if voice_name.lower() in v.lower() or v.lower() in voice_name.lower():
                    return model
        return None

    def get_status(self, model_id: str) -> dict[str, object]:
        """Return status dictionary for a model."""
        defn = MODEL_CATALOG.get(model_id)
        if not defn:
            return {"id": model_id, "error": "Model not found", "status": "error"}

        installed = self.is_ready(model_id)
        is_downloading = model_id in self._downloads and not self._downloads[model_id].done()
        err = self._errors.get(model_id)

        status = "ready" if installed else ("downloading" if is_downloading else ("error" if err else "not_installed"))
        data = defn.to_dict(installed=installed, status=status)
        stats = self._stats.get(model_id, {})
        data["progress_percent"] = stats.get("progress_percent", 100 if installed else 0)
        data["downloaded_mb"] = stats.get("downloaded_mb", defn.size_mb if installed else 0.0)
        data["total_mb"] = stats.get("total_mb", defn.size_mb)
        data["speed_kbps"] = stats.get("speed_kbps", 0.0)
        data["eta_seconds"] = stats.get("eta_seconds", 0)
        data["error"] = err
        if not installed and defn.user_defined and not defn.is_downloadable:
            data["install_hint"] = (
                "This model references local files only. Place them in the models directory; "
                "no download is performed."
            )
        return data

    def list_all_models(self, kind: str | None = None) -> list[dict[str, object]]:
        """Return full list of models with current statuses, optionally by kind."""

        return [
            self.get_status(m_id)
            for m_id, definition in MODEL_CATALOG.items()
            if kind is None or definition.kind == kind
        ]

    def recommended_model_ids(self, kind: str | None = None) -> list[str]:
        """Ids that are safe to use as automatic defaults (deprecated excluded)."""

        return [
            m_id
            for m_id, definition in MODEL_CATALOG.items()
            if definition.catalog_status != model_catalog.CATALOG_STATUS_DEPRECATED
            and (kind is None or definition.kind == kind)
        ]

    async def download_model(self, model_id: str) -> Path:
        """Download model files asynchronously with concurrency protection."""
        defn = MODEL_CATALOG.get(model_id)
        if not defn:
            raise ValueError(f"Unknown TTS model: {model_id}")

        if defn.is_cloud or defn.is_builtin:
            return get_model_storage_dir()

        if not defn.is_downloadable:
            raise ValueError(
                f"Model '{model_id}' declares only local assets; install its files manually "
                "into a model search directory"
            )

        if self.is_ready(model_id):
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

    def verify_checksums(self, model_id: str) -> bool:
        """Verify sha256 checksums of all installed files for a given model."""
        defn = MODEL_CATALOG.get(model_id)
        if not defn or not defn.files:
            return True
        for spec in defn.files:
            if not spec.sha256:
                continue
            path = find_model_file(spec.filename)
            if not path or not path.is_file():
                return False
            stat = path.stat()
            cache_key = (str(path), stat.st_size, stat.st_mtime_ns, spec.sha256.lower())
            cached = self._checksum_cache.get(cache_key)
            if cached is None:
                cached = compute_file_sha256(path) == spec.sha256.lower()
                self._checksum_cache[cache_key] = cached
            if not cached:
                return False
        return True

    async def _do_download(self, defn: TTSModelDefinition) -> None:
        target_dir = get_model_storage_dir()
        start_time = time.time()
        self._progress[defn.id] = 0
        self._errors.pop(defn.id, None)
        self._stats[defn.id] = {
            "progress_percent": 0,
            "downloaded_bytes": 0,
            "total_bytes": int(defn.size_mb * 1024 * 1024),
            "downloaded_mb": 0.0,
            "total_mb": defn.size_mb,
            "speed_kbps": 0.0,
            "eta_seconds": 0,
        }

        def _sync_download_file(file_spec: ModelFileSpec, dest_path: Path, current_idx: int, total_files: int) -> None:
            part_path = dest_path.with_suffix(dest_path.suffix + ".part")
            hasher = hashlib.sha256()
            try:
                req = urllib.request.Request(
                    file_spec.url,
                    headers={"User-Agent": "Voice-Of-Luna-Model-Downloader"},
                )
                with urllib.request.urlopen(req, timeout=120) as resp, open(part_path, "wb") as f:
                    content_length = resp.headers.get("Content-Length")
                    file_total_bytes = int(content_length) if content_length and content_length.isdigit() else int(defn.size_mb * 1024 * 1024)
                    downloaded_bytes = 0

                    while chunk := resp.read(1024 * 128):
                        f.write(chunk)
                        hasher.update(chunk)
                        downloaded_bytes += len(chunk)
                        elapsed = max(0.1, time.time() - start_time)
                        speed_kbps = round((downloaded_bytes / elapsed) / 1024, 1)

                        if file_total_bytes > 0:
                            file_pct = downloaded_bytes / file_total_bytes
                            overall_pct = int(((current_idx + file_pct) / total_files) * 100)
                            progress = min(99, max(1, overall_pct))
                            self._progress[defn.id] = progress
                            remaining = max(0, file_total_bytes - downloaded_bytes)
                            eta = int(remaining / (speed_kbps * 1024)) if speed_kbps > 5 else 0
                            self._stats[defn.id] = {
                                "progress_percent": progress,
                                "downloaded_bytes": downloaded_bytes,
                                "total_bytes": file_total_bytes,
                                "downloaded_mb": round(downloaded_bytes / (1024 * 1024), 1),
                                "total_mb": round(file_total_bytes / (1024 * 1024), 1),
                                "speed_kbps": speed_kbps,
                                "eta_seconds": eta,
                            }

                if file_spec.sha256:
                    computed_hash = hasher.hexdigest().lower()
                    if computed_hash != file_spec.sha256.lower():
                        part_path.unlink(missing_ok=True)
                        raise ValueError(
                            f"SHA256 mismatch for '{file_spec.filename}': "
                            f"expected {file_spec.sha256}, got {computed_hash}"
                        )

                part_path.replace(dest_path)
            except Exception as e:
                part_path.unlink(missing_ok=True)
                raise e

        try:
            total_files = len(defn.files)
            for idx, file_spec in enumerate(defn.files):
                dest = target_dir / file_spec.filename
                if dest.is_file() and dest.stat().st_size > 0:
                    if file_spec.sha256:
                        file_hash = compute_file_sha256(dest)
                        if file_hash == file_spec.sha256.lower():
                            continue
                        logger.warning("Existing file '%s' failed sha256 check, re-downloading", dest)
                        dest.unlink(missing_ok=True)
                    else:
                        continue
                logger.info("Downloading TTS model file '%s' from %s", file_spec.filename, file_spec.url)
                await asyncio.to_thread(_sync_download_file, file_spec, dest, idx, total_files)

                self._progress[defn.id] = 100
            self._invalidate_checksum_cache(defn.id)
            self._stats[defn.id] = {
                "progress_percent": 100,
                "downloaded_mb": defn.size_mb,
                "total_mb": defn.size_mb,
                "speed_kbps": 0.0,
                "eta_seconds": 0,
            }
            logger.info("Successfully downloaded and verified all files for model '%s'", defn.id)
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
