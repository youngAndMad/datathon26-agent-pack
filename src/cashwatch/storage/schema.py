"""SQLite schema and forward-only migrations for the durable job store.

All timestamps are ISO-8601 UTC strings produced by ``cashwatch.deadline.iso_utc`` (fixed
millisecond precision, ``+00:00`` suffix) so that lexicographic comparison equals time order.
"""

from __future__ import annotations

import sqlite3

from cashwatch.deadline import iso_utc, utc_now

MIGRATIONS: list[tuple[int, str]] = [
    (
        1,
        """
        CREATE TABLE jobs (
            id                 TEXT PRIMARY KEY,
            principal_id       TEXT NOT NULL,
            state              TEXT NOT NULL CHECK (state IN
                                 ('receiving','queued','running','succeeded','failed','expired')),
            received_at        TEXT NOT NULL,   -- trusted ingress request start (UTC)
            accepted_at        TEXT NOT NULL,
            deadline_at        TEXT NOT NULL,   -- original hard internal deadline, never reset
            source_hash        TEXT NOT NULL,
            source_path        TEXT NOT NULL,   -- relative to data_dir
            size_bytes         INTEGER NOT NULL,
            stage              TEXT,
            progress_json      TEXT,
            attempt            INTEGER NOT NULL DEFAULT 1,
            generation         INTEGER NOT NULL DEFAULT 0,  -- lease fencing generation
            config_version     TEXT NOT NULL,
            error_code         TEXT,
            result_path        TEXT,            -- relative to data_dir, set only by CAS commit
            started_at         TEXT,
            finished_at        TEXT,
            updated_at         TEXT NOT NULL
        );
        CREATE INDEX jobs_state_accepted ON jobs(state, accepted_at);

        CREATE TABLE leases (
            job_id        TEXT PRIMARY KEY REFERENCES jobs(id) ON DELETE CASCADE,
            worker_id     TEXT NOT NULL,
            expires_at    TEXT NOT NULL,
            heartbeat_at  TEXT NOT NULL,
            generation    INTEGER NOT NULL
        );

        CREATE TABLE reservations (
            id             TEXT PRIMARY KEY,
            principal_id   TEXT NOT NULL,
            created_at     TEXT NOT NULL,
            expires_at     TEXT NOT NULL,
            capacity_slot  INTEGER NOT NULL
        );

        CREATE TABLE idempotency (
            principal_id  TEXT NOT NULL,
            key_hash      TEXT NOT NULL,
            content_hash  TEXT NOT NULL,
            job_id        TEXT NOT NULL,
            expires_at    TEXT NOT NULL,
            PRIMARY KEY (principal_id, key_hash)
        );

        CREATE TABLE worker_status (
            worker_id     TEXT PRIMARY KEY,
            ready         INTEGER NOT NULL,
            info          TEXT NOT NULL,
            heartbeat_at  TEXT NOT NULL
        );
        """,
    ),
]


def connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path, timeout=10.0, isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 10000")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA synchronous = FULL")
    return conn


def migrate(conn: sqlite3.Connection) -> int:
    """Apply pending migrations; returns the resulting schema version."""
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
    )
    conn.execute("BEGIN IMMEDIATE")
    try:
        done = {r[0] for r in conn.execute("SELECT version FROM schema_migrations")}
        for version, sql in MIGRATIONS:
            if version in done:
                continue
            for stmt in [s for s in sql.split(";") if s.strip()]:
                conn.execute(stmt)
            conn.execute("INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                         (version, iso_utc(utc_now())))
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    return max(v for v, _ in MIGRATIONS)
