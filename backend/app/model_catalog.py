"""Neutral model-catalog vocabulary, per-voice capability metadata, and
validation of user-supplied model entries.

This module is intentionally pure data/validation logic. It must never import
the runtime managers (``tts_manager``, ``stt_manager``, ``speak``, ``codex``)
so that it stays free of import cycles and can be unit-tested without network
or model files.

User-defined models are declared in a single JSON file referenced by
``VOICE_OF_LUNA_MODELS_CONFIG``. See ``docs/07 — Model Catalog & User Models.md``
for the schema.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

# --- catalog lifecycle status -------------------------------------------------

CATALOG_STATUS_RECOMMENDED = "recommended"
CATALOG_STATUS_LEGACY = "legacy"
CATALOG_STATUS_DEPRECATED = "deprecated"
CATALOG_STATUSES = (
    CATALOG_STATUS_RECOMMENDED,
    CATALOG_STATUS_LEGACY,
    CATALOG_STATUS_DEPRECATED,
)

# --- code-switching capability -----------------------------------------------

CODE_SWITCHING_NATIVE = "native"
CODE_SWITCHING_SEGMENT_ONLY = "segment_only"
CODE_SWITCHING_NONE = "none"
CODE_SWITCHING_MODES = (
    CODE_SWITCHING_NATIVE,
    CODE_SWITCHING_SEGMENT_ONLY,
    CODE_SWITCHING_NONE,
)

# --- model kinds --------------------------------------------------------------

MODEL_KIND_TTS = "tts"
MODEL_KIND_STT = "stt"
MODEL_KIND_LLM = "llm"

USER_CONFIG_ENV = "VOICE_OF_LUNA_MODELS_CONFIG"
USER_CONFIG_SCHEMA_VERSION = 1

_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_FORBIDDEN_FILENAME_CHARS = ("/", "\\", "..", "\x00")


def normalize_language(value: str) -> str:
    """Return the primary ISO-639-1 language subtag for a locale-like string."""

    token = (value or "").strip().lower().replace("_", "-")
    return token.split("-")[0]


def normalize_languages(values: Iterable[str] | None) -> tuple[str, ...]:
    """Normalize a language collection, preserving order and dropping blanks."""

    seen: list[str] = []
    for value in values or ():
        prefix = normalize_language(str(value))
        if prefix and prefix not in seen:
            seen.append(prefix)
    return tuple(seen)


def languages_for_locale(locale: str) -> tuple[str, ...]:
    """Derive language prefixes from a locale tag.

    Sentinel locales used by the catalog (``multi``/``system``/``auto``) have no
    single language and intentionally return an empty tuple.
    """

    tokens = [
        normalize_language(part)
        for part in re.split(r"[,/|]", locale or "")
        if part and part.strip()
    ]
    return tuple(token for token in tokens if token and token not in {"multi", "system", "auto"})


@dataclass(frozen=True)
class VoiceCapability:
    """Per-voice language capability exposed to the UI and to #13's router."""

    languages: tuple[str, ...] = ()
    multilingual: bool = False
    code_switching: str = CODE_SWITCHING_NONE
    status: str = CATALOG_STATUS_RECOMMENDED
    deprecation_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "languages": list(self.languages),
            "multilingual": self.multilingual,
            "code_switching": self.code_switching,
            "catalog_status": self.status,
            "deprecation_reason": self.deprecation_reason,
        }


