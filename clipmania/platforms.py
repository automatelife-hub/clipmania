"""Platform target specifications for video export."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


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
) -> list[str]:
    """Validate video properties against a platform spec.

    *video_info* should contain ``duration``, ``width``, and ``height`` keys.
    Returns a list of warning strings (empty means all checks passed).
    """
    warnings: list[str] = []
    duration = video_info.get("duration", 0.0)
    width = int(video_info.get("width", 0))
    height = int(video_info.get("height", 0))

    if spec.max_duration_seconds is not None and duration > spec.max_duration_seconds:
        warnings.append(
            f"Duration {duration:.1f}s exceeds {spec.name} limit of "
            f"{spec.max_duration_seconds}s"
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


def list_platforms() -> list[str]:
    """Return sorted list of platform keys."""
    return sorted(PLATFORM_SPECS.keys())
