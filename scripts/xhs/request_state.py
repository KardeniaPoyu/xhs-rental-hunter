"""Keep only hashes and uncertain request IDs so retries survive CLI restarts."""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path


class RequestState:
    def __init__(self):
        override = os.environ.get("XHS_REQUEST_STATE")
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / ".cache"))
        self.path = Path(override) if override else base / "xhs-bridge" / "requests.sqlite3"

    def _connect(self):
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=5)
        connection.execute(
            "CREATE TABLE IF NOT EXISTS pending (key TEXT PRIMARY KEY, request_id TEXT NOT NULL)"
        )
        return connection

    def obtain(self, key, proposed, explicit=False):
        connection = self._connect()
        try:
            with connection:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute(
                    "SELECT request_id FROM pending WHERE key=?", (key,)
                ).fetchone()
                if row and not explicit:
                    return row[0]
                connection.execute("INSERT OR REPLACE INTO pending VALUES (?, ?)", (key, proposed))
                return proposed
        finally:
            connection.close()

    def complete(self, key, request_id):
        connection = self._connect()
        try:
            with connection:
                connection.execute(
                    "DELETE FROM pending WHERE key=? AND request_id=?", (key, request_id)
                )
        finally:
            connection.close()
