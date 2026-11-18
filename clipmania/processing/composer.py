"""Programmatic video composition, montage concatenation, and watermark overlays."""

from __future__ import annotations

import tempfile
from pathlib import Path

from rich.console import Console

from .ffmpeg import probe, run_ffmpeg_with_progress

console = Console()

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
        # H-11: Check if all clips have audio streams
        all_have_audio = True
        for p in clip_paths:
            try:
                clip_info = probe(p)
                if clip_info.audio_codec is None:
                    all_have_audio = False
                    break
            except Exception:
                all_have_audio = False
                break

        args: list[str] = []
        for p in clip_paths:
            args.extend(["-i", str(p)])

        n = len(clip_paths)
        if all_have_audio:
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
            inputs_str = "".join(f"[{i}:v]" for i in range(n))
            filter_complex = f"{inputs_str}concat=n={n}:v=1:a=0[outv]"
            args.extend([
                "-filter_complex", filter_complex,
                "-map", "[outv]",
                "-c:v", "libx264", "-preset", preset, "-crf", str(crf),
                "-an",
                str(output_path),
            ])
    else:
        # Stream-copy via concat demuxer
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as f:
            for p in clip_paths:
                # L-2: Escape single quotes in paths for FFmpeg concat demuxer
                escaped = str(p.resolve().as_posix()).replace("'", "'\\''")
                f.write(f"file '{escaped}'\n")
            list_file = Path(f.name)

        try:
            args = [
                "-f", "concat",
                "-safe", "0",
                "-i", str(list_file),
                "-c", "copy",
                str(output_path),
            ]
            run_ffmpeg_with_progress(args, duration=total_duration, description="Merging clips")
        finally:
            list_file.unlink(missing_ok=True)

        console.print(f"[green]✓[/] Montage created → {output_path}")
        return output_path

    run_ffmpeg_with_progress(args, duration=total_duration, description="Merging clips")
    console.print(f"[green]✓[/] Montage created → {output_path}")
    return output_path

