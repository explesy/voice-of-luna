.PHONY: setup setup-silero test run dev preflight matrix stt-corpus stt-benchmark

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

stt-corpus:
	cd backend && uv run python scripts/build_stt_corpus.py --out models/stt-corpus-synthetic

stt-benchmark:
	cd backend && for provider in tone vosk kroko nemotron320 nemotron560 pseudo-whisper whisper; do \
		uv run --with sherpa-onnx python scripts/run_stt_benchmark.py \
			models/stt-corpus-synthetic/manifest.jsonl \
			--provider $$provider \
			--output benchmark-results/$$provider.json || exit 1; \
	done

dev:
	./scripts/run.sh

run:
	./scripts/run.sh

