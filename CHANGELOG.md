# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.28.8] - 2026-09-09

### Changed
- Re-render plugin-owned settings panels dynamically when the active plugin changes, including through the realtime WebSocket path.

## [0.28.7] - 2026-09-09

### Changed
- Render plugin settings and actions from generic panel metadata instead of Project Room-specific markup in the conversation template.

## [0.28.6] - 2026-09-09

### Added
- Exposed plugin-owned panel metadata and Project Room action declarations through the generic plugin listing API.

## [0.28.5] - 2026-09-09

### Added
- Added generic plugin actions and Project Room-owned `Refresh`/`Forget` operations for its project card.

## [0.28.4] - 2026-09-09

### Added
- Project Room now builds a bounded, plugin-owned project card during `before_turn` from safe Git metadata and tracked document names.

## [0.28.3] - 2026-09-09

### Changed
- Kept the Project Room Git context implementation entirely under the plugin package.

## [0.28.2] - 2026-09-09

### Changed
- Kept Project Room's Git context resolver inside the Project Room plugin package.

## [0.28.1] - 2026-09-09

### Changed
- Moved Project Room repository configuration behind the plugin-owned settings contract; the conversation core no longer resolves or stores `ProjectContext`.
- Exposed generic plugin settings and panel metadata in the plugin selection API.

## [0.28.0] - 2026-09-09

### Added
- Added a generic plugin-owned settings/configuration contract so domain-specific state can remain inside plugins.
- Documented the strict separation between the neutral conversation core and Project Room domain behavior.

## [0.27.1] - 2026-09-09

### Fixed
- Use Responses API-compatible dynamic tool names while preserving qualified plugin dispatch internally.

## [0.27.0] - 2026-09-09

### Added
- Added entry-point discovery for separately installed, trusted Voice of Luna plugins.
- Added a plugin-scoped state facade for hook persistence without exposing internal namespaces.
- Started the separately versioned `voice-of-luna-relationships` plugin repository.

### Changed
- Plugin identifiers must be unique; duplicate registrations now fail explicitly.

## [0.26.3] - 2026-09-09

### Fixed
- Made the session toolbar wrap cleanly at constrained widths and kept session metadata from breaking mid-label.

## [0.26.2] - 2026-09-09

### Fixed
- Aligned the Project Room workspace control with the terminal toolbar styling and replaced browser-default input/button rendering.

## [0.26.1] - 2026-09-09

### Fixed
- Removed retired GPT-5.4 model IDs from the local fallback catalog and default settings.
- Keep the model picker limited to the live local Codex model list; unavailable saved values are no longer inserted back into the picker.

### Documentation
- Document the ChatGPT-authenticated Codex retirement of GPT-5.4 Mini.

## [0.26.0] - 2026-09-09

### Changed
- Unified voice interaction around the radar and keyboard shortcut with explicit ready, listening, transcribing, thinking, speaking, and error states.
- Made replay contextual to each assistant response, hid the stop control until speech is active, and simplified the text composer for conversational use.
- Scoped workspace context visibility to the Project Room plugin and refreshed the empty-session guidance.

## [0.25.0] - 2026-09-09

### Added
- Added project-scoped Project Room memory and Git repository context resolution.
- Added default-deny repository file access for credentials, ignored files, and secret paths.
- Added exact pending tool-call approval for external writes with argument-hash verification.
- Added bounded repository, memory, and GitHub tool results.
- Added direct browser VAD trace export compatibility with the experiment harness and explicit ROOT apply control.

### Changed
- Dynamic tool instructions now allow only active plugin host tools and reject capability expansion from untrusted content.

## [0.24.2] - 2026-09-08

### Documentation
- Reconciled the README and project documents with the implemented Project Room plugin, native tool permissions, local HTTP startup, and streaming TTS pipeline.
- Removed retired plugin names and corrected the documented Python test count and local-first privacy wording.
- Documented Project Room, plugin storage, project-root, model-directory, and optional GitHub runtime configuration.

## [0.24.1] - 2026-09-08

### Changed
- Document the native tool protocol, Project Room storage boundary, project-root selection, and one-shot external write approval.

## [0.24.0] - 2026-09-08

### Added
- Added explicit project-root selection for Project Room through the plugin API.
- Added tool permission metadata and one-shot, 60-second approval for `external.write` actions.
- Added a local-only GitHub capability gateway and guarded `github.issues` / `github.create_issue` tools.

## [0.23.0] - 2026-09-08

### Added
- Added opt-in local VAD trace capture/export with volume, threshold, state, silence, and endpoint markers; audio and transcripts are never recorded.

## [0.22.5] - 2026-09-08

