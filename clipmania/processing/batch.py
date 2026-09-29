"""Batch video processing engine with concurrent worker pool."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

from rich.console import Console
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn, TimeRemainingColumn

from ..config import ClipManiaConfig, load_config
from ..platforms import get_spec
from ..utils import extract_channel, format_timestamp, safe_filename
from .layouts import Layout
from .pipeline import process_clip

console = Console()


@dataclass
class ClipJob:
    """Specification for an individual clipping task."""

    url: str
    start: str | None = None
    end: str | None = None
    chapter: str | None = None
    layout: str | None = None
    platform: str | None = None
    output_filename: str | None = None


def load_batch_config(config_path: Path) -> list[ClipJob]:
    """Parse a batch clipping specification from a JSON or TOML file."""
    if not config_path.is_file():
        raise FileNotFoundError(f"Batch config file not found: {config_path}")

    suffix = config_path.suffix.lower()
    raw_jobs: list[dict[str, Any]] = []

    if suffix == ".json":
        with open(config_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            raw_jobs = data.get("clips") or []
    elif suffix in (".toml", ".tml"):
        try:
            import tomllib
        except ImportError:
            import tomli as tomllib  # type: ignore[no-redef]
        with open(config_path, "rb") as f:
            data = tomllib.load(f)
            raw_jobs = data.get("clips") or []
    else:
        raise ValueError(f"Unsupported batch file format {suffix!r}. Use .json or .toml")

    jobs: list[ClipJob] = []
    for item in raw_jobs:
        if not item.get("url"):
            continue
        jobs.append(
            ClipJob(
                url=item["url"],
                start=item.get("start"),
                end=item.get("end"),
                chapter=item.get("chapter"),
                layout=item.get("layout"),
                platform=item.get("platform"),
                output_filename=item.get("output_filename"),
            )
        )

    if not jobs:
        raise ValueError(f"No valid clipping jobs found in {config_path}")

    return jobs


def _execute_single_job(
    job: ClipJob,
    out_dir: Path,
    cfg: ClipManiaConfig,
) -> Path:
    """Execute download and processing for a single job."""
    from ..ingestion.metadata import fetch_metadata, find_chapter
    from ..ingestion.ytdlp import clip_section

    start = job.start
    end = job.end

    if job.chapter and (not start or not end):
        meta = fetch_metadata(job.url)
        ch = find_chapter(meta.chapters, job.chapter)
        if not ch:
            raise ValueError(f"Chapter {job.chapter!r} not found for {job.url}")
        start = format_timestamp(ch.start_time)
        end = format_timestamp(ch.end_time)

    if not start or not end:
        raise ValueError(f"Job for {job.url} missing start/end timestamps or chapter.")

    channel = extract_channel(job.url)
    raw_name = f"_raw_{uuid4().hex}.mp4"
    raw_path = out_dir / raw_name

    actual_raw = clip_section(job.url, start, end, raw_path)

    try:
        layout_str = job.layout or cfg.default_layout
        layout_mode = Layout.from_string(layout_str)
        final = process_clip(
            actual_raw,
            layout=layout_mode,
            platform=job.platform,
            output_dir=out_dir,
            config=cfg,
            channel=channel,
        )

        # Honour user-specified output filename if provided
        if job.output_filename:
            requested = out_dir / job.output_filename
            requested.parent.mkdir(parents=True, exist_ok=True)
            final.rename(requested)
            final = requested

        return final
    finally:
        actual_raw.unlink(missing_ok=True)


def process_batch(
    jobs: list[ClipJob],
    workers: int = 4,
    output_dir: Path | None = None,
    config: ClipManiaConfig | None = None,
) -> list[Path]:
    """Execute multiple clipping tasks concurrently using a worker pool."""
    cfg = config or load_config()
    out_dir = output_dir or Path(cfg.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    console.print(
        f"\n[bold]ClipMania Batch[/] — Processing {len(jobs)} clips "
        f"across {workers} worker threads\n"
    )

    results: list[Path] = []
    errors: list[tuple[ClipJob, Exception]] = []

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        TimeRemainingColumn(),
        console=console,
    ) as progress:
        task_id = progress.add_task("Clipping jobs", total=len(jobs))

        with ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
            future_to_job = {
                executor.submit(_execute_single_job, job, out_dir, cfg): job
                for job in jobs
            }

            for future in as_completed(future_to_job):
                job = future_to_job[future]
                try:
                    result_path = future.result()
                    results.append(result_path)
                    progress.console.print(f"[green]✓ Completed:[/] {result_path.name}")
                except Exception as exc:
                    errors.append((job, exc))
                    progress.console.print(f"[red]✗ Failed ({job.url}):[/] {exc}")
                finally:
                    progress.advance(task_id)

    console.print(
        f"\n[bold]Batch complete:[/] [green]{len(results)} succeeded[/], "
        f"[red]{len(errors)} failed[/]\n"
    )
    return results
