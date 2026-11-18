"""Tests for clipmania.processing.pipeline orchestrator."""

from pathlib import Path
from unittest.mock import MagicMock, patch

from clipmania.config import ClipManiaConfig
from clipmania.processing.layouts import Layout
from clipmania.processing.pipeline import process_clip, process_clip_single_pass


class TestProcessClip:
    @patch("clipmania.processing.pipeline.probe")
    @patch("clipmania.processing.pipeline.apply_layout")
    @patch("clipmania.processing.pipeline.normalize_audio")
    @patch("clipmania.processing.pipeline.validate_output")
    def test_process_clip_with_audio_runs_normalization(
        self, mock_validate, mock_norm, mock_layout, mock_probe, tmp_path
    ):
        mock_probe.return_value = MagicMock(
            width=1920, height=1080, duration=30.0,
            audio_codec="aac", video_codec="h264",
            aspect_ratio="16:9", fps=30.0, bitrate=5000000,
        )

        in_file = tmp_path / "in.mp4"
        in_file.write_text("input")

        # Fake the intermediate audio normalized file
        def fake_norm(layout_in, audio_out, **kwargs):
            audio_out.write_text("normalized")
            return audio_out

        mock_norm.side_effect = fake_norm
        mock_validate.return_value = []

        cfg = ClipManiaConfig(output_dir=tmp_path / "out")
        out_file = process_clip(
            in_file,
            layout=Layout.VERTICAL_BLUR,
            platform="tiktok",
            config=cfg,
            channel="gamer",
        )

        assert out_file.exists()
        assert out_file.parent == tmp_path / "out"
        assert "gamer" in out_file.name
        mock_layout.assert_called_once()
        mock_norm.assert_called_once()
        # M-5: file_size_bytes is validated
        assert mock_validate.called
        assert "file_size_bytes" in mock_validate.call_args[1]

    @patch("clipmania.processing.pipeline.probe")
    @patch("clipmania.processing.pipeline.apply_layout")
    @patch("clipmania.processing.pipeline.normalize_audio")
    def test_process_clip_silent_skips_audio_normalization(
        self, mock_norm, mock_layout, mock_probe, tmp_path
    ):
        mock_probe.return_value = MagicMock(
            width=1920, height=1080, duration=15.0,
            audio_codec=None, video_codec="h264",
            aspect_ratio="16:9", fps=30.0, bitrate=3000000,
        )

        in_file = tmp_path / "silent.mp4"
        in_file.write_text("input")

        def fake_layout(in_p, out_p, *args, **kwargs):
            out_p.write_text("layout_done")
            return out_p

        mock_layout.side_effect = fake_layout

        cfg = ClipManiaConfig(output_dir=tmp_path / "out")
        out_file = process_clip(
            in_file,
            layout=Layout.VERTICAL_CROP,
            config=cfg,
            channel="silentguy",
        )

        assert out_file.exists()
        mock_layout.assert_called_once()
        mock_norm.assert_not_called()


class TestProcessClipSinglePass:
    @patch("clipmania.processing.pipeline.probe")
    @patch("clipmania.processing.pipeline.run_ffmpeg_with_progress")
    def test_single_pass_blur_creates_output_atomically(
        self, mock_ffmpeg, mock_probe, tmp_path
    ):
        mock_probe.return_value = MagicMock(
            width=1920, height=1080, duration=20.0,
            audio_codec="aac", video_codec="h264",
            aspect_ratio="16:9", fps=30.0, bitrate=4000000,
        )

        in_file = tmp_path / "in.mp4"
        in_file.write_text("input")
        out_file = tmp_path / "final.mp4"

        # Simulate FFmpeg writing to the temp target passed in args
        def fake_ffmpeg(args, **kwargs):
            temp_out = Path(args[-1])
            temp_out.write_text("rendered output")

        mock_ffmpeg.side_effect = fake_ffmpeg

        res = process_clip_single_pass(
            in_file,
            out_file,
            layout=Layout.VERTICAL_BLUR,
        )

        assert res == out_file
        assert out_file.exists()
        assert out_file.read_text() == "rendered output"

    @patch("clipmania.processing.pipeline.process_clip")
    @patch("clipmania.processing.pipeline.probe")
    def test_single_pass_fallback_for_stacked_layout(
        self, mock_probe, mock_process, tmp_path
    ):
        mock_probe.return_value = MagicMock(
            width=1920, height=1080, duration=20.0,
            audio_codec="aac", video_codec="h264",
        )
        in_file = tmp_path / "in.mp4"
        out_file = tmp_path / "final.mp4"
        mock_process.return_value = out_file

        res = process_clip_single_pass(
            in_file,
            out_file,
            layout=Layout.VERTICAL_STACKED,
        )
        assert res == out_file
        mock_process.assert_called_once()