### Fixed
- Preserve every final agent-message item when Codex events arrive before item metadata.
- Discard buffered notifications for interrupted or completed turns to prevent stale-event buildup.

## [0.22.4] - 2026-09-08

### Changed
- Cache verified TTS model checksums until a model file's metadata changes, avoiding repeated hashing on status polling.

## [0.22.3] - 2026-09-08

### Fixed
- Preserve an explicit failed state for remote warmup instead of reporting a completed warmup after an error.

## [0.22.2] - 2026-09-08

### Fixed
- Keep a deterministic system-voice fallback candidate when running on hosts without macOS `say`.

## [0.22.1] - 2026-09-08

### Fixed
- Use a locale-aware TTS fallback chain and report the actual engine used by streamed turns.
- Allow English Piper Lessac synthesis and require verified Piper artifacts before loading or prewarming them.

## [0.22.0] - 2026-09-08

### Removed
- Removed the legacy Focus Sprint, Spanish Buddy, and diagnostic Test Plugin registrations and implementations.
- Removed client-side Spanish Buddy-specific plugin behavior and retired its plugin-specific regression tests.

## [0.21.0] - 2026-09-08

### Added
- Added local SQLite/FTS5 plugin storage with plugin-scoped memory records.
- Added the first-party Project Room plugin with persistent memory and safe read-only repository tools.

## [0.20.1] - 2026-09-08

### Changed
- Use one locale-aware TTS fallback chain and preserve requested, actual, and fallback engine telemetry through streamed turns.
- Allow the English Piper Lessac model to synthesize English text.
- Require verified Piper artifacts before loading or prewarming them.

## [0.19.0] - 2026-09-08

### Changed
- Preserve the user's selected voice across plugin switches while resolving plugin-compatible effective voices per turn.

## [0.18.3] - 2026-09-08

### Fixed
- Make the production CI import smoke use the environment created by `uv sync --no-dev` without re-syncing development dependencies.

## [0.18.2] - 2026-09-08

### Fixed
- Upgrade immutable CI artifact uploads to `actions/upload-artifact` v6 for Node 24 runner compatibility.

## [0.18.1] - 2026-09-08

### Fixed
- Treat checksum verification as the readiness gate for TTS model status, voice selection, and voice discovery.
- Relax the number-normalizer performance regression guard to remain reliable on shared CI runners.
- Document the English Piper Lessac voice alongside the other offline voices.

## [0.18.0] - 2026-09-08

### Added
- Added a reproducible VAD A/B harness for fast (300 ms), normal (450 ms), and patient (600 ms) endpointing profiles.
- Added an example trace format for measuring endpoint timing and false-end events from captured sessions.

## [0.17.8] - 2026-09-08

### Changed
- Removed the unused assistant-text argument from `TurnLanguage` resolution and derive TTS language from the preceding user turn.
- Pinned CI actions to immutable current SHAs and Node 24-compatible major releases.

## [0.17.7] - 2026-09-08

### Changed
- Centralized Whisper language and prompt resolution for HTTP and WebSocket audio transports.
- Added regression coverage for locale and plugin-specific STT policy.

## [0.17.6] - 2026-09-08

### Added
- Added a deterministic Playwright browser smoke for WebSocket text turns, settings changes, and fake microphone start/stop.
- CI now runs the browser smoke on Ubuntu and macOS and uploads its diagnostics.

## [0.17.5] - 2026-09-08

### Fixed
- Removed the last Linux CI assertion that depended on a macOS-only `Milena` voice.

## [0.17.4] - 2026-09-08

### Fixed
- Made CI voice and speech tests deterministic across macOS and Linux runners.
- Prevented Codex reply wrappers from retrying a remote turn after an internal `TypeError`.
- Invalidated remote warmup state when base instructions recreate the Codex thread.
- Removed the direct Silero download bypass and require checksum-verified model files.
- Buffered Codex message deltas until their item phase is known, preserving legacy untagged streams.
- Added Python 3.12/macOS CI coverage and uploaded pytest reports for failed runs.

## [0.17.3] - 2026-09-08

### Fixed
- **Duplicate Model Responses from Commentary/Preamble Messages**:
  - Filtered out interim commentary messages (`phase: "commentary"`) emitted by Codex during tool/search execution, streaming only the final answer (`phase: "final_answer"`).
  - Buffered commentary text in the stream reader as a fallback in case no final answer item is emitted, ensuring no information loss while eliminating duplicated text and repeated source listings in speech and UI.
- **Concurrent HTMX & WebSocket Form Submissions**:
  - Registered the text prompt form submit listener during the DOM capture phase (`{ capture: true }`) with `event.stopImmediatePropagation()`.
  - Added an `htmx:configRequest` cancel handler when the WebSocket is open to guarantee HTMX no-JS fallback does not fire a simultaneous HTTP POST turn alongside the WebSocket turn.

