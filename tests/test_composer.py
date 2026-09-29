"""Tests for clipmania.processing.composer."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from clipmania.processing.composer import (
    add_watermark,
    concat_clips,
    has_moviepy,
)


class TestComposer:
    def test_has_moviepy_returns_bool(self):
        assert isinstance(has_moviepy(), bool)

    def test_concat_empty_raises(self, tmp_path):
        with pytest.raises(ValueError, match="at least one clip"):
            concat_clips([], tmp_path / "out.mp4")

    @patch("shutil.copyfile")
    def test_concat_single_clip_copies(self, mock_copy, tmp_path):
        c1 = tmp_path / "c1.mp4"
        out = tmp_path / "out.mp4"
        res = concat_clips([c1], out)
        assert res == out
        mock_copy.assert_called_once_with(str(c1), str(out))

    @patch("clipmania.processing.composer.probe")
    @patch("clipmania.processing.composer.run_ffmpeg_with_progress")
    def test_concat_multiple_reencode(self, mock_ffmpeg, mock_probe, tmp_path):
        mock_probe.return_value = MagicMock(duration=10.0)
        c1 = tmp_path / "c1.mp4"
        c2 = tmp_path / "c2.mp4"
        out = tmp_path / "out.mp4"

        concat_clips([c1, c2], out, reencode=True)
        assert mock_ffmpeg.called
        args = mock_ffmpeg.call_args[0][0]
        assert "-filter_complex" in args
        assert "concat=n=2:v=1:a=1[outv][outa]" in args[args.index("-filter_complex") + 1]

    @patch("clipmania.processing.composer.probe")
    @patch("clipmania.processing.composer.run_ffmpeg_with_progress")
    def test_concat_multiple_stream_copy(self, mock_ffmpeg, mock_probe, tmp_path):
        mock_probe.return_value = MagicMock(duration=10.0)
        c1 = tmp_path / "c1.mp4"
        c2 = tmp_path / "c2.mp4"
        out = tmp_path / "out.mp4"

        concat_clips([c1, c2], out, reencode=False)
        assert mock_ffmpeg.called
        args = mock_ffmpeg.call_args[0][0]
        assert "-f" in args
        assert "concat" in args
        assert "-c" in args
        assert "copy" in args

    def test_add_watermark_missing_file_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            add_watermark(tmp_path / "in.mp4", tmp_path / "out.mp4", tmp_path / "missing.png")

    @patch("clipmania.processing.composer.probe")
    @patch("clipmania.processing.composer.run_ffmpeg_with_progress")
    def test_add_watermark_success(self, mock_ffmpeg, mock_probe, tmp_path):
        mock_probe.return_value = MagicMock(duration=10.0)
        in_file = tmp_path / "in.mp4"
        wm_file = tmp_path / "logo.png"
        wm_file.write_text("fake png")
        out_file = tmp_path / "out.mp4"

        add_watermark(in_file, out_file, wm_file, position="top-right")
        assert mock_ffmpeg.called
        args = mock_ffmpeg.call_args[0][0]
        assert "-filter_complex" in args
        filter_str = args[args.index("-filter_complex") + 1]
        assert "overlay=" in filter_str
