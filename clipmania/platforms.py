"""Platform target specifications for video export and validation."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path


class AspectRatio(Enum):
    """Supported video aspect ratios."""

    LANDSCAPE = "16:9"
    PORTRAIT = "9:16"
    SQUARE = "1:1"


@dataclass(frozen=True)
class PlatformSpec:
    """Export specification for a target platform."""

    name: str
    aspect_ratios: tuple[AspectRatio, ...]
    width: int
    height: int
    max_duration_seconds: int | None  # None → unlimited
    codec: str
    container: str
    audio_lufs: float
    audio_bitrate: str
    notes: str
    min_width: int = 480
    max_file_size_mb: int = 287
    recommended_bitrate: str = "192k"


# ---------------------------------------------------------------------------
# Platform registry
# ---------------------------------------------------------------------------

PLATFORM_SPECS: dict[str, PlatformSpec] = {
    "tiktok": PlatformSpec(
        name="TikTok",
        aspect_ratios=(AspectRatio.PORTRAIT,),
        width=1080,
        height=1920,
        max_duration_seconds=60,
        codec="libx264",
        container="mp4",
        audio_lufs=-14.0,
        audio_bitrate="192k",
        notes="Optimal under 60s, 9:16 vertical required",
        min_width=720,
        max_file_size_mb=287,
    ),
    "youtube_shorts": PlatformSpec(
        name="YouTube Shorts",
        aspect_ratios=(AspectRatio.PORTRAIT, AspectRatio.SQUARE),
        width=1080,
        height=1920,
        max_duration_seconds=60,
        codec="libx264",
        container="mp4",
        audio_lufs=-14.0,
        audio_bitrate="192k",
        notes="Strictly under 60 seconds",
        min_width=720,
        max_file_size_mb=500,
    ),
    "kick": PlatformSpec(
        name="Kick Clips",
        aspect_ratios=(AspectRatio.LANDSCAPE, AspectRatio.PORTRAIT),
        width=1920,
        height=1080,
        max_duration_seconds=60,
        codec="libx264",
        container="mp4",
        audio_lufs=-14.0,
        audio_bitrate="192k",
        notes="Supports horizontal and vertical mobile feed",
        min_width=720,
        max_file_size_mb=250,
    ),
    "twitch": PlatformSpec(
        name="Twitch Highlights",
        aspect_ratios=(AspectRatio.LANDSCAPE,),
        width=1920,
        height=1080,
        max_duration_seconds=None,
        codec="libx264",
        container="mp4",
        audio_lufs=-14.0,
        audio_bitrate="192k",
        notes="Standard broadcast resolution, clips ≤60s",
        min_width=1280,
        max_file_size_mb=1000,
    ),
    "x": PlatformSpec(
        name="X (Twitter)",
        aspect_ratios=(AspectRatio.LANDSCAPE, AspectRatio.PORTRAIT),
        width=1920,
        height=1080,
        max_duration_seconds=140,
        codec="libx264",
        container="mp4",
        audio_lufs=-14.0,
        audio_bitrate="192k",
        notes="High mobile consumption in 9:16",
        min_width=480,
        max_file_size_mb=512,
    ),
}


def get_spec(platform: str) -> PlatformSpec:
    """Look up a platform specification by key.

    Raises :class:`ValueError` if the platform is unknown.
    """
    key = platform.lower().replace(" ", "_").replace("-", "_")
    if key not in PLATFORM_SPECS:
        valid = ", ".join(sorted(PLATFORM_SPECS))
        raise ValueError(f"Unknown platform {platform!r}. Choose from: {valid}")
    return PLATFORM_SPECS[key]


def validate_output(
    video_info: dict[str, float | int],
    spec: PlatformSpec,
    file_size_bytes: int | None = None,
) -> list[str]:
    """Validate video properties against a platform spec.

    *video_info* should contain ``duration``, ``width``, and ``height`` keys.
    Returns a list of warning strings (empty means all checks passed).
    """
    warnings: list[str] = []
    duration = float(video_info.get("duration", 0.0))
    width = int(video_info.get("width", 0))
    height = int(video_info.get("height", 0))

    if spec.max_duration_seconds is not None and duration > spec.max_duration_seconds:
        warnings.append(
            f"Duration {duration:.1f}s exceeds {spec.name} limit of "
            f"{spec.max_duration_seconds}s"
        )

    if width > 0 and width < spec.min_width:
        warnings.append(
            f"Width {width}px is below {spec.name} minimum requirement of {spec.min_width}px"
        )

    if file_size_bytes is not None:
        mb = file_size_bytes / (1024 * 1024)
        if mb > spec.max_file_size_mb:
            warnings.append(
                f"File size {mb:.1f}MB exceeds {spec.name} limit of {spec.max_file_size_mb}MB"
            )

    # Check if resolution matches any supported aspect ratio
    if width and height:
        actual_ratio = width / height
        landscape_ratio = 16 / 9
        portrait_ratio = 9 / 16
        square_ratio = 1.0

        ratio_ok = False
        for ar in spec.aspect_ratios:
            expected = {
                AspectRatio.LANDSCAPE: landscape_ratio,
                AspectRatio.PORTRAIT: portrait_ratio,
                AspectRatio.SQUARE: square_ratio,
            }[ar]
            if abs(actual_ratio - expected) < 0.1:
                ratio_ok = True
                break
        if not ratio_ok:
            expected_strs = [ar.value for ar in spec.aspect_ratios]
            warnings.append(
                f"Aspect ratio {width}x{height} does not match "
                f"{spec.name} requirement ({', '.join(expected_strs)})"
            )

    return warnings


def recommend_platform(width: int, height: int, duration: float) -> list[str]:
    """Return compatible platforms ranked by fit, using the specs registry.

    Checks aspect ratio and duration limits against every registered platform.
    Returns an empty list when no platform matches.
    """
    if width <= 0 or height <= 0:
        return []

    actual_ratio = width / height
    _RATIO_MAP = {
        AspectRatio.LANDSCAPE: 16 / 9,
        AspectRatio.PORTRAIT: 9 / 16,
        AspectRatio.SQUARE: 1.0,
    }
    TOLERANCE = 0.1

    matches: list[str] = []
    for key, spec in PLATFORM_SPECS.items():
        # Duration gate
        if spec.max_duration_seconds is not None and duration > spec.max_duration_seconds:
            continue
        # Aspect ratio gate
        ratio_ok = any(
            abs(actual_ratio - _RATIO_MAP[ar]) < TOLERANCE
            for ar in spec.aspect_ratios
        )
        if not ratio_ok:
            continue
        matches.append(key)

    return matches


def list_platforms() -> list[str]:
    """Return sorted list of platform keys."""
    return sorted(PLATFORM_SPECS.keys())
