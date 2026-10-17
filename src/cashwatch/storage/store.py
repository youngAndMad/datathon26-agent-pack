"""Durable job store: SQLite (WAL) state + private job directories.

Layout (decision D006), all under ``settings.data_dir``::

    db/cashwatch.sqlite3
    tmp/<reservation_id>.part          bounded streamed upload (pre-rename)
    jobs/<job_id>/source.mov           persisted original (evidence authority)
    jobs/<job_id>/work/                worker scratch (thumbnails)
    jobs/<job_id>/out-g<gen>.tmp/      generation-specific staging (invisible)
    jobs/<job_id>/out-g<gen>/          promoted result (jury.json, report.json, evidence/, preview.mp4)

Invariants:
* every transaction is short (``BEGIN IMMEDIATE`` ... ``COMMIT``); no lock is held during
  file I/O beyond a rename, and never during decode/inference;
* a terminal state is written exactly once by a compare-and-set on (state='running',
  generation); stale workers are fenced out by the generation;
* the original ``deadline_at`` is never reset; recovery retries at most once.
"""

from __future__ import annotations

import contextlib
import json
import math
import os
import re
import shutil
import sqlite3
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from cashwatch.deadline import iso_utc, parse_utc, utc_now
from cashwatch.dto import (
    ERROR_MESSAGES_RU,
    AnalysisReport,
    ErrorInfo,
    JobInput,
    JuryDoneResult,
    Stage,
    StageProgress,
)
from cashwatch.settings import Settings
from cashwatch.storage.schema import connect, migrate

# Local constants (candidates for Settings fields, see coordination/requests/A.md).
WORKER_FRESH_SEC = 10.0  # worker heartbeat considered live for /readyz and admission
RETRY_MIN_REMAINING_SEC = 120.0  # retry an abandoned job only with this much deadline left
MAX_ATTEMPTS = 2  # first attempt + at most one retry
RESERVATION_MARGIN_SEC = 30.0  # reservation expiry = max_upload_sec + margin
ORPHAN_GRACE_SEC = 600.0  # unreferenced tmp files / job dirs older than this are removed
MIN_FREE_BYTES = 512 * 1024 * 1024

NONTERMINAL = ("receiving", "queued", "running")
_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
_HEX_RE = re.compile(r"^[0-9a-f]{32}$")
_EVIDENCE_ID_RE = re.compile(r"^[A-Za-z0-9_.:\-]{1,128}$")


def is_job_id(value: str) -> bool:
    return bool(_UUID_RE.match(value))


class CapacityFull(Exception):
    def __init__(self, retry_after_sec: int) -> None:
        super().__init__("capacity full")
        self.retry_after_sec = retry_after_sec


class IdempotencyConflict(Exception):
    pass




@dataclass(frozen=True)
class ClaimedJobImpl:
    job: JobInput
    work_dir: Path


@dataclass(frozen=True)
class JobView:
    id: str
    principal_id: str
    state: str
    received_at: str
    accepted_at: str
    deadline_at: str
    source_path: str
    stage: Stage | None
    progress: StageProgress | None
    attempt: int
    generation: int
    error_code: str | None
    result_path: str | None
    finished_at: str | None

    @property
    def public_status(self) -> str:
        if self.state == "succeeded":
            return "done"
        if self.state in ("failed", "expired"):
            return "failed" if self.state == "failed" else "expired"
        return "processing"

    def error(self) -> ErrorInfo:
        code = self.error_code if self.error_code in ERROR_MESSAGES_RU else "internal_error"
        return ErrorInfo(code=code, message=ERROR_MESSAGES_RU[code])  # type: ignore[arg-type]


