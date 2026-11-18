"""Live highlight cutter engine.

Monitors rolling live stream buffer segments, evaluates audio excitement peaks,
and enforces interval-based and energy-based clipping.
"""

from __future__ import annotations

import logging
import math
import re
import subprocess
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path

from rich.console import Console

from clipmania.config import ClipManiaConfig
from clipmania.ingestion.streamlink_live import clip_from_buffer
from clipmania.processing.layouts import Layout
from clipmania.processing.pipeline import process_clip, process_clip_single_pass

logger = logging.getLogger(__name__)
console = Console()

_MEAN_VOLUME_RE = re.compile(r"mean_volume:\s*(-?[\d.]+)\s*dB")


def measure_segment_volume(segment_path: Path) -> float | None:
    """Measure the mean audio volume (in dB) of a video/audio segment via FFmpeg volumedetect.

    Returns:
        Mean volume in dB (e.g. -24.5), or None if audio is absent or probe fails.
    """
    if not segment_path.exists():
        return None

    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-nostats",
        "-i", str(segment_path),
        "-filter:a", "volumedetect",
        "-f", "null",
        "-",
    ]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=10,
        )
        match = _MEAN_VOLUME_RE.search(proc.stderr)
        if match:
            return float(match.group(1))
    except Exception as exc:
        logger.debug("Failed to measure audio volume for %s: %exc", segment_path, exc)

    return None


@dataclass
class LiveCutterConfig:
    """Configuration for automated live stream highlight cutter."""

    clip_window_seconds: int = 60
    interval_seconds: int = 300  # 5 minutes
    enable_audio_trigger: bool = True
    energy_threshold_multiplier: float = 2.0  # e.g., 2.0x amplitude (~+6 dB)
    cooldown_seconds: int = 120  # min time between automatic clips
    layout: Layout = Layout.VERTICAL_SMART
    platform: str | None = None
    channel: str = "live"
    fast_single_pass: bool = False


class LiveCutter:
    """Cuts and processes highlights from a rolling HLS buffer."""

    def __init__(
        self,
        buffer_dir: Path,
        output_dir: Path | None = None,
        config: LiveCutterConfig | None = None,
    ) -> None:
        self.buffer_dir = Path(buffer_dir)
        self.output_dir = Path(output_dir or ClipManiaConfig().output_dir)
        self.config = config or LiveCutterConfig()

        self.last_clip_time: float = 0.0
        self.last_interval_time: float = time.time()
        self._volume_history: deque[float] = deque(maxlen=20)

    def can_clip(self, current_time: float | None = None) -> bool:
        """Check if enough time has elapsed since the last clip to respect cooldown."""
        now = time.time() if current_time is None else current_time
        return (now - self.last_clip_time) >= self.config.cooldown_seconds

    def trigger_clip(
        self,
        reason: str = "manual",
        current_time: float | None = None,
    ) -> Path | None:
        """Extract a clip of recent buffer history and run it through the layout pipeline."""
        now = time.time() if current_time is None else current_time
        console.print(f"[bold cyan]✂ Triggering live clip ({reason})[/] [dim]window={self.config.clip_window_seconds}s[/]")

        try:
            raw_clip = clip_from_buffer(
                seconds=self.config.clip_window_seconds,
                buffer_dir=self.buffer_dir,
            )
        except Exception as exc:
            console.print(f"[yellow]⚠ Failed to extract live clip from buffer:[/] {exc}")
            return None

        try:
            if self.config.fast_single_pass:
                final_path = self.output_dir / f"{self.config.channel}_{int(now)}.mp4"
                processed = process_clip_single_pass(
                    input_path=raw_clip,
                    output_path=final_path,
                    layout=self.config.layout,
                    channel=self.config.channel,
                    platform=self.config.platform,
                )
            else:
                processed = process_clip(
                    input_path=raw_clip,
                    output_dir=self.output_dir,
                    layout=self.config.layout,
                    channel=self.config.channel,
                    platform=self.config.platform,
                )

            self.last_clip_time = now
            console.print(f"[bold green]✓ Live clip produced:[/] {processed.name}")
            return processed
        except Exception as exc:
            console.print(f"[red]✗ Failed to process live clip:[/] {exc}")
            return None

    def check_interval_timer(self, current_time: float | None = None) -> Path | None:
        """Check if the periodic interval timer has expired and trigger a clip if ready."""
        now = time.time() if current_time is None else current_time
        if (now - self.last_interval_time) >= self.config.interval_seconds:
            self.last_interval_time = now
            if self.can_clip(now):
                return self.trigger_clip(reason="interval", current_time=now)
        return None

    def evaluate_new_segment(
        self,
        segment_path: Path,
        current_time: float | None = None,
    ) -> Path | None:
        """Evaluate a new incoming HLS buffer segment for audio energy spikes."""
        if not self.config.enable_audio_trigger:
            return None

        now = time.time() if current_time is None else current_time
        volume = measure_segment_volume(segment_path)
        if volume is None:
            return None

        # Check against running baseline if we have enough history
        spike_detected = False
        if len(self._volume_history) >= 2:
            running_avg = sum(self._volume_history) / len(self._volume_history)
            # Difference in dB needed to exceed amplitude multiplier
            required_db_increase = 20.0 * math.log10(max(1.01, self.config.energy_threshold_multiplier))
            if (volume - running_avg) >= required_db_increase:
                spike_detected = True

        self._volume_history.append(volume)

        if spike_detected and self.can_clip(now):
            return self.trigger_clip(reason="audio_energy_spike", current_time=now)

        return None
