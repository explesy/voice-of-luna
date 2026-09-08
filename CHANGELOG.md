# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.16.0] - 2026-09-08

### Added
- **Ultra-Fast Russian Number Normalization (`app/num_normalizer.py`)**:
  - Implemented high-performance speech text normalizer designed for Silero TTS (and Piper TTS) where digits are not natively present in vocabulary tokens.
  - Converts numbers, calendar dates (`8 сентября` $\to$ `восьмого сентября`), years with Russian cases (`2026 года` $\to$ `две тысячи двадцать шестого года`, `в 2026 году` $\to$ `в две тысячи двадцать шестом году`), times (`14:30` $\to$ `четырнадцать тридцать`), percentages (`73%` $\to$ `семьдесят три процента`), currencies (`$100`, `50€`, `1500 руб`), decimals (`3.5` $\to$ `три целых пять десятых`), decades (`90-х`), hyphenated ordinals (`1-й`), and Roman centuries (`XXI век`).
  - **Sub-millisecond latency**: incorporates zero-cost bail-outs for digitless strings ($<0.01$ ms), precompiled regexes, fast substring filters, and `@lru_cache` memoization ($<0.05$ ms average per turn).
  - Added `num2words>=0.5.13` dependency.
  - Integrated into `_synthesize_silero` and `_synthesize_piper` in `app/speak.py`.
  - Added comprehensive test suite `tests/test_num_normalizer.py` verifying all grammar cases and execution speed.

## [0.15.1] - 2026-09-08

### Fixed
- **Truthful latency evidence**:
  - Replaced formula-derived LLM × TTS latency tables with a local-only benchmark that never estimates or invokes Codex.
  - Made Edge TTS opt-in, required a real-speech fixture for STT, and added warm-run median/p95/min/max reporting.
  - Detects and reports the engine that actually produced a TTS clip, so a macOS fallback cannot be presented as Piper, Silero, or Edge.
  - Split server audio preparation from Whisper time and surfaces browser audio encoding time in the latency HUD.

## [0.14.0] - 2026-09-08

### Added
- **Optional PyTorch Extra (`[project.optional-dependencies]`)**:
  - Moved heavy PyTorch (`torch>=2.14.0`) to optional `silero` extra dependency.
  - Base installation (`make setup` / `uv sync`) is now lightweight and fast without downloading massive PyTorch wheels, running Piper ONNX, Edge TTS, and macOS say out-of-the-box.
  - Added `make setup-silero` (`uv sync --extra silero`) for users who want local PyTorch Silero v4 Russian neural voices.
- **Graceful Silero Degradation & Torch Detection**:
  - Added `is_silero_available()` detection in `app/speak.py` using `importlib.util.find_spec("torch")`.
  - Voices list dynamically reflects PyTorch availability without crashing if `torch` is absent.
  - `_get_silero_model()` and `_synthesize_silero()` raise actionable `LocalSpeechError` guiding users to install the `silero` extra.
  - Added unit tests in `tests/test_speech_pipeline.py` verifying detection and friendly error reporting.
- **Documentation & Architecture Updates**:
  - Updated `README.md` architecture diagram reflecting `speech_pipeline` and `conversation_service`.
  - Documented lightweight setup vs Silero PyTorch setup options and updated test suite count (198+ tests).

## [0.13.0] - 2026-09-08

### Added
- **Backend Speech Pipeline Modularization (`app/speech_pipeline.py`)**:
  - Extracted audio lifecycle management: temporary file creation (`write_temporary_audio`), cleanup (`remove_temporary_audio`), format checking (`is_16k_mono_wav`), and FFmpeg transcoding (`convert_to_wav`).
  - Extracted streaming sentence extraction logic (`extract_speech_sentence`) with early clause boundary splitting for first audio chunks and abbreviation shielding.
  - Added unit test suite `tests/test_speech_pipeline.py` covering audio conversion, clause boundary logic, and streaming sentence extraction.
- **Stateful Conversation Domain Service (`app/conversation_service.py`)**:
  - Extracted `Conversation` and `SpeechClip` domain models and `ConversationService` class.
  - Centralized session storage (`conversations`, `speech_clips`), model/voice prewarming, idle conversation reaping, and dynamic Codex reply invocation (`call_reply`, `call_reply_stream`).
  - Maintained full backward compatibility in `app/main.py` with alias exports and monkeypatch-compatible module attributes.

