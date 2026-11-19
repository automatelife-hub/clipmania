"""ClipMania CLI — multi-platform video clipping pipeline."""

from __future__ import annotations

import sys
from pathlib import Path

import click
from rich.console import Console

from . import __version__
from .config import ClipManiaConfig, load_config
from .deps import check_all_dependencies
from .platforms import list_platforms
from .processing.layouts import Layout
from .utils import detect_platform, extract_channel

console = Console()


def _ensure_utf8_stdio() -> None:
    """Switch stdout/stderr to UTF-8 so Rich symbols (✓, 📡, …) can't crash
    the CLI on legacy Windows consoles that default to cp1252."""
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        encoding = (getattr(stream, "encoding", None) or "").lower().replace("-", "")
        if encoding == "utf8" or not hasattr(stream, "reconfigure"):
            continue
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            pass


# ---------------------------------------------------------------------------
# CLI group
# ---------------------------------------------------------------------------

@click.group()
@click.version_option(__version__, prog_name="clipmania")
@click.option(
    "--config", "config_path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    help="Path to clipmania.toml config file.",
)
@click.option(
    "--output-dir", "-o",
    type=click.Path(path_type=Path),
    default=None,
    help="Output directory for processed clips.",
)
@click.option("--verbose", "-v", is_flag=True, help="Show verbose output.")
@click.pass_context
def main(ctx: click.Context, config_path: Path | None, output_dir: Path | None, verbose: bool) -> None:
    """ClipMania — capture, clip, transform, and export video from any platform."""
    _ensure_utf8_stdio()
    ctx.ensure_object(dict)

    overrides = {}
    if output_dir:
        overrides["output_dir"] = str(output_dir)

    cfg = load_config(config_path=config_path, overrides=overrides or None)
    ctx.obj["config"] = cfg
    ctx.obj["verbose"] = verbose

    if not ctx.resilient_parsing:
        check_all_dependencies(verbose=verbose)


# ---------------------------------------------------------------------------
# clip — VOD / saved video section clipping
# ---------------------------------------------------------------------------

@main.command()
@click.argument("url")
@click.option("--start", "-s", default=None, help="Start timestamp (e.g. 1:30, 1h2m3s).")
@click.option("--end", "-e", default=None, help="End timestamp.")
@click.option("--chapter", "-c", default=None, help="Chapter title or 1-based index to clip.")
@click.option(
    "--layout", "-l",
    type=click.Choice([l.value for l in Layout], case_sensitive=False),
    default=None,
    help="Layout transform to apply.",
)
@click.option(
    "--platform", "-p",
    type=click.Choice(list_platforms(), case_sensitive=False),
    default=None,
    help="Target platform for spec validation.",
)
@click.option("--raw", is_flag=True, help="Skip processing; output raw clip only.")
@click.pass_context
def clip(
    ctx: click.Context,
    url: str,
    start: str | None,
    end: str | None,
    chapter: str | None,
    layout: str | None,
    platform: str | None,
    raw: bool,
) -> None:
    """Clip a section from a VOD, saved video, or chapter.

    \b
    Examples:
      clipmania clip "https://youtube.com/watch?v=..." --start 1:30 --end 2:00
      clipmania clip "https://youtube.com/watch?v=..." --chapter 2 --layout vertical-smart
      clipmania clip "https://kick.com/video/..." -s 12:30 -e 13:15 --layout vertical-blur
    """
    from .ingestion.metadata import fetch_metadata, find_chapter
    from .ingestion.ytdlp import clip_section
    from .processing.pipeline import process_clip
    from .utils import format_timestamp, parse_timestamp

    cfg: ClipManiaConfig = ctx.obj["config"]
    channel = extract_channel(url)

    # Resolve timestamps from chapter if --chapter is provided
    if chapter:
        console.print(f"[dim]   Querying video chapters for '{chapter}'...[/]")
        try:
            meta = fetch_metadata(url)
            ch = find_chapter(meta.chapters, chapter)
            if not ch:
                avail = ", ".join(f"'{c.title}'" for c in meta.chapters[:5]) or "None found"
                raise click.ClickException(f"Chapter '{chapter}' not found. Available: {avail}")
            start = format_timestamp(ch.start_time)
            end = format_timestamp(ch.end_time)
            console.print(f"[bold cyan]📖 Chapter found:[/] {ch.title} ({start} → {end})")
        except Exception as e:
            if isinstance(e, click.ClickException):
                raise
            raise click.ClickException(f"Failed to resolve chapter: {e}")
    elif not start or not end:
        raise click.ClickException("Must provide either --start and --end timestamps, or --chapter.")

    # Validate timestamps before passing to yt-dlp (M6)
    try:
        start_sec = parse_timestamp(start)
        end_sec = parse_timestamp(end)
    except ValueError as e:
        raise click.ClickException(f"Invalid timestamp: {e}")
    if start_sec >= end_sec:
        raise click.ClickException(
            f"Start ({start}) must be before end ({end})."
        )

    console.print(f"\n[bold]ClipMania[/] — Clipping from [cyan]{url}[/]\n")

    # Step 1: Download section
    raw_path = Path(cfg.output_dir) / f"_raw_{channel}.mp4"
    actual_raw: Path | None = None
    try:
        actual_raw = clip_section(url, start, end, raw_path)

        if raw:
            console.print(f"[green]✓ Raw clip saved:[/] {actual_raw}")
            return

        # Step 2: Process
        layout_mode = Layout.from_string(layout or cfg.default_layout)
        final = process_clip(
            actual_raw,
            layout=layout_mode,
            platform=platform,
            output_dir=Path(cfg.output_dir),
            config=cfg,
            channel=channel,
        )

        console.print(f"\n[bold green]🎬 Done![/] {final}\n")
    finally:
        # C-1: Only clean up raw file when not in --raw mode
        # H-6: Clean up even if clip_section() partially wrote a file
        if not raw:
            raw_path.unlink(missing_ok=True)
            if actual_raw is not None and actual_raw != raw_path:
                actual_raw.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# download — full video download (TikTok, X)
