from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from ..plugin_storage import PluginStorage
from .base import Plugin, ToolCallContext, ToolResult, ToolSpec


def _text(value: Any) -> dict[str, Any]:
    return {"type": "text", "text": str(value)}


class ProjectRoomPlugin(Plugin):
    """First-party read-only project context and persistent memory tools."""

    id = "project_room"
    name = "Project Room"
    description = "Persistent project decisions plus safe read-only repository context"

    def tools(self) -> list[ToolSpec]:
        return [
            ToolSpec(
                "memory", "search", "Search persistent project memory.",
                {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
                "storage.read",
            ),
            ToolSpec(
                "memory", "remember", "Store a project decision, fact, TODO, or question.",
                {
                    "type": "object",
                    "properties": {
                        "text": {"type": "string"},
                        "kind": {"type": "string"},
                        "tags": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["text"],
                },
                "storage.write",
            ),
            ToolSpec(
                "repo", "search", "Search text in the selected project repository.",
                {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
                "repo.read",
            ),
            ToolSpec(
                "repo", "read", "Read a UTF-8 text file from the selected project.",
                {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
                "repo.read",
            ),
        ]

    async def call_tool(self, name: str, arguments: dict[str, Any], ctx: ToolCallContext) -> ToolResult:
        qualified = str(ctx.metadata.get("tool_qualified_name", name))
        if qualified == "memory.search":
            if ctx.storage is None:
                raise RuntimeError("Plugin storage is unavailable")
            rows = await ctx.storage.search(self.id, str(arguments.get("query", "")))
            return ToolResult(content_items=[_text(rows or "No matching project memory found.")])
        if qualified == "memory.remember":
            if ctx.storage is None:
                raise RuntimeError("Plugin storage is unavailable")
            doc_id = await ctx.storage.remember(
                self.id,
                "plugin",
                str(arguments.get("text", "")),
                str(arguments.get("kind", "note")),
                arguments.get("tags") if isinstance(arguments.get("tags"), list) else [],
            )
            return ToolResult(content_items=[_text(f"Remembered project note #{doc_id}.")])
        root = self._project_root(ctx)
        if qualified == "repo.read":
            path = self._safe_path(root, str(arguments.get("path", "")))
            if path is None:
                raise ValueError("Path is outside the selected project")
            if not path.is_file() or path.stat().st_size > 512_000:
                raise ValueError("File is missing or too large")
            return ToolResult(content_items=[_text(path.read_text(encoding="utf-8"))])
        if qualified == "repo.search":
            query = str(arguments.get("query", "")).strip()
            if not query:
                return ToolResult(content_items=[_text("Search query is empty")])
            matches = await asyncio.to_thread(self._search_repo, root, query)
            return ToolResult(content_items=[_text("\n".join(matches) or "No repository matches found.")])
        raise ValueError(f"Unknown Project Room tool: {name}")

    @staticmethod
    def _project_root(ctx: ToolCallContext) -> Path:
        raw = ctx.metadata.get("project_root")
        if not raw:
            raise ValueError("No project root selected")
        root = Path(str(raw)).expanduser().resolve()
        if not root.is_dir():
            raise ValueError("Selected project root does not exist")
        return root

    @staticmethod
    def _safe_path(root: Path, relative: str) -> Path | None:
        candidate = (root / relative).resolve()
        try:
            candidate.relative_to(root)
        except ValueError:
            return None
        if any(part in {".env", ".git"} for part in candidate.relative_to(root).parts):
            return None
        return candidate

    @classmethod
    def _search_repo(cls, root: Path, query: str) -> list[str]:
        results: list[str] = []
        ignored = {".git", "node_modules", ".venv", "__pycache__", "data"}
        for path in root.rglob("*"):
            if len(results) >= 40 or not path.is_file() or any(part in ignored for part in path.parts):
                continue
            try:
                if path.stat().st_size > 512_000:
                    continue
                for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                    if query.casefold() in line.casefold():
                        results.append(f"{path.relative_to(root)}:{line_no}: {line[:300]}")
                        if len(results) >= 40:
                            break
            except (OSError, UnicodeDecodeError):
                continue
        return results
