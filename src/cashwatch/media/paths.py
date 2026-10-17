"""Local-file-only input validation and containment-checked output paths."""

from __future__ import annotations

import hashlib
import os
import re
import stat
from pathlib import Path

from cashwatch.errors import PipelineError

_URL_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.\-]*:")
_WINDOWS_ABSOLUTE_RE = re.compile(r"^[A-Za-z]:[\\/]")


def _is_local_path(raw: str) -> bool:
    # A drive letter is not a URI scheme on Windows. Drive-relative paths,
    # UNC shares, device namespaces and NTFS alternate streams are not inputs.
    if "\x00" in raw or raw.startswith(("\\\\", "//")):
        return False
    if os.name == "nt" and _WINDOWS_ABSOLUTE_RE.match(raw):
        return ":" not in raw[2:]
    return not _URL_RE.match(raw) and (os.name != "nt" or ":" not in raw)


def validate_source(local_path: Path | str) -> Path:
    """Return the absolute path of an existing, regular, local, non-empty media file.

    URLs / protocol prefixes (``http:``, ``file:``, ``concat:`` ...) are rejected: the media
    layer only reads local files that storage has already persisted.
    """
    raw = os.fspath(local_path)
    if not isinstance(raw, str) or not _is_local_path(raw):
        raise PipelineError("invalid_media", "source must be a local filesystem path")
    p = Path(raw).expanduser().absolute()
    try:
        p = p.resolve()
        if not _is_local_path(str(p)):
            raise PipelineError("invalid_media", "source must be a local filesystem path")
        st = p.stat()
    except OSError as exc:
        raise PipelineError("invalid_media", f"source not accessible: {type(exc).__name__}") from None
    if not stat.S_ISREG(st.st_mode):
        raise PipelineError("invalid_media", "source is not a regular file")
    if st.st_size == 0:
        raise PipelineError("invalid_media", "source is empty")
    return p


def sha256_file(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while block := f.read(chunk):
            h.update(block)
    return h.hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def contained(base: Path, rel: str) -> Path:
    """Absolute path of ``base/rel`` that must stay inside ``base`` (no symlink escape).

    Only checks the path string and existing components; the leaf may not exist yet.
    """
    if rel.startswith("/") or "\\" in rel or ".." in rel.split("/") or not rel:
        raise PipelineError("internal_error", "relative path not contained")
    base_r = base.resolve()
    target = base_r / rel
    # resolve existing parents (non-strict) and ensure containment
    resolved = target.resolve()
    if not resolved.is_relative_to(base_r):
        raise PipelineError("internal_error", "path escapes job directory")
    return target


def safe_write(base: Path, rel: str, data: bytes) -> Path:
    """Atomically write ``data`` to ``base/rel`` refusing symlinked leaves or parents."""
    target = contained(base, rel)
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.parent.resolve().is_relative_to(base.resolve()):
        raise PipelineError("internal_error", "output directory escapes job directory")
    if target.is_symlink():
        raise PipelineError("internal_error", "refusing to overwrite a symlink")
    tmp = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp, target)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return target


def resolve_existing(base: Path, rel: str) -> Path:
    """Containment-checked absolute path of an existing regular file (symlinks refused)."""
    target = contained(base, rel)
    if target.is_symlink():
        raise PipelineError("internal_error", "frame path is a symlink")
    try:
        resolved = target.resolve(strict=True)
    except OSError:
        raise PipelineError("internal_error", "frame image missing") from None
    if not resolved.is_relative_to(base.resolve()) or not resolved.is_file():
        raise PipelineError("internal_error", "frame path not contained")
    return resolved