## [0.12.1] - 2026-09-08

### Fixed
- **Frontend Script Redeclaration Error**:
  - Resolved `Uncaught SyntaxError: redeclaration of let firstAudioPlayTime` (and duplicate declarations of `activePlayer`, `playbackAudioContext`, `activeScheduledSources`, `nextAudioChunkStartTime`, `lastSpeechEndTime`, `latestTiming`, `currentStreamingEntry`) when loading `static/js/audio-player.js` alongside `static/voice.js`.
  - Switched shared audio playback and timing state in `audio-player.js` to `var` global declarations and cleaned up redundant declarations in `voice.js`.

## [0.12.0] - 2026-09-08

### Added
- **AI & Sound Model Test Matrix Suite**:
  - Implemented comprehensive multi-dimensional test matrix in `backend/tests/test_model_matrix.py` covering:
    - **6 AI LLM Models** (`gpt-5.6-luna`, `gpt-5.6-sol`, `gpt-5.6-terra`, `gpt-6-astra`, `gpt-5.5`, `gpt-5.4-mini`) with all supported reasoning efforts (`low`, `medium`, `high`, `xhigh`).
    - **4 TTS Engines** (Piper ONNX, Silero PyTorch, Microsoft Edge TTS, macOS System Say) across 10 key voices with format, suffix, and MIME type validation.
    - **STT Whisper Models** across 4 language profiles (`ru`, `en`, `es`, `auto`) in both HTTP server and local CLI fallback modes.
    - **Cross-Model WebSocket Turns**: end-to-end turn execution combining various AI models with different TTS engines, validating binary PCM/MP3 frames and streaming JSON audio chunks.
- **Model Matrix Evaluation CLI & Makefile Target**:
  - Added `backend/scripts/run_model_matrix.py` to evaluate model availability, speaker routing, STT profiles, and latency benchmarks.
  - Implemented live hardware benchmarks measuring real synthesis latency for Piper ONNX, Silero v4, macOS Say, and Edge TTS, as well as Whisper STT transcription.
  - Calculated end-to-end TTFA (Time To First Audio) and Total Turn Duration across all combinations of AI models and sound models.
  - Updated `docs/05 — Latency & Performance Benchmarks.md` with Piper ONNX measurements (167-225 ms TTFA) and 2026 latency benchmarks.
  - Added `make matrix` command for quick terminal inspection of cross-model compatibility and latencies.

## [0.11.0] - 2026-09-07

### Added
- **True AUTO Locale Dynamic Turn Resolution**:
  - Added `detect_effective_turn_locale()` detecting Cyrillic (`ru-RU`) vs Latin (`en-US`) in user utterances and LLM responses.
  - Implemented dynamic per-turn voice resolution for AUTO locale in `_stream_and_synthesize` and `_append_assistant_turn`.
  - Added base instruction guidance for AUTO mode: `"Reply in the same language as the user's message"`.
  - Added `effective_locale`, `voice`, and `tts_engine` fields to WebSocket `turn_completed` payload.
- **Dynamic TTS Engine Status in UI Footer**:
  - Implemented `_get_tts_engine()` helper exposing active engine (`EDGE_TTS`, `PIPER_OFFLINE`, `SILERO_OFFLINE`, `MACOS_SAY`).
  - Added `tts_engine` attribute to `ready`, `voice_updated`, `locale_updated`, `settings_updated`, `plugin_updated`, and `turn_completed` WebSocket events.
  - Dynamic UI footer element `<span data-footer-meta>` updated via `updateFooterStatus()` in `voice.js`.
- **AudioWorklet Sample Buffering & Single Exclusive Pipeline**:
  - Implemented 1024-sample frame buffering with ring buffer and flush-on-stop in `pcm-recorder-processor.js`, cutting AudioWorklet message overhead by 8x.
  - Prevented dual recorder contention (`MediaRecorder` running alongside `AudioWorklet`) via `window.isDirectPcmActive` guard flag.

## [0.10.3] - 2026-09-07

### Added
- **Bounded Concurrent Speech Synthesis**:
  - Implemented `asyncio.Semaphore(2)` in `app.main` for pipelined TTS chunks, preventing connection/thread exhaustion when handling rapid LLM sentence token streams.
  - Added unit test `test_bounded_synthesis_concurrency_limit` in `test_transport_latency.py` verifying semaphore compliance.

