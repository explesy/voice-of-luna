"""Minimal, local-only JSON-RPC client for the installed Codex app-server."""

from __future__ import annotations

import asyncio
import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class CodexUnavailable(RuntimeError):
    """Raised when the local Codex runtime cannot complete a request."""


@dataclass(frozen=True)
class RuntimeStatus:
    available: bool
    detail: str


class CodexAppServer:
    """One local Codex process and ephemeral thread for one conversation.

    The process inherits the operator's existing Codex login. This class never
    reads, stores, or returns credential files or tokens.
    """

    def __init__(self, *, command: str = "codex", workdir: Path | None = None) -> None:
        self.command = command
        self.workdir = workdir or Path("/tmp")
        self._process: asyncio.subprocess.Process | None = None
        self._thread_id: str | None = None
        self._next_id = 1

    async def status(self) -> RuntimeStatus:
        if shutil.which(self.command) is None:
            return RuntimeStatus(False, "Codex CLI is not installed")
        process = await asyncio.create_subprocess_exec(
            self.command,
            "login",
            "status",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        output, _ = await process.communicate()
        text = output.decode("utf-8", errors="replace").strip()
        if process.returncode == 0 and "Logged in" in text:
            return RuntimeStatus(True, "Local Codex is connected")
        return RuntimeStatus(False, "Sign in to Codex locally before starting a conversation")

    async def reply(self, text: str) -> str:
        if not text.strip():
            raise ValueError("Message must not be empty")
        return await self._reply_with_input([{"type": "text", "text": text}])

    async def _reply_with_input(self, input_items: list[dict[str, str]]) -> str:
        thread_id = await self._ensure_thread()
        try:
            turn = await self._request(
                "turn/start",
                {"threadId": thread_id, "input": input_items},
            )
            turn_id = turn["turn"]["id"]
            return await self._wait_for_answer(thread_id, turn_id)
        except CodexUnavailable:
            await self.close()
            raise
        except (KeyError, TypeError, asyncio.TimeoutError) as error:
            await self.close()
            raise CodexUnavailable("Codex app-server returned an unexpected response") from error

    async def _ensure_thread(self) -> str:
        if self._process is not None and self._process.returncode is None and self._thread_id:
            return self._thread_id

        await self.close()
        runtime = await self.status()
        if not runtime.available:
            raise CodexUnavailable(runtime.detail)

        self._process = await asyncio.create_subprocess_exec(
            self.command,
            "app-server",
            "--stdio",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            cwd=str(self.workdir),
        )
        try:
            await self._request(
                "initialize",
                {"clientInfo": {"name": "voice-of-luna", "version": "0.1.0"}},
            )
            await self._notify("initialized", {})
            thread = await self._request(
                "thread/start",
                {
                    "cwd": str(self.workdir),
                    "ephemeral": True,
                    "approvalPolicy": "never",
                    "sandbox": "read-only",
                    "baseInstructions": (
                        "You are the voice of a personal conversation app. "
                        "Reply naturally and concisely. Do not use tools, access files, "
                        "or describe internal reasoning."
                    ),
                },
            )
            self._thread_id = thread["thread"]["id"]
            return self._thread_id
        except CodexUnavailable:
            await self.close()
            raise
        except (KeyError, TypeError, asyncio.TimeoutError) as error:
            await self.close()
            raise CodexUnavailable("Codex app-server returned an unexpected response") from error

    async def _wait_for_answer(self, thread_id: str, turn_id: str) -> str:
        messages: list[str] = []
        while True:
            message = await self._read_message()
            if message.get("method") == "item/completed":
                params = message.get("params", {})
                item = params.get("item", {})
                if params.get("threadId") == thread_id and item.get("type") == "agentMessage":
                    messages.append(item.get("text", ""))
            if message.get("method") == "turn/completed":
                params = message.get("params", {})
                if params.get("threadId") == thread_id and params.get("turn", {}).get("id") == turn_id:
                    response = "\n".join(part for part in messages if part).strip()
                    if response:
                        return response
                    raise CodexUnavailable("Codex finished without a spoken response")

    async def _request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        request_id = self._next_id
        self._next_id += 1
        await self._write({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
        while True:
            message = await self._read_message()
            if message.get("id") != request_id:
                continue
            if "error" in message:
                raise CodexUnavailable(message["error"].get("message", "Codex rejected the request"))
            return message["result"]

    async def _notify(self, method: str, params: dict[str, Any]) -> None:
        await self._write({"jsonrpc": "2.0", "method": method, "params": params})

    async def _write(self, message: dict[str, Any]) -> None:
        if self._process is None or self._process.stdin is None:
            raise CodexUnavailable("Codex app-server is not running")
        self._process.stdin.write((json.dumps(message) + "\n").encode())
        await self._process.stdin.drain()

    async def _read_message(self) -> dict[str, Any]:
        if self._process is None or self._process.stdout is None:
            raise CodexUnavailable("Codex app-server is not running")
        raw = await asyncio.wait_for(self._process.stdout.readline(), timeout=120)
        if not raw:
            raise CodexUnavailable("Codex app-server stopped unexpectedly")
        return json.loads(raw)

    async def close(self) -> None:
        if self._process is None:
            return
        if self._process.returncode is None:
            self._process.terminate()
            try:
                await asyncio.wait_for(self._process.wait(), timeout=3)
            except asyncio.TimeoutError:
                self._process.kill()
                await self._process.wait()
        self._process = None
        self._thread_id = None
        self._next_id = 1
