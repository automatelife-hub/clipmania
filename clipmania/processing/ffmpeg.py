"""FFmpeg and FFprobe subprocess wrappers with progress tracking."""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from rich.console import Console
from rich.progress import (
    BarColumn,
    Progress,
    TextColumn,
    TimeRemainingColumn,
)

console = Console()

_TIME_RE = re.compile(r"time=(\d+):(\d+):(\d+(?:\.\d+)?)")


@dataclass
class VideoInfo:
    """Probed video metadata."""

    path: Path
    duration: float
    width: int
    height: int
    video_codec: str
    audio_codec: str | None
    fps: float
    audio_sample_rate: int | None
    audio_channels: int | None
    bitrate: int | None

    @property
    def aspect_ratio(self) -> str:
        if self.width <= 0 or self.height <= 0:
            return "unknown"
        val = self.width / self.height
        if abs(val - 16 / 9) < 0.05:
            return "16:9"
        if abs(val - 9 / 16) < 0.05:
            return "9:16"
        if abs(val - 4 / 3) < 0.05:
            return "4:3"
        if abs(val - 1.0) < 0.05:
            return "1:1"
        if abs(val - 21 / 9) < 0.05:
            return "21:9"
        import math
        g = math.gcd(self.width, self.height)
        return f"{self.width // g}:{self.height // g}"

    @property
    def is_landscape(self) -> bool:
        return self.width > self.height

    @property
    def is_portrait(self) -> bool:
        return self.height > self.width


def _safe_int(val: object, default: int | None = None) -> int | None:
    """Convert a value to int, returning *default* on failure (e.g. ``'N/A'``)."""
    try:
        return int(val)  # type: ignore[arg-type]
    except (ValueError, TypeError):
        return default


def _safe_float(val: object, default: float = 0.0) -> float:
    """Convert a value to float, returning *default* on failure (e.g. ``'N/A'``)."""
    try:
        return float(val)  # type: ignore[arg-type]
    except (ValueError, TypeError):
        return default


def probe(path: Path) -> VideoInfo:
    """Probe a video file with ``ffprobe`` and return structured metadata."""
    cmd = [
        "ffprobe",
        "-v", "quiet",
        "-print_format", "json",
        "-show_format",
        "-show_streams",
        str(path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if result.returncode != 0:
        raise RuntimeError(f"ffprobe failed on {path}: {result.stderr}")

    data = json.loads(result.stdout)
    streams = data.get("streams", [])
    fmt = data.get("format", {})

    # Find video and audio streams
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)

    if video is None:
        raise RuntimeError(f"No video stream found in {path}")

    # Parse FPS from r_frame_rate (e.g. "30000/1001")
    fps = 30.0
    rfr = video.get("r_frame_rate", "30/1")
    if "/" in rfr:
        num, den = rfr.split("/")
        den_int = _safe_int(den, 1) or 1
        num_int = _safe_int(num, 30) or 30
        fps = num_int / den_int
    else:
        fps = _safe_float(rfr, 30.0)

    duration = _safe_float(
        fmt.get("duration", video.get("duration", 0)),
        default=0.0,
    )
    total_bitrate = _safe_int(fmt.get("bit_rate"))

    return VideoInfo(
        path=path,
        duration=duration,
        width=_safe_int(video.get("width"), 0) or 0,
        height=_safe_int(video.get("height"), 0) or 0,
        video_codec=video.get("codec_name", "unknown"),
        audio_codec=audio.get("codec_name") if audio else None,
        fps=round(fps, 3),
        audio_sample_rate=_safe_int(audio.get("sample_rate")) if audio else None,
        audio_channels=_safe_int(audio.get("channels")) if audio else None,
        bitrate=total_bitrate,
    )


def run_ffmpeg(
    args: list[str],
    duration: float | None = None,
    progress_callback: Callable[[float], None] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run an FFmpeg command with optional progress tracking.

    Parses ``time=HH:MM:SS.ms`` from stderr to compute progress percentage
    when *duration* is provided.
    """
    full_cmd = ["ffmpeg", "-y", "-hide_banner"] + args

    if progress_callback and duration and duration > 0:
        proc = subprocess.Popen(
            full_cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )

        try:
            assert proc.stderr is not None
            stderr_lines: list[str] = []
            for line in proc.stderr:
                stderr_lines.append(line)
                match = _TIME_RE.search(line)
                if match:
                    h, m, s = match.groups()
                    current = int(h) * 3600 + int(m) * 60 + float(s)
                    pct = min(current / duration * 100, 100.0)
                    progress_callback(pct)

            proc.wait(timeout=30)
        except BaseException:
            proc.kill()
            proc.wait(timeout=5)
            raise

        stderr_text = "".join(stderr_lines)
        if proc.returncode != 0:
            raise subprocess.CalledProcessError(
                proc.returncode, full_cmd, stderr=stderr_text
            )
        return subprocess.CompletedProcess(full_cmd, proc.returncode, stderr=stderr_text)

    # No progress tracking — simple blocking run
    return subprocess.run(
        full_cmd,
        capture_output=True,
        text=True,
        check=True,
        timeout=3600,  # H-5: 1 hour timeout
    )


def run_ffmpeg_with_progress(
    args: list[str],
    duration: float,
    description: str = "Processing",
) -> None:
    """Run FFmpeg with a Rich progress bar."""
    with Progress(
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("{task.percentage:>3.0f}%"),
        TimeRemainingColumn(),
        console=console,
    ) as progress:
        task = progress.add_task(description, total=100)

        def _update(pct: float) -> None:
            progress.update(task, completed=pct)

        run_ffmpeg(args, duration=duration, progress_callback=_update)
        progress.update(task, completed=100)



