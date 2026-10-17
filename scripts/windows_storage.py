"""Disposable, synthetic-only SQLite diagnostic; does not change any ACL.

Run explicitly to distinguish inherited workspace access from an inaccessible
private runtime directory. Never reads existing application data or secrets.
"""
from __future__ import annotations

import sqlite3
import uuid
from pathlib import Path


def main() -> None:
    root = Path(__file__).resolve().parent.parent
    probe = root / (".windows-storage-probe-" + uuid.uuid4().hex)
    probe.mkdir()
    database = probe / "synthetic.sqlite3"
    try:
        with sqlite3.connect(database) as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("CREATE TABLE synthetic (value TEXT)")
            connection.execute("INSERT INTO synthetic VALUES ('synthetic probe only')")
            assert connection.execute("SELECT value FROM synthetic").fetchone() == (
                "synthetic probe only",
            )
        connection.close()
        print("Inherited workspace directory: SQLite WAL write/read succeeded")
    finally:
        for name in ("synthetic.sqlite3", "synthetic.sqlite3-wal", "synthetic.sqlite3-shm"):
            (probe / name).unlink(missing_ok=True)
        probe.rmdir()


if __name__ == "__main__":
    main()
