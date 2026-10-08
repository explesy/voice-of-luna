.PHONY: setup setup-silero test run dev preflight matrix

setup:
	cd backend && uv sync

setup-silero:
	cd backend && uv sync --extra silero

test:
	cd backend && uv run pytest -q

preflight:
	cd backend && uv run python scripts/preflight.py

matrix:
	cd backend && uv run python scripts/run_model_matrix.py

dev:
	./scripts/run.sh

run:
	./scripts/run.sh

