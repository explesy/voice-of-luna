"""Local speech-to-text through whisper.cpp; no audio leaves the machine."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import tempfile
from pathlib import Path


class LocalTranscriptionError(RuntimeError):
    """Raised when the local Whisper runtime cannot transcribe a recording."""


class LocalWhisperTranscriber:
    def __init__(self, model_path: Path | None = None) -> None:
        project_root = Path(__file__).resolve().parents[2]
        configured_path = os.environ.get("VOICE_OF_LUNA_WHISPER_MODEL")
        self.model_path = model_path or Path(configured_path or project_root / "data/models/ggml-small.bin")

    async def transcribe(self, audio_path: Path) -> str:
        if shutil.which("whisper-cli") is None:
            raise LocalTranscriptionError("whisper-cpp is not installed locally")
        if not self.model_path.is_file():
            raise LocalTranscriptionError("The local Whisper model is not installed")

        descriptor, raw_output_base = tempfile.mkstemp(prefix="voice-of-luna-transcript-")
        os.close(descriptor)
        output_base = Path(raw_output_base)
        output_json = Path(f"{output_base}.json")
        output_base.unlink(missing_ok=True)
        try:
            process = await asyncio.create_subprocess_exec(
                "whisper-cli",
                "-m",
                str(self.model_path),
                "-f",
                str(audio_path),
                "-l",
                "auto",
                "-np",
                "-nt",
                "-oj",
                "-of",
                str(output_base),
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            if await process.wait() != 0 or not output_json.is_file():
                raise LocalTranscriptionError("Local Whisper could not transcribe this recording")
            result = json.loads(await asyncio.to_thread(output_json.read_text, encoding="utf-8"))
            transcript = " ".join(item["text"].strip() for item in result.get("transcription", [])).strip()
            if not transcript:
                raise LocalTranscriptionError("No speech was detected in this recording")
            return transcript
        finally:
            output_base.unlink(missing_ok=True)
            output_json.unlink(missing_ok=True)