# Built-in voice capability table. Single-language neural voices are honestly
# marked ``segment_only``: they can speak their own language, so mixed-language
# input must be split and routed per segment. No built-in voice is currently
# known to switch language natively inside one utterance, so none is marked
# ``native`` here; user-defined models may declare ``native`` explicitly.
VOICE_CAPABILITIES: dict[str, VoiceCapability] = {
    # Piper (offline, single language per model)
    "Dmitri (Piper Neural · Offline)": VoiceCapability(("ru",), False, CODE_SWITCHING_SEGMENT_ONLY),
    "Irina (Piper Neural · Offline)": VoiceCapability(("ru",), False, CODE_SWITCHING_SEGMENT_ONLY),
    "Denis (Piper Neural · Offline)": VoiceCapability(("ru",), False, CODE_SWITCHING_SEGMENT_ONLY),
    "Ruslan (Piper Neural · Offline)": VoiceCapability(("ru",), False, CODE_SWITCHING_SEGMENT_ONLY),
    "Lessac (Piper Neural · Offline)": VoiceCapability(("en",), False, CODE_SWITCHING_SEGMENT_ONLY),
    # Silero v4 is superseded by v5
    "Ksenia (Silero Neural · Offline)": VoiceCapability(
        ("ru",), False, CODE_SWITCHING_SEGMENT_ONLY, CATALOG_STATUS_DEPRECATED, "Superseded by Silero v5"
    ),
    "Baya (Silero Neural · Offline)": VoiceCapability(
        ("ru",), False, CODE_SWITCHING_SEGMENT_ONLY, CATALOG_STATUS_DEPRECATED, "Superseded by Silero v5"
    ),
    "Aidar (Silero Neural · Offline)": VoiceCapability(
        ("ru",), False, CODE_SWITCHING_SEGMENT_ONLY, CATALOG_STATUS_DEPRECATED, "Superseded by Silero v5"
    ),
    "Eugene (Silero Neural · Offline)": VoiceCapability(
        ("ru",), False, CODE_SWITCHING_SEGMENT_ONLY, CATALOG_STATUS_DEPRECATED, "Superseded by Silero v5"
    ),
    "Raya (Silero Neural · Offline)": VoiceCapability(
        ("ru",), False, CODE_SWITCHING_SEGMENT_ONLY, CATALOG_STATUS_DEPRECATED, "Superseded by Silero v5"
    ),
    "Ksenia v5 (Silero Neural · Offline)": VoiceCapability(("ru",), False, CODE_SWITCHING_SEGMENT_ONLY),
    "Baya v5 (Silero Neural · Offline)": VoiceCapability(("ru",), False, CODE_SWITCHING_SEGMENT_ONLY),
    "Aidar v5 (Silero Neural · Offline)": VoiceCapability(("ru",), False, CODE_SWITCHING_SEGMENT_ONLY),
    "Eugene v5 (Silero Neural · Offline)": VoiceCapability(("ru",), False, CODE_SWITCHING_SEGMENT_ONLY),
    "Raya v5 (Silero Neural · Offline)": VoiceCapability(("ru",), False, CODE_SWITCHING_SEGMENT_ONLY),
    # Edge neural voices (single language per voice id)
    "Svetlana (Neural · Edge)": VoiceCapability(("ru",), False, CODE_SWITCHING_SEGMENT_ONLY),
    "Dmitry (Neural · Edge)": VoiceCapability(("ru",), False, CODE_SWITCHING_SEGMENT_ONLY),
    "Jenny (Neural · Edge)": VoiceCapability(("en",), False, CODE_SWITCHING_SEGMENT_ONLY),
    "Guy (Neural · Edge)": VoiceCapability(("en",), False, CODE_SWITCHING_SEGMENT_ONLY),
    "Aria (Neural · Edge)": VoiceCapability(("en",), False, CODE_SWITCHING_SEGMENT_ONLY),
    "Elvira (Neural · Edge)": VoiceCapability(("es",), False, CODE_SWITCHING_SEGMENT_ONLY),
    "Alvaro (Neural · Edge)": VoiceCapability(("es",), False, CODE_SWITCHING_SEGMENT_ONLY),
    # macOS system voices (unknown single language until listed at runtime)
    "Milena": VoiceCapability(("ru",), False, CODE_SWITCHING_SEGMENT_ONLY),
}

VOICE_CAPABILITIES_CASEFOLDED = {key.casefold(): value for key, value in VOICE_CAPABILITIES.items()}


