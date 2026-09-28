"""ClipMania CLI — multi-platform video clipping pipeline."""

from __future__ import annotations

from pathlib import Path

import click
from rich.console import Console

from . import __version__
from .config import ClipManiaConfig, load_config
from .deps import check_all_dependencies
from .platforms import PLATFORM_SPECS, get_spec, list_platforms
from .processing.layouts import Layout
from .utils import Platform, detect_platform, extract_channel, is_live_url

console = Console()


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
    ctx.ensure_object(dict)

    overrides = {}
    if output_dir:
        overrides["output_dir"] = str(output_dir)

    cfg = load_config(config_path=config_path, overrides=overrides or None)
    ctx.obj["config"] = cfg
    ctx.obj["verbose"] = verbose

    check_all_dependencies(verbose=verbose)


# ---------------------------------------------------------------------------
# clip — VOD / saved video section clipping
# ---------------------------------------------------------------------------

@main.command()
@click.argument("url")
@click.option("--start", "-s", required=True, help="Start timestamp (e.g. 1:30, 1h2m3s).")
@click.option("--end", "-e", required=True, help="End timestamp.")
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
    start: str,
    end: str,
    layout: str | None,
    platform: str | None,
    raw: bool,
) -> None:
    """Clip a section from a VOD or saved video.

    \b
    Examples:
      clipmania clip "https://youtube.com/watch?v=..." --start 1:30 --end 2:00
      clipmania clip "https://kick.com/video/..." -s 12:30 -e 13:15 --layout vertical-blur
    """
    from .ingestion.ytdlp import clip_section
    from .processing.pipeline import process_clip
    from .utils import parse_timestamp

    cfg: ClipManiaConfig = ctx.obj["config"]
    channel = extract_channel(url)

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
    actual_raw = clip_section(url, start, end, raw_path)

    try:
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
        # Clean up raw file (M8: inside try/finally)
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

    state = start_live_buffer(
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

    raw_path = clip_from_buffer(
        seconds=seconds,
        buffer_dir=cfg.buffer_dir,
    )

    if raw:
        console.print(f"[green]✓ Raw live clip saved:[/] {raw_path}")
        return

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
@click.option("--cam-crop", default=None, help="Facecam crop region w:h:x:y (for vertical-stacked).")
@click.option("--game-crop", default=None, help="Gameplay crop region w:h:x:y (for vertical-stacked).")
@click.pass_context
def convert(
    ctx: click.Context,
    input_file: Path,
    layout: str,
    platform: str | None,
    single_pass: bool,
    cam_crop: str | None,
    game_crop: str | None,
) -> None:
    """Convert an existing video file for a target platform.

    \b
    Examples:
      clipmania convert raw.mp4 --layout vertical-blur --platform tiktok
      clipmania convert gameplay.mp4 -l vertical-stacked --cam-crop 640:480:1280:0
    """
    from .processing.pipeline import process_clip, process_clip_single_pass
    from .utils import validate_crop

    cfg: ClipManiaConfig = ctx.obj["config"]
    layout_mode = Layout.from_string(layout)

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
