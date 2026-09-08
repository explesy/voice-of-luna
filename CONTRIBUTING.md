# Contributing to Voice of Luna

Thanks for improving the project. Voice of Luna is intentionally local-first: changes must not weaken the boundary between the browser, the local backend, and the operator's existing Codex login.

## Before opening a pull request

1. Discuss a non-trivial feature in an issue first, especially a new provider or plugin capability.
2. Keep a pull request focused; do not mix refactors, visual redesign, and behavior changes.
3. Never add credentials, token files, local transcripts, raw audio, `.env` files, or generated build artifacts.
4. Update the relevant document in `docs/` when changing the product boundary, privacy model, or protocol.

## Local checks

```bash
cd backend
uv sync --group dev
uv run pytest -q
```

The ordinary test suite must not call a real model or consume a contributor's Codex allowance. Any manual real-runtime smoke check must be explicit and documented in the pull request.

## Design constraints

- Do not expose `codex app-server` outside localhost, stdio, or a local Unix socket.
- Do not read, serialize, log, or proxy Codex OAuth credentials.
- Do not introduce cloud storage or telemetry by default.
- Plugins receive only documented, capability-limited data; they never receive raw audio or credentials. Project Room may read UTF-8 files and search only inside the explicitly selected project root; it does not write repository files.

By contributing, you agree that your contributions are licensed under the [MIT License](LICENSE).
