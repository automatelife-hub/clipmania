"""End-to-end processing pipeline orchestrator."""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from ..config import ClipManiaConfig, load_config
from ..platforms import PLATFORM_SPECS, PlatformSpec, get_spec, validate_output
from ..utils import Platform, ensure_dir, safe_filename
from .audio import build_loudnorm_filter, normalize_audio
from .ffmpeg import VideoInfo, probe, run_ffmpeg_with_progress
from .layouts import Layout, apply_layout, build_filter_for_layout

console = Console()


def process_clip(
    input_path: Path,
    layout: Layout,
    platform: str | None = None,
    output_dir: Path | None = None,
    config: ClipManiaConfig | None = None,
    channel: str = "clip",
    *,
    cam_crop: str | None = None,
    game_crop: str | None = None,
) -> Path:
    """Run the full processing pipeline on a raw clip.

    Steps:
      1. Probe input video
      2. Apply layout transform (lossless audio stream copy)
      3. Apply two-pass audio normalization (encodes to AAC)
      4. Validate against platform spec (if provided)
      5. Move to output directory with structured filename

    Returns the path to the final processed file.
    """
    cfg = config or load_config()
    out_dir = output_dir or cfg.output_dir
    ensure_dir(out_dir)

    info = probe(input_path)
    _print_input_summary(info)

    # Resolve platform spec
    spec: PlatformSpec | None = None
    if platform:
        spec = get_spec(platform)

    # Decide platform string for filename
    plat_str = platform or "clip"

    # Use a temp directory for intermediate files
    with tempfile.TemporaryDirectory(prefix="clipmania_") as tmp_dir_str:
        tmp_dir = Path(tmp_dir_str)

        layout_kwargs: dict[str, object] = {}
        if cam_crop:
            layout_kwargs["cam_crop"] = cam_crop
        if game_crop:
            layout_kwargs["game_crop"] = game_crop

        # Step 1: Layout transform
        # Q1: If video has audio, copy audio stream into an MKV intermediate container
        # to prevent lossy double-encoding before normalization.
        if info.audio_codec:
            layout_out = tmp_dir / "layout.mkv"
            apply_layout(
                input_path, layout_out, layout,
                crf=cfg.crf, preset=cfg.preset,
                audio_codec="copy",
                **layout_kwargs,  # type: ignore[arg-type]
            )

            # Step 2: Audio normalization (two-pass)
            audio_out = tmp_dir / "audio.mp4"
            normalize_audio(
                layout_out, audio_out,
                target_lufs=cfg.audio_target_lufs,
                lra=cfg.audio_lra,
                true_peak=cfg.audio_true_peak,
                audio_bitrate=cfg.audio_bitrate,
                copy_video=True,
            )
        else:
            # M3: Silent video — skip audio normalization completely
            console.print("[dim]   No audio stream detected — skipping normalization.[/]")
            layout_out = tmp_dir / "layout.mp4"
            apply_layout(
                input_path, layout_out, layout,
                crf=cfg.crf, preset=cfg.preset,
                **layout_kwargs,  # type: ignore[arg-type]
            )
            audio_out = layout_out

        # Step 3: Validate against platform spec (M-5: include file_size_bytes)
        if spec:
            final_info = probe(audio_out)
            file_size = audio_out.stat().st_size
            warnings = validate_output(
                {"duration": final_info.duration, "width": final_info.width, "height": final_info.height},
                spec,
                file_size_bytes=file_size,
            )
            if warnings:
                console.print("[yellow]⚠ Platform validation warnings:[/]")
                for w in warnings:
                    console.print(f"   • {w}")
            else:
                console.print(f"[green]✓ Passes {spec.name} spec validation[/]")

        # Step 4: Move to final output location
        filename = safe_filename(plat_str, channel)
        final_path = out_dir / filename
        shutil.move(str(audio_out), str(final_path))

        _print_output_summary(final_path, probe(final_path))
        return final_path


def process_clip_single_pass(
    input_path: Path,
    output_path: Path,
    layout: Layout,
    target_lufs: float = -14.0,
    audio_bitrate: str = "192k",
    crf: int = 20,
    preset: str = "fast",
    config: ClipManiaConfig | None = None,
    channel: str = "clip",
    platform: str | None = None,
    *,
    cam_crop: str | None = None,
    game_crop: str | None = None,
) -> Path:
    """Combined layout + audio normalization in a single FFmpeg invocation.

    Only works for layouts that re-encode video. Uses single-pass ``loudnorm``
    (slightly less precise than two-pass, but halves the processing time).
    """
    info = probe(input_path)

    console.print(f"[bold blue]⚡ Single-pass processing:[/] {layout.value} + audio normalization")

    # Only vertical blur and vertical crop are suitable for simple single-pass
    if layout not in (Layout.VERTICAL_BLUR, Layout.VERTICAL_CROP):
        console.print("[dim]   Layout doesn't support single-pass; falling back to two-step pipeline.[/]")
        return process_clip(
            input_path,
            layout=layout,
            platform=platform,
            output_dir=output_path.parent,
            config=config,
            channel=channel,
            cam_crop=cam_crop,
            game_crop=game_crop,
        )

    # Reuse shared filter string definitions
    filter_str, is_complex = build_filter_for_layout(layout)
    loudnorm = build_loudnorm_filter(target_lufs=target_lufs)

    # M3: Guard against silent videos in single-pass mode
    has_audio = info.audio_codec is not None

    output_path.parent.mkdir(parents=True, exist_ok=True)

    args: list[str] = ["-i", str(input_path)]

    if is_complex:
        args += ["-filter_complex", filter_str]
    else:
        args += ["-vf", filter_str]

    if has_audio:
        args += ["-af", loudnorm]

    args += ["-c:v", "libx264", "-preset", preset, "-crf", str(crf)]

    if has_audio:
        args += ["-c:a", "aac", "-b:a", audio_bitrate]
    else:
        args += ["-an"]

    tmp_output = output_path.with_name(f".tmp_{output_path.name}")
    args += [str(tmp_output)]

    try:
        run_ffmpeg_with_progress(args, duration=info.duration, description="Single-pass processing")
        tmp_output.replace(output_path)
    finally:
        tmp_output.unlink(missing_ok=True)

    console.print(f"[green]✓[/] Single-pass output → {output_path}")
    return output_path


# ---------------------------------------------------------------------------
# Pretty printing
# ---------------------------------------------------------------------------

def _print_input_summary(info: VideoInfo) -> None:
    table = Table(title="Input Video", show_header=False, title_style="bold")
    table.add_column("Property", style="cyan")
    table.add_column("Value")
    table.add_row("Resolution", f"{info.width}×{info.height} ({info.aspect_ratio})")
    table.add_row("Duration", f"{info.duration:.1f}s")
    table.add_row("Video Codec", info.video_codec)
    table.add_row("Audio Codec", info.audio_codec or "None")
    table.add_row("FPS", f"{info.fps:.2f}")
    if info.bitrate:
        table.add_row("Bitrate", f"{info.bitrate / 1000:.0f} kbps")
    console.print(table)


def _print_output_summary(path: Path, info: VideoInfo) -> None:
    size_mb = path.stat().st_size / (1024 * 1024)
    console.print(Panel(
        f"[bold green]✓ Output ready:[/] {path}\n"
        f"  Resolution: {info.width}×{info.height}  |  Duration: {info.duration:.1f}s\n"
        f"  Size: {size_mb:.1f} MB  |  Codec: {info.video_codec}",
        title="🎬 Done",
        border_style="green",
    ))