def voice_capability(
    name: str,
    locale: str = "",
    *,
    status: str | None = None,
    deprecation_reason: str | None = None,
) -> VoiceCapability:
    """Return the declared capability for a voice, falling back to its locale."""

    known = VOICE_CAPABILITIES.get(name) or VOICE_CAPABILITIES_CASEFOLDED.get((name or "").casefold())
    if known is not None:
        if status is None and deprecation_reason is None:
            return known
        return VoiceCapability(
            languages=known.languages,
            multilingual=known.multilingual,
            code_switching=known.code_switching,
            status=status or known.status,
            deprecation_reason=deprecation_reason if deprecation_reason is not None else known.deprecation_reason,
        )
    return VoiceCapability(
        languages=languages_for_locale(locale),
        multilingual=False,
        code_switching=CODE_SWITCHING_SEGMENT_ONLY if languages_for_locale(locale) else CODE_SWITCHING_NONE,
        status=status or CATALOG_STATUS_RECOMMENDED,
        deprecation_reason=deprecation_reason or "",
    )


def is_deprecated(status: str) -> bool:
    return status == CATALOG_STATUS_DEPRECATED


def is_hidden_by_default(status: str) -> bool:
    """Deprecated entries are hidden until the user opts in."""

    return is_deprecated(status)


# --- user model configuration -------------------------------------------------


@dataclass(frozen=True)
class UserModelAsset:
    filename: str
    url: str = ""
    sha256: str = ""
    size_bytes: int = 0

    @property
    def downloadable(self) -> bool:
        return bool(self.url)


@dataclass(frozen=True)
class UserVoiceSpec:
    name: str
    languages: tuple[str, ...]
    multilingual: bool
    code_switching: str

    def to_capability(self) -> VoiceCapability:
        return VoiceCapability(
            languages=self.languages,
            multilingual=self.multilingual,
            code_switching=self.code_switching,
        )


@dataclass(frozen=True)
class UserModelSpec:
    id: str
    kind: str
    engine: str
    name: str
    locale: str
    description: str
    size_mb: float
    catalog_status: str
    deprecation_reason: str
    languages: tuple[str, ...]
    multilingual: bool
    code_switching: str
    voices: tuple[UserVoiceSpec, ...] = ()
    assets: tuple[UserModelAsset, ...] = ()
    model_key: str = ""


@dataclass
class UserCatalogLoad:
    models: list[UserModelSpec] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    path: str | None = None


def _validate_filename(value: Any) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return "filename must be a non-empty string"
    name = value.strip()
    if any(token in name for token in _FORBIDDEN_FILENAME_CHARS):
        return f"unsafe filename '{name}' (must be a plain basename)"
    if Path(name).name != name:
        return f"unsafe filename '{name}' (must be a plain basename)"
    return None


def _validate_asset(raw: Any) -> tuple[UserModelAsset | None, str | None]:
    if not isinstance(raw, dict):
        return None, "each asset must be an object"
    filename = raw.get("filename")
    problem = _validate_filename(filename)
    if problem:
        return None, problem
    url = raw.get("url")
    sha256 = raw.get("sha256")
    if url in (None, ""):
        if sha256:
            return None, f"'{filename}': sha256 has no meaning without a download url"
        return UserModelAsset(filename=filename.strip()), None
    if not isinstance(url, str) or not url.startswith("https://"):
        return None, f"'{filename}': download url must use https"
    if not isinstance(sha256, str) or not _SHA256_RE.match(sha256.lower()):
        return None, f"'{filename}': downloadable assets require a 64-hex sha256"
    size_bytes = raw.get("size_bytes") or 0
    try:
        size_int = int(size_bytes)
    except (TypeError, ValueError):
        return None, f"'{filename}': size_bytes must be an integer"
    return UserModelAsset(filename=filename.strip(), url=url, sha256=sha256.lower(), size_bytes=size_int), None


def _string_list(value: Any) -> list[str] | None:
    """Return a list of strings for a JSON array, or None for any other type."""

    if isinstance(value, str) or not isinstance(value, (list, tuple)):
        return None
    if not all(isinstance(item, str) for item in value):
        return None
    return list(value)


