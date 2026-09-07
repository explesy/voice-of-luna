# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.9.0] - 2026-09-07

### Added
- **Modular Frontend Architecture (`app/static/js/`)**:
  - Extracted Web Audio scheduling, 16kHz resampling, and WAV encoding into `audio-player.js`.
  - Extracted terminal text markdown, citation link formatting, and speech sanitization into `terminal-formatter.js`.
  - Extracted Voice Activity Detection (VAD) state and control handlers into `vad.js`.
  - Updated `index.html` to load modular scripts with zero-build native browser support.

### Changed
- **Decomposed Backend Architecture Preparation**:
  - Prepared component boundaries for audio handling, speech pipeline, conversation services, and REST/WebSocket routing.
  - Ensured complete API and import backward compatibility across all test suites with 100% pass rate.

## [0.8.1] - 2026-09-07

### Fixed
- **Dynamic Project Version in Terminal Header**: Replaced the static hardcoded badge `v0.1` in `index.html` with dynamic template context rendering the actual project version (`v{{ version }}`), automatically reflecting updates across releases.

## [0.8.0] - 2026-09-07

### Added
- **Modern AudioWorklet Architecture (`pcm-recorder-processor.js`)**: Migrated client microphone capture from deprecated `ScriptProcessorNode` on the main UI thread to a dedicated `AudioWorkletProcessor` on the Web Audio rendering thread, eliminating UI stutter and audio frame drops with graceful fallback.
- **Binary WebSocket Audio Transport**: Implemented structured binary WebSocket audio frames (`[0x01][header_length][JSON metadata][raw audio bytes]`), cutting payload size by ~33% and eliminating Base64 encode/decode CPU overhead in both backend and frontend.
- **Persistent HTTP Connection Pooling for Whisper**: Integrated reusable `httpx.AsyncClient` singleton pool across `LocalWhisperTranscriber` and `WhisperServerManager`, reusing keep-alive TCP connections and eliminating per-request handshake latency.
- **Pipelined 1-Ahead TTS Pre-Synthesis**: Replaced sequential stop-and-wait TTS delivery with an asynchronous pipeline that pre-synthesizes sentence $n+1$ in the background while sentence $n$ is actively streamed and delivered to the browser.
- **Transport Latency Test Suite (`test_transport_latency.py`)**: Added automated unit and integration tests verifying binary frame encoding, singleton HTTP connection pooling, and pipelined pre-synthesis sequencing.

## [0.7.0] - 2026-09-07

### Added
- **Language-Neutral Core & Multi-Locale Architecture**: Introduced first-class `locale` support (`ru-RU`, `en-US`, `auto`) across `Conversation`, cookies (`voice_of_luna_locale`), REST `/api/settings`, `/api/locale`, and WebSocket messages (`set_locale`, `locale_updated`).
- **English Edge TTS Voices**: Registered high-quality Microsoft Edge Neural English voices: `Jenny (Neural · Edge)`, `Guy (Neural · Edge)`, and `Aria (Neural · Edge)` in speech synthesis.
- **Locale-Aware Voice Defaults**: Implemented `get_default_voice_for_locale(locale)` to automatically switch to the most appropriate high-quality voice when a language or locale changes (e.g. `Jenny` for `en-US`, `Milena` / `Svetlana` for `ru-RU`).
- **Localized Codex Instructions**: Added `get_base_instructions(locale)` configuring model responses and sources section (`Sources:` vs `Источники:`) according to the active locale.
- **UI Locale Selector**: Added terminal header `LANG:` selector chip for switching active session language with live client synchronization.
- **Locale-Aware STT**: Whisper transcription automatically adapts language hints to session locale (`en` or `ru`).

### Changed
- **Silero Isolation**: Silero offline neural TTS strictly enforces Cyrillic text (`supported_locales={"ru"}`) and no longer attempts to phonetically transliterate pure English sentences.
- **Smart Latin Transliteration**: Phonetical Latin transliteration is now strictly scoped to isolated loanwords inside primarily Cyrillic sentences.

## [0.6.2] - 2026-09-07

### Fixed
- **CI Stability on Linux**: Added cross-platform fallback voices for non-macOS environments so that UI voice selectors, templates, and Spanish language fallback tests pass reliably in headless Linux CI where macOS `say` is unavailable.
- **Safe Background Tasks**: Introduced `_safe_background_task()` wrapper to track async background jobs and prevent unretrieved exceptions from fire-and-forget `prewarm` tasks when Codex CLI is absent.
- **Runtime Dependency Packaging**: Moved `httpx` from `dev` dependencies into core `dependencies` in `pyproject.toml` so clean production installations no longer fail on runtime imports in `transcribe.py` and `whisper_server.py`.

## [0.6.1] - 2026-09-07

### Changed
- Comprehensive revamp of `README.md` for open-source GitHub release: interactive Mermaid architecture diagram, performance cross-matrix benchmarks summary, multi-tier TTS guide, and quickstart with `uv`.
- Adopted official project naming and visual identity: **Voice of Lúna** (featuring acute accent `ú`) across documentation, Cyber-Tarot headers, and `Lúna (Core)` plugin descriptor.

## [0.6.0] - 2026-09-07

