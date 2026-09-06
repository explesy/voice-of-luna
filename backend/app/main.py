from __future__ import annotations

import asyncio
import logging
import os
import shutil
import tempfile
import time
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile, status
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from starlette.background import BackgroundTask

from .codex import CodexAppServer, CodexUnavailable
from .speak import LocalMacOsSpeaker, LocalSpeechError
from .transcribe import LocalTranscriptionError, LocalWhisperTranscriber


@asynccontextmanager
async def lifespan(_app: FastAPI):
    try:
        yield
    finally:
        await asyncio.gather(
            *(conversation.model.close() for conversation in conversations.values()),
            return_exceptions=True,
        )


app = FastAPI(title="Voice of Luna", version="0.1.0", lifespan=lifespan)
app.mount("/static", StaticFiles(directory="app/static"), name="static")
templates = Jinja2Templates(directory="app/templates")


logger = logging.getLogger("voice_of_luna")


@dataclass
class Conversation:
    id: str
    turns: list[dict[str, str]] = field(default_factory=list)
    model: CodexAppServer = field(default_factory=CodexAppServer)


@dataclass
class SpeechClip:
    conversation_id: str
    path: Path


conversations: dict[str, Conversation] = {}
speech_clips: dict[str, SpeechClip] = {}


async def _close_and_delete_conversation(conversation_id: str) -> bool:
    conversation = conversations.pop(conversation_id, None)
    if conversation is not None:
        await conversation.model.close()
    for clip_id, clip in list(speech_clips.items()):
        if clip.conversation_id == conversation_id:
            _remove_temporary_audio(clip.path)
            del speech_clips[clip_id]
    return conversation is not None


class TurnInput(BaseModel):
    text: str = Field(min_length=1, max_length=8_000)


MAX_AUDIO_BYTES = 12 * 1024 * 1024
AUDIO_SUFFIXES = {
    "audio/webm": ".webm",
    "audio/ogg": ".ogg",
    "audio/mp4": ".m4a",
    "audio/mpeg": ".mp3",
    "audio/wav": ".wav",
    "audio/x-wav": ".wav",
}


class AudioConversionError(RuntimeError):
    """Raised when a browser recording cannot be decoded locally."""


@app.get("/healthz")
async def healthz() -> dict[str, object]:
    runtime = await CodexAppServer().status()
    return {"ok": True, "codex": asdict(runtime)}


@app.get("/", response_class=HTMLResponse)
async def home(request: Request) -> HTMLResponse:
    runtime = await CodexAppServer().status()
    russian_voice = os.environ.get("VOICE_OF_LUNA_RUSSIAN_VOICE", "Milena")
    return templates.TemplateResponse(
        request, "index.html", {"runtime": runtime, "russian_voice": russian_voice}
    )


@app.post("/conversations", response_class=HTMLResponse)
async def create_conversation_fragment(request: Request) -> HTMLResponse:
    conversation = Conversation(id=str(uuid4()))
    conversations[conversation.id] = conversation
    russian_voice = os.environ.get("VOICE_OF_LUNA_RUSSIAN_VOICE", "Milena")
    return templates.TemplateResponse(
        request, "conversation.html", {"conversation": conversation, "russian_voice": russian_voice}
    )


@app.delete("/conversations/{conversation_id}", response_class=HTMLResponse)
async def delete_conversation_fragment(request: Request, conversation_id: str) -> HTMLResponse:
    await _close_and_delete_conversation(conversation_id)
    runtime = await CodexAppServer().status()
    russian_voice = os.environ.get("VOICE_OF_LUNA_RUSSIAN_VOICE", "Milena")
    return templates.TemplateResponse(
        request, "empty_conversation.html", {"runtime": runtime, "russian_voice": russian_voice}
    )


@app.post("/conversations/{conversation_id}/turns", response_class=HTMLResponse)
async def create_turn_fragment(
    request: Request, conversation_id: str, text: str = Form(min_length=1, max_length=8_000)
) -> HTMLResponse:
    conversation = _recover_html_conversation(conversation_id)
    conversation.turns.append({"role": "user", "text": text})
    t_start = time.perf_counter()
    error = None
    try:
        reply = await conversation.model.reply(text)
        t_llm = time.perf_counter()
        error = await _append_assistant_turn(conversation, reply)
        t_tts = time.perf_counter()
        logger.info(
            "Text turn latency: llm=%.3fs, tts=%.3fs, total=%.3fs",
            t_llm - t_start,
            t_tts - t_llm,
            t_tts - t_start,
        )
    except CodexUnavailable as exception:
        error = str(exception)
    russian_voice = os.environ.get("VOICE_OF_LUNA_RUSSIAN_VOICE", "Milena")
    return templates.TemplateResponse(
        request,
        "conversation.html",
        {"conversation": conversation, "error": error, "russian_voice": russian_voice},
    )