def _validate_voice(raw: Any, index: int) -> tuple[UserVoiceSpec | None, str | None]:
    if not isinstance(raw, dict):
        return None, f"voice #{index + 1} must be an object"
    name = raw.get("name")
    if not isinstance(name, str) or not name.strip():
        return None, f"voice #{index + 1} requires a non-empty name"
    raw_languages = _string_list(raw.get("languages"))
    if raw_languages is None:
        return None, f"voice '{name}': languages must be a list of strings"
    languages = normalize_languages(raw_languages)
    if not languages:
        return None, f"voice '{name}': languages must be a non-empty list"
    multilingual = raw.get("multilingual", False)
    if not isinstance(multilingual, bool):
        return None, f"voice '{name}': multilingual must be a boolean"
    code_switching = raw.get("code_switching", CODE_SWITCHING_NONE)
    if not isinstance(code_switching, str) or code_switching not in CODE_SWITCHING_MODES:
        return None, f"voice '{name}': code_switching must be one of {', '.join(CODE_SWITCHING_MODES)}"
    if len(languages) > 1 and not multilingual:
        return None, f"voice '{name}': multiple languages require multilingual=true"
    if code_switching == CODE_SWITCHING_NATIVE and not multilingual:
        return None, f"voice '{name}': code_switching 'native' requires multilingual=true"
    return UserVoiceSpec(name=name.strip(), languages=languages, multilingual=multilingual, code_switching=code_switching), None


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _parse_model_entry(
    raw: Any,
    index: int,
    *,
    reserved_ids: set[str],
    reserved_filenames: set[str],
    reserved_voices: set[str],
    seen_ids: set[str],
    seen_filenames: set[str],
    seen_voices: set[str],
) -> tuple[UserModelSpec | None, list[str]]:
    label = f"models[{index}]"
    errors: list[str] = []
    if not isinstance(raw, dict):
        return None, [f"{label}: entry must be an object"]

    model_id = raw.get("id")
    if not isinstance(model_id, str) or not _ID_RE.match(model_id):
        return None, [f"{label}: id must match {_ID_RE.pattern}"]
    folded_id = model_id.casefold()
    if folded_id in reserved_ids or folded_id in seen_ids:
        return None, [f"{label}: id '{model_id}' already exists in the catalog"]
    label = f"'{model_id}'"

    kind = raw.get("kind")
    engine = raw.get("engine")
    if not isinstance(kind, str) or not isinstance(engine, str) or (kind, engine) not in {
        (MODEL_KIND_TTS, "piper"),
        (MODEL_KIND_STT, "whisper"),
    }:
        return None, [f"{label}: only tts/piper and stt/whisper entries are supported"]

    name = raw.get("name")
    if not isinstance(name, str) or not name.strip():
        return None, [f"{label}: name is required"]
    locale = raw.get("locale")
    if not isinstance(locale, str) or not locale.strip():
        return None, [f"{label}: locale is required"]

    catalog_status = raw.get("catalog_status", CATALOG_STATUS_RECOMMENDED)
    if not isinstance(catalog_status, str) or catalog_status not in CATALOG_STATUSES:
        return None, [f"{label}: catalog_status must be one of {', '.join(CATALOG_STATUSES)}"]
    deprecation_reason = str(raw.get("deprecation_reason") or "").strip()
    if catalog_status == CATALOG_STATUS_DEPRECATED and not deprecation_reason:
        errors.append(f"{label}: deprecated entries require a deprecation_reason")

    raw_assets = raw.get("assets") or raw.get("files")
    if not isinstance(raw_assets, list) or not raw_assets:
        return None, [f"{label}: at least one asset is required"]
    assets: list[UserModelAsset] = []
    entry_filenames: set[str] = set()
    for asset_index, raw_asset in enumerate(raw_assets):
        asset, problem = _validate_asset(raw_asset)
        if problem:
            errors.append(f"{label} asset #{asset_index + 1}: {problem}")
            continue
        assert asset is not None
        folded = asset.filename.casefold()
        if folded in reserved_filenames or folded in seen_filenames or folded in entry_filenames:
            errors.append(f"{label}: asset filename '{asset.filename}' collides with an existing model file")
            continue
        entry_filenames.add(folded)
        assets.append(asset)
    if not assets:
        return None, errors or [f"{label}: no valid assets"]

    voices: list[UserVoiceSpec] = []
    entry_voices: set[str] = set()
    multilingual = False
    code_switching = CODE_SWITCHING_NONE
    languages: tuple[str, ...] = ()
    model_key = ""
    if kind == MODEL_KIND_TTS:
        raw_voices = raw.get("voices")
        if not isinstance(raw_voices, list) or len(raw_voices) != 1:
            errors.append(f"{label}: Piper models must declare exactly one voice")
            return None, errors
        voice, problem = _validate_voice(raw_voices[0], 0)
        if problem:
            errors.append(f"{label}: {problem}")
            return None, errors
        assert voice is not None
        folded_voice = voice.name.casefold()
        if folded_voice in reserved_voices or folded_voice in seen_voices:
            errors.append(f"{label}: voice name '{voice.name}' collides with an existing voice")
            return None, errors
        entry_voices.add(folded_voice)
        voices = [voice]
        languages = voice.languages
        multilingual = voice.multilingual
        code_switching = voice.code_switching

        onnx = [a for a in assets if a.filename.endswith(".onnx")]
        config = [a for a in assets if a.filename.endswith(".onnx.json")]
        if len(onnx) != 1 or len(config) != 1 or onnx[0].filename[: -len(".onnx")] != config[0].filename[: -len(".onnx.json")]:
            errors.append(f"{label}: Piper assets must be exactly one '<stem>.onnx' and its matching '<stem>.onnx.json'")
            return None, errors
        model_key = onnx[0].filename[: -len(".onnx")]
    else:
        bad = [a.filename for a in assets if not re.match(r"^ggml-.+\.bin$", a.filename)]
        if bad:
            errors.append(f"{label}: Whisper assets must be named ggml-*.bin (got {', '.join(bad)})")
            return None, errors
        raw_languages = _string_list(raw.get("languages"))
        if raw_languages is None:
            errors.append(f"{label}: languages must be a list of strings")
            return None, errors
        languages = normalize_languages(raw_languages)
        if not languages:
            errors.append(f"{label}: Whisper entries require a non-empty languages list")
            return None, errors
        multilingual = raw.get("multilingual", False)
        if not isinstance(multilingual, bool):
            errors.append(f"{label}: multilingual must be a boolean")
            return None, errors
        code_switching = raw.get("code_switching", CODE_SWITCHING_NONE)
        if not isinstance(code_switching, str) or code_switching not in CODE_SWITCHING_MODES:
            errors.append(f"{label}: code_switching must be one of {', '.join(CODE_SWITCHING_MODES)}")
            return None, errors
        if len(languages) > 1 and not multilingual:
            errors.append(f"{label}: multiple languages require multilingual=true")
            return None, errors
        if code_switching == CODE_SWITCHING_NATIVE and not multilingual:
            errors.append(f"{label}: code_switching 'native' requires multilingual=true")
            return None, errors

    if errors:
        # Never partially accept an entry; reservations are committed only below.
        return None, errors

    size_mb = raw.get("size_mb")
    try:
        size_float = float(size_mb) if size_mb is not None else sum(a.size_bytes for a in assets) / (1024 * 1024)
    except (TypeError, ValueError):
        size_float = sum(a.size_bytes for a in assets) / (1024 * 1024)
    if size_float <= 0:
        size_float = sum(a.size_bytes for a in assets) / (1024 * 1024)

    spec = UserModelSpec(
        id=model_id,
        kind=kind,
        engine=engine,
        name=name.strip(),
        locale=locale.strip(),
        description=str(raw.get("description") or f"User-defined {engine} model"),
        size_mb=round(size_float, 1),
        catalog_status=catalog_status,
        deprecation_reason=deprecation_reason,
        languages=languages,
        multilingual=multilingual,
        code_switching=code_switching,
        voices=tuple(voices),
        assets=tuple(assets),
        model_key=model_key,
    )
    # Commit identity reservations only now that the whole entry is accepted.
    seen_ids.add(folded_id)
    seen_filenames.update(entry_filenames)
    seen_voices.update(entry_voices)
    return spec, errors


