"""Session persistence: SQLite for metadata + search, JSONL for the transcript.

Unlike the previous implementation this stores *complete* messages -- tool
calls, tool results and reasoning included -- so resuming a session restores
the exact conversation the model had, not a lossy text summary of it.

    ~/.ollamacode/sessions/
      sessions.db
      <id>/messages.jsonl
      <id>/checkpoints.jsonl + blobs/
"""

from __future__ import annotations

import asyncio
import builtins
import json
import sqlite3
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 2


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class SessionStore:
    def __init__(self, base_dir: Path | None = None):
        self.base_dir = Path(base_dir or (Path.home() / ".ollamacode"))
        self.sessions_dir = self.base_dir / "sessions"
        self.sessions_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.sessions_dir / "sessions.db"
        self.current_id: str | None = None
        self._init_db()

    # -- database --------------------------------------------------------

    @contextmanager
    def _db(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _init_db(self) -> None:
        with self._db() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    title TEXT,
                    project_path TEXT,
                    model TEXT,
                    message_count INTEGER DEFAULT 0,
                    prompt_tokens INTEGER DEFAULT 0,
                    completion_tokens INTEGER DEFAULT 0,
                    parent_id TEXT,
                    branch_point INTEGER,
                    status TEXT DEFAULT 'active',
                    schema_version INTEGER DEFAULT 2
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    seq INTEGER NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT,
                    timestamp TEXT NOT NULL
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_msg_session ON messages(session_id, seq)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_sessions_updated ON sessions(updated_at DESC)")

            has_fts = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='messages_fts'"
            ).fetchone()
            if not has_fts:
                conn.execute(
                    """
                    CREATE VIRTUAL TABLE messages_fts USING fts5(
                        content, session_id UNINDEXED,
                        content='messages', content_rowid='id'
                    )
                    """
                )
                conn.execute(
                    """
                    CREATE TRIGGER IF NOT EXISTS messages_ai AFTER INSERT ON messages BEGIN
                        INSERT INTO messages_fts(rowid, content, session_id)
                        VALUES (new.id, new.content, new.session_id);
                    END
                    """
                )
                conn.execute(
                    """
                    CREATE TRIGGER IF NOT EXISTS messages_ad AFTER DELETE ON messages BEGIN
                        INSERT INTO messages_fts(messages_fts, rowid, content, session_id)
                        VALUES('delete', old.id, old.content, old.session_id);
                    END
                    """
                )

            # forward-compatible column adds for databases written by 0.2.x
            existing = {row["name"] for row in conn.execute("PRAGMA table_info(sessions)")}
            for column, ddl in (
                ("prompt_tokens", "INTEGER DEFAULT 0"),
                ("completion_tokens", "INTEGER DEFAULT 0"),
                ("parent_id", "TEXT"),
                ("schema_version", "INTEGER DEFAULT 2"),
            ):
                if column not in existing:
                    try:
                        conn.execute(f"ALTER TABLE sessions ADD COLUMN {column} {ddl}")
                    except sqlite3.OperationalError:
                        pass

    # -- lifecycle -------------------------------------------------------

    def create(
        self,
        project_path: str | None = None,
        model: str | None = None,
        parent_id: str | None = None,
        branch_point: int | None = None,
    ) -> str:
        session_id = uuid.uuid4().hex[:8]
        now = _now()
        with self._db() as conn:
            conn.execute(
                "INSERT INTO sessions (id, created_at, updated_at, project_path, model, parent_id, branch_point)"
                " VALUES (?,?,?,?,?,?,?)",
                (session_id, now, now, project_path, model, parent_id, branch_point),
            )
        directory = self.sessions_dir / session_id
        directory.mkdir(exist_ok=True)
        (directory / "messages.jsonl").touch()
        self.current_id = session_id
        return session_id

    def directory(self, session_id: str | None = None) -> Path:
        sid = session_id or self.current_id
        if not sid:
            raise ValueError("no active session")
        path = self.sessions_dir / sid
        path.mkdir(parents=True, exist_ok=True)
        return path

    # -- messages --------------------------------------------------------

    def append(self, message: dict[str, Any], session_id: str | None = None) -> None:
        sid = session_id or self.current_id
        if not sid:
            return

        record = dict(message)
        record["timestamp"] = _now()
        # images are large and already on disk; keep the transcript readable
        if record.get("images"):
            record["images"] = [f"<{len(record['images'])} image(s)>"]

        try:
            with (self.sessions_dir / sid / "messages.jsonl").open("a") as handle:
                handle.write(json.dumps(record, default=str) + "\n")
        except OSError:
            return

        content = record.get("content") or ""
        if not isinstance(content, str):
            content = json.dumps(content, default=str)

        with self._db() as conn:
            row = conn.execute(
                "SELECT COALESCE(MAX(seq), -1) AS s FROM messages WHERE session_id=?", (sid,)
            ).fetchone()
            conn.execute(
                "INSERT INTO messages (session_id, seq, role, content, timestamp) VALUES (?,?,?,?,?)",
                (sid, (row["s"] if row else -1) + 1, record.get("role", "?"), content, record["timestamp"]),
            )
            conn.execute(
                "UPDATE sessions SET updated_at=?, message_count=message_count+1 WHERE id=?",
                (record["timestamp"], sid),
            )

    async def append_async(self, message: dict[str, Any], session_id: str | None = None) -> None:
        await asyncio.to_thread(self.append, message, session_id)

    def load_messages(self, session_id: str | None = None) -> builtins.list[dict[str, Any]]:
        sid = session_id or self.current_id
        if not sid:
            return []
        path = self.sessions_dir / sid / "messages.jsonl"
        if not path.exists():
            return []
        messages: list[dict[str, Any]] = []
        for line in path.read_text().splitlines():
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            record.pop("timestamp", None)
            record.pop("images", None)
            messages.append(record)
        return messages

    def record_usage(self, prompt_tokens: int, completion_tokens: int, session_id: str | None = None) -> None:
        sid = session_id or self.current_id
        if not sid:
            return
        with self._db() as conn:
            conn.execute(
                "UPDATE sessions SET prompt_tokens=?, completion_tokens=completion_tokens+?, updated_at=? WHERE id=?",
                (prompt_tokens, completion_tokens, _now(), sid),
            )

    # -- queries ---------------------------------------------------------

    def info(self, session_id: str | None = None) -> dict[str, Any] | None:
        sid = session_id or self.current_id
        if not sid:
            return None
        with self._db() as conn:
            row = conn.execute("SELECT * FROM sessions WHERE id=?", (sid,)).fetchone()
            return dict(row) if row else None

    def list(self, limit: int = 20, project_path: str | None = None, status: str = "active") -> builtins.list[dict[str, Any]]:
        query = "SELECT * FROM sessions WHERE status=?"
        params: list[Any] = [status]
        if project_path:
            query += " AND project_path=?"
            params.append(project_path)
        query += " ORDER BY updated_at DESC LIMIT ?"
        params.append(limit)
        with self._db() as conn:
            return [dict(row) for row in conn.execute(query, params).fetchall()]

    def search(self, query: str, limit: int = 20) -> builtins.list[dict[str, Any]]:
        with self._db() as conn:
            try:
                rows = conn.execute(
                    """
                    SELECT m.session_id, m.role, s.title, s.updated_at,
                           snippet(messages_fts, 0, '[', ']', '...', 24) AS snippet
                    FROM messages_fts
                    JOIN messages m ON messages_fts.rowid = m.id
                    JOIN sessions s ON s.id = m.session_id
                    WHERE messages_fts MATCH ?
                    ORDER BY rank LIMIT ?
                    """,
                    (query, limit),
                ).fetchall()
            except sqlite3.OperationalError:
                return []
            return [dict(row) for row in rows]

    def set_title(self, title: str, session_id: str | None = None) -> None:
        sid = session_id or self.current_id
        if not sid:
            return
        with self._db() as conn:
            conn.execute("UPDATE sessions SET title=?, updated_at=? WHERE id=?", (title, _now(), sid))

    def set_status(self, status: str, session_id: str | None = None) -> None:
        sid = session_id or self.current_id
        if not sid:
            return
        with self._db() as conn:
            conn.execute("UPDATE sessions SET status=?, updated_at=? WHERE id=?", (status, _now(), sid))

    def set_model(self, model: str, session_id: str | None = None) -> None:
        sid = session_id or self.current_id
        if not sid:
            return
        with self._db() as conn:
            conn.execute("UPDATE sessions SET model=?, updated_at=? WHERE id=?", (model, _now(), sid))

    def resolve_id(self, prefix: str) -> str | None:
        """Accept a short id prefix, like git does."""
        with self._db() as conn:
            row = conn.execute(
                "SELECT id FROM sessions WHERE id LIKE ? ORDER BY updated_at DESC LIMIT 1",
                (prefix + "%",),
            ).fetchone()
            return row["id"] if row else None

    def branch(self, session_id: str | None = None, at: int | None = None) -> str:
        sid = session_id or self.current_id
        if not sid:
            raise ValueError("no session to branch")
        original = self.info(sid)
        if not original:
            raise ValueError(f"unknown session {sid}")

        messages = self.load_messages(sid)
        if at is not None:
            messages = messages[:at]

        new_id = self.create(
            project_path=original.get("project_path"),
            model=original.get("model"),
            parent_id=sid,
            branch_point=len(messages),
        )
        for message in messages:
            self.append(message, session_id=new_id)
        if original.get("title"):
            self.set_title(f"{original['title']} (branch)", session_id=new_id)
        return new_id

    def export_markdown(self, session_id: str | None = None) -> str:
        sid = session_id or self.current_id
        info = self.info(sid) or {}
        lines = [
            f"# {info.get('title') or 'OllamaCoder session ' + str(sid)}",
            "",
            f"- **Session**: `{sid}`",
            f"- **Model**: {info.get('model') or 'n/a'}",
            f"- **Project**: {info.get('project_path') or 'n/a'}",
            f"- **Created**: {info.get('created_at')}",
            "",
            "---",
            "",
        ]
        for message in self.load_messages(sid):
            role = str(message.get("role", "?")).upper()
            content = message.get("content") or ""
            if role == "TOOL":
                lines.append(f"### 🔧 tool: {message.get('name', '?')}")
                lines.append("```\n" + str(content)[:4000] + "\n```")
            else:
                lines.append(f"### {role}")
                lines.append(str(content))
            for call in message.get("tool_calls") or []:
                function = call.get("function", {}) if isinstance(call, dict) else {}
                lines.append(f"> calls `{function.get('name')}` with `{function.get('arguments')}`")
            lines.append("")
        return "\n".join(lines)

    def delete(self, session_id: str, hard: bool = False) -> None:
        if not hard:
            self.set_status("deleted", session_id)
            return
        with self._db() as conn:
            conn.execute("DELETE FROM messages WHERE session_id=?", (session_id,))
            conn.execute("DELETE FROM sessions WHERE id=?", (session_id,))
        directory = self.sessions_dir / session_id
        if directory.exists():
            import shutil

            shutil.rmtree(directory, ignore_errors=True)

    @property
    def short_id(self) -> str:
        return (self.current_id or "")[:6]
