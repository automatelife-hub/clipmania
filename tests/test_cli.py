"""CLI test suite using Click CliRunner."""

from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest
from click.testing import CliRunner

from clipmania.cli import main
from clipmania.ingestion.metadata import SourceMetadata, VideoChapter


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture(autouse=True)
def mock_dependencies():
    """Mock external tool dependency checks so tests can run in any environment."""
    with patch("clipmania.cli.check_all_dependencies"):
        yield


class TestCliBasic:
    """Test general CLI options and help pages."""

    def test_version(self, runner):
        result = runner.invoke(main, ["--version"])
        assert result.exit_code == 0
        assert "0.2.0" in result.output

    def test_help(self, runner):
        result = runner.invoke(main, ["--help"])
        assert result.exit_code == 0
        assert "clip" in result.output
        assert "download" in result.output
        assert "live-start" in result.output
        assert "clip-now" in result.output
        assert "live-stop" in result.output
        assert "convert" in result.output
        assert "info" in result.output
        assert "montage" in result.output
        assert "wizard" in result.output
        assert "validate" in result.output
        assert "batch" in result.output

    @pytest.mark.parametrize("cmd", [
        "clip", "download", "live-start", "clip-now", "live-stop", "convert", "info", "montage", "wizard", "validate", "batch"
    ])
    def test_subcommand_help(self, runner, cmd):
        result = runner.invoke(main, [cmd, "--help"])
        assert result.exit_code == 0
        assert "Usage:" in result.output


class TestCliValidation:
    """Test CLI input validation before calling subprocesses."""

    def test_clip_start_after_end_rejected(self, runner):
        result = runner.invoke(main, [
            "clip", "https://youtube.com/watch?v=123",
            "--start", "2:00",
            "--end", "1:00",
        ])
        assert result.exit_code != 0
        assert "must be before end" in result.output

    def test_clip_invalid_timestamp_rejected(self, runner):
        result = runner.invoke(main, [
            "clip", "https://youtube.com/watch?v=123",
            "--start", "invalid_time",
            "--end", "1:00",
        ])
        assert result.exit_code != 0
        assert "Invalid timestamp" in result.output

    def test_clip_missing_both_timestamps_and_chapter_rejected(self, runner):
        result = runner.invoke(main, [
            "clip", "https://youtube.com/watch?v=123",
        ])
        assert result.exit_code != 0
        assert "Must provide either --start and --end timestamps, or --chapter" in result.output

    @patch("clipmania.cli.extract_channel", return_value="test_chan")
    @patch("clipmania.ingestion.metadata.fetch_metadata")
    @patch("clipmania.ingestion.ytdlp.clip_section")
    def test_clip_with_chapter_success(self, mock_clip_sec, mock_fetch, _mock_chan, runner, tmp_path):
        mock_fetch.return_value = SourceMetadata(
            url="https://youtube.com/watch?v=123",
            title="Video With Chapters",
            uploader="Creator",
            duration=300.0,
            chapters=[
                VideoChapter(index=1, title="Intro", start_time=0.0, end_time=30.0),
                VideoChapter(index=2, title="Highlight Moment", start_time=30.0, end_time=90.0),
            ],
        )
        fake_raw = tmp_path / "raw.mp4"
        fake_raw.write_text("dummy")
        mock_clip_sec.return_value = fake_raw

        result = runner.invoke(main, [
            "clip", "https://youtube.com/watch?v=123",
            "--chapter", "Highlight",
            "--raw",
        ])
        assert result.exit_code == 0
        assert "Highlight Moment" in result.output
        mock_clip_sec.assert_called_once()
        # Verify timestamps passed to clip_section
        call_args = mock_clip_sec.call_args[0]
        assert call_args[1] == "0:30.000"
        assert call_args[2] == "1:30.000"

    def test_convert_invalid_crop_rejected(self, runner, tmp_path):
        dummy = tmp_path / "video.mp4"
        dummy.write_text("data")
        result = runner.invoke(main, [
            "convert", str(dummy),
            "--layout", "vertical-stacked",
            "--cam-crop", "not:a:valid:crop:format",
        ])
        assert result.exit_code != 0
        assert "Invalid crop format" in result.output

    def test_live_stop_empty(self, runner, tmp_path):
        result = runner.invoke(main, [
            "--config", "clipmania.toml",
            "live-stop",
        ])
        assert result.exit_code == 0
        assert "No active live buffer to stop" in result.output

    def test_clip_now_zero_seconds_rejected(self, runner):
        result = runner.invoke(main, ["clip-now", "--seconds", "0"])
        assert result.exit_code != 0

    def test_clip_now_negative_seconds_rejected(self, runner):
        result = runner.invoke(main, ["clip-now", "--seconds", "-5"])
        assert result.exit_code != 0

    @patch("clipmania.processing.ffmpeg.probe")
    def test_validate_cli_passes(self, mock_probe, runner, tmp_path):
        dummy = tmp_path / "valid.mp4"
        dummy.write_text("data")
        mock_probe.return_value = MagicMock(
            width=1080, height=1920, duration=30.0, aspect_ratio="9:16", video_codec="h264"
        )
        result = runner.invoke(main, ["validate", str(dummy), "--platform", "tiktok"])
        assert result.exit_code == 0
        assert "Basic video properties validated for TikTok" in result.output

    @patch("clipmania.processing.ffmpeg.probe")
    def test_validate_cli_fails(self, mock_probe, runner, tmp_path):
        dummy = tmp_path / "invalid.mp4"
        dummy.write_text("data")
        # Landscape video failing TikTok portrait requirement
        mock_probe.return_value = MagicMock(
            width=1920, height=1080, duration=150.0, aspect_ratio="16:9", video_codec="h264"
        )
        result = runner.invoke(main, ["validate", str(dummy), "--platform", "tiktok"])
        assert result.exit_code != 0
        assert "does not comply" in result.output

    @patch("clipmania.processing.batch.load_batch_config")
    @patch("clipmania.processing.batch.process_batch", return_value=[Path("c1.mp4"), Path("c2.mp4")])
    def test_batch_cli(self, mock_process, mock_load, runner, tmp_path):
        dummy_cfg = tmp_path / "batch.json"
        dummy_cfg.write_text("{}")
        mock_load.return_value = [MagicMock(), MagicMock()]

        result = runner.invoke(main, ["batch", str(dummy_cfg), "--workers", "2"])
        assert result.exit_code == 0
        assert "2 clips created" in result.output
        mock_process.assert_called_once()
