"""Local speech-to-text through whisper.cpp; no audio leaves the machine."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import tempfile
import time
from pathlib import Path
from typing import Protocol


import httpx


class LocalTranscriptionError(RuntimeError):
    """Raised when the local Whisper runtime cannot transcribe a recording."""


class SpeechToTextProvider(Protocol):
    """Minimal batch STT capability used by the conversation runtime."""

    async def transcribe(self, wav_path: Path) -> str:
        """Return the final transcript for a prepared mono 16 kHz WAV file."""


class StreamingSpeechToTextSession(Protocol):
    """Wire-level session contract for future online STT providers."""

    async def push_pcm(self, samples: bytes, sample_rate: int) -> None:
        """Accept one signed 16-bit PCM frame."""

    async def finalize(self) -> str:
        """Return the authoritative final transcript."""

    async def cancel(self) -> None:
        """Cancel and release all session resources."""


def tone_shadow_configured() -> bool:
    """Return whether the optional local T-One shadow runner is configured."""

    return bool(
        os.environ.get("VOICE_OF_LUNA_TONE_MODEL")
        and os.environ.get("VOICE_OF_LUNA_TONE_TOKENS")
    )


async def run_tone_shadow(wav_bytes: bytes) -> dict[str, object]:
    """Run optional T-One diagnostics without affecting the authoritative turn.

    The adapter is deliberately subprocess-based: Sherpa-ONNX is optional and
    must not alter Luna's Python dependency set or local default path. Raw
    speech and the returned transcript are never logged or persisted.
    """

    model = os.environ.get("VOICE_OF_LUNA_TONE_MODEL")
    tokens = os.environ.get("VOICE_OF_LUNA_TONE_TOKENS")
    executable = os.environ.get("VOICE_OF_LUNA_TONE_EXECUTABLE", "sherpa-onnx")
    if not model or not tokens:
        return {"status": "unavailable", "reason": "model_or_tokens_not_configured"}
    if shutil.which(executable) is None:
        return {"status": "unavailable", "reason": "sherpa_executable_not_found"}

    descriptor, raw_path = tempfile.mkstemp(prefix="voice-of-luna-tone-shadow-", suffix=".wav")
    os.close(descriptor)
    path = Path(raw_path)
    started = time.perf_counter()
    try:
        await asyncio.to_thread(path.write_bytes, wav_bytes)
        process = await asyncio.create_subprocess_exec(
            executable,
            f"--t-one-ctc-model={model}",
            f"--tokens={tokens}",
            str(path),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        return_code = await process.wait()
        return {
            "status": "ok" if return_code == 0 else "error",
            "elapsed_ms": round((time.perf_counter() - started) * 1000),
        }
    except OSError:
        return {"status": "error", "reason": "sherpa_process_failed"}
    finally:
        path.unlink(missing_ok=True)


class LocalWhisperTranscriber:
    _shared_http_client: httpx.AsyncClient | None = None

    @classmethod
    def get_shared_http_client(cls) -> httpx.AsyncClient:
        if cls._shared_http_client is None or cls._shared_http_client.is_closed:
            cls._shared_http_client = httpx.AsyncClient(
                timeout=httpx.Timeout(15.0, connect=2.0),
                limits=httpx.Limits(max_keepalive_connections=5, max_connections=10),
            )
        return cls._shared_http_client

    @classmethod
    async def close_shared_http_client(cls) -> None:
        if cls._shared_http_client is not None:
            client = cls._shared_http_client
            cls._shared_http_client = None
            if not client.is_closed:
                try:
                    await client.aclose()
                except Exception:
                    pass

    def __init__(
        self,
        model_path: Path | None = None,
        language: str | None = None,
        server_url: str | None = None,
        prompt: str | None = None,
    ) -> None:
        project_root = Path(__file__).resolve().parents[2]
        configured_path = os.environ.get("VOICE_OF_LUNA_WHISPER_MODEL")
        self.model_path = model_path or Path(configured_path or project_root / "data/models/ggml-small.bin")
        self.language = language or os.environ.get("VOICE_OF_LUNA_WHISPER_LANGUAGE", "ru")
        self.prompt = prompt or os.environ.get("VOICE_OF_LUNA_WHISPER_PROMPT")
        host = os.environ.get("VOICE_OF_LUNA_WHISPER_HOST", "127.0.0.1")
        port = os.environ.get("VOICE_OF_LUNA_WHISPER_PORT", "8089")
        self.server_url = server_url or os.environ.get(
            "VOICE_OF_LUNA_WHISPER_URL", f"http://{host}:{port}"
        )
        self._http_client: httpx.AsyncClient | None = None

    def _get_http_client(self) -> httpx.AsyncClient:
        if self._http_client is not None:
            if self._http_client.is_closed:
                self._http_client = self.get_shared_http_client()
            return self._http_client
        return self.get_shared_http_client()

    async def aclose(self) -> None:
        if self._http_client is not None and not self._http_client.is_closed:
            await self._http_client.aclose()
            self._http_client = None

    async def transcribe(
        self,
        audio_path: Path,
        language: str | None = None,
        prompt: str | None = None,
    ) -> str:
        target_language = language or self.language
        target_prompt = prompt or self.prompt
        transcript = await self._transcribe_http(audio_path, target_language, target_prompt)
        if transcript is not None:
            return transcript
        return await self._transcribe_cli(audio_path, target_language, target_prompt)

    async def _transcribe_http(
        self, audio_path: Path, language: str, prompt: str | None = None
    ) -> str | None:
        url = f"{self.server_url.rstrip('/')}/inference"
        try:
            client = self._get_http_client()
            with open(audio_path, "rb") as audio_file:
                files = {"file": (audio_path.name, audio_file, "audio/wav")}
                data = {"language": language, "response_format": "json"}
                if prompt:
                    data["prompt"] = prompt
                response = await client.post(url, files=files, data=data)
            if response.status_code == 200:
                payload = response.json()
                transcript = payload.get("text", "").strip()
                if not transcript:
                    raise LocalTranscriptionError("No speech was detected in this recording")
                return transcript
        except LocalTranscriptionError:
            raise
        except Exception:
            return None
        return None

    async def _transcribe_cli(
        self, audio_path: Path, language: str, prompt: str | None = None
    ) -> str:
        if shutil.which("whisper-cli") is None:
            raise LocalTranscriptionError("whisper-cpp is not installed locally")
        if not self.model_path.is_file():
            raise LocalTranscriptionError("The local Whisper model is not installed")

        descriptor, raw_output_base = tempfile.mkstemp(prefix="voice-of-luna-transcript-")
        os.close(descriptor)
        output_base = Path(raw_output_base)
        output_json = Path(f"{output_base}.json")
        output_base.unlink(missing_ok=True)
        threads = str(min(os.cpu_count() or 4, 8))
        cmd = [
            "whisper-cli",
            "-m",
            str(self.model_path),
            "-f",
            str(audio_path),
            "-l",
            language,
            "-t",
            threads,
            "-np",
            "-nt",
            "-oj",
            "-of",
            str(output_base),
        ]
        if prompt:
            cmd.extend(["--prompt", prompt])
        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
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
