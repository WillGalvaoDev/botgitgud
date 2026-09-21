"""OS lease plus transactional state/audit, separate from agent checkouts."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .protocol import Blocked


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical(value: Any) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


@contextmanager
def lease(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as handle:
        if path.stat().st_size == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise Blocked("Another orchestration stage owns the repository lease") from exc
        try:
            yield
        finally:
            if os.name == "nt":
                import msvcrt

                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle, fcntl.LOCK_UN)


class Store:
    def __init__(self, root: Path) -> None:
        self.root = root
        root.mkdir(parents=True, exist_ok=True)
        self.path = root / "state.sqlite3"
        with self.connect() as connection:
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS state (id INTEGER PRIMARY KEY CHECK(id=1), body TEXT);
                CREATE TABLE IF NOT EXISTS audit (
                    id INTEGER PRIMARY KEY, body TEXT NOT NULL, previous TEXT NOT NULL,
                    hash TEXT NOT NULL);
            """)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=5)
        connection.execute("PRAGMA synchronous=FULL")
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def read(self) -> dict[str, Any] | None:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT body, previous, hash FROM audit ORDER BY id"
            ).fetchall()
            previous = ""
            for body, parent, checksum in rows:
                if parent != previous or digest((parent + body).encode()) != checksum:
                    raise Blocked("Audit integrity failure")
                previous = checksum
            row = connection.execute("SELECT body FROM state WHERE id=1").fetchone()
            if row and (not rows or json.loads(rows[-1][0])["state"] != json.loads(row[0])):
                raise Blocked("State does not match audit")
            return json.loads(row[0]) if row else None

    def save(self, state: dict[str, Any], event: str, details: Any = None) -> None:
        body = canonical(
            {
                "time": datetime.now(UTC).isoformat(),
                "event": event,
                "state": state,
                "details": details,
            }
        )
        with self.connect() as connection:
            previous = connection.execute(
                "SELECT hash FROM audit ORDER BY id DESC LIMIT 1"
            ).fetchone()
            parent = previous[0] if previous else ""
            connection.execute(
                "INSERT INTO audit(body, previous, hash) VALUES (?, ?, ?)",
                (body, parent, digest((parent + body).encode())),
            )
            connection.execute("INSERT OR REPLACE INTO state VALUES (1, ?)", (canonical(state),))

    def events(self) -> list[dict[str, Any]]:
        self.read()
        with self.connect() as connection:
            return [
                json.loads(row[0])
                for row in connection.execute("SELECT body FROM audit ORDER BY id")
            ]
