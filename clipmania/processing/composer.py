"""Programmatic video composition, montage concatenation, and watermark overlays."""

from __future__ import annotations

import tempfile
from pathlib import Path

from rich.console import Console

from .ffmpeg import probe, run_ffmpeg_with_progress

console = Console()

try:
    import moviepy.editor as mp  # type: ignore[import-untyped]
    _MOVIEPY_AVAILABLE = True
except ImportError:
    mp = None
    _MOVIEPY_AVAILABLE = False


def has_moviepy() -> bool:
    """Return True if MoviePy is installed."""
    return _MOVIEPY_AVAILABLE


def concat_clips(
    clip_paths: list[Path],
    output_path: Path,
    reencode: bool = True,
    crf: int = 20,
    preset: str = "fast",
) -> Path:
    """Concatenate multiple video clips sequentially into a single video montage.

    Uses FFmpeg concat filter when re-encoding to guarantee timestamp alignment
    and avoid audio drift.
    """
    if not clip_paths:
        raise ValueError("Must provide at least one clip path to concatenate.")

    if len(clip_paths) == 1:
        import shutil
        output_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(str(clip_paths[0]), str(output_path))
        return output_path

    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Calculate total duration for progress reporting
    total_duration = 0.0
    for p in clip_paths:
        try:
            total_duration += probe(p).duration
        except Exception:
            pass

    console.print(f"[bold blue]🎬 Concatenating {len(clip_paths)} clips[/] → {output_path}")

    if reencode:
        # Build filter_complex concat graph:
        # e.g.: [0:v][0:a][1:v][1:a]concat=n=2:v=1:a=1[v][a]
        args: list[str] = []
        for p in clip_paths:
            args.extend(["-i", str(p)])

        n = len(clip_paths)
        inputs_str = "".join(f"[{i}:v][{i}:a]" for i in range(n))
        filter_complex = f"{inputs_str}concat=n={n}:v=1:a=1[outv][outa]"

        args.extend([
            "-filter_complex", filter_complex,
            "-map", "[outv]",
            "-map", "[outa]",
            "-c:v", "libx264", "-preset", preset, "-crf", str(crf),
            "-c:a", "aac", "-b:a", "192k",
            str(output_path),
        ])
    else:
        # Stream-copy via concat demuxer
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as f:
            for p in clip_paths:
                f.write(f"file '{p.resolve().as_posix()}'\n")
            list_file = Path(f.name)

        try:
            args = [
                "-f", "concat",
                "-safe", "0",
                "-i", str(list_file),
                "-c", "copy",
                str(output_path),
            ]
        finally:
            list_file.unlink(missing_ok=True)

    run_ffmpeg_with_progress(args, duration=total_duration, description="Merging clips")
    console.print(f"[green]✓[/] Montage created → {output_path}")
    return output_path


def add_watermark(
    input_path: Path,
    output_path: Path,
    watermark_path: Path,
    position: str = "bottom-right",
    padding: int = 24,
    crf: int = 20,
    preset: str = "fast",
) -> Path:
    """Overlay a logo or branding watermark image onto the video.

    Parameters
    ----------
    position:
        "bottom-right", "top-right", "top-left", or "bottom-left"
    padding:
        Offset in pixels from the frame border.
    """
    if not watermark_path.is_file():
        raise FileNotFoundError(f"Watermark file not found: {watermark_path}")

    info = probe(input_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    pos_map = {
        "bottom-right": f"W-w-{padding}:H-h-{padding}",
        "top-right": f"W-w-{padding}:{padding}",
        "top-left": f"{padding}:{padding}",
        "bottom-left": f"{padding}:H-h-{padding}",
        "center": "(W-w)/2:(H-h)/2",
    }
    overlay_coords = pos_map.get(position, pos_map["bottom-right"])

    args = [
        "-i", str(input_path),
        "-i", str(watermark_path),
        "-filter_complex", f"[0:v][1:v]overlay={overlay_coords}[v]",
        "-map", "[v]",
        "-map", "0:a?",
        "-c:v", "libx264", "-preset", preset, "-crf", str(crf),
        "-c:a", "copy",
        str(output_path),
    ]

    run_ffmpeg_with_progress(args, duration=info.duration, description="Applying watermark")
    console.print(f"[green]✓[/] Watermarked clip created → {output_path}")
    return output_path
