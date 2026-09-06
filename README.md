# Voice of Luna

[![CI](https://github.com/explesy/voice-of-luna/actions/workflows/ci.yml/badge.svg)](https://github.com/explesy/voice-of-luna/actions/workflows/ci.yml)

Personal, local-first voice interface for talking to a strong text model through an existing Codex login.

> **Status: early prototype.** Text chat, browser recording, local speech-to-text, and local Russian speech work as one voice loop on macOS. Realtime UX includes visual states, immediate barge-in, mute, collapsible transcript, and server-side latency metrics. Streaming, continuous VAD endpointing, durable history, and plugins are still future work.

The current milestone is deliberately small: a FastAPI + htmx shell that starts one local `codex app-server` process and one ephemeral Codex thread for each active conversation. The browser records a short message, the backend transcribes it with local Whisper, sends only the resulting text to Codex, and renders Cyrillic replies through the local macOS voice before the browser plays them.

## Privacy model

- The app uses the local Codex runtime already signed in on the owner's machine.
- It does not read, copy, return, or store Codex OAuth tokens.
- The app-server uses stdio locally; it must not be exposed on a public network.
- A recording, its converted WAV file, Whisper's JSON output, and generated reply audio are temporary files. The first three are deleted after each turn; reply audio is deleted after its one-time browser request. Conversation text currently remains in memory only, until the server stops.
- Speech-to-text is local. The model response still goes through the owner's already-authorized Codex runtime and is subject to that account's normal usage limits.

This is a personal local tool, not a shared hosted service. A public deployment needs a separate API-key provider and proper authentication.

## Run locally

Prerequisites: Python 3.12+, [uv](https://docs.astral.sh/uv/), a local Codex login (`codex login`), `ffmpeg`, and [`whisper.cpp`](https://github.com/ggml-org/whisper.cpp)'s `whisper-cli` with a local multilingual model.

On macOS with Homebrew, install the local audio tools and download the recommended Whisper small model (about 465 MB):

```bash
brew install ffmpeg whisper-cpp
mkdir -p data/models
curl -L --retry 3 https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-small.bin \
  -o data/models/ggml-small.bin
```

`data/` is ignored by Git. To keep the model somewhere else, set `VOICE_OF_LUNA_WHISPER_MODEL` to its absolute path before starting the server.

```bash
cd backend
uv sync --group dev
uv run uvicorn app.main:app --reload
```

Open <http://127.0.0.1:8000/>. The page is server-rendered HTML enhanced with htmx; there is no React client application. Press **Start recording**, allow the browser's microphone permission, speak, and press **Stop recording**. Cyrillic replies are rendered by the local macOS `Milena` voice and played in the browser; the visible audio controls remain available if autoplay is blocked. Set `VOICE_OF_LUNA_RUSSIAN_VOICE` to use another installed macOS Russian voice.

## Verify

```bash
cd backend
uv run pytest -q
```

The test suite uses no model calls. A real one-turn Codex smoke check should be run intentionally because it consumes the account's available Codex usage.

`make setup`, `make test`, and `make run` provide the same common local workflow from the repository root.

## Roadmap

1. Measured latency, browser/device behavior, and a clearer runtime readiness screen.
2. Barge-in and more reliable speech playback controls.
3. A capability-limited plugin boundary for optional conversation behaviors, such as training protocols.

See [`docs/`](docs/) for product, architecture, conversation-core, and MVP documents.

## Contributing and security

Please read [CONTRIBUTING.md](CONTRIBUTING.md) before opening a pull request. Security-sensitive reports, especially anything related to local credentials or conversation data, belong in the process described in [SECURITY.md](SECURITY.md), not in a public issue.

## License

Voice of Luna is available under the [MIT License](LICENSE).
