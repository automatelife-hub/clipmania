"""External dependency checker for ffmpeg, ffprobe, streamlink, and yt-dlp."""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass

import click
from rich.console import Console
from rich.table import Table

console = Console()


@dataclass
class DepInfo:
    """Information about an external dependency."""

    name: str
    path: str | None
    version: str | None
    required: bool


def _get_version(cmd: str) -> str | None:
    """Run ``<cmd> --version`` and return the first line of output."""
    try:
        result = subprocess.run(
            [cmd, "--version"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        first_line = (result.stdout or result.stderr or "").strip().splitlines()[0]
        return first_line
    except (FileNotFoundError, subprocess.TimeoutExpired, IndexError):
        return None


def check_dependency(name: str, required: bool = True) -> DepInfo:
    """Check whether *name* is available on ``PATH``."""
    path = shutil.which(name)
    version = _get_version(name) if path else None
    return DepInfo(name=name, path=path, version=version, required=required)


# All four tools — streamlink is only strictly required for live capture.
_DEPS = [
    ("ffmpeg", True),
    ("ffprobe", True),
    ("yt-dlp", True),
    ("streamlink", False),  # only needed for live buffer
]


def check_all_dependencies(verbose: bool = False) -> list[DepInfo]:
    """Verify required dependencies and optionally print a status table.

    Raises :class:`click.ClickException` if any required dependency is missing.
    """
    results: list[DepInfo] = []
    missing: list[str] = []

    for name, required in _DEPS:
        info = check_dependency(name, required=required)
        results.append(info)
        if required and info.path is None:
            missing.append(name)

    if verbose:
        table = Table(title="Dependency Check", show_lines=True)
        table.add_column("Tool", style="bold")
        table.add_column("Status")
        table.add_column("Path")
        table.add_column("Version")

        for dep in results:
            status = "[green]✓ Found[/]" if dep.path else (
                "[red]✗ MISSING[/]" if dep.required else "[yellow]⚠ Not found (optional)[/]"
            )
            table.add_row(
                dep.name,
                status,
                dep.path or "—",
                dep.version or "—",
            )
        console.print(table)

    if missing:
        names = ", ".join(missing)
        raise click.ClickException(
            f"Required dependencies not found: {names}\n"
            "Install them and ensure they are on your PATH.\n"
            "  • FFmpeg: https://ffmpeg.org/download.html\n"
            "  • yt-dlp: pip install yt-dlp\n"
            "  • Streamlink: pip install streamlink"
        )

    return results
