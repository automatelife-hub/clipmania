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
        assert "watch" in result.output

    @pytest.mark.parametrize("cmd", [
        "clip", "download", "live-start", "clip-now", "live-stop", "convert", "info", "montage", "wizard", "validate", "batch", "watch"
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
        assert fake_raw.exists() is True, "C-1 regression: raw file was deleted!"
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

    @patch("clipmania.ingestion.ytdlp.clip_section")
    def test_clip_raw_preserves_file(self, mock_clip, runner, tmp_path):
        """C-1 regression test: clip --raw does not delete the downloaded file in finally."""
        raw_file = tmp_path / "_raw_user.mp4"
        raw_file.write_text("mp4 content")
        mock_clip.return_value = raw_file

        result = runner.invoke(main, [
            "--output-dir", str(tmp_path),
            "clip", "https://twitch.tv/user",
            "--start", "1:00", "--end", "2:00",
            "--raw",
        ])
        assert result.exit_code == 0
        assert "Raw clip saved" in result.output
        assert raw_file.exists() is True

    @patch("clipmania.ingestion.streamlink_live.get_buffer_status")
    @patch("clipmania.ingestion.streamlink_live.clip_from_buffer")
    def test_clip_now_raw_uses_output_dir(self, mock_clip_buf, mock_status, runner, tmp_path):
        """M-8 regression test: clip-now --raw saves to output_dir, not CWD."""
        from clipmania.ingestion.streamlink_live import BufferState
        mock_status.return_value = BufferState(
            url="https://twitch.tv/shroud",
            channel="shroud",
            platform="twitch",
            buffer_dir=str(tmp_path),
            playlist_path=str(tmp_path / "live.m3u8"),
        )
        fake_out = tmp_path / "custom_out" / "clip.mp4"
        mock_clip_buf.return_value = fake_out

        result = runner.invoke(main, [
            "--output-dir", str(tmp_path / "custom_out"),
            "clip-now", "--seconds", "30", "--raw",
        ])
        assert result.exit_code == 0
        assert mock_clip_buf.called
        output_arg = mock_clip_buf.call_args[1].get("output_path")
        assert output_arg is not None
        assert str(tmp_path / "custom_out") in str(output_arg)

    @patch("clipmania.ingestion.live_watcher.LiveWatcher.run")
    def test_watch_cli_invocation(self, mock_run, runner):
        result = runner.invoke(main, [
            "watch", "https://rumble.com/c/Bongino",
            "--check-interval", "15",
            "--clip-interval", "300",
            "--window", "60",
        ])
        assert result.exit_code == 0
        mock_run.assert_called_once()

    def test_watch_cli_invalid_intervals_rejected(self, runner):
        res1 = runner.invoke(main, ["watch", "https://twitch.tv/user", "--check-interval", "0"])
        assert res1.exit_code != 0
        assert "must be positive" in res1.output

        res2 = runner.invoke(main, ["watch", "https://twitch.tv/user", "--clip-interval", "-5"])
        assert res2.exit_code != 0
        assert "must be positive" in res2.output

        res3 = runner.invoke(main, ["watch", "https://twitch.tv/user", "--window", "0"])
        assert res3.exit_code != 0
        assert "must be positive" in res3.output