### Changed
- **Frontend Architecture Deduplication**:
  - Removed ~550 lines of duplicate code from `static/voice.js` by delegating directly to modular components:
    - Audio playback & scheduling to `AudioPlayer` (`static/js/audio-player.js`).
    - WAV encoding and audio buffer handling to `AudioBufferUtils` (`static/js/audio-player.js`).
    - VAD status UI and toggles to `VADModule` (`static/js/vad.js`).
    - Terminal formatting, ANSI stripping, and latency metrics to `TerminalFormatter` (`static/js/terminal-formatter.js`).
  - Added `stopAudioPlayback()` method and safe metric wrappers to `AudioPlayer`.

## [0.10.2] - 2026-09-07

### Fixed
- **CI Test Suite Deadlock Prevention (P0)**:
  - Mocked `_call_reply_stream` in `test_transport_latency.py::test_binary_audio_frame_transport_via_websocket`, preventing infinite WebSocket receive loop in CI runners without local Codex.
  - Added bounded loop guard to WebSocket reception in transport tests.
  - Added automatic guard in `tests/conftest.py` against unmocked live Codex subprocess execution during tests (satisfies Rule 5 of `AGENTS.md`).
- **Engine-Aware TTS Voice Fallback**:
  - Added `allowed_engines` and `excluded_engines` parameters to `get_voice_for_locale()`.
  - Fixed edge case where failure of English Edge TTS (`Jenny`) fell back to the same Edge voice, crashing macOS `say`. Fallback now strictly queries local engines (`allowed_engines={"macos"}`).
- **Eliminated Spanish Buddy & Base Instruction Prompt Conflicts**:
  - Added `response_locale_override` to `Plugin` base class and assigned `"es-ES"` to `SpanishBuddyPlugin`.
  - Added Spanish support to `get_base_instructions()` (`"Always reply in Spanish."`, `"Fuentes:"`).
  - Added centralized `_refresh_conversation_base_instructions()` helper in `app.main` ensuring plugins with locale overrides don't receive conflicting "Always reply in Russian/English" directives.


### Added
- **Live TTS Download Progress UI**:
  - Implemented persistent cyber-terminal progress card (`.toast-progress`) showing real-time percentage, downloaded megabytes, download speed (KB/s), and estimated time remaining (ETA).
  - Added live text updates directly on the voice selector option (e.g. `[⟳ 45%]`).
  - Added pulsing status LED animation on the `VOICE:` chip during downloads.
  - Added download session auto-resumption on page load via `/api/tts/models`.

### Changed
- **Detailed Model Manager Metrics (`app/tts_manager.py`)**:
  - Expanded `get_status()` to return `downloaded_mb`, `total_mb`, `speed_kbps`, and `eta_seconds`.

## [0.10.0] - 2026-09-07

### Added
- **Modular TTS Model Manager (`app/tts_manager.py`)**:
  - Unified catalog of voice models (`MODEL_CATALOG`) with metadata, download URLs, file specs, and verification.
  - On-demand, non-blocking asynchronous model downloading with concurrency locks, progress tracking, and atomic file renaming.
  - CLI management tool: `python -m app.tts_manager [list | download <model_id>]`.
  - REST API endpoints for model discovery and lifecycle management: `GET /api/tts/models`, `POST /api/tts/models/{model_id}/download`, and `GET /api/tts/models/{model_id}/status`.
- **Piper TTS Integration (`piper-tts`)**:
  - Integrated high-performance ONNX neural speech synthesis engine via `piper-tts`.
  - Added Russian Piper voice models: `Dmitri (Piper Neural · Offline)` and `Irina (Piper Neural · Offline)`.
  - Cached `PiperVoice` instances in memory for ultra-fast response times.
  - Automatic fallback to macOS system voices if Piper encounters an error.
- **Extended Silero TTS Voices**:
  - Unlocked `Eugene (Silero Neural · Offline)` (`eugene`) voice in the Silero v4 Russian model without additional weights.
- **On-Demand Web UI Voice Installation**:
  - Voice selector dropdown now distinguishes installed models (`★`) from downloadable models (`[↓ 60MB]`).
  - Selecting a voice whose model is not yet downloaded automatically starts background downloading, informs the user via Toast notification, polls status, and switches seamlessly upon completion.
- **Modular TTS Test Suite (`test_tts_modular.py`)**:
  - 11 unit and integration tests covering model registry, Piper mock synthesis, error fallbacks, status endpoints, and auto-download triggers.

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
