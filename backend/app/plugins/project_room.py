from __future__ import annotations

import asyncio
import fnmatch
import subprocess
import time
from pathlib import Path
from typing import Any

from ..plugin_storage import PluginStorage
from .project_room_context import resolve_project_context
from .base import Plugin, PluginTurnResult, ToolCallContext, ToolResult, ToolSpec


def _text(value: Any) -> dict[str, Any]:
    return {"type": "text", "text": str(value)}


class ProjectRoomPlugin(Plugin):
    """First-party read-only project context and persistent memory tools."""

    id = "project_room"
    name = "Project Room"
    description = "Persistent project decisions plus safe read-only repository context"

    async def configure(self, settings: dict[str, Any]) -> dict[str, Any]:
        """Own and normalize Project Room's selected repository configuration."""
        raw_root = str(settings.get("root") or settings.get("project_root") or "").strip()
        if not raw_root:
            return {}
        try:
            context = resolve_project_context(Path(raw_root))
        except ValueError as exc:
            raise ValueError(str(exc)) from exc
        return {
            "root": str(context.root),
            "project_id": context.project_id,
            "github_repository": context.github_repository,
        }

    def panel_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "fields": [{
                "name": "root",
                "type": "directory",
                "label": "Project root",
                "placeholder": "/Users/you/projects/my-repo",
                "help": "Вставьте полный путь к корню Git-репозитория, затем нажмите APPLY.",
            }],
            "actions": [
                {"name": "refresh", "label": "Refresh"},
                {"name": "forget", "label": "Forget"},
            ],
        }

    def tool_context_metadata(self, settings: dict[str, Any]) -> dict[str, Any]:
        root = str(settings.get("root") or "").strip() or None
        project_id = str(settings.get("project_id") or "").strip()
        return {
            "project_root": root,
            "project_scope": f"project:{project_id}" if project_id else "plugin",
            "github_repository": settings.get("github_repository"),
        }

    async def before_turn(self, ctx) -> Any:
        """Inject a small, plugin-owned project card when a root is configured."""
        settings = ctx.metadata.get("plugin_settings", {})
        root = str(settings.get("root") or "").strip()
        if not root or ctx.state is None:
            return PluginTurnResult()
        card = await ctx.state.get("project_card")
        if not card:
            card = await asyncio.to_thread(self._build_project_card, Path(root))
            await ctx.state.set("project_card", card)
        return PluginTurnResult(prompt_context=card)

    async def action(self, name: str, settings: dict[str, Any], state: Any = None) -> dict[str, Any]:
        if name not in {"refresh", "forget"}:
            raise ValueError("Unknown Project Room action")
        if state is None:
            raise ValueError("Project Room state is unavailable")
        if name == "forget":
            await state.delete("project_card")
            return {"ok": True, "action": name, "card": None}
        root = str(settings.get("root") or "").strip()
        if not root:
            raise ValueError("No project root selected")
        card = await asyncio.to_thread(self._build_project_card, Path(root))
        await state.set("project_card", card)
        return {"ok": True, "action": name, "card": card}

    @staticmethod
    def _build_project_card(root: Path) -> str:
        """Build bounded metadata only; source files are read on demand by tools."""
        try:
            branch = subprocess.run(["git", "branch", "--show-current"], cwd=root, text=True, capture_output=True, check=False).stdout.strip() or "(detached)"
            status = subprocess.run(["git", "status", "--short"], cwd=root, text=True, capture_output=True, check=False).stdout.splitlines()
            tracked = subprocess.run(["git", "ls-files"], cwd=root, text=True, capture_output=True, check=False).stdout.splitlines()
        except OSError:
            return "Project card unavailable: Git metadata could not be read."
        important = [p for p in tracked if Path(p).name.lower() in {"readme.md", "agents.md", "pyproject.toml", "package.json", "changelog.md"}][:12]
        dirty = f"{len(status)} changed path(s)" if status else "clean"
        return (
            f"Project card (generated {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime())}). "
            f"Git branch: {branch}; working tree: {dirty}. "
            f"Important tracked documents: {', '.join(important) or 'none detected'}. "
            "Treat this as metadata, not proof of implementation; read relevant files with repo tools before making current code claims."
        )

    def tools(self) -> list[ToolSpec]:
        return [
            ToolSpec(
                "memory", "search", "Search persistent project memory.",
                {"type": "object", "properties": {"query": {"type": "string"}, "max_matches": {"type": "integer", "maximum": 40}}, "required": ["query"]},
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
                {"type": "object", "properties": {"path": {"type": "string"}, "start_line": {"type": "integer"}, "max_lines": {"type": "integer", "maximum": 200}}, "required": ["path"]},
                "repo.read",
            ),
            ToolSpec(
                "github", "issues", "List issues in the configured project repository.",
                {"type": "object", "properties": {"state": {"type": "string"}}},
                "network.read",
            ),
            ToolSpec(
                "github", "create_issue", "Create an issue in the configured project repository.",
                {"type": "object", "properties": {"title": {"type": "string"}, "body": {"type": "string"}}, "required": ["title", "body"]},
                "external.write",
            ),
        ]

    async def call_tool(self, name: str, arguments: dict[str, Any], ctx: ToolCallContext) -> ToolResult:
        qualified = str(ctx.metadata.get("tool_qualified_name", name))
        if qualified == "memory.search":
            if ctx.storage is None:
                raise RuntimeError("Plugin storage is unavailable")
            scope = str(ctx.metadata.get("project_scope", "plugin"))
            rows = await ctx.storage.search(
                self.id,
                str(arguments.get("query", "")),
                limit=min(40, max(1, int(arguments.get("max_matches", 8)))),
                scope=scope,
            )
            return ToolResult(content_items=[_text(rows or "No matching project memory found.")])
        if qualified == "memory.remember":
            if ctx.storage is None:
                raise RuntimeError("Plugin storage is unavailable")
            doc_id = await ctx.storage.remember(
                self.id,
                str(ctx.metadata.get("project_scope", "plugin")),
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
            start_line = max(1, int(arguments.get("start_line", 1)))
            max_lines = min(200, max(1, int(arguments.get("max_lines", 120))))
            lines = path.read_text(encoding="utf-8").splitlines()
            return ToolResult(
                content_items=[_text("\n".join(lines[start_line - 1:start_line - 1 + max_lines]))],
                metadata={"evidence": [{"kind": "file", "path": str(path.relative_to(root)), "start_line": start_line, "end_line": min(len(lines), start_line - 1 + max_lines)}]},
            )
        if qualified == "repo.search":
            query = str(arguments.get("query", "")).strip()
            if not query:
                return ToolResult(content_items=[_text("Search query is empty")])
            matches = await asyncio.to_thread(self._search_repo, root, query, min(40, max(1, int(arguments.get("max_matches", 20)))))
            evidence = []
            for match in matches:
                head = match.split(":", 2)
                if len(head) >= 2 and head[1].isdigit():
                    evidence.append({"kind": "search", "path": head[0], "line": int(head[1])})
            return ToolResult(content_items=[_text("\n".join(matches) or "No repository matches found.")], metadata={"evidence": evidence[:40]})
        if qualified == "github.issues":
            if ctx.github is None:
                raise RuntimeError("GitHub gateway is unavailable")
            issues = await ctx.github.issues(str(arguments.get("state", "open")), ctx.metadata.get("github_repository"))
            summary = [{"number": item.get("number"), "title": str(item.get("title", ""))[:200], "state": item.get("state"), "url": item.get("html_url")} for item in issues[:10]]
            return ToolResult(content_items=[_text(summary)])
        if qualified == "github.create_issue":
            if ctx.github is None:
                raise RuntimeError("GitHub gateway is unavailable")
            issue = await ctx.github.create_issue(str(arguments.get("title", "")), str(arguments.get("body", "")), ctx.metadata.get("github_repository"))
            return ToolResult(content_items=[_text({"number": issue.get("number"), "url": issue.get("html_url")})])
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
        if not cls_is_allowed_project_file(root, candidate):
            return None
        return candidate

    @classmethod
    def _search_repo(cls, root: Path, query: str, max_matches: int = 20) -> list[str]:
        results: list[str] = []
        ignored = {".git", "node_modules", ".venv", "__pycache__", "data", ".aws", ".ssh", ".gnupg"}
        for path in root.rglob("*"):
            if len(results) >= max_matches or not path.is_file() or any(part in ignored for part in path.relative_to(root).parts) or not cls_is_allowed_project_file(root, path):
                continue
            try:
                if path.stat().st_size > 512_000:
                    continue
                for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                    if query.casefold() in line.casefold():
                        results.append(f"{path.relative_to(root)}:{line_no}: {line[:300]}")
                        if len(results) >= max_matches:
                            break
            except (OSError, UnicodeDecodeError):
                continue
        return results


def cls_is_allowed_project_file(root: Path, candidate: Path) -> bool:
    """Default-deny local credentials and ignored files before model access."""
    try:
        rel = candidate.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    parts = rel.parts
    if any(part in {".git", ".aws", ".ssh", ".gnupg", "node_modules", ".venv", "__pycache__", "data"} for part in parts):
        return False
    name = candidate.name.lower()
    blocked = {".npmrc", ".netrc", "credentials.json", "credentials.yml", "secrets.json"}
    if name in blocked or name.startswith(".env") or any(fnmatch.fnmatch(name, pattern) for pattern in ("*.pem", "*.key", "*.p12", "*.pfx", "*credentials*", "*secret*")):
        return False
    if candidate.is_symlink() or not candidate.is_file():
        return False
    try:
        ignored = subprocess.run(["git", "check-ignore", "-q", "--", str(candidate)], cwd=root, capture_output=True, check=False)
        if ignored.returncode == 0:
            return False
    except OSError:
        pass
    return True