@app.post("/conversations/{conversation_id}/audio", response_class=HTMLResponse)
async def create_audio_turn_fragment(
    request: Request, conversation_id: str, audio: UploadFile = File(...)
) -> HTMLResponse:
    conversation = _recover_html_conversation(conversation_id)
    if not (audio.content_type or "").startswith("audio/"):
        raise HTTPException(status_code=415, detail="Expected an audio recording")

    recording = await audio.read(MAX_AUDIO_BYTES + 1)
    if not recording or len(recording) > MAX_AUDIO_BYTES:
        raise HTTPException(status_code=413, detail="Audio must be between 1 byte and 12 MB")

    suffix = AUDIO_SUFFIXES.get(audio.content_type or "", ".webm")
    temporary_path = _write_temporary_audio(recording, suffix)
    wav_path: Path | None = None
    t_start = time.perf_counter()
    error = None
    try:
        wav_path = await _convert_to_wav(temporary_path)
        t_wav = time.perf_counter()
        transcript = await LocalWhisperTranscriber().transcribe(wav_path)
        t_stt = time.perf_counter()
        conversation.turns.append({"role": "user", "text": transcript})
        reply = await conversation.model.reply(transcript)
        t_llm = time.perf_counter()
        error = await _append_assistant_turn(conversation, reply)
        t_tts = time.perf_counter()
        logger.info(
            "Audio turn latency: audio_prep=%.3fs, stt=%.3fs, llm=%.3fs, tts=%.3fs, total=%.3fs",
            t_wav - t_start,
            t_stt - t_wav,
            t_llm - t_stt,
            t_tts - t_llm,
            t_tts - t_start,
        )
    except AudioConversionError as exception:
        error = str(exception)
    except LocalTranscriptionError as exception:
        error = str(exception)
    except CodexUnavailable as exception:
        error = str(exception)
    finally:
        await asyncio.to_thread(_remove_temporary_audio, temporary_path)
        if wav_path is not None:
            await asyncio.to_thread(_remove_temporary_audio, wav_path)
    russian_voice = os.environ.get("VOICE_OF_LUNA_RUSSIAN_VOICE", "Milena")
    return templates.TemplateResponse(
        request,
        "conversation.html",
        {"conversation": conversation, "error": error, "russian_voice": russian_voice},
    )


@app.get("/api/runtime")
async def runtime() -> dict[str, object]:
    return asdict(await CodexAppServer().status())


@app.post("/api/conversations", status_code=status.HTTP_201_CREATED)
async def create_conversation() -> dict[str, str]:
    conversation = Conversation(id=str(uuid4()))
    conversations[conversation.id] = conversation
    return {"id": conversation.id}


@app.post("/api/conversations/{conversation_id}/turns")
async def create_turn(conversation_id: str, body: TurnInput) -> dict[str, str]:
    conversation = conversations.get(conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    conversation.turns.append({"role": "user", "text": body.text})
    try:
        reply = await conversation.model.reply(body.text)
    except CodexUnavailable as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    await _append_assistant_turn(conversation, reply)
    return {"text": reply}


@app.delete("/api/conversations/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_conversation(conversation_id: str) -> None:
    if not await _close_and_delete_conversation(conversation_id):
        raise HTTPException(status_code=404, detail="Conversation not found")


@app.get("/speech/{clip_id}")
async def get_speech(clip_id: str) -> FileResponse:
    clip = speech_clips.pop(clip_id, None)
    if clip is None or not clip.path.is_file():
        raise HTTPException(status_code=404, detail="Speech clip not found")
    conversation = conversations.get(clip.conversation_id)
    if conversation is not None:
        for turn in conversation.turns:
            if turn.get("audio_url") == f"/speech/{clip_id}":
                turn.pop("audio_url", None)
    return FileResponse(
        clip.path,
        media_type="audio/mp4",
        background=BackgroundTask(_remove_temporary_audio, clip.path),
    )


async def _append_assistant_turn(conversation: Conversation, text: str) -> str | None:
    turn = {"role": "assistant", "text": text}
    try:
        speech_path = await LocalMacOsSpeaker().synthesize(text)
    except LocalSpeechError as exception:
        conversation.turns.append(turn)
        return str(exception)
    if speech_path is not None:
        clip_id = str(uuid4())
        speech_clips[clip_id] = SpeechClip(conversation_id=conversation.id, path=speech_path)
        turn["audio_url"] = f"/speech/{clip_id}"
    conversation.turns.append(turn)
    return None


def _recover_html_conversation(conversation_id: str) -> Conversation:
    """Keep a stale browser form usable after a local --reload restart.

    Conversations intentionally live only in process memory. A page rendered
    before a development-server restart has an obsolete id, but its next text
    or audio turn is still safe to use as the first turn of a new conversation.
    JSON routes remain strict so callers can distinguish a missing resource.
    """

    conversation = conversations.get(conversation_id)
    if conversation is not None:
        return conversation
    conversation = Conversation(id=str(uuid4()))
    conversations[conversation.id] = conversation
    return conversation


def _write_temporary_audio(recording: bytes, suffix: str) -> Path:
    descriptor, raw_path = tempfile.mkstemp(prefix="voice-of-luna-", suffix=suffix)
    with os.fdopen(descriptor, "wb") as destination:
        destination.write(recording)
    return Path(raw_path)


def _remove_temporary_audio(path: Path) -> None:
    path.unlink(missing_ok=True)


async def _convert_to_wav(source: Path) -> Path:
    if shutil.which("ffmpeg") is None:
        raise AudioConversionError("ffmpeg is required to decode this browser recording locally")
    descriptor, raw_destination = tempfile.mkstemp(prefix="voice-of-luna-", suffix=".wav")
    os.close(descriptor)
    destination = Path(raw_destination)
    process = await asyncio.create_subprocess_exec(
        "ffmpeg",
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(source),
        "-ac",
        "1",
        "-ar",
        "16000",
        "-c:a",
        "pcm_s16le",
        str(destination),
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    if await process.wait() == 0 and destination.stat().st_size:
        return destination
    _remove_temporary_audio(destination)
    raise AudioConversionError("The recording could not be decoded locally")
