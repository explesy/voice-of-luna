# Voice of Luna — Architecture Overview

Updated: 2026-09-14

This document is a readable map of the current project: what exists, how the pieces fit together, and where to look next.

## 1. Short summary

Voice of Luna is a local-first personal voice interface. A browser captures speech and plays responses. A local FastAPI backend manages conversations, speech processing, plugins, interruption, and the bridge to an already-authenticated local Codex runtime.

## 2. System map

```text
Browser client
  microphone, VAD, HUD, text input, playback, barge-in
        |
        | HTTP / WebSocket
        v
Local FastAPI backend
  conversations, transcript, events, cancellation, plugins
        |                         |
        |                         v
        |                  STT / TTS adapters
        |                  Whisper, Piper, Silero, Edge, macOS
        v
Codex app-server
  model turn, streaming text, available Codex account runtime
```

The browser is the interaction surface. The backend is the local orchestration layer. Codex is the model/runtime layer. Plugins add specialized workflows.

## 3. Browser client

The browser owns the user-facing loop:

- microphone capture;
- client-side VAD and endpointing;
- radar/HUD state;
- text fallback input;
- playback;
- instant barge-in controls.

The frontend stays lightweight: FastAPI templates, HTMX, and vanilla JavaScript rather than a heavy single-page application.

## 4. Local backend

The backend owns the application state and the turn pipeline:

- conversation lifecycle;
- transcript and event state;
- HTTP and WebSocket transport;
- speech-to-text orchestration;
- Codex bridge;
- streamed text handling;
- text-to-speech orchestration;
- cancellation and barge-in propagation;
- plugin discovery and capability checks;
- local persistence.

It is designed as a local single-user tool, not a hosted public multi-user service.

## 5. Speech and model pipeline

A normal voice turn is:

```text
user speaks
  -> browser VAD detects the end of the phrase
  -> backend receives the turn
  -> local STT produces text
  -> backend sends text to Codex
  -> Codex streams assistant text
  -> speech pipeline chunks early clauses/sentences
  -> TTS starts before the full answer is complete
  -> browser plays audio chunks
```

The product goal is low perceived latency: the assistant should begin speaking before the whole answer has finished generating.

## 6. Barge-in

Barge-in is a core interaction rule. When the assistant is speaking and the user starts talking again:

1. browser playback stops;
2. queued audio is discarded;
3. backend cancellation propagates to pending synthesis;
4. a new user turn can begin.

This makes Luna feel closer to a real conversation and avoids rigid turn-taking.

## 7. Plugin architecture

The core application should remain a neutral voice shell. It should not directly know about relationship training, Git branches, project cards, specific coaching flows, or other domain concepts.

Specialized behavior belongs in plugins. A plugin may own:

- prompt additions;
- before/after turn hooks;
- settings;
- panels;
- namespaced local state;
- tools;
- evidence returned from tool calls.

Plugins are trusted local Python extensions. The host can narrow what it exposes to them, but installed plugin code should still be treated as trusted local code.

## 8. Current plugin directions

### Project Room

Project Room owns project context: selected project root, project memory, safe repository reading, evidence, and project-facing tools. The neutral Luna core should not absorb this logic.

### Voice Trainer

Voice Trainer is a separate plugin/product repository. Its training scenarios, roadmap, and product decisions belong outside the Voice of Luna core.

### Development-control plugin

This is the planned voice interface for supervising development work. The target workflow is:

```text
inspect issue / PR / CI
  -> ask a coding agent to investigate or fix
  -> review diff and test results
  -> request correction
  -> confirm final state
```

The goal is orchestration by voice, not editing large code patches by dictation.

## 9. Development infrastructure direction

Current infrastructure already includes Python tests, JavaScript syntax checks, production import smoke checks, Playwright browser smoke tests, and GitHub Actions on Linux and macOS.

The next step is to make the project agent-friendly and phone-first:

- add one canonical full verification command, for example `make verify`;
- add deterministic fake STT, fake Codex, and fake TTS adapters;
- exercise a simulated end-to-end browser-to-backend-to-response path in CI;
- publish PR artifacts that are useful from a phone: screenshots, traces, logs, and compact failure reports;
- document rules clearly enough that a coding agent can work without reconstructing conventions from old chats.

## 10. What still needs real-device validation

CI can cover most repository changes, but some behavior still needs a real runtime:

- microphone behavior;
- browser permission behavior;
- real VAD feel;
- subjective barge-in naturalness;
- local Whisper, Codex, and TTS latency;
- audio smoothness.

These should become explicit validation gates rather than implicit blockers for every task.

## 11. Where to look next

- Current state: `docs/CURRENT_STATUS.md`
- Full technical architecture: `docs/02 — Technical Architecture.md`
- Conversation protocol: `docs/03 — Conversation Protocol.md`
- Performance: `docs/05 — Latency & Performance Benchmarks.md`
- Active work: GitHub Issues
- Voice Trainer: `explesy/voice-trainer`

## 12. Mental model

Think of the project as four layers:

```text
Human conversation layer
  browser, voice, HUD, barge-in

Local orchestration layer
  FastAPI, conversations, speech pipeline, plugins

Model/runtime layer
  Codex app-server and streamed model turns

Workflow extension layer
  Project Room, Voice Trainer, development-control plugin
```

Most design confusion comes from mixing these layers. The useful question is: which layer should own this concept?

If the answer is a specialized workflow, it probably belongs in a plugin rather than in the neutral Luna core.
