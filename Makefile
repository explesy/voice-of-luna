.PHONY: setup test run

setup:
	cd backend && uv sync --group dev

test:
	cd backend && uv run pytest -q

run:
	cd backend && uv run uvicorn app.main:app --reload
