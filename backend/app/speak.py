"""Local macOS speech synthesis for replies written in Cyrillic."""

from __future__ import annotations

import asyncio
import os
import re
import shutil
import tempfile
from pathlib import Path


class LocalSpeechError(RuntimeError):
    """Raised when a local response cannot be rendered to audio."""


class LocalMacOsSpeaker:
    """Render Cyrillic text through a local macOS voice, never a cloud TTS API."""

    async def synthesize(self, text: str) -> Path | None:
        if not re.search(r"[\u0400-\u052f]", text):
            return None
        if shutil.which("say") is None or shutil.which("ffmpeg") is None:
            raise LocalSpeechError("macOS say and ffmpeg are required for local Russian speech")

        voice = os.environ.get("VOICE_OF_LUNA_RUSSIAN_VOICE", "Milena")
        descriptor, raw_aiff = tempfile.mkstemp(prefix="voice-of-luna-speech-", suffix=".aiff")
        os.close(descriptor)
        source = Path(raw_aiff)
        source.unlink(missing_ok=True)
        descriptor, raw_m4a = tempfile.mkstemp(prefix="voice-of-luna-speech-", suffix=".m4a")
        os.close(descriptor)
        destination = Path(raw_m4a)
        destination.unlink(missing_ok=True)
        try:
            render = await asyncio.create_subprocess_exec(
                "say",
                "-v",
                voice,
                "-o",
                str(source),
                text,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            if await render.wait() != 0 or not source.is_file():
                raise LocalSpeechError(f"The local macOS voice '{voice}' could not speak this reply")
            convert = await asyncio.create_subprocess_exec(
                "ffmpeg",
                "-nostdin",
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-i",
                str(source),
                "-c:a",
                "aac",
                "-b:a",
                "128k",
                str(destination),
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            if await convert.wait() != 0 or not destination.is_file():
                raise LocalSpeechError("The local speech file could not be encoded for the browser")
            return destination
        except BaseException:
            destination.unlink(missing_ok=True)
            raise
        finally:
            source.unlink(missing_ok=True)