# ---------------------------------------------------------------------------

@main.command()
@click.argument("url")
@click.option(
    "--output", "-o",
    type=click.Path(path_type=Path),
    default=None,
    help="Output file path.",
)
@click.pass_context
def download(ctx: click.Context, url: str, output: Path | None) -> None:
    """Download a complete video (TikTok, X, or any supported platform).

    \b
    Examples:
      clipmania download "https://tiktok.com/@user/video/..."
      clipmania download "https://x.com/user/status/..."
    """
    from .ingestion.ytdlp import download_full

    cfg: ClipManiaConfig = ctx.obj["config"]
    channel = extract_channel(url)

    if output is None:
        platform = detect_platform(url)
        from .utils import safe_filename
        output = Path(cfg.output_dir) / safe_filename(platform, channel)

    output.parent.mkdir(parents=True, exist_ok=True)
    console.print(f"\n[bold]ClipMania[/] — Downloading from [cyan]{url}[/]\n")
    result = download_full(url, output)
    console.print(f"\n[bold green]✓ Downloaded:[/] {result}\n")


# ---------------------------------------------------------------------------
# live-start — start rolling live buffer
# ---------------------------------------------------------------------------

@main.command("live-start")
@click.argument("url")
@click.option("--quality", "-q", default=None, help="Stream quality (default: best).")
@click.pass_context
def live_start(ctx: click.Context, url: str, quality: str | None) -> None:
    """Start a rolling live-stream buffer for instant clipping.

    \b
    Examples:
      clipmania live-start "https://twitch.tv/shroud"
      clipmania live-start "https://kick.com/xqc"
    """
    from .ingestion.streamlink_live import start_live_buffer

    cfg: ClipManiaConfig = ctx.obj["config"]

    console.print(f"\n[bold]ClipMania[/] — Starting live buffer for [cyan]{url}[/]\n")

    start_live_buffer(
        url,
        buffer_dir=cfg.buffer_dir,
        segment_duration=cfg.segment_duration,
        playlist_size=cfg.playlist_size,
        quality=quality or cfg.quality,
    )

    console.print(
        f"\n[dim]Buffer is running. Use [bold]clipmania clip-now[/bold] to grab a clip,[/]"
        f"\n[dim]or [bold]clipmania live-stop[/bold] to end the session.[/]\n"
    )


# ---------------------------------------------------------------------------
# clip-now — extract from active live buffer
# ---------------------------------------------------------------------------

