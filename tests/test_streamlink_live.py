"""Tests for live stream buffer management."""

from unittest.mock import MagicMock, patch
from pathlib import Path

import pytest

from clipmania.ingestion.streamlink_live import start_live_buffer


@patch("subprocess.Popen")
@patch("clipmania.ingestion.streamlink_live._acquire_lock")
@patch("clipmania.ingestion.streamlink_live._release_lock")
def test_start_live_buffer_includes_retry_flags(mock_rel, mock_acq, mock_popen, tmp_path):
    mock_sl = MagicMock(pid=1001, poll=lambda: None)
    mock_ff = MagicMock(pid=1002, poll=lambda: None)
    mock_popen.side_effect = [mock_sl, mock_ff]

    start_live_buffer("https://kick.com/xqc", buffer_dir=tmp_path, retry_streams=5, retry_max=3)

    sl_cmd = mock_popen.call_args_list[0][0][0]
    assert "--retry-streams" in sl_cmd
    assert "5" in sl_cmd
    assert "--retry-max" in sl_cmd
    assert "3" in sl_cmd


@patch("subprocess.Popen")
@patch("clipmania.ingestion.streamlink_live._acquire_lock")
@patch("clipmania.ingestion.streamlink_live._release_lock")
def test_start_live_buffer_default_retry_flags(mock_rel, mock_acq, mock_popen, tmp_path):
    mock_sl = MagicMock(pid=1001, poll=lambda: None)
    mock_ff = MagicMock(pid=1002, poll=lambda: None)
    mock_popen.side_effect = [mock_sl, mock_ff]

    start_live_buffer("https://twitch.tv/shroud", buffer_dir=tmp_path)

    sl_cmd = mock_popen.call_args_list[0][0][0]
    assert "--retry-streams" in sl_cmd
    assert "--retry-max" in sl_cmd
