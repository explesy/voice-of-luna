# AGENTS.md — Voice of Lúna

Repository-wide rules for AI coding agents.

## Start here

Voice of Lúna is a local-first, single-user voice/conversation bridge around local STT/TTS and an already-authenticated local `codex app-server`.

For a fresh task:
1. Read `docs/CURRENT_STATUS.md` for compact current truth.
2. Open only the task-specific canonical doc/code next.
3. For active implementation work, use the relevant GitHub Issue rather than reconstructing a TODO list from old docs/chats.

Routing:
- product/UX → `docs/00 — PROJECT README.md`, `docs/01 — Product & UX Spec.md`
- architecture → `docs/02 — Technical Architecture.md`
- turn-taking/plugin protocol → `docs/03 — Conversation Protocol.md`
- implementation history/plans → `docs/04 — MVP & Implementation Plan.md` only when relevant; active scoped work belongs in Issues
- performance → `docs/05 — Latency & Performance Benchmarks.md`
- historical changes → `CHANGELOG.md` / Git
- detailed agent/release procedure → `docs/AGENT_WORKFLOW.md`

## Non-negotiable boundaries

- Local-first by default. Do not expose the service publicly or add telemetry without an explicit product decision.
- Never log/commit Codex credentials, tokens, raw recordings, private conversation files or secret-bearing environment data.
- Plugins are trusted in-process Python extensions, not a sandbox. Keep host capability contracts narrow and domain-neutral.
- Do not put personal Relationship scenarios/history into this repository.
- Voice Trainer product semantics belong in its standalone package/repo (issue #1), not in the neutral core; do not expand `backend/app/plugins/training.py` as the long-term product implementation.
- Automated tests must never make real OpenAI/Codex/paid model calls or consume user quota.

## Completion gate

Every completed change must follow `docs/AGENT_WORKFLOW.md`.
At minimum:
- run the required test gate (`cd backend && uv run pytest -q` unless an explicitly documented narrower docs-only rule applies);
- apply SemVer and synchronize version/changelog/lock when the change requires a release;
- create the required lightweight version tag;
- make logical Conventional Commit(s) and push completed work + release tag according to the repository workflow.

A task is not complete when required versioning/tests/tag/push are missing.
