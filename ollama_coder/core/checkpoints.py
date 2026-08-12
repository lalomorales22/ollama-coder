"""File checkpoints: an undo stack for anything the agent writes.

Before a file is modified its previous bytes (or the fact that it did not
exist) are copied into the session directory. `/undo` walks the stack back.
This is deliberately independent of git so it also protects untracked files
and works in directories that are not repositories at all.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass
class Checkpoint:
    id: str
    path: str          # absolute path of the file that was touched
    blob: str | None  # stored copy of the previous content, None if it did not exist
    timestamp: float
    label: str = ""

    @property
    def existed(self) -> bool:
        return self.blob is not None


class CheckpointStore:
    def __init__(self, root: Path, enabled: bool = True, max_entries: int = 200):
        self.root = Path(root)
        self.enabled = enabled
        self.max_entries = max_entries
        self.blobs = self.root / "blobs"
        self.index_path = self.root / "checkpoints.jsonl"
        self._entries: list[Checkpoint] = []
        self._lock = asyncio.Lock()
        if self.enabled:
            self.blobs.mkdir(parents=True, exist_ok=True)
            self._load()

    def _load(self) -> None:
        if not self.index_path.exists():
            return
        try:
            for line in self.index_path.read_text().splitlines():
                if line.strip():
                    self._entries.append(Checkpoint(**json.loads(line)))
        except (OSError, ValueError, TypeError):
            self._entries = []

    async def snapshot(self, path: Path, label: str = "") -> Checkpoint | None:
        """Record the current state of `path` before it is written."""
        if not self.enabled:
            return None

        async with self._lock:
            entry_id = uuid.uuid4().hex[:10]
            blob_name: str | None = None
            if path.exists() and path.is_file():
                blob_name = f"{entry_id}{path.suffix or '.bin'}"
                try:
                    await asyncio.to_thread(shutil.copy2, path, self.blobs / blob_name)
                except OSError:
                    return None

            entry = Checkpoint(
                id=entry_id,
                path=str(path),
                blob=blob_name,
                timestamp=time.time(),
                label=label or path.name,
            )
            self._entries.append(entry)
            try:
                with self.index_path.open("a") as handle:
                    handle.write(json.dumps(asdict(entry)) + "\n")
            except OSError:
                pass

            if len(self._entries) > self.max_entries:
                self._prune()
            return entry

    def _prune(self) -> None:
        excess = self._entries[: len(self._entries) - self.max_entries]
        self._entries = self._entries[len(excess) :]
        for entry in excess:
            if entry.blob:
                try:
                    (self.blobs / entry.blob).unlink(missing_ok=True)
                except OSError:
                    pass
        try:
            self.index_path.write_text(
                "".join(json.dumps(asdict(e)) + "\n" for e in self._entries)
            )
        except OSError:
            pass

    async def undo_last(self) -> str | None:
        """Restore the most recent snapshot. Returns a description, or None."""
        async with self._lock:
            if not self._entries:
                return None
            entry = self._entries.pop()
            target = Path(entry.path)
            try:
                if entry.blob:
                    await asyncio.to_thread(
                        shutil.copy2, self.blobs / entry.blob, target
                    )
                    action = f"restored {target.name}"
                else:
                    if target.exists():
                        await asyncio.to_thread(target.unlink)
                    action = f"removed {target.name} (it did not exist before)"
            except OSError as exc:
                return f"undo failed: {exc}"

            try:
                self.index_path.write_text(
                    "".join(json.dumps(asdict(e)) + "\n" for e in self._entries)
                )
            except OSError:
                pass
            return action

    def recent(self, limit: int = 20) -> list[Checkpoint]:
        return list(reversed(self._entries[-limit:]))

    def __len__(self) -> int:
        return len(self._entries)
