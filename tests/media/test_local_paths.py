"""Local-path validation, including Windows drives and forbidden network inputs."""

import os
import shutil
from pathlib import Path
from uuid import uuid4

import pytest

from cashwatch.errors import PipelineError
from cashwatch.media.paths import contained, validate_source
from cashwatch.media.tools import ffprobe, find_tool


@pytest.fixture
def local_dir():
    root = Path(__file__).resolve().parents[1] / "fixtures" / "generated"
    folder = root / ("local-path-" + uuid4().hex)
    folder.mkdir(parents=True)
    try:
        yield folder
    finally:
        shutil.rmtree(folder)


def test_existing_absolute_and_relative_file(local_dir):
    source = local_dir / "synthetic path.bin"
    source.write_bytes(b"synthetic path validation fixture")
    assert validate_source(source) == source.resolve()
    assert validate_source(source.as_posix()) == source.resolve()
    assert validate_source(os.path.relpath(source)) == source.resolve()


@pytest.mark.parametrize("source", [
    "http://example.invalid/video.mov", "https://example.invalid/video.mov",
    "file:/video.mov", "concat:a.mov|b.mov", "C:relative.mov",
    r"\\server\share\video.mov", "//server/share/video.mov",
    r"\\?\C:\video.mov", r"\\.\C:\video.mov", "bad\x00path",
])
def test_protocols_and_remote_paths_rejected_before_io(source, monkeypatch):
    def forbidden_stat(*args, **kwargs):
        pytest.fail("rejected inputs must not attempt filesystem access")
    with monkeypatch.context() as patch:
        patch.setattr(Path, "stat", forbidden_stat)
        with pytest.raises(PipelineError) as exc:
            validate_source(source)
    assert exc.value.code == "invalid_media"


@pytest.mark.skipif(os.name != "nt", reason="Windows drive paths")
def test_windows_alternate_stream_rejected(local_dir):
    with pytest.raises(PipelineError) as exc:
        validate_source(str(local_dir / "source.mov") + ":stream")
    assert exc.value.code == "invalid_media"


@pytest.mark.skipif(os.name != "nt", reason="Windows network symlink target")
def test_windows_resolved_remote_target_rejected(local_dir, monkeypatch):
    def forbidden_stat(*args, **kwargs):
        pytest.fail("resolved remote targets must not be accessed")
    with monkeypatch.context() as patch:
        patch.setattr(Path, "resolve", lambda self: Path(r"\\server\share\source.mov"))
        patch.setattr(Path, "stat", forbidden_stat)
        with pytest.raises(PipelineError) as exc:
            validate_source(local_dir / "link.mov")
    assert exc.value.code == "invalid_media"


@pytest.mark.skipif(os.name == "nt", reason="POSIX must not interpret Windows drives")
def test_windows_drive_rejected_on_posix():
    with pytest.raises(PipelineError):
        validate_source("C:/video.mov")


def test_invalid_files_and_containment(local_dir):
    empty = local_dir / "empty.mov"
    empty.touch()
    for source in (local_dir, empty, local_dir / "missing.mov"):
        with pytest.raises(PipelineError) as exc:
            validate_source(source)
        assert exc.value.code == "invalid_media"
    for relative in ("../escape", "/escape", r"..\escape", ""):
        with pytest.raises(PipelineError):
            contained(local_dir, relative)


def test_ffprobe_file_prefix_with_absolute_local_mov(local_dir):
    import av
    import numpy as np

    tool = find_tool("ffprobe")
    repo = Path(__file__).resolve().parents[2]
    bundled = repo / ".tools" / "ffmpeg" / "bin" / "ffprobe.exe"
    if tool is None and os.name == "nt" and bundled.is_file():
        tool = str(bundled)
    if tool is None:
        pytest.skip("ffprobe is not installed")
    source = local_dir / "synthetic path.mov"
    with av.open(str(source), mode="w", format="mov") as container:
        stream = container.add_stream("mpeg4", rate=30)
        stream.width = stream.height = 32
        stream.pix_fmt = "yuv420p"
        frame = av.VideoFrame.from_ndarray(np.zeros((32, 32, 3), dtype=np.uint8), format="rgb24")
        for packet in stream.encode(frame):
            container.mux(packet)
        for packet in stream.encode():
            container.mux(packet)
    metadata = ffprobe(tool, validate_source(source), timeout=10)
    assert metadata["streams"][0]["codec_type"] == "video"
    assert metadata["streams"][0]["width"] == 32