## [0.17.2] - 2026-09-08

### Fixed
- **AudioWorklet Re-initialization Bug**: Tracked AudioWorklet module loading on `audioContext._workletLoaded` per `AudioContext` instance instead of an un-resettable global flag, preventing `DOMException: Unknown AudioWorklet name 'pcm-recorder-processor'` and eliminating automatic fallback to `ScriptProcessorNode` on subsequent voice recordings.
- **WebSocket Send Null Pointer Guard**: Added safe readyState verification after `await audioBlob.arrayBuffer()` in `sendRecording()`, preventing uncaught `TypeError: can't access property "send", socket is null` when the socket closes during audio buffer preparation and falling back cleanly to HTTP.
- **Text Prompt E2E Latency Metric & Stale Timestamps**: Reset `lastSpeechEndTime` and record turn start time on text prompt form submissions, ensuring the client latency HUD accurately measures actual e2e latency instead of inheriting stale timestamps from earlier voice recording attempts.
- **Multi-Item Agent Message Streaming**: Separated distinct `agentMessage` items within the same turn by yielding `\n\n` upon `itemId` changes in Codex stream handler, preventing multi-stage search/revision chunks from fusing words together mid-sentence.

### Changed
- Simplified the README measurement snapshot by moving environment and detailed run mechanics to the canonical benchmark document.

## [0.17.0] - 2026-09-08

### Added
- **Centralized Language & Voice Resolver (`TurnLanguage`)**:
  - Unified turn language resolution across Codex prompt instructions, TTS voice selection, and WebSocket telemetry.
  - Fixes language desynchronization where active plugins (e.g. `Spanish Buddy` with `response_locale_override = "es-ES"`) would be assigned a Russian TTS voice if the user asked a question in Russian in `AUTO` mode.
  - Added Spanish Edge neural voices (`Elvira (Neural · Edge)`, `Alvaro (Neural · Edge)`) and automatic voice-to-locale compatibility matching.
- **Multilingual Source Header Pipeline & Spanish `Fuentes:` Support**:
  - Expanded source header split detection across backend (`SOURCES_SPLIT_RE`, `TRAILING_SOURCES_RE`, `format_terminal_text`) and frontend (`terminal-formatter.js`, `sanitizeForSpeech`).
  - Correctly strips Spanish `Fuentes:` and `Referencias:` sections from spoken audio output and renders them as styled source cards in the UI.
- **Cryptographic SHA256 Verification for TTS Models**:
  - Added `sha256` checksums to `ModelFileSpec` for Piper ONNX models (`dmitri`, `irina`, `lessac`), their JSON configurations, and Silero PyTorch weights (`silero_v4_ru.pt`).
  - Added incremental SHA256 verification during download and atomic `.part` replacement to prevent corrupted or tampered weights from loading.
  - Added `tts_model_manager.verify_checksums(model_id)`.
- **English Offline Voice for Linux (`piper_en_lessac`)**:
  - Added Piper ONNX English voice (`Lessac (Piper Neural · Offline)`) to catalog, enabling full offline English TTS without macOS `say`.
- **UI Internationalization (RU / EN)**:
  - Created `app/i18n.py` providing translated UI strings for headers, tooltips, chips, and option groups.
- **CI / CD Robustness**:
  - Added JavaScript syntax validation (`node --check`) step to GitHub Actions CI workflow.
  - Added `timeout-minutes: 10` and `pytest-timeout>=2.3.1` to prevent hanging async test runners.

### Fixed
- **Platform-Aware macOS Voices**:
  - `get_installed_voices()` no longer injects fake macOS voices (`Milena`, `Samantha`) on Linux systems where `shutil.which("say")` is not available.
- **Accurate Footer Privacy Status**:
  - Footer now accurately reports component states (e.g. `STT:LOCAL // TTS:EDGE_CLOUD // LLM:CODEX`) rather than claiming `100% LOCAL` when cloud Edge TTS is active.

## [0.16.4] - 2026-09-08

### Changed
- Added the dated TTS, STT, and live-Codex measurement snapshot directly to the README, with sample-size and evidence limitations.

## [0.16.3] - 2026-09-08

### Fixed
- Replaced obsolete sub-second TTFA and static model/voice latency claims in the README with the canonical evidence methodology and current limitations.

## [0.16.2] - 2026-09-08

### Changed
- Recorded a private, locally processed real-speech STT latency result without retaining the audio or transcript.

## [0.16.1] - 2026-09-08

### Changed
- Recorded the reproducible local TTS, Edge TTS, and explicit live-Codex timing snapshots with their sample-size and evidence limitations.

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
