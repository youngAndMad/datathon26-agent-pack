"""Resident worker.

``run()`` is a supervisor: it starts the worker core as a child process and restarts it when it
exits (crash, watchdog kill, OOM). The core loads the perception model once, warms it up,
reports readiness, then claims durable jobs one at a time.

Fencing: a heartbeat thread extends the lease; when ``store.heartbeat`` returns False the job
is abandoned and nothing is committed (commit_result is also a CAS on the lease generation).
Watchdog: if a job passes its hard deadline + grace while still running (e.g. a hung decoder),
the job is failed with ``deadline_exceeded`` and the core process exits; the supervisor
restarts it and readiness stays false until warm-up completes again.
"""

from __future__ import annotations

import argparse
import logging
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
import traceback
import uuid
from pathlib import Path

from cashwatch.deadline import Deadline, iso_utc, parse_utc, utc_now
from cashwatch.dto import Stage, StageProgress
from cashwatch.errors import PipelineError, public_error
from cashwatch.pipeline.analyze import LeaseLost, PipelineContext, analyze_job
from cashwatch.settings import REPO_ROOT, Settings, get_settings

log = logging.getLogger("cashwatch.worker")
WATCHDOG_GRACE_SEC = 8.0


def warmup_timeout(settings: Settings) -> float:
    # Only affects explicit Windows local test startup, never the job deadline.
    if os.name == "nt" and settings.mode == "test" and os.environ.get("CASHWATCH_WINDOWS_CPU_LOW_MEMORY") == "1":
        return 600.0
    return 180.0
# R11: no audio processing. mlx-vlm installs audio packages transitively; they must never load.
FORBIDDEN_MODULES = ("mlx_audio", "sounddevice", "miniaudio", "librosa", "soundfile", "torchaudio", "whisper")


def forbidden_modules_loaded() -> list[str]:
    return [m for m in FORBIDDEN_MODULES if m in sys.modules]


def code_version() -> str:
    try:
        out = subprocess.run(["git", "-C", str(REPO_ROOT), "rev-parse", "--short", "HEAD"],
                             capture_output=True, text=True, timeout=5, check=False)
        return out.stdout.strip() or "unknown"
    except Exception:
        return "unknown"


def build_context(settings: Settings, perception=None) -> tuple[PipelineContext, list[str]]:
    """Wire real lane implementations. Returns (context, readiness errors)."""
    from cashwatch.inference import create_perception
    from cashwatch.media import create_media
    from cashwatch.reporting import create_reporting
    from cashwatch.rules import create_rules, load_registry

    errors = list(settings.production_errors())
    registry = load_registry(settings.registry_path, require_approved=settings.mode == "production",
                             allow_test=settings.mode == "test")
    errors += registry.health_errors()
    if not registry.approved_rule_ids():
        errors.append("rule registry has no approved rules")
    perception = perception or create_perception(settings)
    info = perception.info()
    if settings.mode == "production" and info.is_mock:
        errors.append("mock perception adapter in production")
    ctx = PipelineContext(
        media=create_media(max_pixel_area=settings.max_pixel_area,
                           motion_fps=float(settings.profile["sampling"]["motion_proposal_fps"])), perception=perception, registry=registry, rules_factory=create_rules,
        reporting=create_reporting(registry), profile=settings.profile, mode=settings.mode,
        limits={"max_duration_sec": settings.max_duration_sec, "max_pixel_area": settings.max_pixel_area},
        code_version=code_version(), config_version=settings.profile.get("profile_id", "unknown"))
    return ctx, errors


