.PHONY: setup test run matrix

setup:
	cd backend && uv sync --group dev

test:
	cd backend && uv run pytest -q

matrix:
	cd backend && uv run python scripts/run_model_matrix.py

run:
	cd backend && uv run uvicorn app.main:app --reload
