"""CLI test suite using Click CliRunner."""

from unittest.mock import patch
import pytest
from click.testing import CliRunner

from clipmania.cli import main


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
        assert "0.1.0" in result.output

    def test_help(self, runner):
        result = runner.invoke(main, ["--help"])
        assert result.exit_code == 0
        assert "clip" in result.output
        assert "download" in result.output
        assert "live-start" in result.output
        assert "clip-now" in result.output
        assert "live-stop" in result.output
        assert "convert" in result.output

    @pytest.mark.parametrize("cmd", ["clip", "download", "live-start", "clip-now", "live-stop", "convert"])
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
        # When no buffer is active, live-stop should exit cleanly with notice
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
