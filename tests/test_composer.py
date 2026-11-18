"""Tests for clipmania.processing.composer."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from clipmania.processing.composer import concat_clips


class TestComposer:
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

    @patch("clipmania.processing.composer.probe")
    @patch("clipmania.processing.composer.run_ffmpeg_with_progress")
    def test_concat_stream_copy_list_file_exists_during_ffmpeg(self, mock_ffmpeg, mock_probe, tmp_path):
        """C-2 regression test: list_file must exist during FFmpeg execution, not deleted prematurely."""
        mock_probe.return_value = MagicMock(duration=10.0)
        c1 = tmp_path / "c1.mp4"
        c2 = tmp_path / "c2.mp4"
        out = tmp_path / "out.mp4"

        file_existed = []

        def check_file_exists(args, **kwargs):
            list_file_arg = args[args.index("-i") + 1]
            file_existed.append(Path(list_file_arg).exists())

        mock_ffmpeg.side_effect = check_file_exists
        concat_clips([c1, c2], out, reencode=False)
        assert file_existed == [True], "list_file was deleted before FFmpeg could read it!"

    @patch("clipmania.processing.composer.probe")
    @patch("clipmania.processing.composer.run_ffmpeg_with_progress")
    def test_concat_reencode_silent_clips(self, mock_ffmpeg, mock_probe, tmp_path):
        """H-11 test: Concat handles silent video (no audio stream) gracefully."""
        mock_probe.return_value = MagicMock(duration=10.0, audio_codec=None)
        c1 = tmp_path / "silent1.mp4"
        c2 = tmp_path / "silent2.mp4"
        out = tmp_path / "out.mp4"

        concat_clips([c1, c2], out, reencode=True)
        assert mock_ffmpeg.called
        args = mock_ffmpeg.call_args[0][0]
        assert "-an" in args
        filter_str = args[args.index("-filter_complex") + 1]
        assert "v=1:a=0" in filter_str
