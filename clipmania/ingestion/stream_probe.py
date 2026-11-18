"""Stream status prober using streamlink --json."""

from __future__ import annotations

import json
import logging
import subprocess
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class StreamStatus:
    """Metadata describing the live status of a channel URL."""

    is_live: bool
    url: str
    title: str | None = None
    author: str | None = None
    category: str | None = None
    qualities: tuple[str, ...] = ()
    error: str | None = None


def probe_stream_status(url: str, timeout: int = 15) -> StreamStatus:
    """Probe whether a live stream is active at `url` using `streamlink --json`.

    Args:
        url: Creator or stream URL to inspect.
        timeout: Maximum seconds to wait for streamlink probe.

    Returns:
        StreamStatus containing live state, metadata, and available qualities.
    """
    cmd = ["streamlink", "--json", url]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except FileNotFoundError:
        return StreamStatus(
            is_live=False,
            url=url,
            error="streamlink not found on PATH",
        )
    except subprocess.TimeoutExpired:
        return StreamStatus(
            is_live=False,
            url=url,
            error=f"Streamlink probe timed out after {timeout}s",
        )
    except Exception as exc:
        return StreamStatus(
            is_live=False,
            url=url,
            error=f"Streamlink probe failed: {exc}",
        )

    stdout = proc.stdout.strip()
    if not stdout:
        return StreamStatus(
            is_live=False,
            url=url,
            error=proc.stderr.strip() or "Empty output from streamlink",
        )

    try:
        data = json.loads(stdout)
    except json.JSONDecodeError as exc:
        return StreamStatus(
            is_live=False,
            url=url,
            error=f"Failed to parse streamlink JSON: {exc}",
        )

    if not isinstance(data, dict):
        return StreamStatus(
            is_live=False,
            url=url,
            error="Unexpected JSON response format",
        )

    if "error" in data:
        return StreamStatus(
            is_live=False,
            url=url,
            error=str(data["error"]),
        )

    streams = data.get("streams")
    if not isinstance(streams, dict) or not streams:
        return StreamStatus(
            is_live=False,
            url=url,
            error="No active streams available",
        )

    metadata = data.get("metadata") or {}
    qualities = tuple(sorted(streams.keys()))

    return StreamStatus(
        is_live=True,
        url=url,
        title=metadata.get("title"),
        author=metadata.get("author"),
        category=metadata.get("category"),
        qualities=qualities,
        error=None,
    )
