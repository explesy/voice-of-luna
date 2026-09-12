# Voice of Lúna — Current Status

Updated: 2026-09-12
Current release: **0.34.0** (2026-09-12)

Purpose: compact current truth for fresh-session startup. This is not a changelog, task list, benchmark table, or replacement for the numbered canonical docs.

## Product shape

Voice of Lúna is a personal, local-first browser voice bridge to an already-authenticated local `codex app-server`. The intelligence stays in the text-model/Codex path; the application owns microphone capture, local STT, turn-taking, streaming speech output, interruption and observable latency.

The current product is a local single-user tool, not a public multi-user service and not a wrapper that stores Codex OAuth credentials in the browser/backend.

## Implemented major capabilities

- Browser voice + text turns with persistent same-thread Codex conversation.
- Local Whisper speech recognition and multi-tier TTS (Piper, optional Silero, Edge TTS, macOS system fallback).
- Streaming speech segmentation / pipelined TTS and measured latency stages.
- Client-side adaptive VAD and instant barge-in/cancellation.
- Live model discovery from the local Codex runtime and per-session reasoning selection.
- Optional cancellation-safe warmup.
- Trusted in-process plugin system with plugin-owned settings, panels/actions, tools and namespaced persistent state.
- Separately installed plugins can be discovered through Python entry points (`voice_of_luna.plugins`).
- Project Room supplies bounded project context and optional approved external-write tools without moving project semantics into the neutral conversation core.

For product overview and UX: `README.md`, `docs/00 — PROJECT README.md`, `docs/01 — Product & UX Spec.md`.
For architecture: `docs/02 — Technical Architecture.md`.
For neutral turn-taking/plugin protocol: `docs/03 — Conversation Protocol.md`.
For measured performance: `docs/05 — Latency & Performance Benchmarks.md`.

## Architectural boundaries

- **Local-first trust boundary**: do not expose the service publicly by default, send telemetry, or persist raw recordings/secrets/transcripts as a side effect.
- Python plugins are **trusted in-process extensions**, not a security sandbox. Host APIs minimize exposed capability but cannot make arbitrary installed Python code untrusted.
- The conversation/audio core must remain domain-neutral. Specialized workflows belong in plugins/packages.
- Voice Trainer is a separately versioned plugin package in [`explesy/voice-trainer`](https://github.com/explesy/voice-trainer). Voice of Luna exposes only the generic `app.plugin_api` contracts needed by installed plugins; do not add training semantics back to this repository.
- Personal Relationship training scenarios/history remain external Project Memory and must not be committed into this repo.

## Known high-impact limitations / active boundaries

- Live Codex latency remains the dominant variable and benchmark snapshots are not SLAs or permanent model rankings.
- External plugin discovery is intentionally trusted-process integration; stronger sandboxing would require a different execution boundary.
- Voice Trainer is discovered through the generic external-plugin entry point. Its separate release and product roadmap are maintained in its own repository.
- Public multi-user deployment/auth is out of current scope.

## Active work ownership

Concrete work lives in GitHub Issues, not in this file:

- **#1** — extraction record for Voice Trainer; follow-up product work belongs in its standalone repository.
- **#2** — progressive repo-memory/docs refactor (this runtime layer).

If an issue is closed, do not keep its task state here as a parallel TODO. Current state may be refreshed when a release materially changes product shape or boundaries.

## Retrieval routing

- “What is Voice of Luna now?” → this file.
- Architecture → numbered architecture/protocol docs.
- Current implementation task → relevant GitHub Issue + relevant code/docs.
- Why a decision was made → dedicated decision record when one exists; otherwise Issue/Git history.
- Historical evolution → `CHANGELOG.md` / Git history.
- Performance question → benchmark doc, preserving its dated methodology/limits.

Do not reconstruct current truth by reading the whole changelog or old chats when this file answers the question.