def load_user_model_config(
    path: str | Path | None = None,
    *,
    reserved_ids: Iterable[str] = (),
    reserved_filenames: Iterable[str] = (),
    reserved_voices: Iterable[str] = (),
) -> UserCatalogLoad:
    """Parse and validate the optional user model configuration file.

    Returns only valid specs plus a list of human-readable, sanitized errors.
    Invalid entries never disable built-ins and never trigger downloads.
    """

    configured = str(path or os.environ.get(USER_CONFIG_ENV) or "").strip()
    if not configured:
        return UserCatalogLoad(models=[], errors=[], path=None)
    config_path = Path(configured).expanduser()
    result = UserCatalogLoad(path=str(config_path))
    if not config_path.is_file():
        result.errors.append(f"user model config not found: {config_path}")
        return result
    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        result.errors.append(f"user model config is not valid JSON: {exc}")
        return result
    if not isinstance(data, dict):
        result.errors.append("user model config must be a JSON object")
        return result
    if _safe_int(data.get("schema_version"), -1) != USER_CONFIG_SCHEMA_VERSION:
        result.errors.append(f"user model config requires schema_version={USER_CONFIG_SCHEMA_VERSION}")
        return result
    raw_models = data.get("models")
    if not isinstance(raw_models, list):
        result.errors.append("user model config requires a 'models' array")
        return result

    reserved_id_set = {str(item).casefold() for item in reserved_ids}
    reserved_file_set = {str(item).casefold() for item in reserved_filenames}
    reserved_voice_set = {str(item).casefold() for item in reserved_voices}
    seen_ids: set[str] = set()
    seen_filenames: set[str] = set()
    seen_voices: set[str] = set()
    for index, raw in enumerate(raw_models):
        spec, errors = _parse_model_entry(
            raw,
            index,
            reserved_ids=reserved_id_set,
            reserved_filenames=reserved_file_set,
            reserved_voices=reserved_voice_set,
            seen_ids=seen_ids,
            seen_filenames=seen_filenames,
            seen_voices=seen_voices,
        )
        result.errors.extend(errors)
        # An entry with any problem is skipped entirely; it is never partially
        # accepted, and built-ins remain unaffected.
        if spec is not None and not errors:
            result.models.append(spec)
    return result


