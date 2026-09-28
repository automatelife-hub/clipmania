"""yt-dlp based ingestion for VODs and saved videos."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from rich.console import Console
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeRemainingColumn,
)

console = Console()

# yt-dlp format selection: prefer mp4 video + m4a audio, fall back to best.
FORMAT_SELECTION = "bv*[ext=mp4]+ba[ext=m4a]/b[ext=mp4]/best"


def clip_section(
    url: str,
    start: str,
    end: str,
    output_path: Path,
    format_sel: str = FORMAT_SELECTION,
) -> Path:
    """Download a time section from a video URL with frame-accurate cuts.

    Uses ``yt-dlp --download-sections`` and ``--force-keyframes-at-cuts``.

    Parameters
    ----------
    url:
        Video URL (YouTube, Twitch VOD, Kick VOD, etc.)
    start, end:
        Timestamps in any format yt-dlp accepts (e.g. ``"12:30"``, ``"1:02:30"``).
    output_path:
        Destination file path.
    format_sel:
        yt-dlp format selection string.

    Returns
    -------
    Path to the downloaded clip.
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    cmd = [
        "yt-dlp",
        "--download-sections", f"*{start}-{end}",
        "--force-keyframes-at-cuts",
        "-f", format_sel,
        "--merge-output-format", "mp4",
        "--newline",
        "--no-warnings",
        "-o", str(output_path),
        url,
    ]

    console.print(f"[bold blue]⬇  Clipping[/] {url}")
    console.print(f"   Section: {start} → {end}")

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("{task.percentage:>3.0f}%"),
        TimeRemainingColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("Downloading…", total=100)

        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )

        assert proc.stdout is not None
        for line in proc.stdout:
            line = line.strip()
            # yt-dlp progress lines look like: [download]  45.2% of ...
            if "[download]" in line and "%" in line:
                try:
                    pct_str = line.split("%")[0].rsplit(None, 1)[-1]
                    pct = float(pct_str)
                    progress.update(task, completed=pct)
                except (ValueError, IndexError):
                    pass

        proc.wait()

    if proc.returncode != 0:
        raise RuntimeError(
            f"yt-dlp exited with code {proc.returncode}. "
            f"Run with --verbose for details."
        )

    # yt-dlp may add format extensions; find the actual output file
    actual = _find_output(output_path)
    console.print(f"[green]✓[/] Saved clip: {actual}")
    return actual


def download_full(
    url: str,
    output_path: Path,
    format_sel: str = FORMAT_SELECTION,
) -> Path:
    """Download a complete video (TikTok, X short-form content).

    Parameters
    ----------
    url:
        Video URL.
    output_path:
        Destination file path.

    Returns
    -------
    Path to the downloaded file.
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    cmd = [
        "yt-dlp",
        "-f", format_sel,
        "--merge-output-format", "mp4",
        "--newline",
        "--no-warnings",
        "-o", str(output_path),
        url,
    ]

    console.print(f"[bold blue]⬇  Downloading[/] {url}")

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("{task.percentage:>3.0f}%"),
        TimeRemainingColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("Downloading…", total=100)

        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )

        assert proc.stdout is not None
        for line in proc.stdout:
            line = line.strip()
            if "[download]" in line and "%" in line:
                try:
                    pct_str = line.split("%")[0].rsplit(None, 1)[-1]
                    pct = float(pct_str)
                    progress.update(task, completed=pct)
                except (ValueError, IndexError):
                    pass

        proc.wait()

    if proc.returncode != 0:
        raise RuntimeError(
            f"yt-dlp exited with code {proc.returncode}. "
            f"Run with --verbose for details."
        )

    actual = _find_output(output_path)
    console.print(f"[green]✓[/] Saved: {actual}")
    return actual


def get_video_info(url: str) -> dict:
    """Retrieve video metadata without downloading.

    Uses ``yt-dlp --dump-json``.
    """
    cmd = ["yt-dlp", "--dump-json", "--no-warnings", url]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if result.returncode != 0:
        raise RuntimeError(f"yt-dlp metadata fetch failed: {result.stderr}")
    return json.loads(result.stdout)


def _find_output(expected: Path) -> Path:
    """Locate the actual output file, accounting for yt-dlp suffix changes."""
    if expected.exists():
        return expected
    # yt-dlp sometimes outputs a different container format (.mkv, .webm)
    # Search for any file with the same stem, preferring newest
    candidates = sorted(
        expected.parent.glob(f"{expected.stem}*"),
        key=lambda p: p.stat().st_mtime if p.is_file() else 0,
        reverse=True,
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    # Fall back: return expected and let the caller handle it
    return expected
