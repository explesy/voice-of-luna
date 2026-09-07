"""Manager for a resident whisper-server process to avoid model reload latency."""

from __future__ import annotations

import asyncio
import os
import shutil
from pathlib import Path

import httpx


class WhisperServerManager:
    """Manages a background whisper-server instance on a local loopback port."""

    def __init__(
        self,
        *,
        host: str | None = None,
        port: int | None = None,
        model_path: Path | None = None,
        language: str | None = None,
    ) -> None:
        project_root = Path(__file__).resolve().parents[2]
        self.host = host or os.environ.get("VOICE_OF_LUNA_WHISPER_HOST", "127.0.0.1")
        self.port = port or int(os.environ.get("VOICE_OF_LUNA_WHISPER_PORT", "8089"))
        configured_path = os.environ.get("VOICE_OF_LUNA_WHISPER_MODEL")
        self.model_path = model_path or Path(configured_path or project_root / "data/models/ggml-small.bin")
        self.language = language or os.environ.get("VOICE_OF_LUNA_WHISPER_LANGUAGE", "auto")
        self.threads = min(os.cpu_count() or 4, 8)
        self._process: asyncio.subprocess.Process | None = None
        self._http_client: httpx.AsyncClient | None = None

    @property
    def server_url(self) -> str:
        return f"http://{self.host}:{self.port}"

    @property
    def inference_url(self) -> str:
        return f"{self.server_url}/inference"

    def _get_http_client(self) -> httpx.AsyncClient:
        if self._http_client is None or self._http_client.is_closed:
            self._http_client = httpx.AsyncClient(
                timeout=httpx.Timeout(0.5, connect=0.2),
                limits=httpx.Limits(max_keepalive_connections=2, max_connections=4),
            )
        return self._http_client

    async def is_healthy(self) -> bool:
        try:
            client = self._get_http_client()
            response = await client.get(self.server_url)
            return response.status_code in (200, 404, 405)
        except (httpx.HTTPError, OSError):
            return False

    async def start(self) -> bool:
        enabled = os.environ.get("VOICE_OF_LUNA_WHISPER_SERVER", "true").lower() not in ("false", "0", "no")
        if not enabled:
            return False
        if shutil.which("whisper-server") is None or not self.model_path.is_file():
            return False

        if await self.is_healthy():
            return True

        self._process = await asyncio.create_subprocess_exec(
            "whisper-server",
            "-m",
            str(self.model_path),
            "--host",
            self.host,
            "--port",
            str(self.port),
            "-l",
            self.language,
            "-t",
            str(self.threads),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )

        for _ in range(30):
            if await self.is_healthy():
                return True
            if self._process.returncode is not None:
                self._process = None
                return False
            await asyncio.sleep(0.1)

        await self.close()
        return False

    async def close(self) -> None:
        if self._http_client is not None:
            client = self._http_client
            self._http_client = None
            if not client.is_closed:
                try:
                    await client.aclose()
                except Exception:
                    pass
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
