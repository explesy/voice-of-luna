# Agent Workflow & Release Rules

This document holds detailed repository workflow that is authoritative but does not need to be loaded for every fresh task. `AGENTS.md` contains the compact mandatory bootstrap.

## SemVer

Voice of Lúna follows SemVer 2.0.0.

- **MINOR** — new user-visible functionality or capability: new plugin, endpoint, speech/transcription mode, settings option, UI/UX capability, WebSocket protocol extension, etc.
- **PATCH** — bug/security fixes, stability/latency optimization, refactor without new user capability, documentation-only changes.
- **MAJOR** — incompatible public API/protocol contracts or fundamental breaking architecture change.

A release-bearing task is incomplete until the version/changelog/tag workflow is finished.

### Version synchronization

When bumping version, keep all of these in sync:
1. `backend/pyproject.toml` → `version = "X.Y.Z"`.
2. `backend/app/__init__.py` → `__version__ = "X.Y.Z"`.
3. `CHANGELOG.md` → dated release section with Added/Changed/Fixed/etc as appropriate.
4. `cd backend && uv sync` → refresh `backend/uv.lock`.
5. `cd backend && uv run pytest tests/test_version.py` → version consistency must pass.
6. create lightweight tag `git tag vX.Y.Z`.

## Test gate

Before completing code work run:

```bash
cd backend && uv run pytest -q
```

All existing tests must pass.

Tests must not perform real OpenAI/Codex calls, real paid model requests, or spend user quota. Mock Codex/model and transcription boundaries where necessary.

For a purely documentation/routing change, the applying agent may use a narrowly justified docs verification only if the current repository instructions explicitly allow it; versioning/tag rules still apply when documentation is released as PATCH.

## Git workflow

After a completed logical block:
1. inspect the actual diff and test results;
2. ensure required version/changelog/tag work is complete;
3. create logical Conventional Commit(s) (`feat:`, `fix:`, `docs:`, `refactor:` etc.);
4. push the completed branch automatically;
5. for a release, push the lightweight version tag (`git push origin vX.Y.Z` or equivalent).

Do not leave finished, verified work uncommitted/unpushed merely for the user to perform the mechanical Git step.

## Trust boundary details

- Default runtime is localhost/local single-user.
- Raw recordings are temporary and should not be persisted by normal operation.
- Codex authentication belongs to the local supported Codex runtime, not browser storage or a shared server credential store.
- Do not serialize or log credentials, OAuth sessions, secret-bearing environment values or private conversation payloads.
- Plugin contexts should expose the minimum capabilities needed. Installed Python entry-point plugins are trusted code; capability APIs are an architectural boundary, not a hostile-code sandbox.

## Common commands

| Action | Command |
|---|---|
| Setup | `make setup` or `cd backend && uv sync --group dev` |
| Full tests | `make test` or `cd backend && uv run pytest -q` |
| Dev server | `make run` or `cd backend && uv run uvicorn app.main:app --reload` |
| Version consistency | `cd backend && uv run pytest tests/test_version.py` |
