"""Shared utilities: timestamp parsing, URL detection, file naming."""

from __future__ import annotations

import re
from datetime import datetime
from enum import Enum
from pathlib import Path
from urllib.parse import urlparse


class Platform(Enum):
    """Supported video platforms."""

    TWITCH = "twitch"
    YOUTUBE = "youtube"
    KICK = "kick"
    TIKTOK = "tiktok"
    X = "x"
    UNKNOWN = "unknown"


# ---------------------------------------------------------------------------
# Timestamp helpers
# ---------------------------------------------------------------------------

_HMS_RE = re.compile(
    r"(?:(?P<h>\d+)\s*h)?\s*"
    r"(?:(?P<m>\d+)\s*m)?\s*"
    r"(?:(?P<s>\d+(?:\.\d+)?)\s*s)?",
    re.IGNORECASE,
)


def parse_timestamp(ts: str) -> float:
    """Parse a timestamp string to total seconds.

    Supported formats:
      - Plain seconds:   ``"90"`` → 90.0
      - MM:SS:           ``"1:30"`` → 90.0
      - HH:MM:SS:        ``"1:02:30"`` → 3750.0
      - Human-readable:  ``"1h2m3s"``, ``"1m30s"``, ``"30s"``
    """
    ts = ts.strip()

    # Try colon-separated first  (SS | MM:SS | HH:MM:SS)
    if ":" in ts:
        parts = ts.split(":")
        if len(parts) == 2:
            m, s = parts
            return int(m) * 60 + float(s)
        if len(parts) == 3:
            h, m, s = parts
            return int(h) * 3600 + int(m) * 60 + float(s)
        raise ValueError(f"Invalid timestamp format: {ts!r}")

    # Try human-readable  (1h2m3s, 5m, 30s, etc.)
    match = _HMS_RE.fullmatch(ts)
    if match and any(match.group(g) for g in ("h", "m", "s")):
        h = int(match.group("h") or 0)
        m = int(match.group("m") or 0)
        s = float(match.group("s") or 0)
        return h * 3600 + m * 60 + s

    # Try bare number (seconds)
    try:
        return float(ts)
    except ValueError:
        pass

    raise ValueError(f"Cannot parse timestamp: {ts!r}")


def format_timestamp(seconds: float) -> str:
    """Format seconds to ``HH:MM:SS.mmm`` string."""
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    if h > 0:
        return f"{h}:{m:02d}:{s:06.3f}"
    return f"{m}:{s:06.3f}"


# ---------------------------------------------------------------------------
# URL / platform detection
# ---------------------------------------------------------------------------

_PLATFORM_DOMAINS: dict[str, Platform] = {
    "twitch.tv": Platform.TWITCH,
    "www.twitch.tv": Platform.TWITCH,
    "youtube.com": Platform.YOUTUBE,
    "www.youtube.com": Platform.YOUTUBE,
    "youtu.be": Platform.YOUTUBE,
    "m.youtube.com": Platform.YOUTUBE,
    "kick.com": Platform.KICK,
    "www.kick.com": Platform.KICK,
    "tiktok.com": Platform.TIKTOK,
    "www.tiktok.com": Platform.TIKTOK,
    "vm.tiktok.com": Platform.TIKTOK,
    "twitter.com": Platform.X,
    "www.twitter.com": Platform.X,
    "x.com": Platform.X,
    "www.x.com": Platform.X,
}


def detect_platform(url: str) -> Platform:
    """Detect the platform from a URL's domain."""
    parsed = urlparse(url)
    host = parsed.hostname or ""
    return _PLATFORM_DOMAINS.get(host, Platform.UNKNOWN)


def extract_channel(url: str) -> str:
    """Best-effort extraction of channel/user name from a URL."""
    parsed = urlparse(url)
    parts = [p for p in (parsed.path or "").split("/") if p]
    if not parts:
        return "unknown"
    platform = detect_platform(url)
    if platform == Platform.YOUTUBE:
        # /@channel → channel; /c/channel → channel
        if parts[0].startswith("@"):
            return parts[0].lstrip("@")
        if parts[0] in ("c", "channel", "user") and len(parts) > 1:
            return parts[1]
        # /watch?v=xxx → video ID from query params
        if parts[0] == "watch":
            qs = dict(p.split("=", 1) for p in (parsed.query or "").split("&") if "=" in p)
            return qs.get("v", "unknown")
        # /shorts/<id>, /embed/<id>, /live/<id> → video ID from path
        if parts[0] in ("shorts", "embed", "live") and len(parts) > 1:
            return parts[1]
        return parts[0]
    # Twitch, Kick, TikTok, X: first path segment is typically the user
    return parts[0].lstrip("@")


# Segments that indicate a VOD/saved video rather than a live channel page.
_VOD_SEGMENTS = frozenset({
    "video", "videos", "watch", "clip", "clips", "v", "status", "shorts", "embed",
})


def is_live_url(url: str) -> bool:
    """Heuristic: a URL points to a live channel page (no /video, /watch, /clip path).

    Uses segment-level matching to avoid false positives on channel names
    that happen to contain VOD keywords (e.g. ``videogamedunkey``).
    """
    parsed = urlparse(url)
    segments = [s.lower() for s in (parsed.path or "").split("/") if s]
    return not any(seg in _VOD_SEGMENTS for seg in segments)


# ---------------------------------------------------------------------------
# File naming
# ---------------------------------------------------------------------------

_UNSAFE_RE = re.compile(r"[^\w\-.]")


def safe_filename(
    platform: Platform | str,
    channel: str = "clip",
    ext: str = ".mp4",
) -> str:
    """Generate a timestamped filename: ``{platform}_{channel}_{YYYYMMDD_HHmmss}.mp4``."""
    plat = platform.value if isinstance(platform, Platform) else str(platform)
    clean_channel = _UNSAFE_RE.sub("_", channel)[:40]
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"{plat}_{clean_channel}_{ts}{ext}"


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------

_CROP_RE = re.compile(r"^\d+:\d+:\d+:\d+$")


def validate_crop(value: str) -> str:
    """Validate an FFmpeg crop parameter string (``w:h:x:y``).

    Raises :class:`ValueError` if the format is invalid, preventing
    injection of arbitrary FFmpeg filter nodes.
    """
    if not _CROP_RE.fullmatch(value.strip()):
        raise ValueError(
            f"Invalid crop format: {value!r}. Expected w:h:x:y (e.g. '640:480:1280:0')"
        )
    return value.strip()


# ---------------------------------------------------------------------------
# Directory helpers
# ---------------------------------------------------------------------------


def ensure_dir(path: Path) -> Path:
    """Create directory (and parents) if it doesn't exist, then return it."""
    path.mkdir(parents=True, exist_ok=True)
    return path
