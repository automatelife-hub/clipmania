"""Video source metadata and chapter extraction engine."""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field


@dataclass
class VideoChapter:
    """A chapter section from a video."""

    index: int
    title: str
    start_time: float
    end_time: float

    @property
    def duration(self) -> float:
        return max(0.0, self.end_time - self.start_time)


@dataclass
class SourceMetadata:
    """Rich metadata extracted from a video URL via yt-dlp."""

    url: str
    title: str
    uploader: str
    duration: float
    view_count: int | None = None
    upload_date: str | None = None
    thumbnail_url: str | None = None
    chapters: list[VideoChapter] = field(default_factory=list)
    available_resolutions: list[str] = field(default_factory=list)
    is_live: bool = False


def fetch_metadata(url: str) -> SourceMetadata:
    """Extract metadata, formats, and chapters from a video URL using yt-dlp.

    Raises
    ------
    RuntimeError
        If yt-dlp fails to extract metadata.
    """
    cmd = [
        "yt-dlp",
        "--dump-json",
        "--no-playlist",
        "--no-warnings",
        url,
    ]

    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=60,  # H-5: Prevent indefinite hangs
    )

    if result.returncode != 0:
        err = result.stderr.strip() or "Unknown error"
        raise RuntimeError(f"Failed to fetch metadata from {url}: {err}")

    raw = result.stdout.strip()
    if not raw:
        raise RuntimeError(f"No metadata returned for {url}")

    # yt-dlp can output multiple lines if multiple formats/entries, take the first valid json object
    first_json = raw.splitlines()[0]
    data = json.loads(first_json)

    # Parse chapters
    chapters: list[VideoChapter] = []
    raw_chapters = data.get("chapters") or []
    for idx, c in enumerate(raw_chapters, start=1):
        st = float(c.get("start_time", 0.0))
        et = float(c.get("end_time", 0.0))
        title = str(c.get("title") or f"Chapter {idx}")
        chapters.append(VideoChapter(index=idx, title=title, start_time=st, end_time=et))

    # Parse unique resolutions
    formats = data.get("formats") or []
    resolutions_set: set[str] = set()
    for f in formats:
        h = f.get("height")
        fps = f.get("fps")
        if h and isinstance(h, int) and h > 0:
            res_str = f"{h}p"
            if fps and isinstance(fps, (int, float)) and fps >= 50:
                res_str += f"{int(fps)}"
            resolutions_set.add(res_str)

    # Sort resolutions by height (the number before 'p'), descending
    def _res_sort_key(r: str) -> tuple[int, int]:
        """Return (height, fps) for sorting; e.g. '720p60' → (720, 60)."""
        import re as _re
        m = _re.match(r"(\d+)p(\d+)?", r)
        if m:
            height = int(m.group(1))
            fps = int(m.group(2)) if m.group(2) else 0
            return (height, fps)
        return (0, 0)

    sorted_resolutions = sorted(
        resolutions_set,
        key=_res_sort_key,
        reverse=True,
    )

    return SourceMetadata(
        url=url,
        title=data.get("title") or "Unknown Title",
        uploader=data.get("uploader") or data.get("channel") or data.get("creator") or "Unknown Creator",
        duration=float(data.get("duration") or 0.0),
        view_count=data.get("view_count"),
        upload_date=data.get("upload_date"),
        thumbnail_url=data.get("thumbnail"),
        chapters=chapters,
        available_resolutions=sorted_resolutions,
        is_live=bool(data.get("is_live")),
    )


def find_chapter(chapters: list[VideoChapter], query: str | int) -> VideoChapter | None:
    """Find a chapter by 1-based index or case-insensitive title search."""
    if not chapters:
        return None

    # Try numeric index match
    try:
        idx = int(query)
        if 1 <= idx <= len(chapters):
            return chapters[idx - 1]
    except ValueError:
        pass

    # Try case-insensitive title matching
    query_str = str(query).strip().lower()
    for c in chapters:
        if query_str in c.title.lower():
            return c

    return None
