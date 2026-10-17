"""Streaming multipart receiver and cheap container sniff.

The body is fed chunk by chunk into python-multipart's ``MultipartParser`` and the single
``file`` part is written straight to a bounded temp file while SHA-256 is computed; the
upload is never held in memory and no Starlette ``UploadFile`` spooling is involved.
MIME type and filename are ignored (hints only). Deep decode validation is the worker's job.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import struct
import time
from dataclasses import dataclass
from pathlib import Path

from python_multipart.multipart import MultipartParser, parse_options_header
from starlette.requests import ClientDisconnect, Request

from cashwatch.api.errors import ApiError

MULTIPART_OVERHEAD_BYTES = 1024 * 1024  # boundaries + part headers allowance

# Top-level QuickTime / ISO-BMFF atom types accepted as the first atom.
FIRST_ATOMS = {b"ftyp", b"moov", b"mdat", b"wide", b"free", b"skip", b"pnot", b"uuid", b"junk"}
MAX_ATOMS_WALKED = 512


@dataclass(frozen=True)
class ReceivedFile:
    sha256: str
    size: int


class _TooLarge(Exception):
    pass


class _BadForm(Exception):
    pass


def _boundary(content_type: str) -> bytes:
    ctype, params = parse_options_header(content_type)
    if ctype != b"multipart/form-data":
        raise ApiError(422, "invalid_multipart")
    boundary = params.get(b"boundary")
    if not boundary or len(boundary) > 200:
        raise ApiError(422, "invalid_multipart")
    return boundary


async def receive_file(request: Request, dest: Path, *, max_file_bytes: int, max_upload_sec: float,
                       started_mono: float) -> ReceivedFile:
    boundary = _boundary(request.headers.get("content-type", ""))
    hasher = hashlib.sha256()
    st = {"parts": 0, "file_parts": 0, "in_file": False, "size": 0, "ended": False,
          "field": b"", "value": b"", "headers": {}}
    fd = os.open(dest, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    fh = os.fdopen(fd, "wb")

    def on_part_begin() -> None:
        st["parts"] += 1
        st["headers"] = {}
        st["in_file"] = False

    def on_header_field(data: bytes, start: int, end: int) -> None:
        st["field"] += data[start:end]

    def on_header_value(data: bytes, start: int, end: int) -> None:
        st["value"] += data[start:end]

    def on_header_end() -> None:
        st["headers"][st["field"].lower()] = st["value"]
        st["field"], st["value"] = b"", b""

    def on_headers_finished() -> None:
        disp, params = parse_options_header(st["headers"].get(b"content-disposition", b""))
        if disp != b"form-data" or params.get(b"name") != b"file" or st["file_parts"] >= 1:
            raise _BadForm()
        st["file_parts"] += 1
        st["in_file"] = True

    def on_part_data(data: bytes, start: int, end: int) -> None:
        if not st["in_file"]:
            return
        n = end - start
        st["size"] += n
        if st["size"] > max_file_bytes:
            raise _TooLarge()
        chunk = data[start:end]
        hasher.update(chunk)
        fh.write(chunk)

    def on_part_end() -> None:
        st["in_file"] = False

    def on_end() -> None:
        st["ended"] = True

    parser = MultipartParser(boundary, {
        "on_part_begin": on_part_begin, "on_header_field": on_header_field,
        "on_header_value": on_header_value, "on_header_end": on_header_end,
        "on_headers_finished": on_headers_finished, "on_part_data": on_part_data,
        "on_part_end": on_part_end, "on_end": on_end,
    })
    body_limit = max_file_bytes + MULTIPART_OVERHEAD_BYTES
    raw = 0
    remaining = max_upload_sec - (time.monotonic() - started_mono)
    try:
        if remaining <= 0:
            raise ApiError(400, "upload_timeout")
        async with asyncio.timeout(remaining):
            async for chunk in request.stream():
                if not chunk:
                    continue
                raw += len(chunk)
                if raw > body_limit:
                    raise _TooLarge()
                parser.write(chunk)
        parser.finalize()
        if not st["ended"] or st["file_parts"] != 1:
            raise _BadForm()
        if st["size"] == 0:
            raise ApiError(422, "invalid_media")
        fh.flush()
        os.fsync(fh.fileno())
    except _TooLarge:
        raise ApiError(413, "payload_too_large") from None
    except TimeoutError:
        raise ApiError(400, "upload_timeout") from None
    except ClientDisconnect:
        raise ApiError(400, "invalid_request") from None
    except ApiError:
        raise
    except OSError as e:
        if e.errno == 28:  # ENOSPC
            raise ApiError(503, "storage_full") from None
        raise
    except Exception:  # _BadForm and python-multipart parse errors
        raise ApiError(422, "invalid_multipart") from None
    finally:
        fh.close()
    return ReceivedFile(sha256=hasher.hexdigest(), size=st["size"])


def sniff_container(path: Path) -> str:
    """Walk top-level atoms. Returns 'ok' | 'empty' | 'unsupported' | 'corrupt'.

    'unsupported': the bytes do not start with a QuickTime/ISO-BMFF atom (e.g. PNG, random).
    'corrupt': atom structure starts plausibly but no movie header (moov/moof) is reachable.
    An atom overrunning the file after a moov was seen is tolerated (worker decides).
    """
    size = path.stat().st_size
    if size == 0:
        return "empty"
    seen: set[bytes] = set()
    off = 0
    with path.open("rb") as f:
        for n in range(MAX_ATOMS_WALKED):
            if off >= size:
                break
            f.seek(off)
            hdr = f.read(8)
            if len(hdr) < 8:
                if n == 0:
                    return "unsupported"
                break
            asize, atype = struct.unpack(">I4s", hdr)
            hlen = 8
            if asize == 1:
                ext = f.read(8)
                if len(ext) < 8:
                    break
                asize = struct.unpack(">Q", ext)[0]
                hlen = 16
            elif asize == 0:
                asize = size - off
            if n == 0 and atype not in FIRST_ATOMS:
                return "unsupported"
            if not all(32 <= c < 127 for c in atype) or asize < hlen:
                return "unsupported" if n == 0 else "corrupt" if not (seen & {b"moov", b"moof"}) else "ok"
            seen.add(atype)
            if off + asize > size:
                break
            off += asize
    return "ok" if seen & {b"moov", b"moof"} else "corrupt"