def group_catalog(models: list[dict[str, Any]]) -> dict[str, Any]:
    """Group normalized catalog entries by kind/engine/locale for the UI."""

    groups: dict[str, dict[str, Any]] = {}
    status_counts: dict[str, int] = {status: 0 for status in CATALOG_STATUSES}
    for model in models:
        status = str(model.get("catalog_status") or CATALOG_STATUS_RECOMMENDED)
        status_counts[status] = status_counts.get(status, 0) + 1
        kind = str(model.get("kind") or "other")
        engine = str(model.get("engine") or "other")
        locale = str(model.get("locale") or "n/a")
        key = f"{kind}/{engine}/{locale}"
        group = groups.setdefault(
            key,
            {"key": key, "kind": kind, "engine": engine, "locale": locale, "models": []},
        )
        group["models"].append(model)
    ordered = sorted(
        groups.values(),
        key=lambda group: (
            group["kind"],
            group["engine"],
            group["locale"] == "multi",
            group["locale"],
        ),
    )
    return {"groups": ordered, "status_counts": status_counts}


__all__ = [
    "CATALOG_STATUS_DEPRECATED",
    "CATALOG_STATUS_LEGACY",
    "CATALOG_STATUS_RECOMMENDED",
    "CATALOG_STATUSES",
    "CODE_SWITCHING_MODES",
    "CODE_SWITCHING_NATIVE",
    "CODE_SWITCHING_NONE",
    "CODE_SWITCHING_SEGMENT_ONLY",
    "MODEL_KIND_LLM",
    "MODEL_KIND_STT",
    "MODEL_KIND_TTS",
    "USER_CONFIG_ENV",
    "UserCatalogLoad",
    "UserModelAsset",
    "UserModelSpec",
    "UserVoiceSpec",
    "VoiceCapability",
    "VOICE_CAPABILITIES",
    "group_catalog",
    "is_deprecated",
    "is_hidden_by_default",
    "languages_for_locale",
    "load_user_model_config",
    "normalize_language",
    "normalize_languages",
    "voice_capability",
]
