"""Tests for processing, layouts, ffmpeg probe parsing, and ingestion helpers."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from clipmania.ingestion.streamlink_live import BufferState, _get_playlist_duration
from clipmania.ingestion.ytdlp import _find_output
from clipmania.processing.ffmpeg import VideoInfo, _safe_float, _safe_int
from clipmania.processing.layouts import Layout, build_filter_for_layout


class TestSafeNumberParsing:
    """Test C3 safe parsing of ffprobe output including 'N/A' strings."""

    def test_safe_int_valid(self):
        assert _safe_int("12345") == 12345
        assert _safe_int(67890) == 67890

    def test_safe_int_na(self):
        assert _safe_int("N/A") is None
        assert _safe_int("N/A", default=0) == 0

    def test_safe_int_invalid(self):
        assert _safe_int("none") is None
        assert _safe_int(None) is None

    def test_safe_float_valid(self):
        assert _safe_float("12.34") == 12.34
        assert _safe_float(56.78) == 56.78

    def test_safe_float_na(self):
        assert _safe_float("N/A") == 0.0
        assert _safe_float("N/A", default=10.0) == 10.0

    def test_safe_float_invalid(self):
        assert _safe_float("invalid") == 0.0
        assert _safe_float(None, default=-1.0) == -1.0


class TestVideoInfoAspectRatio:
    """Test accurate aspect ratio calculation."""

    def test_16_9(self):
        v = VideoInfo(
            path=Path("dummy.mp4"), duration=10.0,
            width=1920, height=1080, video_codec="h264",
            audio_codec="aac", fps=30.0, audio_sample_rate=48000,
            audio_channels=2, bitrate=5000000,
        )
        assert v.aspect_ratio == "16:9"
        assert v.is_landscape is True

    def test_9_16(self):
        v = VideoInfo(
            path=Path("dummy.mp4"), duration=10.0,
            width=1080, height=1920, video_codec="h264",
            audio_codec="aac", fps=30.0, audio_sample_rate=48000,
            audio_channels=2, bitrate=5000000,
        )
        assert v.aspect_ratio == "9:16"
        assert v.is_portrait is True

    def test_4_3(self):
        v = VideoInfo(
            path=Path("dummy.mp4"), duration=10.0,
            width=640, height=480, video_codec="h264",
            audio_codec="aac", fps=30.0, audio_sample_rate=48000,
            audio_channels=2, bitrate=1000000,
        )
        assert v.aspect_ratio == "4:3"

    def test_1_1(self):
        v = VideoInfo(
            path=Path("dummy.mp4"), duration=10.0,
            width=1080, height=1080, video_codec="h264",
            audio_codec="aac", fps=30.0, audio_sample_rate=48000,
            audio_channels=2, bitrate=1000000,
        )
        assert v.aspect_ratio == "1:1"

    def test_unknown(self):
        v = VideoInfo(
            path=Path("dummy.mp4"), duration=10.0,
            width=0, height=0, video_codec="h264",
            audio_codec=None, fps=30.0, audio_sample_rate=None,
            audio_channels=None, bitrate=None,
        )
        assert v.aspect_ratio == "unknown"


class TestLayoutFilters:
    """Test build_filter_for_layout helper."""

    def test_vertical_blur(self):
        filter_str, is_complex = build_filter_for_layout(Layout.VERTICAL_BLUR)
        assert is_complex is True
        assert "boxblur" in filter_str
        assert "overlay" in filter_str

    def test_vertical_crop(self):
        filter_str, is_complex = build_filter_for_layout(Layout.VERTICAL_CROP)
        assert is_complex is False
        assert "crop=ih*9/16" in filter_str

    def test_vertical_stacked(self):
        filter_str, is_complex = build_filter_for_layout(
            Layout.VERTICAL_STACKED,
            cam_crop="640:480:0:0",
            game_crop="1920:1080:0:0",
            cam_height=500,
            game_height=1420,
        )
        assert is_complex is True
        assert "crop=640:480:0:0" in filter_str
        assert "scale=1080:500" in filter_str
        assert "vstack" in filter_str

    def test_horizontal(self):
        filter_str, is_complex = build_filter_for_layout(Layout.HORIZONTAL)
        assert is_complex is False
        assert "scale=1920:1080" in filter_str


class TestBufferStateAndPlaylist:
    """Test BufferState loading, corrupted handling, and playlist parsing."""

    def test_save_and_load(self, tmp_path):
        state_file = tmp_path / "buffer_state.json"
        state = BufferState(
            url="https://twitch.tv/test",
            channel="test",
            platform="twitch",
            buffer_dir=str(tmp_path),
            playlist_path=str(tmp_path / "live.m3u8"),
            streamlink_pid=1234,
            ffmpeg_pid=5678,
            started_at="2026-09-22T00:00:00Z",
            streamlink_create_time=100.0,
            ffmpeg_create_time=101.0,
        )
        state.save(state_file)
        loaded = BufferState.load(state_file)
        assert loaded.channel == "test"
        assert loaded.streamlink_pid == 1234
        assert loaded.streamlink_create_time == 100.0

    def test_load_corrupted_json_cleans_up_and_raises(self, tmp_path):
        state_file = tmp_path / "buffer_state.json"
        state_file.write_text("not json {{{")
        with pytest.raises(FileNotFoundError):
            BufferState.load(state_file)
        # Should have unlinked the corrupted file
        assert not state_file.exists()

    def test_playlist_duration_parsing(self, tmp_path):
        playlist = tmp_path / "live.m3u8"
        playlist.write_text(
            "#EXTM3U\n"
            "#EXT-X-VERSION:3\n"
            "#EXT-X-TARGETDURATION:6\n"
            "#EXTINF:5.005,\n"
            "segment_001.ts\n"
            "#EXTINF:5.000,\n"
            "segment_002.ts\n"
            "#EXTINF:4.995,\n"
            "segment_003.ts\n"
        )
        duration = _get_playlist_duration(playlist)
        assert duration is not None
        assert abs(duration - 15.0) < 0.01

    def test_playlist_duration_nonexistent(self, tmp_path):
        duration = _get_playlist_duration(tmp_path / "none.m3u8")
        assert duration is None


class TestFindOutputExtension:
    """Test M4 extension-agnostic output file finding."""

    def test_exact_match(self, tmp_path):
        target = tmp_path / "test.mp4"
        target.write_text("data")
        assert _find_output(target) == target

    def test_mkv_fallback(self, tmp_path):
        # yt-dlp created .mkv instead of requested .mp4
        target = tmp_path / "test.mp4"
        actual = tmp_path / "test.mkv"
        actual.write_text("data")
        assert _find_output(target) == actual

    def test_webm_fallback(self, tmp_path):
        target = tmp_path / "test.mp4"
        actual = tmp_path / "test.webm"
        actual.write_text("data")
        assert _find_output(target) == actual

    def test_ignores_part_and_temp_files(self, tmp_path):
        """H-8 regression test: Incomplete .part/.ytdl files must never be chosen."""
        target = tmp_path / "test.mp4"
        part_file = tmp_path / "test.part"
        part_file.write_text("incomplete data")
        ytdl_file = tmp_path / "test.ytdl"
        ytdl_file.write_text("incomplete data")

        # When only .part files exist, it shouldn't return them
        assert _find_output(target) == target

        # When a valid completed file exists alongside .part, pick the completed file
        completed = tmp_path / "test.mkv"
        completed.write_text("complete data")
        assert _find_output(target) == completed


class TestClampCrop:
    """H-10 test: dynamic crop clamping for non-1080p source resolutions."""

    def test_clamp_crop_within_bounds(self):
        from clipmania.processing.layouts import clamp_crop
        res = clamp_crop("640:480:100:50", 1920, 1080)
        assert res == "640:480:100:50"

    def test_clamp_crop_exceeds_width(self):
        from clipmania.processing.layouts import clamp_crop
        # For a 1280x720 source, offset x=1280 is outside frame!
        res = clamp_crop("640:480:1280:0", 1280, 720)
        w, h, x, y = [int(p) for p in res.split(":")]
        assert x + w <= 1280
        assert y + h <= 720
        assert x == 640

    def test_clamp_crop_exceeds_both_dimensions(self):
        from clipmania.processing.layouts import clamp_crop
        # 1920:1080 crop on 1280x720 video
        res = clamp_crop("1920:1080:0:0", 1280, 720)
        assert res == "1280:720:0:0"


class TestAudioSilentGuard:
    """H-11 test: guard against measuring/normalizing silent video without audio streams."""

    @patch("clipmania.processing.audio.probe")
    def test_measure_loudness_raises_on_silent_video(self, mock_probe):
        from clipmania.processing.audio import measure_loudness
        mock_probe.return_value = MagicMock(audio_codec=None)
        with pytest.raises(ValueError, match="has no audio stream"):
            measure_loudness(Path("silent.mp4"))

    @patch("clipmania.processing.audio.probe")
    def test_normalize_audio_raises_on_silent_video(self, mock_probe, tmp_path):
        from clipmania.processing.audio import normalize_audio
        mock_probe.return_value = MagicMock(audio_codec=None)
        with pytest.raises(ValueError, match="has no audio stream"):
            normalize_audio(Path("silent.mp4"), tmp_path / "out.mp4")