class _Lease:
    def __init__(self, store, job_id: str, generation: int, interval: float) -> None:
        self.store, self.job_id, self.gen = store, job_id, generation
        self.lost = threading.Event()
        self.stop = threading.Event()
        self.stage: Stage | None = None
        self.progress: StageProgress | None = None
        self._lock = threading.Lock()
        self._t = threading.Thread(target=self._loop, args=(interval,), daemon=True)
        self._t.start()

    def _beat(self) -> bool:
        with self._lock:
            ok = self.store.heartbeat(self.job_id, self.gen, stage=self.stage, progress=self.progress)
        if not ok:
            self.lost.set()
        return ok

    def _loop(self, interval: float) -> None:
        while not self.stop.wait(interval):
            if not self._beat():
                return

    def update(self, stage: Stage, progress: StageProgress | None) -> bool:
        if self.lost.is_set():
            return False
        self.stage, self.progress = stage, progress
        return self._beat()

    def close(self) -> None:
        self.stop.set()


def process_one(store, ctx: PipelineContext, settings: Settings, worker_id: str) -> bool:
    claimed = store.claim_job(worker_id)
    if claimed is None:
        return False
    job, work_dir = claimed.job, Path(claimed.work_dir)
    lease = _Lease(store, job.job_id, job.lease_generation, max(1.0, settings.lease_sec / 3))
    deadline = Deadline(job.deadline_at, soft_margin_sec=float(settings.profile.get("budget_sec", {}).get("soft_margin", 30)))
    ctx.progress = lease.update
    done = threading.Event()

    def watchdog() -> None:
        if done.wait(max(0.0, deadline.remaining_sec()) + WATCHDOG_GRACE_SEC):
            return
        log.error("watchdog: job exceeded hard deadline; failing and restarting worker core")
        store.fail_job(job.job_id, job.lease_generation, public_error("deadline_exceeded"))
        os._exit(3)

    threading.Thread(target=watchdog, daemon=True).start()
    out_dir = Path(store.output_dir(job))
    t0 = time.monotonic()
    try:
        work_dir.mkdir(parents=True, exist_ok=True)
        analyze_job(job, Path(store.source_path(job)), work_dir, out_dir, ctx, deadline)
        if lease.lost.is_set():
            raise LeaseLost("before commit")
        if store.commit_result(job.job_id, job.lease_generation, out_dir):
            log.info("job committed", extra={"job_id": job.job_id, "ms": int((time.monotonic() - t0) * 1000)})
            _maybe_preview(store, ctx, settings, job, out_dir)
        else:
            log.warning("commit fenced out (stale lease)", extra={"job_id": job.job_id})
    except LeaseLost:
        log.warning("lease lost; abandoning job without publishing", extra={"job_id": job.job_id})
        shutil.rmtree(out_dir, ignore_errors=True)
    except PipelineError as e:
        log.warning("job failed code=%s", e.code, extra={"job_id": job.job_id})
        store.fail_job(job.job_id, job.lease_generation, e.public())
        shutil.rmtree(out_dir, ignore_errors=True)
    except Exception:
        # Detailed diagnostics stay local; no media/scene text is included in exceptions.
        log.error("internal error\n%s", traceback.format_exc(limit=8))
        store.fail_job(job.job_id, job.lease_generation, public_error("internal_error"))
        shutil.rmtree(out_dir, ignore_errors=True)
    finally:
        done.set()
        lease.close()
        shutil.rmtree(work_dir, ignore_errors=True)
    return True


def _maybe_preview(store, ctx: PipelineContext, settings: Settings, job, out_dir: Path) -> None:
    """Browser preview generated after the jury result is committed (never delays it)."""
    if not settings.preview_enabled:
        return
    final_dir = out_dir.with_name(out_dir.name.removesuffix(".tmp"))
    if not final_dir.is_dir():
        return
    try:
        src = Path(store.source_path(job))
        meta = ctx.media.probe_video(src, timeout_sec=20)
        tmp = final_dir / "preview.mp4.part.mp4"
        res = ctx.media.make_preview(src, meta, tmp, deadline=Deadline.in_seconds(120), max_sec=90)
        if res is not None and tmp.exists():
            os.replace(tmp, final_dir / "preview.mp4")
        else:
            tmp.unlink(missing_ok=True)
    except Exception as e:  # preview is optional
        log.warning("preview skipped: %s", type(e).__name__)


