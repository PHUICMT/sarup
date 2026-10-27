"""CCR (Compression-and-Cache-Retrieval) store — hash → original content."""

from __future__ import annotations

import hashlib
import os
import sqlite3
import time
from dataclasses import dataclass
from typing import Optional

DB_FILENAME = "sarup-cache.db"


def default_db_path() -> Optional[str]:
    """Where the shared store lives: SARUP_DB_PATH, else the plugin's data dir.

    The plugin passes SARUP_DB_PATH (server env) and `--db` (hook args) under
    its data directory, which survives plugin updates; CLAUDE_PLUGIN_DATA is a
    fallback for hosts that export it. Without either there is no shared store
    (memory only). The parent directory is created if missing.
    """
    path = os.environ.get("SARUP_DB_PATH")
    if not path:
        data_dir = os.environ.get("CLAUDE_PLUGIN_DATA")
        if not data_dir:
            return None
        path = os.path.join(data_dir, DB_FILENAME)
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    return path


@dataclass
class StoreEntry:
    original: str
    compressed: str
    original_tokens: int
    compressed_tokens: int
    created_at: float


class CompressionStore:
    """
    In-memory store with optional SQLite persistence.
    Hash is SHA256[:24] of original UTF-8 bytes — deterministic, stable.
    """

    def __init__(self, db_path: Optional[str] = None) -> None:
        self._memory: dict[str, StoreEntry] = {}
        self._db_path = db_path
        if db_path:
            self._init_db()

    def _init_db(self) -> None:
        with sqlite3.connect(self._db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS entries (
                    hash TEXT PRIMARY KEY,
                    original TEXT NOT NULL,
                    compressed TEXT NOT NULL,
                    original_tokens INTEGER NOT NULL,
                    compressed_tokens INTEGER NOT NULL,
                    created_at REAL NOT NULL
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_created ON entries(created_at)")

    @staticmethod
    def make_hash(content: str) -> str:
        # surrogatepass so a stray lone surrogate (e.g. from Windows console
        # capture) can never crash hashing.
        return hashlib.sha256(content.encode("utf-8", "surrogatepass")).hexdigest()[:24]

    def store(
        self,
        original: str,
        compressed: str,
        original_tokens: int,
        compressed_tokens: int,
    ) -> str:
        h = self.make_hash(original)
        entry = StoreEntry(
            original=original,
            compressed=compressed,
            original_tokens=original_tokens,
            compressed_tokens=compressed_tokens,
            created_at=time.time(),  # wall-clock, consistent with the SQLite row
        )
        self._memory[h] = entry

        if self._db_path:
            with sqlite3.connect(self._db_path) as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO entries VALUES (?,?,?,?,?,?)",
                    (h, original, compressed, original_tokens, compressed_tokens, time.time()),
                )
        return h

    def retrieve(self, hash_key: str) -> Optional[str]:
        if hash_key in self._memory:
            return self._memory[hash_key].original
        if self._db_path:
            with sqlite3.connect(self._db_path) as conn:
                row = conn.execute(
                    "SELECT original FROM entries WHERE hash = ?", (hash_key,)
                ).fetchone()
                if row:
                    return row[0]
        return None

    def exists(self, hash_key: str) -> bool:
        return self.retrieve(hash_key) is not None

    def verify(self, hash_key: str, original: str) -> bool:
        """Roundtrip guarantee: stored original is byte-for-byte recoverable.

        This is what makes Sarup's "100% accurate" claim provable — a lossy
        compressed view is safe precisely because the original survives intact.
        """
        return self.retrieve(hash_key) == original

    @property
    def size(self) -> int:
        return len(self._memory)