@main.command("clip-now")
@click.option("--seconds", "-s", type=click.IntRange(min=1), default=60, help="Seconds to extract (min: 1, default: 60).")
@click.option(
    "--layout", "-l",
    type=click.Choice([l.value for l in Layout], case_sensitive=False),
    default=None,
    help="Layout transform to apply.",
)
@click.option(
    "--platform", "-p",
    type=click.Choice(list_platforms(), case_sensitive=False),
    default=None,
    help="Target platform for spec validation.",
)
@click.option("--raw", is_flag=True, help="Skip processing; output raw clip only.")
@click.pass_context
def clip_now(
    ctx: click.Context,
    seconds: int,
    layout: str | None,
    platform: str | None,
    raw: bool,
) -> None:
    """Extract the last N seconds from the active live buffer.

    \b
    Examples:
      clipmania clip-now --seconds 30
      clipmania clip-now -s 60 --layout vertical-blur --platform tiktok
    """
    from .ingestion.streamlink_live import clip_from_buffer, get_buffer_status
    from .processing.pipeline import process_clip

    cfg: ClipManiaConfig = ctx.obj["config"]

    status = get_buffer_status(cfg.buffer_dir)
    if status is None:
        raise click.ClickException(
            "No active live buffer. Start one with: clipmania live-start <URL>"
        )

    console.print(
        f"\n[bold]ClipMania[/] — Clipping last {seconds}s from "
        f"[cyan]{status.channel}[/] live buffer\n"
    )

    from .utils import safe_filename
    from uuid import uuid4

    out_dir = Path(cfg.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    if raw:
        target = out_dir / safe_filename(status.platform, status.channel)
        raw_path = clip_from_buffer(
            seconds=seconds,
            output_path=target,
            buffer_dir=cfg.buffer_dir,
        )
        console.print(f"[green]✓ Raw live clip saved:[/] {raw_path}")
        return

    # Use a unique temporary file inside output_dir for processing
    temp_raw = out_dir / f"_raw_{status.channel}_{uuid4().hex[:6]}.mp4"
    raw_path = clip_from_buffer(
        seconds=seconds,
        output_path=temp_raw,
        buffer_dir=cfg.buffer_dir,
    )

    try:
        layout_mode = Layout.from_string(layout or cfg.default_layout)
        final = process_clip(
            raw_path,
            layout=layout_mode,
            platform=platform,
            output_dir=Path(cfg.output_dir),
            config=cfg,
            channel=status.channel,
        )

        console.print(f"\n[bold green]🎬 Done![/] {final}\n")
    finally:
        raw_path.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# live-stop — stop the live buffer
# ---------------------------------------------------------------------------

@main.command("live-stop")
@click.option("--cleanup", is_flag=True, help="Remove buffer segment files.")
@click.pass_context
def live_stop(ctx: click.Context, cleanup: bool) -> None:
    """Stop the running live buffer.

    \b
    Examples:
      clipmania live-stop
      clipmania live-stop --cleanup
    """
    from .ingestion.streamlink_live import stop_live_buffer

    cfg: ClipManiaConfig = ctx.obj["config"]
    console.print("\n[bold]ClipMania[/] — Stopping live buffer\n")
    stop_live_buffer(buffer_dir=cfg.buffer_dir, cleanup=cleanup)


# ---------------------------------------------------------------------------
# convert — standalone post-processing
# ---------------------------------------------------------------------------

@main.command()
@click.argument("input_file", type=click.Path(exists=True, path_type=Path))
@click.option(
    "--layout", "-l",
    type=click.Choice([l.value for l in Layout], case_sensitive=False),
    required=True,
    help="Layout transform to apply.",
)
@click.option(
    "--platform", "-p",
    type=click.Choice(list_platforms(), case_sensitive=False),
    default=None,
    help="Target platform for spec validation.",
)
@click.option("--single-pass", "single_pass", is_flag=True, help="Use single-pass processing (faster, less precise audio).")
@click.option("--auto-crop", is_flag=True, help="Automatically detect facecam and gameplay regions via computer vision.")
@click.option("--cam-crop", default=None, help="Facecam crop region w:h:x:y (for vertical-stacked).")
@click.option("--game-crop", default=None, help="Gameplay crop region w:h:x:y (for vertical-stacked).")
@click.pass_context
def convert(
    ctx: click.Context,
    input_file: Path,
    layout: str,
    platform: str | None,
    single_pass: bool,
    auto_crop: bool,
    cam_crop: str | None,
    game_crop: str | None,
) -> None:
    """Convert an existing video file for a target platform.

    \b
    Examples:
      clipmania convert raw.mp4 --layout vertical-blur --platform tiktok
      clipmania convert gameplay.mp4 -l vertical-stacked --auto-crop
      clipmania convert gameplay.mp4 -l vertical-stacked --cam-crop 640:480:1280:0
    """
    from .processing.pipeline import process_clip, process_clip_single_pass
    from .utils import validate_crop

    cfg: ClipManiaConfig = ctx.obj["config"]
    layout_mode = Layout.from_string(layout)

    # Auto-detect crops if requested
    if auto_crop:
        from .processing.autocrop import auto_detect_crops
        console.print("[dim]   Auto-detecting facecam and gameplay regions…[/]")
        c_crop, g_crop = auto_detect_crops(input_file)
        cam_crop = c_crop.crop_str
        game_crop = g_crop.crop_str
        console.print(f"[green]✓ Detected crops:[/] cam={cam_crop}, game={game_crop}")

    # Validate crop parameters (C4: prevent FFmpeg filter injection)
    if cam_crop:
        try:
            cam_crop = validate_crop(cam_crop)
        except ValueError as e:
            raise click.ClickException(str(e))
    if game_crop:
        try:
            game_crop = validate_crop(game_crop)
        except ValueError as e:
            raise click.ClickException(str(e))

    console.print(f"\n[bold]ClipMania[/] — Converting [cyan]{input_file.name}[/]\n")

    if single_pass:
        from .utils import safe_filename as sf
        output_path = Path(cfg.output_dir) / sf(platform or "clip", input_file.stem)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        final = process_clip_single_pass(
            input_file, output_path, layout_mode,
            target_lufs=cfg.audio_target_lufs,
            audio_bitrate=cfg.audio_bitrate,
            crf=cfg.crf, preset=cfg.preset,
            config=cfg,
            channel=input_file.stem,
            platform=platform,
            cam_crop=cam_crop,
            game_crop=game_crop,
        )
    else:
        final = process_clip(
            input_file,
            layout=layout_mode,
            platform=platform,
            output_dir=Path(cfg.output_dir),
            config=cfg,
            channel=input_file.stem,
            cam_crop=cam_crop,
            game_crop=game_crop,
        )

    console.print(f"\n[bold green]🎬 Done![/] {final}\n")


# ---------------------------------------------------------------------------
# info — inspect video metadata, qualities, and chapters
# ---------------------------------------------------------------------------

@main.command("info")
@click.argument("url")
def info(url: str) -> None:
    """Inspect video metadata, available qualities, and chapters."""
    from rich.table import Table
    from .ingestion.metadata import fetch_metadata
    from .utils import format_timestamp

    console.print(f"\n[bold]ClipMania[/] — Querying metadata for [cyan]{url}[/]…\n")
    try:
        meta = fetch_metadata(url)
    except Exception as e:
        raise click.ClickException(str(e))

    table = Table(title="Video Details", show_header=False, title_style="bold green")
    table.add_column("Property", style="cyan", width=18)
    table.add_column("Value")
    table.add_row("Title", meta.title)
    table.add_row("Channel / Uploader", meta.uploader)
    table.add_row("Duration", format_timestamp(meta.duration))
    if meta.view_count is not None:
        table.add_row("Views", f"{meta.view_count:,}")
    if meta.upload_date:
        table.add_row("Upload Date", meta.upload_date)
    table.add_row("Live Status", "[red]LIVE[/]" if meta.is_live else "VOD / Video")
    if meta.available_resolutions:
        table.add_row("Qualities", ", ".join(meta.available_resolutions[:8]))
    console.print(table)

    if meta.chapters:
        ch_table = Table(title=f"Chapters ({len(meta.chapters)})", title_style="bold yellow")
        ch_table.add_column("#", style="dim", width=4)
        ch_table.add_column("Chapter Title", style="bold")
        ch_table.add_column("Start", style="green")
        ch_table.add_column("End", style="red")
        ch_table.add_column("Duration", style="cyan")
        for ch in meta.chapters:
            ch_table.add_row(
                str(ch.index),
                ch.title,
                format_timestamp(ch.start_time),
                format_timestamp(ch.end_time),
                f"{ch.duration:.1f}s",
            )
        console.print(ch_table)
        console.print("\n[dim]Tip: Clip directly with: [bold]clipmania clip <URL> --chapter <# or name>[/][/]\n")


# ---------------------------------------------------------------------------
# montage — concatenate multiple clips into a montage
# ---------------------------------------------------------------------------

@main.command("montage")
@click.argument("clips", nargs=-1, type=click.Path(exists=True, path_type=Path), required=True)
@click.option("--output", "-o", type=click.Path(path_type=Path), default=None, help="Output file path.")
@click.option("--copy", is_flag=True, help="Fast stream copy without re-encoding.")
@click.pass_context
def montage(ctx: click.Context, clips: tuple[Path, ...], output: Path | None, copy: bool) -> None:
    """Concatenate multiple clips into a single video montage.

    \b
    Examples:
      clipmania montage clip1.mp4 clip2.mp4 clip3.mp4 -o highlight.mp4
      clipmania montage ./output/*.mp4 --copy
    """
    from .processing.composer import concat_clips

    cfg: ClipManiaConfig = ctx.obj["config"]

    if len(clips) < 2:
        raise click.ClickException("Must specify at least 2 video clips to create a montage.")

    out = output or (Path(cfg.output_dir) / "montage.mp4")
    concat_clips(list(clips), out, reencode=not copy, crf=cfg.crf, preset=cfg.preset)
    console.print(f"\n[bold green]🎬 Montage complete![/] {out}\n")


# ---------------------------------------------------------------------------
# wizard — interactive prompt-driven clipping
# ---------------------------------------------------------------------------

@main.command("wizard")
@click.pass_context
def wizard(ctx: click.Context) -> None:
    """Interactive clipping wizard."""
    from rich.prompt import Confirm, Prompt
    from .ingestion.metadata import fetch_metadata
    from .processing.pipeline import process_clip
    from .ingestion.ytdlp import clip_section
    from .utils import format_timestamp, parse_timestamp

    cfg: ClipManiaConfig = ctx.obj["config"]
    console.print("\n[bold cyan]✨ ClipMania Interactive Wizard ✨[/]\n")

    url = Prompt.ask("[bold]Enter video URL[/]")
    if not url.strip():
        raise click.ClickException("No URL provided.")

    console.print("[dim]Checking video information...[/]")
    meta = None
    try:
        meta = fetch_metadata(url)
        console.print(f"[green]✓ Found:[/] {meta.title} ({format_timestamp(meta.duration)})")
    except Exception:
        console.print("[yellow]Could not fetch online metadata; proceeding with manual timestamps.[/]")

    start = ""
    end = ""
    if meta and meta.chapters:
        use_ch = Confirm.ask(f"Found {len(meta.chapters)} chapters. Pick a chapter?", default=True)
        if use_ch:
            for c in meta.chapters:
                console.print(f"  [cyan]{c.index}.[/] {c.title} ({format_timestamp(c.start_time)} - {format_timestamp(c.end_time)})")
            choice = Prompt.ask("Chapter number or search keyword")
            from .ingestion.metadata import find_chapter
            selected = find_chapter(meta.chapters, choice)
            if selected:
                start = format_timestamp(selected.start_time)
                end = format_timestamp(selected.end_time)
                console.print(f"[green]Selected:[/] {selected.title}")

    if not start:
        start = Prompt.ask("Start timestamp (e.g. 0:30, 1:15)")
    if not end:
        end = Prompt.ask("End timestamp (e.g. 1:00, 2:00)")

    # M-7: Validate timestamps before passing to yt-dlp
    try:
        start_sec = parse_timestamp(start)
        end_sec = parse_timestamp(end)
    except ValueError as e:
        raise click.ClickException(f"Invalid timestamp: {e}")
    if start_sec >= end_sec:
        raise click.ClickException(f"Start ({start}) must be before end ({end}).")

    layout_choices = [l.value for l in Layout]
    layout = Prompt.ask(
        "Choose layout",
        choices=layout_choices,
        default=cfg.default_layout,
    )

    platform = Prompt.ask(
        "Target platform (or 'none')",
        choices=list_platforms() + ["none"],
        default="tiktok",
    )
    if platform == "none":
        platform = None

    channel = extract_channel(url)
    raw_path = Path(cfg.output_dir) / f"_raw_{channel}.mp4"
    console.print(f"\n[bold]Starting extraction:[/] {start} → {end}...")
    actual_raw = clip_section(url, start, end, raw_path)

    try:
        layout_mode = Layout.from_string(layout)
        final = process_clip(
            actual_raw,
            layout=layout_mode,
            platform=platform,
            output_dir=Path(cfg.output_dir),
            config=cfg,
            channel=channel,
        )
        console.print(f"\n[bold green]🎉 All done![/] Saved to: {final}\n")
    finally:
        actual_raw.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# validate — pre-flight / post-flight platform validation
# ---------------------------------------------------------------------------

@main.command("validate")
@click.argument("file", type=click.Path(exists=True, path_type=Path))
@click.option(
    "--platform", "-p",
    type=click.Choice(list_platforms(), case_sensitive=False),
    default=None,
    help="Target platform specification to validate against (auto-detects if omitted).",
)
def validate(file: Path, platform: str | None) -> None:
    """Validate a video file against social platform technical specifications.

    \b
    Examples:
      clipmania validate output/clip.mp4 --platform tiktok
      clipmania validate raw_video.mp4
    """
    from rich.panel import Panel
    from .platforms import get_spec, recommend_platform, validate_output
    from .processing.ffmpeg import probe

    info = probe(file)
    file_size = file.stat().st_size

    if not platform:
        recommendations = recommend_platform(info.width, info.height, info.duration)
        if recommendations:
            rec = recommendations[0]
            console.print(
                f"[bold cyan]Auto-detected recommended platform:[/] [bold]{rec}[/] "
                f"(based on {info.width}×{info.height}, {info.duration:.1f}s)"
            )
            platform = rec
        else:
            raise click.ClickException(
                f"No compatible platform found for {info.width}×{info.height}, "
                f"{info.duration:.1f}s. Specify one with --platform."
            )

    spec = get_spec(platform)
    warnings = validate_output(
        {"duration": info.duration, "width": info.width, "height": info.height},
        spec,
        file_size_bytes=file_size,
    )

    if warnings:
        warn_text = "\n".join(f"  • {w}" for w in warnings)
        console.print(
            Panel(
                f"[bold red]Validation Failed for {spec.name}:[/]\n{warn_text}\n\n"
                f"[dim]Resolution: {info.width}×{info.height} | Duration: {info.duration:.1f}s | "
                f"Size: {file_size / (1024 * 1024):.1f}MB[/]",
                title="⚠ Platform Validation",
                border_style="red",
            )
        )
        raise click.ClickException(f"Video does not comply with {spec.name} specification.")
    else:
        container = file.suffix.lstrip(".") or "unknown"
        console.print(
            Panel(
                f"[bold green]✓ Basic video properties validated for {spec.name}[/]\n"
                f"[dim](Checks: duration, resolution, aspect ratio, file size)[/]\n\n"
                f"  • Resolution: {info.width}×{info.height} ({info.aspect_ratio})\n"
                f"  • Duration: {info.duration:.1f}s (Max: {spec.max_duration_seconds or 'Unlimited'}s)\n"
                f"  • File Size: {file_size / (1024 * 1024):.1f}MB (Max: {spec.max_file_size_mb}MB)\n"
                f"  • Codec: {info.video_codec} / Container: {container}",
                title="✓ Basic Validation Passed",
                border_style="green",
            )
        )


# ---------------------------------------------------------------------------
# batch — process multiple clipping jobs in parallel
# ---------------------------------------------------------------------------

@main.command("batch")
@click.argument("config_file", type=click.Path(exists=True, path_type=Path))
@click.option(
    "--workers", "-w",
    type=click.IntRange(min=1, max=16),
    default=4,
    help="Number of concurrent worker threads (1-16, default: 4).",
)
@click.option(
    "--output-dir", "-o",
    type=click.Path(path_type=Path),
    default=None,
    help="Output directory for batch clips.",
)
@click.pass_context
def batch(ctx: click.Context, config_file: Path, workers: int, output_dir: Path | None) -> None:
    """Process multiple video clipping tasks concurrently from a JSON or TOML file.

    \b
    Examples:
      clipmania batch queue.json --workers 4
      clipmania batch clips.toml -o ./output/batch
    """
    from .processing.batch import load_batch_config, process_batch

    cfg: ClipManiaConfig = ctx.obj["config"]
    jobs = load_batch_config(config_file)
    out = output_dir or Path(cfg.output_dir)

    results = process_batch(jobs, workers=workers, output_dir=out, config=cfg)
    console.print(f"[bold green]Batch processing completed:[/] {len(results)} clips created in {out}\n")


# ---------------------------------------------------------------------------
# watch — continuously monitor a live channel and cut highlights
# ---------------------------------------------------------------------------

@main.command("watch")
@click.argument("url")
@click.option(
    "--check-interval",
    default=30,
    type=int,
    help="Seconds between stream live checks when offline (default: 30).",
)
@click.option(
    "--clip-interval",
    default=300,
    type=int,
    help="Seconds between automatic highlight clips when live (default: 300).",
)
@click.option(
    "--window",
    default=60,
    type=int,
    help="Duration in seconds of clips cut from live buffer (default: 60).",
)
@click.option(
    "--cooldown",
    default=120,
    type=int,
    help="Minimum seconds between generated clips (default: 120).",
)
@click.option(
    "--layout",
    type=click.Choice([l.value for l in Layout]),
    default="vertical-smart",
    help="Layout mode for generated clips (default: vertical-smart).",
)
@click.option(
    "--platform", "-p",
    default="tiktok",
    help="Target platform spec for format and dimensions (default: tiktok).",
)
@click.option(
    "--audio-trigger/--no-audio-trigger",
    default=True,
    help="Automatically cut clips on audio excitement / energy spikes.",
)
@click.option(
    "--fast",
    is_flag=True,
    help="Use fast single-pass encoding for real-time live cutting.",
)
@click.pass_context
def watch(
    ctx: click.Context,
    url: str,
    check_interval: int,
    clip_interval: int,
    window: int,
    cooldown: int,
    layout: str,
    platform: str,
    audio_trigger: bool,
    fast: bool,
) -> None:
    """Continuously monitor a creator channel (YouTube, Twitch, Kick, Rumble).

    Automatically starts rolling buffering and cuts vertical highlights when they go live.

    \b
    Examples:
      clipmania watch https://twitch.tv/shroud
      clipmania watch https://kick.com/xqc --layout vertical-smart --window 45
      clipmania watch https://rumble.com/c/Bongino --clip-interval 600
    """
    if check_interval <= 0:
        raise click.UsageError("--check-interval must be positive.")
    if clip_interval <= 0:
        raise click.UsageError("--clip-interval must be positive.")
    if window <= 0:
        raise click.UsageError("--window must be positive.")
    if cooldown < 0:
        raise click.UsageError("--cooldown must be positive or zero.")

    from .ingestion.live_watcher import LiveWatcher
    from .processing.live_cutter import LiveCutterConfig

    from .ingestion.streamlink_live import get_default_buffer_dir
    from .utils import extract_channel

    cfg: ClipManiaConfig = ctx.obj["config"]
    out_dir = Path(cfg.output_dir)

    # Each watch gets its own buffer root so several channels can be watched
    # at once without sharing one buffer state file / lock. The root dir stays
    # reserved for the manual live-start / clip-now / live-stop commands.
    buffer_root = Path(cfg.buffer_dir) if cfg.buffer_dir else get_default_buffer_dir()
    watch_buffer_dir = buffer_root / "watch" / extract_channel(url)

    cutter_cfg = LiveCutterConfig(
        clip_window_seconds=window,
        interval_seconds=clip_interval,
        enable_audio_trigger=audio_trigger,
        cooldown_seconds=cooldown,
        layout=Layout(layout),
        platform=platform,
        fast_single_pass=fast,
    )

    watcher = LiveWatcher(
        url=url,
        check_interval=check_interval,
        buffer_dir=watch_buffer_dir,
        output_dir=out_dir,
        cutter_config=cutter_cfg,
        segment_duration=cfg.segment_duration,
        playlist_size=cfg.playlist_size,
        quality=cfg.quality,
    )

    watcher.run()