def core_main(settings: Settings) -> int:
    from cashwatch.storage import create_store

    store = create_store(settings)
    worker_id = f"w-{uuid.uuid4().hex[:12]}"
    store.worker_heartbeat(worker_id, ready=False, info={"state": "loading"})
    try:
        ctx, errors = build_context(settings)
    except Exception as e:
        log.error("startup failed: %s", type(e).__name__)
        store.worker_heartbeat(worker_id, ready=False, info={"state": "startup_failed", "error": type(e).__name__})
        return 2
    ready = threading.Event()
    stop = threading.Event()
    state = {"info": {"state": "warming", "errors": errors}}

    def beat() -> None:
        while not stop.wait(2.0):
            try:
                store.worker_heartbeat(worker_id, ready=ready.is_set() and ctx.perception.ready(), info=state["info"])
            except Exception:
                pass

    threading.Thread(target=beat, daemon=True).start()
    if errors:
        # Not ready: keep reporting the reason (no secrets) so doctor/readyz are useful.
        state["info"] = {"state": "not_ready", "errors": errors}
        log.error("worker not ready: %s", "; ".join(errors))
        while not stop.is_set():
            time.sleep(5)
        return 2
    try:
        ctx.perception.warmup(timeout_sec=warmup_timeout(settings))
    except Exception as e:
        state["info"] = {"state": "warmup_failed", "error": type(e).__name__}
        log.error("warmup failed: %s", type(e).__name__)
        time.sleep(5)
        return 2
    if bad := forbidden_modules_loaded():
        state["info"] = {"state": "not_ready", "errors": [f"forbidden audio modules loaded: {bad}"]}
        log.error("refusing readiness: audio modules loaded %s", bad)
        return 2
    info = ctx.perception.info()
    state["info"] = {"state": "ready", "mode": settings.mode, "model": info.model_id, "revision": info.revision,
                     "adapter": info.adapter, "is_mock": info.is_mock, "registry": ctx.registry.version}
    ready.set()
    store.worker_heartbeat(worker_id, ready=True, info=state["info"])
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    last_sweep = 0.0
    while not stop.is_set():
        if time.monotonic() - last_sweep > 5:
            store.recover_expired_jobs(iso_utc(utc_now()))
            last_sweep = time.monotonic()
        if not ctx.perception.ready():
            ready.clear()
            try:
                ctx.perception.warmup(timeout_sec=warmup_timeout(settings))
                ready.set()
            except Exception:
                return 2
        if not process_one(store, ctx, settings, worker_id):
            stop.wait(settings.worker_poll_sec)
    ctx.perception.close()
    store.worker_heartbeat(worker_id, ready=False, info={"state": "stopped"})
    return 0


def supervise() -> int:
    stopping = False
    child: subprocess.Popen | None = None

    def _term(*_):
        nonlocal stopping
        stopping = True
        if child and child.poll() is None:
            child.terminate()

    signal.signal(signal.SIGTERM, _term)
    signal.signal(signal.SIGINT, _term)
    backoff = 1.0
    while not stopping:
        started = time.monotonic()
        child = subprocess.Popen([sys.executable, "-m", "cashwatch.pipeline.worker", "--core"])
        rc = child.wait()
        if stopping:
            break
        log.error("worker core exited rc=%s; restarting", rc)
        backoff = 1.0 if time.monotonic() - started > 60 else min(backoff * 2, 30.0)
        time.sleep(backoff)
    return 0


def run() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
    os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
    ap = argparse.ArgumentParser()
    ap.add_argument("--core", action="store_true", help="run worker core directly (no supervisor)")
    args = ap.parse_args()
    settings = get_settings()
    if errs := settings.production_errors():
        log.error("refusing to start: %s", "; ".join(errs))
        sys.exit(2)
    sys.exit(core_main(settings) if args.core else supervise())


if __name__ == "__main__":
    run()
