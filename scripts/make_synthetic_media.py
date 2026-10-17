#!/usr/bin/env python3
"""Generate SYNTHETIC media fixtures for cashwatch media/pipeline tests.

These clips are generated geometry (ffmpeg lavfi ``testsrc2``/``color`` sources) and are test
assets only -- never bank footage and never evidence of anything. Output goes to
``tests/fixtures/generated/`` (git-ignored) unless ``--out`` is given.

Usage::

    .venv/bin/python scripts/make_synthetic_media.py            # build all missing fixtures
    .venv/bin/python scripts/make_synthetic_media.py --only vfr_10s --force
    .venv/bin/python scripts/make_synthetic_media.py --list

Frame barcode
-------------
Every generated video frame carries a binary barcode in the top-left corner of the *stored*
(un-rotated) picture so automated tests can identify exactly which source frame an image
came from: a black box of 7x5 cells (cell = floor(stored_height / 16) px) at offset
(cell/2, cell/2); inside it a 6x4 grid of white/black cells. Bits 0..15 (row-major) are the
encoder-input frame index ``n``; bits 16..23 are the checksum ``(n*37 + 11) mod 256``.
:func:`decode_barcode` reads it back (returns ``None`` on checksum mismatch).

Ground truth
------------
For every decodable fixture a ``<name>.truth.json`` sidecar is written with the
presentation-order frame list produced *independently of PyAV*: ``ffprobe -show_frames``
(frame pts / time base, edit lists applied by libavformat as a player would) zipped with the
barcode read from ``ffmpeg -noautorotate`` rawvideo output of the same frames. Tests compare
the media layer's PTS-derived times against this table.

Fixtures (name -> purpose)
--------------------------
cfr_10s_2997 / cfr_10s_30 / cfr_10s_60  10 s 1280x720 H.264 CFR, no B-frames (T05)
cfr_145s_2997_1080p   145 s 1920x1080 29.97 fps H.264 ~10 Mbit/s, B-frames (T05, latency)
hevc_10s              10 s 1280x720 HEVC tagged hvc1 in MOV
vfr_10s               variable frame rate (60 fps source with irregular drops, mixed 1/60 & 2/60 s)
offset_start_10s      nonzero start time (-output_ts_offset 7.25)
bframes_10s           x264 bframes=3 + b-pyramid, keyint 48 (ctts + B-frame delay edit list)
editlist_10s          stream-copy trim (-ss 2.5 -c copy) of a keyint-60 B-frame clip => edit
                      list whose media_time starts mid-GOP (pre-roll frames must not count)
rotated_cw90_10s      display matrix requiring 90 deg clockwise rotation (phone portrait style)
rotated_ccw90_10s     display matrix requiring 90 deg counter-clockwise rotation
audio_10s             H.264 + AAC sine track (audio must be ignored)
marker_violation_40s  40 s 1280x720 30 fps: grey bg, yellow zone; red square inside the zone
                      until t=20.0 s, outside from t=20.0 s (frame 600) to the end
marker_negative_40s   same scene, red square never leaves the zone
huge_res              8192x4352 single-second clip (decode-bomb / limits test)
many_frames           64x64 @ 120 fps 10 s (1200 frames; frame-count guard test)
truncated_nomoov      first 40 % of a non-faststart MOV (moov missing)
truncated_faststart   first 50 % of a faststart MOV (index present, media data missing)
empty                 zero-byte .mov
png_renamed           PNG image renamed .mov
mkv_renamed           Matroska container renamed .mov (unsupported container)
mov_renamed_bin       valid MOV renamed .bin (must be accepted)
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from collections.abc import Callable
from fractions import Fraction
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = REPO_ROOT / "tests" / "fixtures" / "generated"
FFMPEG = shutil.which("ffmpeg") or "/opt/homebrew/bin/ffmpeg"
FFPROBE = shutil.which("ffprobe") or "/opt/homebrew/bin/ffprobe"
FONT = Path("/System/Library/Fonts/Supplemental/Arial.ttf")
TIMEOUT = 600

INDEX_BITS = 16
CHECK_BITS = 8
COLS, ROWS = 6, 4


def checksum(n: int) -> int:
    return (n * 37 + 11) % 256


# --------------------------------------------------------------------------- barcode

def _barcode_filters(height: int) -> list[str]:
    c = height // 16
    x0 = y0 = c // 2
    f = [f"drawbox=x={x0}:y={y0}:w={7 * c}:h={5 * c}:color=black:t=fill"]
    for bit in range(INDEX_BITS + CHECK_BITS):
        r, col = divmod(bit, COLS)
        x = x0 + c // 2 + col * c
        y = y0 + c // 2 + r * c
        if bit < INDEX_BITS:
            cond = f"mod(floor(n/{2 ** bit})\\,2)"
        else:
            j = bit - INDEX_BITS
            cond = f"mod(floor(mod(n*37+11\\,256)/{2 ** j})\\,2)"
        f.append(f"drawbox=x={x}:y={y}:w={c}:h={c}:color=white:t=fill:enable='{cond}'")
    return f


def decode_barcode(gray: np.ndarray) -> int | None:
    """Decode the frame index from a grayscale array in *stored* orientation (any scale)."""
    h, w = gray.shape[:2]
    c = h / 16.0
    bits = []
    for bit in range(INDEX_BITS + CHECK_BITS):
        r, col = divmod(bit, COLS)
        cx = c / 2 + c / 2 + col * c + c / 2
        cy = c / 2 + c / 2 + r * c + c / 2
        rad = max(1, int(c * 0.2))
        x, y = int(cx), int(cy)
        patch = gray[max(0, y - rad): y + rad + 1, max(0, x - rad): x + rad + 1]
        bits.append(1 if float(patch.mean()) > 128 else 0)
    n = sum(b << i for i, b in enumerate(bits[:INDEX_BITS]))
    chk = sum(b << i for i, b in enumerate(bits[INDEX_BITS:]))
    return n if chk == checksum(n) else None


def _label_filter() -> list[str]:
    if not FONT.exists():
        return []
    return [f"drawtext=fontfile={FONT}:text='SYNTHETIC n=%{{n}} pts=%{{pts}}':x=w*0.45:y=h*0.04:"
            "fontsize=h/24:fontcolor=white:box=1:boxcolor=black@0.6"]


# --------------------------------------------------------------------------- ffmpeg helpers

def run(args: list[str], *, timeout: float = TIMEOUT, input_bytes: bytes | None = None) -> bytes:
    proc = subprocess.run(args, input=input_bytes, capture_output=True, timeout=timeout, check=False)
    if proc.returncode != 0:
        raise RuntimeError(f"command failed ({proc.returncode}): {' '.join(args[:6])} ...\n"
                           + proc.stderr.decode(errors="replace")[-2000:])
    return proc.stdout


def ff(*args: str) -> list[str]:
    return [FFMPEG, "-hide_banner", "-loglevel", "error", "-nostdin", "-y", *args]


def encode(out: Path, *, size: str, rate: str, duration: float, source: str = "testsrc2",
           vcodec: list[str] | None = None, extra_vf: list[str] | None = None,
           post_vf: list[str] | None = None, out_args: list[str] | None = None,
           audio: bool = False, noise: bool = True, label: bool = True) -> None:
    w, h = (int(v) for v in size.split("x"))
    if source == "testsrc2":
        src = f"testsrc2=size={size}:rate={rate}:duration={duration}"
    else:
        src = f"color=c={source}:size={size}:rate={rate}:duration={duration}"
    vf = list(extra_vf or [])
    if noise:
        vf.append("noise=alls=3:allf=t")
    vf += _barcode_filters(h)
    if label:
        vf += _label_filter()
    vf += list(post_vf or [])
    vf.append("format=yuv420p")
    args = ["-f", "lavfi", "-i", src]
    if audio:
        args += ["-f", "lavfi", "-i", f"sine=frequency=440:sample_rate=48000:duration={duration}"]
    args += ["-vf", ",".join(vf), "-map", "0:v"]
    if audio:
        args += ["-map", "1:a", "-c:a", "aac", "-b:a", "96k"]
    args += vcodec or ["-c:v", "libx264", "-preset", "veryfast", "-crf", "24", "-bf", "0", "-g", "60"]
    args += list(out_args or [])
    args += ["-f", "mov", str(out)]
    run(ff(*args))


def ffprobe_json(path: Path, *entries: str) -> dict:
    args = [FFPROBE, "-v", "error", "-protocol_whitelist", "file", "-of", "json", *entries, str(path)]
    return json.loads(run([a for a in args if a]).decode())


def build_truth(path: Path) -> dict:
    """Presentation-order frames: ffprobe pts zipped with barcodes from ffmpeg rawvideo."""
    streams = ffprobe_json(path, "-select_streams", "v:0", "-show_streams")["streams"]
    st = streams[0]
    tb = Fraction(st["time_base"])
    frames = ffprobe_json(path, "-select_streams", "v:0", "-show_frames",
                          "-show_entries", "frame=pts,best_effort_timestamp,duration")["frames"]
    pts = [int(f.get("pts", f.get("best_effort_timestamp"))) for f in frames]
    sw, sh = int(st["width"]), int(st["height"])
    th = 144
    tw = max(2, round(sw * th / sh / 2) * 2)
    raw = run(ff("-noautorotate", "-i", str(path), "-map", "0:v:0", "-fps_mode", "passthrough",
                 "-vf", f"scale={tw}:{th}:flags=area,format=gray", "-f", "rawvideo", "pipe:1"))
    imgs = np.frombuffer(raw, dtype=np.uint8).reshape(-1, th, tw)
    if len(imgs) != len(pts):
        raise RuntimeError(f"truth mismatch for {path.name}: {len(imgs)} images vs {len(pts)} pts")
    codes = [decode_barcode(im) for im in imgs]
    origin = pts[0]
    side = st.get("side_data_list") or []
    rotation = next((sd.get("rotation") for sd in side if "rotation" in sd), None)
    return {
        "time_base": [tb.numerator, tb.denominator],
        "width": sw, "height": sh, "codec": st["codec_name"],
        "ffprobe_rotation_ccw": rotation,
        "frames": [{"pts": p, "rel": float((p - origin) * tb), "barcode": c}
                   for p, c in zip(pts, codes, strict=True)],
    }


# --------------------------------------------------------------------------- fixture builders

def _tmp(out: Path) -> Path:
    return out.with_name(out.stem + ".tmp" + out.suffix)


def fx_cfr(rate: str, duration: float = 10.0, size: str = "1280x720") -> Callable[[Path], None]:
    def build(out: Path) -> None:
        encode(out, size=size, rate=rate, duration=duration)
    return build


def fx_145s(out: Path) -> None:
    encode(out, size="1920x1080", rate="30000/1001", duration=145.0,
           vcodec=["-c:v", "libx264", "-preset", "veryfast", "-b:v", "10M", "-maxrate", "14M",
                   "-bufsize", "20M", "-bf", "2", "-g", "60"])


def fx_hevc(out: Path) -> None:
    encode(out, size="1280x720", rate="30", duration=10.0,
           vcodec=["-c:v", "libx265", "-preset", "fast", "-crf", "24", "-tag:v", "hvc1",
                   "-x265-params", "log-level=error:keyint=60:bframes=3"])


def fx_vfr(out: Path) -> None:
    # 60 fps source; drop frames irregularly -> intervals of 1/60, 2/60 and 3/60 s.
    encode(out, size="1280x720", rate="60", duration=10.0,
           post_vf=["select='not(eq(mod(n\\,7)\\,3))*not(eq(mod(n\\,5)\\,1))*not(between(mod(n\\,120)\\,60\\,61))'"],
           out_args=["-fps_mode", "vfr", "-video_track_timescale", "90000"])


def fx_offset(out: Path) -> None:
    encode(out, size="1280x720", rate="30", duration=10.0, out_args=["-output_ts_offset", "7.25"])


def fx_bframes(out: Path) -> None:
    encode(out, size="1280x720", rate="30", duration=10.0,
           vcodec=["-c:v", "libx264", "-preset", "medium", "-crf", "22", "-bf", "3",
                   "-x264-params", "b-pyramid=normal:keyint=48:min-keyint=48"])


def fx_editlist(out: Path) -> None:
    base = _tmp(out)
    encode(base, size="1280x720", rate="30", duration=12.0,
           vcodec=["-c:v", "libx264", "-preset", "veryfast", "-crf", "22", "-bf", "2", "-g", "60"])
    run(ff("-ss", "2.5", "-i", str(base), "-map", "0:v", "-c", "copy", "-f", "mov", str(out)))
    base.unlink()


def fx_rotated(display_rotation_ccw: int) -> Callable[[Path], None]:
    def build(out: Path) -> None:
        base = _tmp(out)
        encode(base, size="1280x720", rate="30", duration=10.0)
        run(ff("-display_rotation", str(display_rotation_ccw), "-i", str(base), "-map", "0:v",
               "-c", "copy", "-f", "mov", str(out)))
        base.unlink()
    return build


def fx_audio(out: Path) -> None:
    encode(out, size="1280x720", rate="30", duration=10.0, audio=True)


def fx_marker(leaves: bool) -> Callable[[Path], None]:
    def build(out: Path) -> None:
        # zone: yellow rectangle x 760..1160, y 200..600 (1280x720). Red square 120 px.
        zone = "drawbox=x=760:y=200:w=400:h=400:color=yellow:t=fill"
        inside = "drawbox=x=900:y=340:w=120:h=120:color=red:t=fill"
        outside = "drawbox=x=300:y=340:w=120:h=120:color=red:t=fill"
        vf = [zone]
        if leaves:
            vf += [inside + ":enable='lt(t\\,20)'", outside + ":enable='gte(t\\,20)'"]
        else:
            vf += [inside]
        encode(out, size="1280x720", rate="30", duration=40.0, source="gray", extra_vf=vf,
               noise=False, label=False,
               vcodec=["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-bf", "2", "-g", "30"])
    return build


def fx_huge(out: Path) -> None:
    encode(out, size="8192x4352", rate="1", duration=1.0, source="blue", noise=False, label=False,
           vcodec=["-c:v", "libx264", "-preset", "ultrafast", "-crf", "35"])


def fx_many_frames(out: Path) -> None:
    encode(out, size="64x64", rate="120", duration=10.0, source="blue", noise=False, label=False,
           vcodec=["-c:v", "libx264", "-preset", "ultrafast", "-crf", "35", "-bf", "0"])


def fx_truncated(faststart: bool, fraction: float) -> Callable[[Path], None]:
    def build(out: Path) -> None:
        base = _tmp(out)
        encode(base, size="1280x720", rate="30", duration=10.0,
               out_args=["-movflags", "+faststart"] if faststart else None)
        data = base.read_bytes()
        out.write_bytes(data[: int(len(data) * fraction)])
        base.unlink()
    return build


def fx_empty(out: Path) -> None:
    out.write_bytes(b"")


def fx_png(out: Path) -> None:
    from PIL import Image
    tmp = out.with_suffix(".png")
    Image.new("RGB", (320, 240), (40, 120, 200)).save(tmp, format="PNG")
    tmp.rename(out)


def fx_mkv(out: Path) -> None:
    run(ff("-f", "lavfi", "-i", "testsrc2=size=320x240:rate=30:duration=2", "-c:v", "libx264",
           "-preset", "ultrafast", "-f", "matroska", str(out)))


def fx_renamed_bin(out: Path) -> None:
    encode(out, size="640x360", rate="30", duration=3.0)


# name -> (filename, builder, has_truth)
FIXTURES: dict[str, tuple[str, Callable[[Path], None], bool]] = {
    "cfr_10s_2997": ("cfr_10s_2997.mov", fx_cfr("30000/1001"), True),
    "cfr_10s_30": ("cfr_10s_30.mov", fx_cfr("30"), True),
    "cfr_10s_60": ("cfr_10s_60.mov", fx_cfr("60"), True),
    "cfr_145s_2997_1080p": ("cfr_145s_2997_1080p.mov", fx_145s, True),
    "hevc_10s": ("hevc_10s.mov", fx_hevc, True),
    "vfr_10s": ("vfr_10s.mov", fx_vfr, True),
    "offset_start_10s": ("offset_start_10s.mov", fx_offset, True),
    "bframes_10s": ("bframes_10s.mov", fx_bframes, True),
    "editlist_10s": ("editlist_10s.mov", fx_editlist, True),
    "rotated_cw90_10s": ("rotated_cw90_10s.mov", fx_rotated(-90), True),
    "rotated_ccw90_10s": ("rotated_ccw90_10s.mov", fx_rotated(90), True),
    "audio_10s": ("audio_10s.mov", fx_audio, True),
    "marker_violation_40s": ("marker_violation_40s.mov", fx_marker(True), True),
    "marker_negative_40s": ("marker_negative_40s.mov", fx_marker(False), True),
    "huge_res": ("huge_res.mov", fx_huge, False),
    "many_frames": ("many_frames.mov", fx_many_frames, False),
    "truncated_nomoov": ("truncated_nomoov.mov", fx_truncated(False, 0.4), False),
    "truncated_faststart": ("truncated_faststart.mov", fx_truncated(True, 0.5), False),
    "empty": ("empty.mov", fx_empty, False),
    "png_renamed": ("png_renamed.mov", fx_png, False),
    "mkv_renamed": ("mkv_renamed.mov", fx_mkv, False),
    "mov_renamed_bin": ("mov_renamed_bin.bin", fx_renamed_bin, True),
}


def fixture_path(name: str, out_dir: Path = DEFAULT_OUT) -> Path:
    return out_dir / FIXTURES[name][0]


def build(name: str, out_dir: Path = DEFAULT_OUT, *, force: bool = False) -> Path:
    filename, builder, has_truth = FIXTURES[name]
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / filename
    truth = out_dir / f"{name}.truth.json"
    if out.exists() and (not has_truth or truth.exists()) and not force:
        return out
    tmp = out.with_name(out.name + ".part")
    tmp_named = tmp.with_suffix(tmp.suffix)  # builder writes final bytes here
    builder(tmp_named)
    tmp_named.replace(out)
    if has_truth:
        truth.write_text(json.dumps(build_truth(out)), encoding="utf-8")
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--only", action="append", choices=sorted(FIXTURES), help="fixture name (repeatable)")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--force", action="store_true", help="rebuild even if present")
    ap.add_argument("--list", action="store_true")
    a = ap.parse_args(argv)
    if a.list:
        for n, (fn, _, _) in FIXTURES.items():
            print(f"{n:24s} {fn}")
        return 0
    for name in a.only or list(FIXTURES):
        p = build(name, a.out, force=a.force)
        print(f"{name}: {p} ({p.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
