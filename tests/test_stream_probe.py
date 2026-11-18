"""Tests for stream status prober using streamlink --json."""

import json
import subprocess
from unittest.mock import MagicMock, patch

import pytest

from clipmania.ingestion.stream_probe import StreamStatus, probe_stream_status


@patch("subprocess.run")
def test_probe_stream_live_success(mock_run):
    mock_run.return_value = MagicMock(
        returncode=0,
        stdout=json.dumps({
            "plugin": "twitch",
            "metadata": {
                "title": "Live Gaming Session",
                "author": "shroud",
                "category": "Valorant",
            },
            "streams": {
                "720p": {},
                "1080p": {},
                "best": {},
                "worst": {},
            },
        }),
        stderr="",
    )
    status = probe_stream_status("https://twitch.tv/shroud")
    assert status.is_live is True
    assert status.url == "https://twitch.tv/shroud"
    assert status.title == "Live Gaming Session"
    assert status.author == "shroud"
    assert status.category == "Valorant"
    assert "best" in status.qualities
    assert "1080p" in status.qualities
    assert status.error is None


@patch("subprocess.run")
def test_probe_stream_offline(mock_run):
    mock_run.return_value = MagicMock(
        returncode=1,
        stdout=json.dumps({
            "error": "No playable streams found on this URL: https://twitch.tv/shroud"
        }),
        stderr="",
    )
    status = probe_stream_status("https://twitch.tv/shroud")
    assert status.is_live is False
    assert status.url == "https://twitch.tv/shroud"
    assert status.qualities == ()
    assert "No playable streams found" in (status.error or "")


@patch("subprocess.run")
def test_probe_stream_timeout(mock_run):
    mock_run.side_effect = subprocess.TimeoutExpired(cmd=["streamlink"], timeout=15)
    status = probe_stream_status("https://youtube.com/live/abc")
    assert status.is_live is False
    assert status.url == "https://youtube.com/live/abc"
    assert status.error is not None
    assert "timed out" in status.error.lower()


@patch("subprocess.run")
def test_probe_stream_streamlink_not_found(mock_run):
    mock_run.side_effect = FileNotFoundError("streamlink executable not found")
    status = probe_stream_status("https://kick.com/streamer")
    assert status.is_live is False
    assert status.url == "https://kick.com/streamer"
    assert status.error is not None
    assert "streamlink not found" in status.error.lower()


@patch("subprocess.run")
def test_probe_stream_invalid_json(mock_run):
    mock_run.return_value = MagicMock(
        returncode=1,
        stdout="Not valid JSON at all",
        stderr="Failed to execute",
    )
    status = probe_stream_status("https://rumble.com/c/streamer")
    assert status.is_live is False
    assert status.error is not None


@patch("subprocess.run")
def test_probe_stream_empty_streams(mock_run):
    mock_run.return_value = MagicMock(
        returncode=0,
        stdout=json.dumps({
            "plugin": "youtube",
            "metadata": {"title": "Offline stream"},
            "streams": {},
        }),
        stderr="",
    )
    status = probe_stream_status("https://youtube.com/user/xyz")
    assert status.is_live is False
    assert status.qualities == ()
