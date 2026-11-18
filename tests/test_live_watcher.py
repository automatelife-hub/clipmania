"""Tests for live channel watcher daemon."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from clipmania.ingestion.live_watcher import LiveWatcher, WatcherState
from clipmania.ingestion.stream_probe import StreamStatus
from clipmania.ingestion.streamlink_live import BufferState
from clipmania.processing.live_cutter import LiveCutterConfig


@patch("clipmania.ingestion.live_watcher.probe_stream_status")
@patch("clipmania.ingestion.live_watcher.start_live_buffer")
@patch("clipmania.ingestion.live_watcher.stop_live_buffer")
def test_watcher_transitions_offline_to_live(mock_stop, mock_start, mock_probe, tmp_path):
    mock_probe.side_effect = [
        StreamStatus(is_live=False, url="https://twitch.tv/user"),
        StreamStatus(is_live=True, url="https://twitch.tv/user", title="Stream Live", qualities=("best",)),
    ]
    watcher = LiveWatcher("https://twitch.tv/user", check_interval=1, buffer_dir=tmp_path)

    # First poll: offline
    state1 = watcher.poll_once()
    assert state1 == WatcherState.OFFLINE
    mock_start.assert_not_called()

    # Second poll: went live!
    state2 = watcher.poll_once()
    assert state2 == WatcherState.LIVE
    mock_start.assert_called_once()


@patch("clipmania.ingestion.live_watcher.get_buffer_status")
@patch("clipmania.ingestion.live_watcher.probe_stream_status")
@patch("clipmania.ingestion.live_watcher.start_live_buffer")
@patch("clipmania.ingestion.live_watcher.stop_live_buffer")
def test_watcher_transitions_live_to_offline_when_stream_ends(
    mock_stop, mock_start, mock_probe, mock_buffer_status, tmp_path
):
    mock_probe.return_value = StreamStatus(is_live=True, url="https://twitch.tv/user")
    watcher = LiveWatcher("https://twitch.tv/user", check_interval=1, buffer_dir=tmp_path)

    # Go live
    watcher.poll_once()
    assert watcher.state == WatcherState.LIVE

    # Stream ends
    mock_buffer_status.return_value = None  # Buffer exited
    mock_probe.return_value = StreamStatus(is_live=False, url="https://twitch.tv/user")

    state = watcher.poll_once()
    assert state == WatcherState.OFFLINE
    mock_stop.assert_called_once()


@patch("clipmania.ingestion.live_watcher.get_buffer_status")
@patch("clipmania.ingestion.live_watcher.probe_stream_status")
@patch("clipmania.ingestion.live_watcher.start_live_buffer")
def test_watcher_scans_new_segments_while_live(mock_start, mock_probe, mock_buf_status, tmp_path):
    mock_probe.return_value = StreamStatus(is_live=True, url="https://twitch.tv/user")
    mock_buf_status.return_value = MagicMock(spec=BufferState)

    seg_dir = tmp_path / "user"
    seg_dir.mkdir(parents=True, exist_ok=True)

    watcher = LiveWatcher("https://twitch.tv/user", check_interval=1, buffer_dir=tmp_path)
    watcher.poll_once()
    assert watcher.state == WatcherState.LIVE

    # Mock cutter
    watcher.cutter = MagicMock()

    # Create dummy segments
    seg1 = seg_dir / "segment_001.ts"
    seg1.write_text("ts1")

    watcher.poll_once()
    watcher.cutter.evaluate_new_segment.assert_called_with(seg1)
    watcher.cutter.check_interval_timer.assert_called_once()


@patch("clipmania.ingestion.live_watcher.stop_live_buffer")
def test_watcher_stop_cleans_up_live_buffer(mock_stop, tmp_path):
    watcher = LiveWatcher("https://twitch.tv/user", buffer_dir=tmp_path)
    watcher.state = WatcherState.LIVE
    watcher.stop()
    assert watcher.state == WatcherState.STOPPED
    mock_stop.assert_called_once_with(buffer_dir=tmp_path)


@patch("clipmania.ingestion.live_watcher.probe_stream_status")
def test_watcher_run_max_iterations(mock_probe, tmp_path):
    mock_probe.return_value = StreamStatus(is_live=False, url="https://twitch.tv/user")
    watcher = LiveWatcher("https://twitch.tv/user", check_interval=0, buffer_dir=tmp_path)
    watcher.run(max_iterations=2)
    assert mock_probe.call_count == 2

