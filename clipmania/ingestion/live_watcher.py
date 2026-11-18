"""Live stream channel watcher daemon.

Periodically probes channel URLs for live status. When a stream goes live,
starts the rolling HLS buffer and launches the LiveCutter engine to generate
highlights in real-time.
"""

from __future__ import annotations

import logging
import time
from enum import Enum
from pathlib import Path

from rich.console import Console

from clipmania.config import ClipManiaConfig
from clipmania.ingestion.stream_probe import StreamStatus, probe_stream_status
from clipmania.ingestion.streamlink_live import (
    get_buffer_status,
    get_default_buffer_dir,
    start_live_buffer,
    stop_live_buffer,
)
from clipmania.processing.live_cutter import LiveCutter, LiveCutterConfig
from clipmania.utils import extract_channel

logger = logging.getLogger(__name__)
console = Console()


class WatcherState(Enum):
    """Lifecycle state of the channel watcher."""

    OFFLINE = "offline"
    STARTING = "starting"
    LIVE = "live"
    STOPPED = "stopped"


class LiveWatcher:
    """Daemon monitoring a creator channel for live streams and cutting highlights."""

    def __init__(
        self,
        url: str,
        check_interval: int = 30,
        live_poll_interval: int = 5,
        buffer_dir: Path | None = None,
        output_dir: Path | None = None,
        cutter_config: LiveCutterConfig | None = None,
        segment_duration: int = 5,
        playlist_size: int = 24,
        quality: str = "best",
    ) -> None:
        self.url = url
        self.channel = extract_channel(url)
        self.check_interval = check_interval
        self.live_poll_interval = live_poll_interval
        self.buffer_dir = Path(buffer_dir or get_default_buffer_dir())
        self.output_dir = Path(output_dir or ClipManiaConfig().output_dir)
        self.cutter_config = cutter_config or LiveCutterConfig(channel=self.channel)
        self.cutter_config.channel = self.channel

        self.segment_duration = segment_duration
        self.playlist_size = playlist_size
        self.quality = quality

        self.state: WatcherState = WatcherState.OFFLINE
        self.current_status: StreamStatus | None = None
        self.cutter: LiveCutter | None = None
        self._seen_segments: set[Path] = set()
        self._stop_requested: bool = False

    def poll_once(self) -> WatcherState:
        """Perform a single monitoring poll cycle and advance state machine."""
        if self.state == WatcherState.STOPPED:
            return self.state

        if self.state == WatcherState.OFFLINE:
            status = probe_stream_status(self.url)
            self.current_status = status
            if status.is_live:
                console.print(
                    f"[bold green]🔴 Stream is LIVE:[/] {status.title or self.url} "
                    f"by [cyan]{status.author or self.channel}[/]"
                )
                self.state = WatcherState.LIVE
                try:
                    start_live_buffer(
                        self.url,
                        buffer_dir=self.buffer_dir,
                        segment_duration=self.segment_duration,
                        playlist_size=self.playlist_size,
                        quality=self.quality,
                    )
                except Exception as exc:
                    console.print(f"[red]✗ Failed to start live buffer:[/] {exc}")
                    self.state = WatcherState.OFFLINE
                    return self.state

                self.cutter = LiveCutter(
                    buffer_dir=self.buffer_dir,
                    output_dir=self.output_dir,
                    config=self.cutter_config,
                )
                self._seen_segments.clear()
            else:
                logger.debug("Channel %s is offline (%s)", self.channel, status.error)

            return self.state

        if self.state == WatcherState.LIVE:
            # Check if buffer is still running
            buf = get_buffer_status(buffer_dir=self.buffer_dir)
            if buf is None:
                console.print(f"[yellow]⏹ Live buffer stopped for {self.channel}. Returning to OFFLINE mode.[/]")
                self._on_stream_end()
                return self.state

            # If cutter is active, perform interval and segment checks
            if self.cutter is not None:
                self.cutter.check_interval_timer()

                seg_dir = self.buffer_dir / self.channel
                if seg_dir.exists():
                    current_ts = sorted(seg_dir.glob("segment_*.ts"))
                    for ts_file in current_ts:
                        if ts_file not in self._seen_segments:
                            self._seen_segments.add(ts_file)
                            self.cutter.evaluate_new_segment(ts_file)

            return self.state

        return self.state

    def _on_stream_end(self) -> None:
        """Handle stream shutdown and cleanup."""
        try:
            stop_live_buffer(buffer_dir=self.buffer_dir)
        except Exception:
            pass
        self.cutter = None
        self.state = WatcherState.OFFLINE

    def run(self, max_iterations: int | None = None) -> None:
        """Run the watcher loop until stopped or max_iterations reached."""
        console.print(f"[bold blue]👀 Watching channel[/] [cyan]{self.channel}[/] ({self.url})")
        console.print(f"   Offline check interval: {self.check_interval}s")
        console.print(f"   Clip window: {self.cutter_config.clip_window_seconds}s, interval: {self.cutter_config.interval_seconds}s")

        iterations = 0
        try:
            while not self._stop_requested:
                self.poll_once()
                iterations += 1
                if max_iterations is not None and iterations >= max_iterations:
                    break

                sleep_time = self.live_poll_interval if self.state == WatcherState.LIVE else self.check_interval
                time.sleep(sleep_time)
        except KeyboardInterrupt:
            console.print("\n[yellow]Stopping watcher...[/]")
        finally:
            self.stop()

    def stop(self) -> None:
        """Stop watching and terminate live buffer if active."""
        self._stop_requested = True
        if self.state == WatcherState.LIVE:
            try:
                stop_live_buffer(buffer_dir=self.buffer_dir)
            except Exception:
                pass
        self.state = WatcherState.STOPPED
