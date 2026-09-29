"""Video layout transformations for platform-specific aspect ratios."""

from __future__ import annotations

from enum import Enum
from pathlib import Path

from rich.console import Console

from .ffmpeg import probe, run_ffmpeg_with_progress

console = Console()


class Layout(Enum):
    """Available layout modes."""

    HORIZONTAL = "horizontal"
    VERTICAL_BLUR = "vertical-blur"
    VERTICAL_STACKED = "vertical-stacked"
    VERTICAL_CROP = "vertical-crop"
    VERTICAL_SMART = "vertical-smart"

    @classmethod
    def from_string(cls, value: str) -> Layout:
        """Parse a layout name, accepting hyphens and underscores."""
        normalised = value.lower().replace("_", "-")
        for member in cls:
            if member.value == normalised:
                return member
        valid = ", ".join(m.value for m in cls)
        raise ValueError(f"Unknown layout {value!r}. Choose from: {valid}")


def build_filter_for_layout(
    layout: Layout,
    *,
    cam_crop: str = "640:480:1280:0",
    game_crop: str = "1920:1080:0:0",
    cam_height: int = 720,
    game_height: int = 1200,
) -> tuple[str, bool]:
    """Return ``(filter_str, is_complex)`` for the specified layout mode.

    Shared between :func:`apply_layout` and :func:`process_clip_single_pass`.
    """
    if layout == Layout.VERTICAL_BLUR:
        filter_str = (
            "[0:v]scale=1080:1920:force_original_aspect_ratio=increase,"
            "crop=1080:1920,boxblur=20:5[bg];"
            "[0:v]scale=1080:-2[fg];"
            "[bg][fg]overlay=(W-w)/2:(H-h)/2"
        )
        return filter_str, True

    if layout == Layout.VERTICAL_CROP:
        filter_str = (
            "crop=ih*9/16:ih:(iw-ih*9/16)/2:0,"
            "scale=1080:1920"
        )
        return filter_str, False

    if layout == Layout.VERTICAL_STACKED:
        from ..utils import validate_crop
        validate_crop(cam_crop)
        validate_crop(game_crop)
        filter_str = (
            f"[0:v]crop={cam_crop},scale=1080:{cam_height}[cam];"
            f"[0:v]crop={game_crop},scale=1080:{game_height}[game];"
            "[cam][game]vstack"
        )
        return filter_str, True

    if layout in (Layout.HORIZONTAL,):
        filter_str = (
            "scale=1920:1080:force_original_aspect_ratio=decrease,"
            "pad=1920:1080:(ow-iw)/2:(oh-ih)/2"
        )
        return filter_str, False

    if layout == Layout.VERTICAL_SMART:
        # Default baseline filter for smart layout (blur fallback)
        return build_filter_for_layout(Layout.VERTICAL_BLUR)

    raise ValueError(f"Unsupported layout: {layout}")


def apply_layout(
    input_path: Path,
    output_path: Path,
    layout: Layout,
    crf: int = 20,
    preset: str = "fast",
    audio_codec: str = "copy",
    audio_bitrate: str = "192k",
    *,
    cam_crop: str = "640:480:1280:0",
    game_crop: str = "1920:1080:0:0",
    cam_height: int = 720,
    game_height: int = 1200,
) -> Path:
    """Apply a layout transformation and return *output_path*.

    Parameters
    ----------
    cam_crop, game_crop:
        FFmpeg crop parameters ``w:h:x:y`` for ``VERTICAL_STACKED``.
    cam_height, game_height:
        Scaled heights for cam and game panels in ``VERTICAL_STACKED``.
    audio_codec:
        Audio codec for encoding (default ``"copy"`` to avoid lossy double-encoding).
    audio_bitrate:
        Audio bitrate when *audio_codec* is not ``"copy"``.
    """
    info = probe(input_path)

    console.print(f"[bold blue]🖼  Layout:[/] {layout.value}  "
                  f"({info.width}×{info.height} → target)")

    if layout == Layout.VERTICAL_SMART:
        from .smart_crop import detect_facecam
        console.print("[dim]   Scanning video frames for streamer facecam…[/]")
        detection = detect_facecam(input_path)
        if detection:
            console.print(
                f"[bold green]🎯 Smart detection:[/] Found webcam in {detection.position} "
                f"({detection.cam_crop}, confidence={detection.confidence * 100:.0f}%)"
            )
            args = _build_vertical_stacked(
                input_path, output_path, crf, preset,
                audio_codec=audio_codec, audio_bitrate=audio_bitrate,
                cam_crop=detection.cam_crop, game_crop=detection.game_crop,
                cam_height=cam_height, game_height=game_height,
            )
        else:
            console.print("[dim]   No webcam detected — falling back to vertical-blur layout.[/]")
            args = _build_vertical_blur(
                input_path, output_path, crf, preset,
                audio_codec=audio_codec, audio_bitrate=audio_bitrate,
            )
    elif layout == Layout.VERTICAL_STACKED:
        args = _build_vertical_stacked(
            input_path, output_path, crf, preset,
            audio_codec=audio_codec, audio_bitrate=audio_bitrate,
            cam_crop=cam_crop, game_crop=game_crop,
            cam_height=cam_height, game_height=game_height,
        )
    elif layout == Layout.HORIZONTAL:
        args = _build_horizontal(
            input_path, output_path, crf, preset,
            audio_codec=audio_codec, audio_bitrate=audio_bitrate,
        )
    elif layout == Layout.VERTICAL_BLUR:
        args = _build_vertical_blur(
            input_path, output_path, crf, preset,
            audio_codec=audio_codec, audio_bitrate=audio_bitrate,
        )
    elif layout == Layout.VERTICAL_CROP:
        args = _build_vertical_crop(
            input_path, output_path, crf, preset,
            audio_codec=audio_codec, audio_bitrate=audio_bitrate,
        )
    else:
        raise ValueError(f"Unknown layout: {layout}")

    run_ffmpeg_with_progress(args, duration=info.duration, description=f"Applying {layout.value}")
    console.print(f"[green]✓[/] Layout applied → {output_path}")
    return output_path


