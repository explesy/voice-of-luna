from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from app.plugin_storage import PluginStorage
from app.plugins.project_room import ProjectRoomPlugin
from app.plugins.base import ToolCallContext


def test_project_room_memory_survives_new_storage_instance(tmp_path: Path) -> None:
    async def exercise() -> None:
        db_path = tmp_path / "plugins.sqlite"
        first = PluginStorage(db_path)
        plugin = ProjectRoomPlugin()
        ctx = ToolCallContext(
            "one", plugin.id, "default",
            metadata={"tool_qualified_name": "memory.remember"}, storage=first,
        )
        remembered = await plugin.call_tool(
            "remember", {"text": "Use native dynamic tools", "kind": "decision"}, ctx
        )
        assert "Remembered" in remembered.content_items[0]["text"]

        second = PluginStorage(db_path)
        found = await plugin.call_tool(
            "search", {"query": "dynamic tools"},
            ToolCallContext(
                "two", plugin.id, "default",
                metadata={"tool_qualified_name": "memory.search"}, storage=second,
            ),
        )
        assert "native dynamic tools" in found.content_items[0]["text"]

    asyncio.run(exercise())


def test_project_room_repo_tools_cannot_escape_root(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("dynamic tools are here\n", encoding="utf-8")
    outside = tmp_path.parent / "outside-secret.txt"
    outside.write_text("secret", encoding="utf-8")

    async def exercise() -> None:
        plugin = ProjectRoomPlugin()
        ctx = ToolCallContext(
            "one", plugin.id, "default",
            metadata={"project_root": str(tmp_path), "tool_qualified_name": "repo.search"},
        )
        found = await plugin.call_tool("search", {"query": "dynamic"}, ctx)
        assert "README.md:1" in found.content_items[0]["text"]

        safe = await plugin.call_tool(
            "read", {"path": "README.md"},
            ToolCallContext(
                "one", plugin.id, "default",
                metadata={"project_root": str(tmp_path), "tool_qualified_name": "repo.read"},
            ),
        )
        assert "dynamic tools" in safe.content_items[0]["text"]

        with pytest.raises(ValueError, match="outside"):
            await plugin.call_tool(
                "read", {"path": "../outside-secret.txt"},
                ToolCallContext(
                    "one", plugin.id, "default",
                    metadata={"project_root": str(tmp_path), "tool_qualified_name": "repo.read"},
                ),
            )

    asyncio.run(exercise())
