"""Capability gateway for optional GitHub access.

Plugins receive this gateway, never the token it uses. Network access is
disabled unless both repository and token are configured in the local runtime.
"""

from __future__ import annotations

import os
from typing import Any

import httpx


class GitHubGateway:
    def __init__(self) -> None:
        self.token = os.environ.get("VOICE_OF_LUNA_GITHUB_TOKEN", "")
        self.repository = os.environ.get("VOICE_OF_LUNA_GITHUB_REPOSITORY", "")

    def _url(self, suffix: str, repository: str | None = None) -> str:
        target = repository or self.repository
        if not target or "/" not in target:
            raise RuntimeError("GitHub repository is not configured")
        if target != self.repository and self.repository and target != self.repository:
            raise RuntimeError("GitHub repository does not match configured target")
        return f"https://api.github.com/repos/{target}/{suffix}"

    def _headers(self) -> dict[str, str]:
        if not self.token:
            raise RuntimeError("GitHub token is not configured")
        return {"Authorization": f"Bearer {self.token}", "Accept": "application/vnd.github+json"}

    async def issues(self, state: str = "open", repository: str | None = None) -> list[dict[str, Any]]:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(self._url("issues", repository), headers=self._headers(), params={"state": state, "per_page": 30})
            response.raise_for_status()
            return response.json()

    async def create_issue(self, title: str, body: str, repository: str | None = None) -> dict[str, Any]:
        if not title.strip() or len(title) > 200:
            raise ValueError("Issue title is required and must be short")
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                self._url("issues", repository),
                headers=self._headers(),
                json={"title": title.strip(), "body": body[:10000]},
            )
            response.raise_for_status()
            return response.json()
