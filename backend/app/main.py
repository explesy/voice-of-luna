from __future__ import annotations

from dataclasses import asdict, dataclass, field
from uuid import uuid4

from fastapi import FastAPI, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from .codex import CodexAppServer, CodexUnavailable


app = FastAPI(title="Voice of Luna", version="0.1.0")
app.mount("/static", StaticFiles(directory="app/static"), name="static")
templates = Jinja2Templates(directory="app/templates")


@dataclass
class Conversation:
    id: str
    turns: list[dict[str, str]] = field(default_factory=list)


conversations: dict[str, Conversation] = {}


class TurnInput(BaseModel):
    text: str = Field(min_length=1, max_length=8_000)


@app.get("/healthz")
async def healthz() -> dict[str, object]:
    runtime = await CodexAppServer().status()
    return {"ok": True, "codex": asdict(runtime)}


@app.get("/", response_class=HTMLResponse)
async def home(request: Request) -> HTMLResponse:
    runtime = await CodexAppServer().status()
    return templates.TemplateResponse(request, "index.html", {"runtime": runtime})


@app.post("/conversations", response_class=HTMLResponse)
async def create_conversation_fragment(request: Request) -> HTMLResponse:
    conversation = Conversation(id=str(uuid4()))
    conversations[conversation.id] = conversation
    return templates.TemplateResponse(request, "conversation.html", {"conversation": conversation})


@app.post("/conversations/{conversation_id}/turns", response_class=HTMLResponse)
async def create_turn_fragment(
    request: Request, conversation_id: str, text: str = Form(min_length=1, max_length=8_000)
) -> HTMLResponse:
    conversation = conversations.get(conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    conversation.turns.append({"role": "user", "text": text})
    try:
        reply = await CodexAppServer().reply(text)
        conversation.turns.append({"role": "assistant", "text": reply})
        error = None
    except CodexUnavailable as exception:
        error = str(exception)
    return templates.TemplateResponse(
        request, "conversation.html", {"conversation": conversation, "error": error}
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
        reply = await CodexAppServer().reply(body.text)
    except CodexUnavailable as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    conversation.turns.append({"role": "assistant", "text": reply})
    return {"text": reply}


@app.delete("/api/conversations/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_conversation(conversation_id: str) -> None:
    if conversations.pop(conversation_id, None) is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
