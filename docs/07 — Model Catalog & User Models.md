# 07 — Model Catalog & User Models

Status: implemented in 0.44.0 (issue #14).

This document describes the unified model catalog metadata, the per-voice
language-capability vocabulary used by mixed-language work (issue #13), and the
configuration file that lets a user add a Piper or Whisper model without
patching code.

## Catalog sources

Model metadata comes from the runtime managers that already own download and
readiness behaviour:

| Kind | Source | Notes |
|---|---|---|
| `tts` | `backend/app/tts_manager.py` (`MODEL_CATALOG`) | Piper, Silero, Edge, macOS voices |
| `stt` | `backend/app/stt_manager.py` (streaming) and `tts_manager.py` (batch Whisper ggml) | streaming = sherpa-onnx bundles, batch = whisper.cpp ggml |
| `llm` | `backend/app/codex.py` (`model/list`) | tagged `live` or `preset` |

`GET /api/models/catalog` returns one normalized, read-only list plus
`groups` (by kind/engine/locale) and `status_counts`. It does not replace the
existing `/api/tts/models`, `/api/stt/models`, `/api/models` or `/api/voices`
endpoints, which stay backwards compatible.

## Lifecycle status vs install status

Two different concepts are kept separate:

- `catalog_status` — editorial lifecycle: `recommended | legacy | deprecated`.
  `deprecated` always carries a `deprecation_reason`.
- `status` — runtime install state: `ready | not_installed | downloading | error`
  (plus `preset` for LLM fallback entries).

Deprecated models stay selectable when explicitly chosen, but are excluded from
automatic defaults and hidden behind the `DEPRECATED` toggle in the UI. Legacy
models stay visible with a `[LEGACY]` marker.

### Stale-entry decisions

- **Silero v4** (`silero_v4_ru`) is `deprecated`, reason "superseded by Silero
  v5". Its voices are hidden by default and never chosen as a default.
- **Whisper small** (`whisper_small`) is `legacy`, not deprecated: it remains a
  useful low-RAM option. The recommended batch model is
  `whisper_large_v3_turbo`.
- No model files are deleted. Explicit selection and explicit
  `VOICE_OF_LUNA_WHISPER_MODEL=small` keep working.

## Per-voice language capability

Each voice exposes:

- `languages` — ISO-639-1 prefixes the voice can speak.
- `multilingual` — one voice can speak several languages.
- `code_switching` — `native | segment_only | none`:
  - `native`: the voice switches language inside one utterance (mixed text can
    be sent as-is);
  - `segment_only`: the voice speaks one language per segment, so mixed text
    must be split and routed to matched voices;
  - `none`: no cross-language capability.

Built-in single-language neural voices are honestly marked `segment_only`.
No shipped voice is currently marked `native`; a user-defined model may declare
`native` (it must also declare `multilingual: true`). The voice selector shows a
🌐 badge and a `NATIVE` filter; when the filter matches nothing it says so
explicitly instead of inventing capability metadata.

### How the router consumes this metadata (issue #13)

`code_switching` is not only a UI label; it changes the speech path:

- `native` — the selected primary voice receives the whole sentence as-is, with
  no segmentation. Mixed text is spoken by the one voice that can switch
  internally. This is the only mode where the catalog metadata suppresses
  routing.
- `segment_only` — a sentence is split into language runs by
  `speech_pipeline.segment_language_runs` and each run is synthesized with the
  best installed voice for its language. Splitting is conservative: only strong
  evidence (Cyrillic script, Spanish orthography, or fixed English/Spanish cue
  lists) switches the voice, while short shared words, digits and unknown
  scripts inherit the neighbouring run so a sentence cannot flap between voices.
- `none` — no cross-language capability. The run still resolves through the
  normal voice resolver, so it either finds a suitable voice or degrades to the
  primary voice (the same single-voice fallback as before).

Voice selection for embedded and explanation runs is local-first: Piper, then
Silero, then macOS system voices, then Edge. A catalog entry whose model file is
not installed is skipped, so a Russian Piper session with embedded English uses
an installed local English voice before it would ever reach the network-backed
Edge path.

The catalog still has no timbre/pair data, so "matched timbre pairs" are **not**
implemented; voice names are never used to guess gender or timbre.

## STT selection: batch vs live streaming

Two independent mechanisms exist:

- **Batch (selectable):** the model that produces the authoritative transcript
  for a completed recording. Selected per conversation via the `STT:` chip
  (`#stt-model-select`), persisted in the `voice_of_luna_stt_model` cookie, and
  synced over WebSocket `set_settings.stt_model`. `AUTO` uses the recommended
  installed model.
- **Live streaming (locale-routed):** interim text while speaking, chosen by
  locale in `stt_manager`. Not user-selectable.

A selected batch model must be used exactly. If it is not installed, the
selection is not silently swapped: the WebSocket/UI reports
`stt_model_fallback` with the requested id. HTTP transcription is only used when
the resident `whisper-server` is known to have loaded the selected file; a
different selection runs `whisper-cli` explicitly so the transcript can never be
attributed to the wrong model. If the selected file is missing, the turn fails
with a clear error.

## User-defined models

Set `VOICE_OF_LUNA_MODELS_CONFIG` to one UTF-8 JSON file. It is loaded at app
startup (and on explicit reload in tests); changing it requires a restart.

```json
{
  "schema_version": 1,
  "models": [
    {
      "id": "piper_de_thorsten",
      "kind": "tts",
      "engine": "piper",
      "name": "Piper Thorsten (German)",
      "locale": "de_DE",
      "size_mb": 65,
      "catalog_status": "recommended",
      "voices": [
        {
          "name": "Thorsten (Piper Neural · Offline)",
          "languages": ["de"],
          "multilingual": false,
          "code_switching": "segment_only"
        }
      ],
      "assets": [
        {"filename": "de_DE-thorsten-medium.onnx"},
        {"filename": "de_DE-thorsten-medium.onnx.json"}
      ]
    },
    {
      "id": "whisper_de_custom",
      "kind": "stt",
      "engine": "whisper",
      "name": "Whisper German custom",
      "locale": "multi",
      "languages": ["de"],
      "catalog_status": "recommended",
      "assets": [
        {
          "filename": "ggml-de-custom.bin",
          "url": "https://example.invalid/ggml-de-custom.bin",
          "sha256": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
        }
      ]
    }
  ]
}
```

### Assets

- A downloadable asset needs `url` (must be `https://`) **and** a 64-hex
  `sha256`. Downloads reuse the existing model downloader and status endpoints.
- An asset with only `filename` is **local-only**: the app never downloads it.
  Place the file in a model search directory (`VOICE_OF_LUNA_MODELS_DIR`,
  `backend/models`, or `~/.cache/voice-of-luna/models`). Until then the model is
  reported as not installed with a manual-install hint.
- Filenames must be plain basenames (no `/`, `\`, `..`) and must not collide
  (case-insensitively) with a built-in asset.

### Validation rules

- Only `tts`/`piper` and `stt`/`whisper` entries are accepted; user entries can
  never override a built-in id or voice name.
- `id` must match `^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$` and be unique.
- Piper entries declare exactly one voice and exactly one `<stem>.onnx` plus its
  matching `<stem>.onnx.json`.
- Whisper entries require `ggml-*.bin` assets and a non-empty `languages` list.
- A voice with more than one language must set `multilingual: true`;
  `code_switching: native` also requires `multilingual: true`.
- `catalog_status: deprecated` requires a `deprecation_reason`.
- Invalid entries are skipped entirely and reported (sanitized) via
  `tts_model_manager.user_model_errors`, `/api/stt/models` `errors`, and the
  home page context. Valid entries in the same file still load.

### Reload semantics

`TTSModelManager.register_user_models()` is idempotent: it removes the previous
user snapshot and re-registers, so startup/test reloads never accumulate
duplicates. Built-in entries are never mutated.
