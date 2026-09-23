"""Durable count storage backed by SQLite.

Design notes
------------
* All persistence goes through the ``CounterStore`` class. The rest of the app
  never touches SQL directly, so swapping in (or layering on) an AWS IoT
  publisher later is a change confined to this file.
* SQLite is run in WAL mode with ``synchronous=FULL`` so a committed increment
  survives a power loss: the database is never left half-written. On boot we
  simply read the last committed value and resume.
* The count lives in a single row (id = 1). Increments happen inside a
  transaction and return the new value atomically.
* SQLite INTEGER is a signed 64-bit value (max 9,223,372,036,854,775,807),
  which is effectively unbounded for a button-press counter.
"""

from __future__ import annotations

import sqlite3
from typing import Optional


class CounterStore:
    """Single-count persistent store.

    Usage::

        store = CounterStore(db_path)
        current = store.get_count()
        new_value = store.increment()
        store.close()

    Can also be used as a context manager.
    """

    _ROW_ID = 1

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        # check_same_thread=False lets the gpiozero callback thread and the main
        # render thread share the connection. All writes are short and
        # serialized by SQLite's own locking, so this is safe for our use.
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._configure()
        self._init_schema()

    def _configure(self) -> None:
        # WAL: better durability/concurrency than the default rollback journal.
        # synchronous=FULL: fsync on commit so committed data survives power loss.
        self._conn.execute("PRAGMA journal_mode=WAL;")
        self._conn.execute("PRAGMA synchronous=FULL;")

    def _init_schema(self) -> None:
        with self._conn:
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS counter (
                    id    INTEGER PRIMARY KEY CHECK (id = 1),
                    value INTEGER NOT NULL DEFAULT 0
                )
                """
            )
            # Ensure the single row exists. INSERT OR IGNORE keeps an existing
            # value intact across restarts (this is what enables resume-on-boot).
            self._conn.execute(
                "INSERT OR IGNORE INTO counter (id, value) VALUES (?, 0)",
                (self._ROW_ID,),
            )

    def get_count(self) -> int:
        """Return the current count (0 if never incremented)."""
        cur = self._conn.execute(
            "SELECT value FROM counter WHERE id = ?", (self._ROW_ID,)
        )
        row = cur.fetchone()
        return int(row[0]) if row else 0

    def increment(self, by: int = 1) -> int:
        """Atomically add ``by`` to the count and return the new value.

        The UPDATE and the follow-up read run in one transaction so the value
        returned is exactly what was committed to disk.
        """
        with self._conn:  # transaction: commits (and fsyncs) on exit
            self._conn.execute(
                "UPDATE counter SET value = value + ? WHERE id = ?",
                (by, self._ROW_ID),
            )
            cur = self._conn.execute(
                "SELECT value FROM counter WHERE id = ?", (self._ROW_ID,)
            )
            return int(cur.fetchone()[0])

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "CounterStore":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


if __name__ == "__main__":
    # Tiny smoke test that runs anywhere (no GPIO/display needed).
    import tempfile
    import os

    tmp = os.path.join(tempfile.gettempdir(), "counter_store_test.db")
    if os.path.exists(tmp):
        os.remove(tmp)

    with CounterStore(tmp) as s:
        assert s.get_count() == 0, "fresh db should start at 0"
        assert s.increment() == 1
        assert s.increment() == 2
        assert s.increment(by=5) == 7
        assert s.get_count() == 7

    # Reopen to prove persistence across "restarts".
    with CounterStore(tmp) as s:
        assert s.get_count() == 7, "count should survive reopen"
        assert s.increment() == 8

    os.remove(tmp)
    print("storage.py self-test passed")
