"""Offline legal-source snapshot helpers (no network).

Snapshots are captured during preparation by ``cashwatch.rules.capture`` and stored under
``rules/sources``. At runtime the registry only re-hashes the stored files and checks that every
excerpt is a verbatim slice of the derived text file, so a reported clause can never be a
model- or human-paraphrased "quotation".
"""

from __future__ import annotations

import hashlib
import html
import re
from pathlib import Path

_SCRIPT_STYLE = re.compile(r"<script.*?</script>|<style.*?</style>", re.S | re.I)
_BREAK = re.compile(r"<br\s*/?>", re.I)
_BLOCK_END = re.compile(r"</p>|</div>|</h\d>|</tr>|</li>", re.I)
_TAG = re.compile(r"<[^>]+>")


def html_to_text(markup: str) -> str:
    """Deterministic HTML -> plain text used for every derived ``*.txt`` snapshot.

    Changing this function changes text hashes and excerpt offsets; re-run capture if you do.
    """
    t = _SCRIPT_STYLE.sub("", markup)
    t = _BREAK.sub("\n", t)
    t = _BLOCK_END.sub("\n", t)
    t = html.unescape(_TAG.sub("", t))
    t = t.replace("\xa0", " ").replace("\r", "")
    t = re.sub(r"[ \t]+", " ", t)
    t = re.sub(r"\n\s*\n+", "\n", t)
    return t.strip() + "\n"


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def read_text(path: Path) -> str:
    return path.read_bytes().decode("utf-8")


def slice_excerpt(text: str, start: int, end: int) -> str:
    if not (0 <= start < end <= len(text)):
        raise ValueError("excerpt offsets out of range")
    return text[start:end]