def _fsync_dir(path: Path) -> None:
    with contextlib.suppress(OSError):
        fd = os.open(path, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


class JobStore:
    """Implements ``cashwatch.ports.JobStorePort`` plus API-side helpers."""

    def __init__(self, settings: Settings, *, clock: Callable[[], datetime] = utc_now) -> None:
        self.settings = settings
        self.clock = clock
        self.root = Path(settings.data_dir).resolve()
        self.db_path = self.root / "db" / "cashwatch.sqlite3"
        self.tmp_dir = self.root / "tmp"
        self.jobs_dir = self.root / "jobs"
        for d in (self.root, self.db_path.parent, self.tmp_dir, self.jobs_dir):
            d.mkdir(parents=True, exist_ok=True, mode=0o700)
        conn = connect(str(self.db_path))
        try:
            self.schema_version = migrate(conn)
        finally:
            conn.close()
        try:
            self.config_version = str(settings.profile.get("profile_id", "unknown"))
        except Exception:  # missing/invalid profile file: recorded, not fatal for storage
            self.config_version = "unknown"

    # ------------------------------------------------------------------ plumbing

    def _now(self, now_utc: str | None = None) -> datetime:
        return parse_utc(now_utc) if now_utc else self.clock()

    @contextlib.contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        conn = connect(str(self.db_path))
        try:
            conn.execute("BEGIN IMMEDIATE")
            try:
                yield conn
            except BaseException:
                conn.execute("ROLLBACK")
                raise
            conn.execute("COMMIT")
        finally:
            conn.close()

    @contextlib.contextmanager
    def _ro(self) -> Iterator[sqlite3.Connection]:
        conn = connect(str(self.db_path))
        try:
            yield conn
        finally:
            conn.close()

    def _contained(self, rel: str | Path, base: Path | None = None) -> Path:
        base = (base or self.root).resolve()
        p = base / rel
        resolved = p.resolve()
        if not resolved.is_relative_to(base) or p.is_symlink():
            raise ValueError("path escapes storage root")
        return resolved

    def _job_dir(self, job_id: str) -> Path:
        if not is_job_id(job_id):
            raise ValueError("invalid job id")
        return self.jobs_dir / job_id

    @staticmethod
    def _fail_where(conn: sqlite3.Connection, where: str, args: tuple, code: str, now: str) -> int:
        ids = [r[0] for r in conn.execute(f"SELECT id FROM jobs WHERE {where}", args)]
        for jid in ids:
            conn.execute(
                "UPDATE jobs SET state='failed', error_code=?, finished_at=?, updated_at=? WHERE id=?",
                (code, now, now, jid))
            conn.execute("DELETE FROM leases WHERE job_id=?", (jid,))
        return len(ids)

    # ------------------------------------------------------------------ admission (API side)

    def worker_ready(self, now_utc: str | None = None) -> bool:
        cutoff = iso_utc(self._now(now_utc) - timedelta(seconds=WORKER_FRESH_SEC))
        with self._ro() as conn:
            row = conn.execute(
                "SELECT 1 FROM worker_status WHERE ready=1 AND heartbeat_at >= ? LIMIT 1", (cutoff,)
            ).fetchone()
        return row is not None

    def storage_writable(self) -> bool:
        probe = self.tmp_dir / f".probe-{uuid.uuid4().hex}"
        try:
            probe.write_bytes(b"ok")
            probe.unlink()
            return shutil.disk_usage(self.root).free >= MIN_FREE_BYTES
        except OSError:
            return False

    def reserve_capacity(self, principal_id: str, expires_at: str | None = None) -> str:
        """Reserve one admission slot before the upload body is read. Raises CapacityFull."""
        now = self._now()
        now_s = iso_utc(now)
        exp = expires_at or iso_utc(
            now + timedelta(seconds=self.settings.max_upload_sec + RESERVATION_MARGIN_SEC))
        limit = max(1, self.settings.active_jobs) + max(0, self.settings.max_queue_length)
        rid = uuid.uuid4().hex
        with self._tx() as conn:
            stale = [r[0] for r in conn.execute("SELECT id FROM reservations WHERE expires_at <= ?", (now_s,))]
            conn.execute("DELETE FROM reservations WHERE expires_at <= ?", (now_s,))
            active = conn.execute(
                "SELECT COUNT(*) FROM jobs WHERE state IN ('receiving','queued','running')").fetchone()[0]
            held = conn.execute("SELECT COUNT(*) FROM reservations").fetchone()[0]
            if active + held >= limit:
                raise CapacityFull(self._retry_after(conn, now))
            conn.execute(
                "INSERT INTO reservations(id, principal_id, created_at, expires_at, capacity_slot) VALUES (?,?,?,?,?)",
                (rid, principal_id, now_s, exp, active + held))
        for s in stale:
            self.tmp_upload_path(s).unlink(missing_ok=True)
        return rid

    def _retry_after(self, conn: sqlite3.Connection, now: datetime) -> int:
        """Conservative hint: time until the earliest active job must be terminal."""
        rows = conn.execute(
            "SELECT deadline_at FROM jobs WHERE state IN ('receiving','queued','running') "
            "UNION ALL SELECT expires_at FROM reservations").fetchall()
        waits = [(parse_utc(r[0]) - now).total_seconds() for r in rows]
        waits = [w for w in waits if w > 0]
        if not waits:
            return 5
        return int(min(max(1.0, math.ceil(min(waits))), self.settings.total_deadline_sec))

    def release_reservation(self, reservation_id: str) -> None:
        with self._tx() as conn:
            conn.execute("DELETE FROM reservations WHERE id=?", (reservation_id,))
        self.tmp_upload_path(reservation_id).unlink(missing_ok=True)

    def tmp_upload_path(self, reservation_id: str) -> Path:
        if not _HEX_RE.match(reservation_id):
            raise ValueError("invalid reservation id")
        return self.tmp_dir / f"{reservation_id}.part"

    def persist_upload(self, reservation_id: str, job_id: str) -> str:
        """Atomically move the fsynced temp upload into its private job directory.
        Returns the source path relative to data_dir."""
        src = self.tmp_upload_path(reservation_id)
        jdir = self._job_dir(job_id)
        jdir.mkdir(mode=0o700)
        dst = jdir / "source.mov"
        os.replace(src, dst)
        _fsync_dir(jdir)
        _fsync_dir(self.tmp_dir)
        return f"jobs/{job_id}/source.mov"

    def idempotency_lookup(self, principal_id: str, key_hash: str) -> tuple[str, str] | None:
        now_s = iso_utc(self._now())
        with self._ro() as conn:
            row = conn.execute(
                "SELECT content_hash, job_id FROM idempotency WHERE principal_id=? AND key_hash=? AND expires_at > ?",
                (principal_id, key_hash, now_s)).fetchone()
        return (row[0], row[1]) if row else None

    def enqueue_job(
        self, *, job_id: str, principal_id: str, source_rel: str, source_sha256: str, size_bytes: int,
        request_started_at: str, reservation_id: str | None, idempotency_key_hash: str | None = None,
    ) -> tuple[str, bool]:
        """Insert the durable queued job (and idempotency record) in one transaction.

        Returns (job_id, created). With an idempotency key already bound to the same content
        the original job id is returned with created=False (caller removes its own copy).
        Raises IdempotencyConflict when the key is bound to different content.
        """
        now = self._now()
        now_s = iso_utc(now)
        deadline = iso_utc(parse_utc(request_started_at) + timedelta(seconds=self.settings.internal_terminal_sec))
        with self._tx() as conn:
            if idempotency_key_hash:
                row = conn.execute(
                    "SELECT content_hash, job_id, expires_at FROM idempotency WHERE principal_id=? AND key_hash=?",
                    (principal_id, idempotency_key_hash)).fetchone()
                if row and row[2] > now_s:
                    if reservation_id:
                        conn.execute("DELETE FROM reservations WHERE id=?", (reservation_id,))
                    if row[0] == source_sha256:
                        return row[1], False
                    raise IdempotencyConflict()
                if row:
                    conn.execute("DELETE FROM idempotency WHERE principal_id=? AND key_hash=?",
                                 (principal_id, idempotency_key_hash))
            conn.execute(
                "INSERT INTO jobs(id, principal_id, state, received_at, accepted_at, deadline_at, source_hash,"
                " source_path, size_bytes, attempt, generation, config_version, updated_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,1,0,?,?)",
                (job_id, principal_id, "queued", request_started_at, now_s, deadline, source_sha256,
                 source_rel, size_bytes, self.config_version, now_s))
            if idempotency_key_hash:
                exp = iso_utc(now + timedelta(hours=max(self.settings.retention_hours, 1.0)))
                conn.execute(
                    "INSERT INTO idempotency(principal_id, key_hash, content_hash, job_id, expires_at) VALUES (?,?,?,?,?)",
                    (principal_id, idempotency_key_hash, source_sha256, job_id, exp))
            if reservation_id:
                conn.execute("DELETE FROM reservations WHERE id=?", (reservation_id,))
        return job_id, True

    def discard_job_dir(self, job_id: str) -> None:
        """Remove a job directory that was never committed to the database."""
        shutil.rmtree(self._job_dir(job_id), ignore_errors=True)

    # ------------------------------------------------------------------ reads (API side)

    def get_job(self, job_id: str) -> JobView | None:
        if not is_job_id(job_id):
            return None
        with self._ro() as conn:
            r = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if r is None:
            return None
        progress = None
        if r["progress_json"]:
            with contextlib.suppress(ValueError):
                progress = StageProgress.model_validate_json(r["progress_json"])
        return JobView(
            id=r["id"], principal_id=r["principal_id"], state=r["state"], received_at=r["received_at"],
            accepted_at=r["accepted_at"], deadline_at=r["deadline_at"], source_path=r["source_path"],
            stage=r["stage"], progress=progress, attempt=r["attempt"], generation=r["generation"],
            error_code=r["error_code"], result_path=r["result_path"], finished_at=r["finished_at"])

    def result_dir(self, view: JobView) -> Path | None:
        if view.state != "succeeded" or not view.result_path:
            return None
        p = self._contained(view.result_path)
        return p if p.is_dir() else None

    def read_jury_bytes(self, view: JobView) -> bytes | None:
        d = self.result_dir(view)
        if d is None:
            return None
        return self._contained("jury.json", d).read_bytes()

    def read_report(self, view: JobView) -> AnalysisReport | None:
        d = self.result_dir(view)
        if d is None:
            return None
        p = self._contained("report.json", d)
        if not p.is_file():
            return None
        return AnalysisReport.model_validate_json(p.read_bytes())

    def evidence_file(self, view: JobView, evidence_id: str) -> Path | None:
        if not _EVIDENCE_ID_RE.match(evidence_id):
            return None
        report = self.read_report(view)
        d = self.result_dir(view)
        if report is None or d is None:
            return None
        for ev in report.events:
            for fr in ev.evidence:
                if fr.evidence_id == evidence_id:
                    try:
                        p = self._contained(fr.jpeg_path, d)
                    except ValueError:
                        return None
                    return p if p.is_file() else None
        return None

    def media_file(self, view: JobView) -> tuple[Path, str] | None:
        """Preview (H.264 MP4) when present, else the original source."""
        if view.state == "expired":
            return None
        d = self.result_dir(view)
        if d is not None:
            prev = self._contained("preview.mp4", d)
            if prev.is_file():
                return prev, "video/mp4"
        try:
            src = self._contained(view.source_path)
        except ValueError:
            return None
        return (src, "video/quicktime") if src.is_file() else None

    # ------------------------------------------------------------------ worker side (JobStorePort)

    def claim_job(self, worker_id: str, now_utc: str | None = None) -> ClaimedJobImpl | None:
        now = self._now(now_utc)
        now_s = iso_utc(now)
        exp = iso_utc(now + timedelta(seconds=self.settings.lease_sec))
        with self._tx() as conn:
            self._fail_where(conn, "state='queued' AND deadline_at <= ?", (now_s,), "deadline_exceeded", now_s)
            running = conn.execute("SELECT COUNT(*) FROM jobs WHERE state='running'").fetchone()[0]
            if running >= max(1, self.settings.active_jobs):
                return None
            r = conn.execute(
                "SELECT * FROM jobs WHERE state='queued' ORDER BY accepted_at, id LIMIT 1").fetchone()
            if r is None:
                return None
            gen = r["generation"] + 1
            conn.execute(
                "UPDATE jobs SET state='running', generation=?, started_at=COALESCE(started_at, ?),"
                " updated_at=? WHERE id=? AND state='queued'", (gen, now_s, now_s, r["id"]))
            conn.execute(
                "INSERT OR REPLACE INTO leases(job_id, worker_id, expires_at, heartbeat_at, generation)"
                " VALUES (?,?,?,?,?)", (r["id"], worker_id, exp, now_s, gen))
        job = JobInput(
            job_id=r["id"], principal_id=r["principal_id"], source_path=r["source_path"],
            source_sha256=r["source_hash"], request_started_at=r["received_at"],
            accepted_at=r["accepted_at"], deadline_at=r["deadline_at"], attempt=r["attempt"],
            lease_generation=gen, config_version=r["config_version"])
        work = self._job_dir(job.job_id) / "work"
        work.mkdir(parents=True, exist_ok=True, mode=0o700)
        return ClaimedJobImpl(job=job, work_dir=work)

    def heartbeat(self, job_id: str, generation: int, *, stage: Stage | None = None,
                  progress: StageProgress | None = None, now_utc: str | None = None) -> bool:
        now = self._now(now_utc)
        now_s = iso_utc(now)
        exp = iso_utc(now + timedelta(seconds=self.settings.lease_sec))
        with self._tx() as conn:
            r = conn.execute("SELECT state, generation FROM jobs WHERE id=?", (job_id,)).fetchone()
            if r is None or r[0] != "running" or r[1] != generation:
                return False
            cur = conn.execute(
                "UPDATE leases SET expires_at=?, heartbeat_at=? WHERE job_id=? AND generation=?",
                (exp, now_s, job_id, generation))
            if cur.rowcount != 1:
                return False
            sets, args = ["updated_at=?"], [now_s]
            if stage is not None:
                sets.append("stage=?")
                args.append(stage)
            if progress is not None:
                sets.append("progress_json=?")
                args.append(progress.model_dump_json())
            elif stage is not None:
                sets.append("progress_json=NULL")
            conn.execute(f"UPDATE jobs SET {', '.join(sets)} WHERE id=?", (*args, job_id))
        return True

    def source_path(self, job: JobInput) -> Path:
        return self._contained(job.source_path)

    def _out_paths(self, job_id: str, generation: int) -> tuple[Path, Path]:
        jdir = self._job_dir(job_id)
        return jdir / f"out-g{int(generation)}.tmp", jdir / f"out-g{int(generation)}"

    def output_dir(self, job: JobInput) -> Path:
        tmp, _ = self._out_paths(job.job_id, job.lease_generation)
        tmp.mkdir(parents=True, exist_ok=True, mode=0o700)
        return tmp

    def commit_result(self, job_id: str, generation: int, validated_result_dir: Path,
                      now_utc: str | None = None) -> bool:
        """CAS running(gen) -> succeeded. Idempotent across a crash between promotion rename and
        the database commit. Returns False when fenced out (stale generation / not running)."""
        tmp, final = self._out_paths(job_id, generation)
        given = Path(validated_result_dir).resolve()
        if given not in (tmp.resolve(), final.resolve()):
            raise ValueError("result dir is not this job generation's output dir")
        with self._ro() as conn:
            r = conn.execute("SELECT state, generation FROM jobs WHERE id=?", (job_id,)).fetchone()
        if r is None or r[0] != "running" or r[1] != generation:
            return False
        if tmp.is_dir() and not final.exists():
            self._check_result(tmp, job_id)
            _fsync_tree(tmp)
            os.replace(tmp, final)
            _fsync_dir(final.parent)
        elif not final.is_dir():
            raise FileNotFoundError("no staged result for this generation")
        else:
            self._check_result(final, job_id)
        now_s = iso_utc(self._now(now_utc))
        rel = final.relative_to(self.root).as_posix()
        with self._tx() as conn:
            cur = conn.execute(
                "UPDATE jobs SET state='succeeded', result_path=?, stage='complete', progress_json=NULL,"
                " finished_at=?, updated_at=?, error_code=NULL WHERE id=? AND state='running' AND generation=?",
                (rel, now_s, now_s, job_id, generation))
            if cur.rowcount != 1:
                return False
            conn.execute("DELETE FROM leases WHERE job_id=?", (job_id,))
        return True

    @staticmethod
    def _check_result(d: Path, job_id: str) -> None:
        """Last-line structural guard: never publish an unparsable jury object."""
        jury = JuryDoneResult.model_validate_json((d / "jury.json").read_bytes())
        if jury.job_id != job_id:
            raise ValueError("jury.json job_id mismatch")
        if (jury.verdict == "violation") != bool(jury.violations):
            raise ValueError("verdict/violations invariant broken")

    def fail_job(self, job_id: str, generation: int, error: ErrorInfo, now_utc: str | None = None) -> bool:
        now_s = iso_utc(self._now(now_utc))
        code = error.code if error.code in ERROR_MESSAGES_RU else "internal_error"
        with self._tx() as conn:
            cur = conn.execute(
                "UPDATE jobs SET state='failed', error_code=?, finished_at=?, updated_at=?"
                " WHERE id=? AND state='running' AND generation=?",
                (code, now_s, now_s, job_id, generation))
            if cur.rowcount != 1:
                return False
            conn.execute("DELETE FROM leases WHERE job_id=?", (job_id,))
        return True

    def recover_expired_jobs(self, now_utc: str | None = None) -> int:
        """Sweeper: resolve deadlines and abandoned leases; clean orphan reservations/uploads."""
        now = self._now(now_utc)
        now_s = iso_utc(now)
        retry_cutoff = iso_utc(now + timedelta(seconds=RETRY_MIN_REMAINING_SEC))
        changed = 0
        with self._tx() as conn:
            changed += self._fail_where(
                conn, "state IN ('receiving','queued','running') AND deadline_at <= ?", (now_s,),
                "deadline_exceeded", now_s)
            lost = conn.execute(
                "SELECT j.id, j.attempt, j.deadline_at FROM jobs j LEFT JOIN leases l ON l.job_id=j.id"
                " WHERE j.state='running' AND (l.job_id IS NULL OR l.expires_at <= ? OR l.generation != j.generation)",
                (now_s,)).fetchall()
            for jid, attempt, deadline_at in lost:
                conn.execute("DELETE FROM leases WHERE job_id=?", (jid,))
                if attempt < MAX_ATTEMPTS and deadline_at >= retry_cutoff:
                    conn.execute(
                        "UPDATE jobs SET state='queued', attempt=attempt+1, stage=NULL, progress_json=NULL,"
                        " updated_at=? WHERE id=?", (now_s, jid))
                else:
                    conn.execute(
                        "UPDATE jobs SET state='failed', error_code='worker_lost', finished_at=?, updated_at=?"
                        " WHERE id=?", (now_s, now_s, jid))
                changed += 1
            stale = [r[0] for r in conn.execute("SELECT id FROM reservations WHERE expires_at <= ?", (now_s,))]
            conn.execute("DELETE FROM reservations WHERE expires_at <= ?", (now_s,))
        for rid in stale:
            self.tmp_upload_path(rid).unlink(missing_ok=True)
        self.cleanup_orphans(now)
        return changed

    def worker_heartbeat(self, worker_id: str, *, ready: bool, info: dict) -> None:
        now_s = iso_utc(self._now())
        with self._tx() as conn:
            conn.execute(
                "INSERT INTO worker_status(worker_id, ready, info, heartbeat_at) VALUES (?,?,?,?)"
                " ON CONFLICT(worker_id) DO UPDATE SET ready=excluded.ready, info=excluded.info,"
                " heartbeat_at=excluded.heartbeat_at",
                (worker_id, 1 if ready else 0, json.dumps(info, default=str)[:4000], now_s))

    # ------------------------------------------------------------------ cleanup

    def cleanup_orphans(self, now: datetime | None = None) -> int:
        """Remove temp uploads without a live reservation and job dirs without a DB row
        (crash between rename and insert), both only after ORPHAN_GRACE_SEC."""
        now = now or self._now()
        cutoff = now.timestamp() - ORPHAN_GRACE_SEC
        with self._ro() as conn:
            live = {r[0] for r in conn.execute("SELECT id FROM reservations")}
            known = {r[0] for r in conn.execute("SELECT id FROM jobs")}
        removed = 0
        for p in self.tmp_dir.iterdir():
            rid = p.name.removesuffix(".part")
            with contextlib.suppress(OSError):
                if rid not in live and p.lstat().st_mtime < cutoff:
                    if p.is_dir() and not p.is_symlink():
                        shutil.rmtree(p, ignore_errors=True)
                    else:
                        p.unlink()
                    removed += 1
        for p in self.jobs_dir.iterdir():
            with contextlib.suppress(OSError):
                if p.name not in known and p.lstat().st_mtime < cutoff:
                    if p.is_symlink() or not p.is_dir():
                        p.unlink()
                    else:
                        shutil.rmtree(p, ignore_errors=True)
                    removed += 1
        return removed

    def cleanup_retention(self, now_utc: str | None = None) -> int:
        """Delete files of terminal jobs older than retention_hours; keep the row as 'expired'
        (GET -> 410). Disabled when retention_hours <= 0. Never touches nonterminal jobs."""
        if self.settings.retention_hours <= 0:
            return 0
        now = self._now(now_utc)
        now_s = iso_utc(now)
        cutoff = iso_utc(now - timedelta(hours=self.settings.retention_hours))
        with self._tx() as conn:
            ids = [r[0] for r in conn.execute(
                "SELECT id FROM jobs WHERE state IN ('succeeded','failed') AND finished_at IS NOT NULL"
                " AND finished_at <= ?", (cutoff,))]
            for jid in ids:
                conn.execute("UPDATE jobs SET state='expired', result_path=NULL, updated_at=? WHERE id=?",
                             (now_s, jid))
            conn.execute("DELETE FROM idempotency WHERE expires_at <= ?", (now_s,))
        for jid in ids:
            shutil.rmtree(self._job_dir(jid), ignore_errors=True)
        return len(ids)

    def counts(self) -> dict[str, int]:
        with self._ro() as conn:
            return {r[0]: r[1] for r in conn.execute("SELECT state, COUNT(*) FROM jobs GROUP BY state")}


def _fsync_tree(d: Path) -> None:
    for p in sorted(d.rglob("*"), reverse=True):
        with contextlib.suppress(OSError):
            if p.is_file() and not p.is_symlink():
                fd = os.open(p, os.O_RDONLY)
                try:
                    os.fsync(fd)
                finally:
                    os.close(fd)
            elif p.is_dir():
                _fsync_dir(p)
    _fsync_dir(d)