### Added
- Enabled by default a visible `WARM: ON/OFF` control for Codex model warmup. It runs one short, hidden turn in the selected conversation thread for the selected model and reasoning setting, and never adds it to the visible conversation history.

## [0.5.0] - 2026-09-07

### Fixed
- Reclaimed local Codex app-server processes for disconnected conversations after 15 minutes of inactivity, while retaining active WebSocket conversations and short browser reconnects.

## [0.4.0] - 2026-09-07

### Added
- End-to-end voice-turn metrics now show the client VAD endpoint delay separately from server transcription, first model delta, first speech-segment wait, and first TTS synthesis time.
- The selected installed Silero model is preloaded while a conversation is prepared, removing model-load work from the first spoken reply without downloading optional models.

### Changed
- Streaming text reception and TTS now run independently through an ordered queue, so a slow synthesizer no longer holds back transcript deltas or model-stream processing.
- Reduced automatic VAD endpoint silence from 600 ms to 450 ms for more responsive natural dialogue.

## [0.3.2] - 2026-09-07

### Fixed
- Buffered early Codex app-server turn notifications until their stream listener is registered, preventing a fast response from being lost and leaving a conversation waiting indefinitely.
- Closed the short-lived Codex model-discovery client used by `/api/models`, preventing an orphaned local app-server process after a cold cache lookup.
- Kept selected voice settings scoped to the browser cookie and conversation instead of mutating a process-global voice for other conversations.

## [0.3.1] - 2026-09-07

### Added
- Comprehensive latency, throughput, and TTFA benchmark documentation across all 6 local Codex models (`gpt-6-astra`, `gpt-5.6-sol`, `gpt-5.6-terra`, `gpt-5.6-luna`, `gpt-5.5`, `gpt-5.4-mini`) and 3 voice synthesis engines (`Edge TTS`, `Silero TTS`, `macOS say`) in `docs/05 — Latency & Performance Benchmarks.md`.
- Performance cross-matrix analysis establishing optimal model and voice pairings for live conversation.

## [0.3.0] - 2026-09-07

### Added
- Tarot-themed Moon favicon icon set (`favicon.svg`, `favicon.ico`, `favicon-32x32.png`, `favicon-16x16.png`, `apple-touch-icon.png`, `android-chrome-192x192.png`, `android-chrome-512x512.png`, `site.webmanifest`).
- Dedicated root `/favicon.ico` endpoint in `main.py` for direct browser requests.
- High-contrast vector design with clear Tarot crescent profile face and glowing cyan Star, optimized for 16x16 browser tab legibility.
- Comprehensive visual and unit tests for favicon markup and endpoints.

## [0.2.2] - 2026-09-07

### Fixed
- Fixed issue where only the first sentence of assistant replies was synthesized to speech during streaming.
- Corrected `SOURCES_SPLIT_RE` in `main.py` so that markdown link lists (e.g. recommendations with `- [Title](...)`) are not falsely classified as an unvoiced sources section, allowing subsequent sentences and items to be synthesized normally.
- Fixed hardcoded audio chunk MIME type in WebSocket messages (`audio/mpeg` for Edge TTS MP3 and `audio/wav` for WAV).
- Fixed playback queue stall in `voice.js` (`playNextAudioChunkFallback`) by resetting `isAudioQueuePlaying` on chunk finish and properly chaining remaining chunks.

## [0.2.1] - 2026-09-07

### Fixed
- Fixed Silero TTS (`v4_ru.pt`) skipping and dropping Latin/English words (game titles, tech terms, brand names). Added rule-based and dictionary-driven phonetic transliteration (`transliterate_latin_for_speech`) before passing text to Cyrillic-only speech synthesis.

## [0.2.0] - 2026-09-07

### Added
- Free cloud neural speech synthesis via Microsoft Edge TTS (`Svetlana (Neural · Edge)` and `Dmitry (Neural · Edge)`).
- Local neural speech synthesis via Silero TTS v4 (`Ksenia`, `Baya`, `Aidar`) for high-quality offline speech.
- Grouped voice selection in UI: *Нейросеть (Edge Cloud · Бесплатно)*, *Локальная нейросеть (Silero · Offline)*, *Локальные macOS (Offline)*, and *Другие языки (Other)*.
- Automatic multi-tier fallback: Edge TTS -> Silero TTS -> macOS `say` ensuring uninterrupted speech playback.
- Dynamic audio format negotiation (`audio/mpeg` for MP3 and `audio/wav` for WAV).

### Fixed
- Fixed command-line flag interpretation crash in macOS `say` when text starts with hyphens or dashes (`--` argument delimiter).
- Fixed streaming sentence splitting stall on leading newlines and improved segment chunking on lists and abbreviations.

## [0.1.0] - 2026-09-06

### Added
- Local-first conversation bridge connecting browser UI to OpenAI Codex app-server via stdio JSON-RPC.
- Real-time voice interaction with WebSocket audio streaming and barge-in capability.
- Local transcription pipeline using local Whisper and external Whisper server fallback.
- Speech synthesis support for macOS native voices (`say`) and Microsoft Edge TTS.
- Extensible plugin architecture (`focus_sprint`, `spanish_buddy`) with capability isolation.
- Latency profiling and metrics for turns, speech, and transcription.
- Automatic session persistence with recovery for stale conversations.
- Strict localhost boundary ensuring no credentials or telemetry are exposed.
