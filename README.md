# Voice of Luna

Personal, local-first voice interface for talking to a strong text model through an existing Codex login.

The current milestone is deliberately small: a FastAPI + htmx text shell that starts an ephemeral local `codex app-server` thread. It proves the account and trust boundary before microphone capture, STT, and TTS are added.

## Privacy model

- The app uses the local Codex runtime already signed in on the owner's machine.
- It does not read, copy, return, or store Codex OAuth tokens.
- The app-server uses stdio locally; it must not be exposed on a public network.
- Raw audio is not part of the current implementation.

This is a personal local tool, not a shared hosted service. A public deployment needs a separate API-key provider and proper authentication.

## Run locally

Prerequisites: Python 3.12+, [uv](https://docs.astral.sh/uv/), and a local Codex login (`codex login`).

```bash
cd backend
uv sync --group dev
uv run uvicorn app.main:app --reload
```

Open <http://127.0.0.1:8000/>. The page is server-rendered HTML enhanced with htmx; there is no React client application.

## Verify

```bash
cd backend
uv run pytest -q
```

The test suite uses no model calls. A real one-turn Codex smoke check should be run intentionally because it consumes the account's available Codex usage.

## Roadmap

1. Browser microphone, STT, TTS, and barge-in over the existing local conversation path.
2. Measured latency and mobile/browser behavior.
3. A capability-limited plugin boundary for optional conversation behaviors, such as training protocols.

See [`docs/`](docs/) for product, architecture, conversation-core, and MVP documents.

## License

No license has been selected yet. The repository is public for collaboration and review; choose a license before inviting reuse or accepting external contributions.