# ---------------------------------------------------------------------------
# Layout builders — each returns an FFmpeg arg list (without ffmpeg/-y/-hide_banner)
# ---------------------------------------------------------------------------

def _audio_args(codec: str, bitrate: str) -> list[str]:
    if codec == "copy":
        return ["-c:a", "copy"]
    return ["-c:a", codec, "-b:a", bitrate]


def _build_horizontal(
    input_path: Path,
    output_path: Path,
    crf: int,
    preset: str,
    audio_codec: str = "copy",
    audio_bitrate: str = "192k",
    **_kwargs: object,
) -> list[str]:
    """Horizontal: scale to 1920×1080 if needed, otherwise stream-copy."""
    info = probe(input_path)
    if info.width == 1920 and info.height == 1080 and info.video_codec == "h264":
        # Already correct — stream copy
        return [
            "-i", str(input_path),
            "-c", "copy",
            str(output_path),
        ]

    filter_str, _ = build_filter_for_layout(Layout.HORIZONTAL)
    return [
        "-i", str(input_path),
        "-vf", filter_str,
        "-c:v", "libx264", "-preset", preset, "-crf", str(crf),
        *_audio_args(audio_codec, audio_bitrate),
        str(output_path),
    ]


def _build_vertical_blur(
    input_path: Path,
    output_path: Path,
    crf: int,
    preset: str,
    audio_codec: str = "copy",
    audio_bitrate: str = "192k",
    **_kwargs: object,
) -> list[str]:
    """Blurred background fill — centres the video on a 1080×1920 canvas."""
    filter_complex, _ = build_filter_for_layout(Layout.VERTICAL_BLUR)
    return [
        "-i", str(input_path),
        "-filter_complex", filter_complex,
        "-c:v", "libx264", "-preset", preset, "-crf", str(crf),
        *_audio_args(audio_codec, audio_bitrate),
        str(output_path),
    ]


def _build_vertical_stacked(
    input_path: Path,
    output_path: Path,
    crf: int,
    preset: str,
    audio_codec: str = "copy",
    audio_bitrate: str = "192k",
    *,
    cam_crop: str = "640:480:1280:0",
    game_crop: str = "1920:1080:0:0",
    cam_height: int = 720,
    game_height: int = 1200,
    **_kwargs: object,
) -> list[str]:
    """Facecam (top) + gameplay (bottom) vertical stack."""
    filter_complex, _ = build_filter_for_layout(
        Layout.VERTICAL_STACKED,
        cam_crop=cam_crop,
        game_crop=game_crop,
        cam_height=cam_height,
        game_height=game_height,
    )
    return [
        "-i", str(input_path),
        "-filter_complex", filter_complex,
        "-c:v", "libx264", "-preset", preset, "-crf", str(crf),
        *_audio_args(audio_codec, audio_bitrate),
        str(output_path),
    ]


def _build_vertical_crop(
    input_path: Path,
    output_path: Path,
    crf: int,
    preset: str,
    audio_codec: str = "copy",
    audio_bitrate: str = "192k",
    **_kwargs: object,
) -> list[str]:
    """Simple centre crop to 9:16, then scale to 1080×1920."""
    filter_str, _ = build_filter_for_layout(Layout.VERTICAL_CROP)
    return [
        "-i", str(input_path),
        "-vf", filter_str,
        "-c:v", "libx264", "-preset", preset, "-crf", str(crf),
        *_audio_args(audio_codec, audio_bitrate),
        str(output_path),
    ]


def get_layout_filter(layout: Layout) -> str:
    """Return a human-readable description of the filter for a layout."""
    descriptions = {
        Layout.HORIZONTAL: "Scale/pad to 1920×1080 landscape",
        Layout.VERTICAL_BLUR: "Blurred background fill, 1080×1920 portrait",
        Layout.VERTICAL_STACKED: "Facecam + gameplay vertical stack",
        Layout.VERTICAL_CROP: "Centre crop to 9:16 portrait",
        Layout.VERTICAL_SMART: "AI facecam detection with stacked layout (or blur fallback)",
    }
    return descriptions.get(layout, layout.value)
