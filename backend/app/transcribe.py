"""Local speech-to-text through whisper.cpp; no audio leaves the machine."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import tempfile
import threading
import time
from collections import OrderedDict
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


class ToneStreamingSession:
    """Optional Sherpa-ONNX T-One online recognizer session."""

    def __init__(self, model: str, tokens: str, sample_rate: int = 16_000) -> None:
        try:
            import numpy as np
            import sherpa_onnx
        except ImportError as exc:
            raise LocalTranscriptionError(
                "T-One streaming requires the optional sherpa-onnx and numpy packages"
            ) from exc
        self._np = np
        # T-One's feature extractor is configured for 8 kHz. Sherpa accepts
        # arbitrary input sample rates in accept_waveform(), but a single stream
        # must keep a constant input rate, so trailing padding is fed at the same
        # rate as the last pushed frame.
        self._model_sample_rate = 8_000
        self._input_sample_rate = sample_rate or 16_000
        self._recognizer = sherpa_onnx.OnlineRecognizer.from_t_one_ctc(
            model=model,
            tokens=tokens,
            sample_rate=self._model_sample_rate,
            provider=os.environ.get("VOICE_OF_LUNA_TONE_PROVIDER", "cpu"),
            enable_endpoint_detection=True,
        )
        self._stream = self._recognizer.create_stream()

    async def push_pcm(self, samples: bytes, sample_rate: int) -> str:
        if sample_rate:
            self._input_sample_rate = sample_rate
        audio = self._np.frombuffer(samples, dtype=self._np.int16).astype(self._np.float32) / 32768.0
        await asyncio.to_thread(self._decode, audio, sample_rate)
        return self._result_text()

    async def finalize(self) -> str:
        padding = self._np.zeros(int(0.66 * self._input_sample_rate), dtype=self._np.float32)
        await asyncio.to_thread(self._decode, padding, self._input_sample_rate)
        self._stream.input_finished()
        await asyncio.to_thread(self._drain)
        return self._result_text()

    async def cancel(self) -> None:
        self._stream = None

    def _decode(self, audio: object, sample_rate: int) -> None:
        if self._stream is None:
            return
        self._stream.accept_waveform(sample_rate, audio)
        self._drain()

    def _drain(self) -> None:
        if self._stream is None:
            return
        while self._recognizer.is_ready(self._stream):
            self._recognizer.decode_stream(self._stream)

    def _result_text(self) -> str:
        result = self._recognizer.get_result(self._stream)
        return str(getattr(result, "text", result)).strip()


def create_tone_streaming_session(model: str, tokens: str, sample_rate: int) -> ToneStreamingSession:
    """Create the optional online T-One session, failing explicitly if unavailable."""

    return ToneStreamingSession(model=model, tokens=tokens, sample_rate=sample_rate)


# Engine -> (encoder, decoder, joiner) filenames inside a sherpa-onnx bundle.
_TRANSDUCER_FILES = {
    "vosk": ("encoder.int8.onnx", "decoder.onnx", "joiner.int8.onnx"),
    "kroko": ("encoder.onnx", "decoder.onnx", "joiner.onnx"),
    "nemotron": ("encoder.int8.onnx", "decoder.int8.onnx", "joiner.int8.onnx"),
}
logger = logging.getLogger("voice_of_luna.transcribe")

# Keep at most two recognizers resident (Nemotron alone is ~1.2 GB) and
# serialize decoding because a sherpa OnlineRecognizer may be shared by
# several sessions/threads.
_transducer_recognizer_cache: "OrderedDict[tuple[str, str], tuple[object, threading.Lock]]" = OrderedDict()
_transducer_cache_lock = threading.Lock()
_TRANSDUCER_CACHE_SIZE = 2


def _get_transducer_recognizer(engine: str, model_dir: str | Path) -> tuple[object, threading.Lock]:
    key = (engine, str(model_dir))
    with _transducer_cache_lock:
        cached = _transducer_recognizer_cache.get(key)
        if cached is not None:
            _transducer_recognizer_cache.move_to_end(key)
            return cached
    recognizer = _build_transducer_recognizer(engine, model_dir)
    entry = (recognizer, threading.Lock())
    with _transducer_cache_lock:
        existing = _transducer_recognizer_cache.get(key)
        if existing is not None:
            return existing
        _transducer_recognizer_cache[key] = entry
        while len(_transducer_recognizer_cache) > _TRANSDUCER_CACHE_SIZE:
            _transducer_recognizer_cache.popitem(last=False)
    return entry


def _build_transducer_recognizer(engine: str, model_dir: str | Path) -> object:
    try:
        import sherpa_onnx
    except ImportError as exc:
        raise LocalTranscriptionError(
            "Streaming STT requires the optional sherpa-onnx package"
        ) from exc
    files = _TRANSDUCER_FILES.get(engine)
    if files is None:
        raise LocalTranscriptionError(f"Unsupported streaming STT engine: {engine}")
    directory = Path(model_dir)
    encoder, decoder, joiner = files
    return sherpa_onnx.OnlineRecognizer.from_transducer(
        tokens=str(directory / "tokens.txt"),
        encoder=str(directory / encoder),
        decoder=str(directory / decoder),
        joiner=str(directory / joiner),
        num_threads=min(os.cpu_count() or 4, 8),
        sample_rate=16_000,
        feature_dim=80,
        enable_endpoint_detection=True,
        decoding_method="greedy_search",
    )


class TransducerStreamingSession:
    """Optional sherpa-onnx online transducer session (vosk/kroko/nemotron)."""

    def __init__(
        self,
        engine: str,
        model_dir: str | Path,
        sample_rate: int = 16_000,
        language: str | None = None,
    ) -> None:
        try:
            import numpy as np
        except ImportError as exc:  # pragma: no cover - numpy is a core dependency
            raise LocalTranscriptionError("Streaming STT requires numpy") from exc
        self._np = np
        self._recognizer, self._recognizer_lock = _get_transducer_recognizer(engine, model_dir)
        self._stream = self._recognizer.create_stream()
        if language:
            try:
                self._stream.set_option("language", language)
            except Exception as exc:  # pragma: no cover - depends on sherpa build
                logger.warning("Streaming STT language option '%s' was not applied: %s", language, exc)

    async def push_pcm(self, samples: bytes, sample_rate: int) -> str:
        audio = self._np.frombuffer(samples, dtype=self._np.int16).astype(self._np.float32) / 32768.0
        await asyncio.to_thread(self._decode, audio, sample_rate)
        return self._result_text()

    async def finalize(self) -> str:
        self._stream.input_finished()
        await asyncio.to_thread(self._drain)
        return self._result_text()

    async def cancel(self) -> None:
        self._stream = None

    def _decode(self, audio: object, sample_rate: int) -> None:
        if self._stream is None:
            return
        with self._recognizer_lock:
            self._stream.accept_waveform(sample_rate, audio)
            self._drain_locked()

    def _drain(self) -> None:
        if self._stream is None:
            return
        with self._recognizer_lock:
            self._drain_locked()

    def _drain_locked(self) -> None:
        while self._recognizer.is_ready(self._stream):
            self._recognizer.decode_stream(self._stream)

    def _result_text(self) -> str:
        if self._stream is None:
            return ""
        with self._recognizer_lock:
            result = self._recognizer.get_result(self._stream)
        return str(getattr(result, "text", result)).strip()


def create_streaming_session(
    engine: str,
    model_dir: str | Path,
    sample_rate: int,
    language: str | None = None,
) -> ToneStreamingSession | TransducerStreamingSession:
    """Build the online session for a selected engine/model directory."""

    if engine == "tone":
        directory = Path(model_dir)
        return ToneStreamingSession(
            model=str(directory / "model.onnx"),
            tokens=str(directory / "tokens.txt"),
            sample_rate=sample_rate,
        )
    return TransducerStreamingSession(engine, model_dir, sample_rate=sample_rate, language=language)


def tone_shadow_configured() -> bool:
    """Return whether the optional local T-One shadow runner is configured."""

    return bool(
        os.environ.get("VOICE_OF_LUNA_TONE_MODEL")
        and os.environ.get("VOICE_OF_LUNA_TONE_TOKENS")
    )


def tone_streaming_available() -> bool:
    """Return whether a local T-One streaming model is installed/configured."""

    return tone_shadow_configured()


def tone_streaming_configured() -> bool:
    """Return whether online T-One streaming is allowed by configuration.

    Streaming is a supported configuration (resolved decision D8): it is on by
    default whenever a local streaming model is available, rather than
    requiring an explicit opt-in environment flag. The runtime still applies
    the per-conversation UI toggle. ``VOICE_OF_LUNA_TONE_STREAMING=0`` is an
    operator kill switch that forces batch Whisper only.
    """

    return tone_streaming_available() and os.environ.get("VOICE_OF_LUNA_TONE_STREAMING") != "0"


async def run_tone_shadow(wav_bytes: bytes) -> dict[str, object]:
    """Run optional T-One diagnostics without affecting the authoritative turn.

    The adapter is deliberately subprocess-based so a broken sherpa-onnx build
    cannot take down the conversation path. Raw speech and the returned
    transcript are never logged or persisted.
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
        from app.whisper_server import resolve_whisper_model_path

        self.model_path = resolve_whisper_model_path(model_path)
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
