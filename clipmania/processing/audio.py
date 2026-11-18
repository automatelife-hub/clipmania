"""EBU R128 loudness normalization (two-pass)."""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from rich.console import Console

from .ffmpeg import probe, run_ffmpeg, run_ffmpeg_with_progress

console = Console()

_LOUDNORM_JSON_RE = re.compile(
    r"\{[^}]*\"input_i\"[^}]*\}",
    re.DOTALL,
)


@dataclass
class LoudnessStats:
    """Measured loudness statistics from the analysis pass."""

    input_i: float
    input_tp: float
    input_lra: float
    input_thresh: float
    target_offset: float


def measure_loudness(input_path: Path) -> LoudnessStats:
    """Pass 1 — measure loudness without producing output.

    Runs ``ffmpeg -i <input> -af loudnorm=print_format=json -f null -``
    and parses the JSON statistics block from stderr.
    """
    # H-11: Guard against silent / audio-less video
    info = probe(input_path)
    if info.audio_codec is None:
        raise ValueError(f"Cannot measure loudness: {input_path} has no audio stream.")

    console.print("[dim]   Measuring loudness (pass 1/2)…[/]")

    result = subprocess.run(
        [
            "ffmpeg", "-y", "-hide_banner",
            "-i", str(input_path),
            "-af", "loudnorm=print_format=json",
            "-f", "null",
            "-",
        ],
        capture_output=True,
        text=True,
        timeout=3600,  # H-5: 1 hour timeout for long videos
    )

    stderr = result.stderr
    # The loudnorm filter prints a JSON block at the very end of stderr
    match = _LOUDNORM_JSON_RE.search(stderr)
    if not match:
        raise RuntimeError(
            "Could not parse loudnorm output. FFmpeg stderr:\n"
            + stderr[-800:]
        )

    data = json.loads(match.group())
    return LoudnessStats(
        input_i=float(data["input_i"]),
        input_tp=float(data["input_tp"]),
        input_lra=float(data["input_lra"]),
        input_thresh=float(data["input_thresh"]),
        target_offset=float(data["target_offset"]),
    )


def normalize_audio(
    input_path: Path,
    output_path: Path,
    target_lufs: float = -14.0,
    lra: float = 11.0,
    true_peak: float = -1.5,
    audio_bitrate: str = "192k",
    copy_video: bool = True,
) -> Path:
    """Two-pass EBU R128 loudness normalization.

    Pass 1 — measure actual loudness with ``loudnorm=print_format=json``.
    Pass 2 — apply normalization using measured values for precision.

    When *copy_video* is ``True`` the video stream is copied without
    re-encoding (``-c:v copy``), which is much faster.
    """
    info = probe(input_path)
    if info.audio_codec is None:
        raise ValueError(f"Cannot normalize audio: {input_path} has no audio stream.")
    console.print(f"[bold blue]🔊 Audio normalization[/] → {target_lufs} LUFS")

    # Pass 1: measure
    stats = measure_loudness(input_path)
    console.print(
        f"   Measured: I={stats.input_i:.1f} LUFS, "
        f"TP={stats.input_tp:.1f} dBTP, "
        f"LRA={stats.input_lra:.1f} LU"
    )

    # Pass 2: apply with measured values
    loudnorm_filter = (
        f"loudnorm=I={target_lufs}:LRA={lra}:TP={true_peak}"
        f":measured_I={stats.input_i}"
        f":measured_LRA={stats.input_lra}"
        f":measured_TP={stats.input_tp}"
        f":measured_thresh={stats.input_thresh}"
        f":offset={stats.target_offset}"
        ":linear=true"
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)

    args = [
        "-i", str(input_path),
        "-af", loudnorm_filter,
    ]

    if copy_video:
        args += ["-c:v", "copy"]

    args += [
        "-c:a", "aac", "-b:a", audio_bitrate,
        str(output_path),
    ]

    run_ffmpeg_with_progress(
        args, duration=info.duration, description="Normalizing audio (pass 2/2)"
    )

    console.print(f"[green]✓[/] Audio normalized → {output_path}")
    return output_path


def build_loudnorm_filter(
    target_lufs: float = -14.0,
    lra: float = 11.0,
    true_peak: float = -1.5,
) -> str:
    """Return a single-pass ``loudnorm`` filter string (less precise but usable
    for combined layout+audio single-pass encoding)."""
    return f"loudnorm=I={target_lufs}:LRA={lra}:TP={true_peak}"
