"""Integration tests: extracting a clip from a *live* (still-growing) HLS buffer.

A live buffer playlist has no ``#EXT-X-ENDLIST``. Reading it directly with
FFmpeg's HLS demuxer makes FFmpeg treat it as a live stream and block waiting
for new segments, so extraction must not read the playlist as an input.
"""

from __future__ import annotations

import shutil
import subprocess
import threading
from pathlib import Path

import pytest

from clipmania.ingestion.streamlink_live import STATE_FILENAME, BufferState, clip_from_buffer

pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="FFmpeg/ffprobe not installed",
)


def _make_live_buffer(bdir: Path, total_seconds: int = 24, seg_seconds: int = 2) -> Path:
    """Write a synthetic HLS buffer whose playlist has no ENDLIST (like a live one)."""
    seg_dir = bdir / "chan"
    seg_dir.mkdir(parents=True)
    playlist = seg_dir / "live.m3u8"
    subprocess.run(
        [
            "ffmpeg", "-y", "-loglevel", "error",
            "-f", "lavfi", "-i", f"testsrc=size=320x240:rate=25:duration={total_seconds}",
            "-f", "lavfi", "-i", f"sine=frequency=440:duration={total_seconds}",
            "-c:v", "libx264", "-preset", "ultrafast", "-g", str(25 * seg_seconds),
            "-c:a", "aac", "-shortest",
            "-f", "hls", "-hls_time", str(seg_seconds), "-hls_list_size", "0",
            "-hls_flags", "omit_endlist",
            "-hls_segment_filename", str(seg_dir / "segment_%03d.ts"),
            str(playlist),
        ],
        check=True,
        timeout=60,
    )
    assert "#EXT-X-ENDLIST" not in playlist.read_text()
    BufferState(
        url="https://example.com/live", channel="chan", platform="youtube",
        buffer_dir=str(bdir), playlist_path=str(playlist),
    ).save(bdir / STATE_FILENAME)
    return playlist


def _duration(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, check=True,
    )
    return float(out.stdout.strip())


def test_clip_from_live_buffer_completes_and_is_playable(tmp_path):
    bdir = tmp_path / "buffer"
    _make_live_buffer(bdir)
    out = tmp_path / "out" / "clip.mp4"

    result: dict[str, object] = {}

    def run() -> None:
        try:
            result["path"] = clip_from_buffer(seconds=10, output_path=out, buffer_dir=bdir)
        except Exception as exc:  # pragma: no cover - surfaced by assertion below
            result["error"] = exc

    t = threading.Thread(target=run, daemon=True)
    t.start()
    t.join(timeout=30)

    assert not t.is_alive(), "clip_from_buffer hung on a live (no ENDLIST) playlist"
    assert "error" not in result, result.get("error")
    assert result["path"] == out
    # Valid MP4 (has moov atom) covering roughly the requested window
    assert 9.5 <= _duration(out) <= 12.5


def test_clip_from_live_buffer_takes_the_most_recent_segments(tmp_path, monkeypatch):
    bdir = tmp_path / "buffer"
    playlist = _make_live_buffer(bdir, total_seconds=24, seg_seconds=2)
    out = tmp_path / "clip.mp4"

    concat_lists: list[str] = []
    real_run = subprocess.run

    def spy_run(cmd, *args, **kwargs):
        if "concat" in cmd:
            concat_lists.append(Path(cmd[cmd.index("-i") + 1]).read_text())
        return real_run(cmd, *args, **kwargs)

    monkeypatch.setattr("clipmania.ingestion.streamlink_live.subprocess.run", spy_run)
    clip_from_buffer(seconds=4, output_path=out, buffer_dir=bdir)

    all_segs = [ln for ln in playlist.read_text().splitlines() if ln.endswith(".ts")]
    assert len(concat_lists) == 1
    used = [ln.split("/")[-1].rstrip("'") for ln in concat_lists[0].splitlines() if ln]
    assert used == [Path(s).name for s in all_segs[-2:]]
    assert 3.5 <= _duration(out) <= 6.5


def test_clip_from_buffer_errors_when_no_segments(tmp_path):
    bdir = tmp_path / "buffer"
    seg_dir = bdir / "chan"
    seg_dir.mkdir(parents=True)
    playlist = seg_dir / "live.m3u8"
    playlist.write_text("#EXTM3U\n#EXT-X-VERSION:3\n#EXT-X-TARGETDURATION:5\n")
    BufferState(
        url="u", channel="chan", platform="youtube",
        buffer_dir=str(bdir), playlist_path=str(playlist),
    ).save(bdir / STATE_FILENAME)

    with pytest.raises(RuntimeError, match="no segments"):
        clip_from_buffer(seconds=10, output_path=tmp_path / "c.mp4", buffer_dir=bdir)
