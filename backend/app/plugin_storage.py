"""Small local persistent store for capability-limited plugins.

The store deliberately uses one SQLite file and FTS5.  It is not a general
database handle: every operation supplies a plugin namespace and conversations
cannot read another plugin's records.
"""

from __future__ import annotations

import asyncio
import os
import sqlite3
import time
from pathlib import Path
from typing import Any


class PluginStorage:
    def __init__(self, path: Path | None = None) -> None:
        configured = os.environ.get("VOICE_OF_LUNA_PLUGIN_DB")
        self.path = path or Path(configured or "data/voice_of_luna_plugins.sqlite3")

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path)
        db.row_factory = sqlite3.Row
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS plugin_kv (
                plugin_id TEXT NOT NULL,
                scope TEXT NOT NULL,
                item_key TEXT NOT NULL,
                item_value TEXT NOT NULL,
                updated_at REAL NOT NULL,
                PRIMARY KEY (plugin_id, scope, item_key)
            );
            CREATE TABLE IF NOT EXISTS plugin_documents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                plugin_id TEXT NOT NULL,
                scope TEXT NOT NULL,
                kind TEXT NOT NULL,
                text TEXT NOT NULL,
                tags TEXT NOT NULL DEFAULT '',
                created_at REAL NOT NULL
            );
            CREATE VIRTUAL TABLE IF NOT EXISTS plugin_documents_fts USING fts5(
                text, tags, content='plugin_documents', content_rowid='id'
            );
            CREATE TRIGGER IF NOT EXISTS plugin_documents_ai AFTER INSERT ON plugin_documents BEGIN
                INSERT INTO plugin_documents_fts(rowid, text, tags)
                VALUES (new.id, new.text, new.tags);
            END;
            """
        )
        return db

    async def set(self, plugin_id: str, scope: str, key: str, value: str) -> None:
        def write() -> None:
            with self._connect() as db:
                db.execute(
                    """INSERT INTO plugin_kv(plugin_id, scope, item_key, item_value, updated_at)
                       VALUES (?, ?, ?, ?, ?)
                       ON CONFLICT(plugin_id, scope, item_key) DO UPDATE SET
                       item_value=excluded.item_value, updated_at=excluded.updated_at""",
                    (plugin_id, scope, key, value, time.time()),
                )

        await asyncio.to_thread(write)

    async def get(self, plugin_id: str, scope: str, key: str) -> str | None:
        def read() -> str | None:
            with self._connect() as db:
                row = db.execute(
                    "SELECT item_value FROM plugin_kv WHERE plugin_id=? AND scope=? AND item_key=?",
                    (plugin_id, scope, key),
                ).fetchone()
                return str(row[0]) if row else None

        return await asyncio.to_thread(read)

    async def remember(
        self,
        plugin_id: str,
        scope: str,
        text: str,
        kind: str = "note",
        tags: list[str] | None = None,
    ) -> int:
        if not text.strip():
            raise ValueError("Memory text must not be empty")
        normalized_tags = ",".join(str(tag).strip() for tag in (tags or []) if str(tag).strip())

        def write() -> int:
            with self._connect() as db:
                cursor = db.execute(
                    """INSERT INTO plugin_documents(plugin_id, scope, kind, text, tags, created_at)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (plugin_id, scope, kind[:80], text[:12000], normalized_tags[:1000], time.time()),
                )
                return int(cursor.lastrowid)

        return await asyncio.to_thread(write)

    async def search(self, plugin_id: str, query: str, limit: int = 8) -> list[dict[str, Any]]:
        query = query.strip()
        if not query:
            return []
        safe_limit = max(1, min(int(limit), 20))

        def read() -> list[dict[str, Any]]:
            with self._connect() as db:
                # FTS syntax is intentionally constrained to plain terms.
                terms = " ".join(f'"{part.replace(chr(34), "")}"' for part in query.split()[:12])
                rows = db.execute(
                    """SELECT d.id, d.scope, d.kind, d.text, d.tags, d.created_at
                       FROM plugin_documents_fts f
                       JOIN plugin_documents d ON d.id=f.rowid
                       WHERE d.plugin_id=? AND plugin_documents_fts MATCH ?
                       ORDER BY rank LIMIT ?""",
                    (plugin_id, terms, safe_limit),
                ).fetchall()
                return [dict(row) for row in rows]

        return await asyncio.to_thread(read)
