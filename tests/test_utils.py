"""Tests for clipmania.utils."""

import pytest

from clipmania.utils import (
    Platform,
    detect_platform,
    extract_channel,
    format_timestamp,
    is_live_url,
    parse_timestamp,
    safe_filename,
    validate_crop,
)


# ---------------------------------------------------------------------------
# parse_timestamp
# ---------------------------------------------------------------------------

class TestParseTimestamp:
    """Timestamp parsing edge cases."""

    def test_plain_seconds(self):
        assert parse_timestamp("90") == 90.0

    def test_plain_seconds_float(self):
        assert parse_timestamp("1.5") == 1.5

    def test_mm_ss(self):
        assert parse_timestamp("1:30") == 90.0

    def test_hh_mm_ss(self):
        assert parse_timestamp("1:02:30") == 3750.0

    def test_human_hms(self):
        assert parse_timestamp("1h2m3s") == 3723.0

    def test_human_ms(self):
        assert parse_timestamp("1m30s") == 90.0

    def test_human_s_only(self):
        assert parse_timestamp("30s") == 30.0

    def test_human_m_only(self):
        assert parse_timestamp("5m") == 300.0

    def test_whitespace(self):
        assert parse_timestamp("  1:30  ") == 90.0

    def test_zero(self):
        assert parse_timestamp("0") == 0.0

    def test_mm_ss_fractional(self):
        assert parse_timestamp("1:30.5") == 90.5

    def test_invalid_raises(self):
        with pytest.raises(ValueError):
            parse_timestamp("not_a_timestamp")

    def test_too_many_colons_raises(self):
        with pytest.raises(ValueError):
            parse_timestamp("1:2:3:4")

    def test_partial_human_format_rejected(self):
        """'1h2' is ambiguous — should be '1h2m' or '1h2s'."""
        with pytest.raises(ValueError):
            parse_timestamp("1h2")


# ---------------------------------------------------------------------------
# format_timestamp
# ---------------------------------------------------------------------------

class TestFormatTimestamp:
    def test_seconds_only(self):
        result = format_timestamp(45.123)
        assert result == "0:45.123"

    def test_minutes_and_seconds(self):
        result = format_timestamp(90.0)
        assert result == "1:30.000"

    def test_with_hours(self):
        result = format_timestamp(3661.5)
        assert result == "1:01:01.500"


# ---------------------------------------------------------------------------
# detect_platform
# ---------------------------------------------------------------------------

class TestDetectPlatform:
    @pytest.mark.parametrize("url,expected", [
        ("https://www.twitch.tv/shroud", Platform.TWITCH),
        ("https://twitch.tv/videos/123", Platform.TWITCH),
        ("https://www.youtube.com/watch?v=abc", Platform.YOUTUBE),
        ("https://youtu.be/abc", Platform.YOUTUBE),
        ("https://kick.com/xqc", Platform.KICK),
        ("https://www.tiktok.com/@user/video/123", Platform.TIKTOK),
        ("https://twitter.com/user/status/123", Platform.X),
        ("https://x.com/user/status/123", Platform.X),
        ("https://example.com/video", Platform.UNKNOWN),
    ])
    def test_detection(self, url, expected):
        assert detect_platform(url) == expected


# ---------------------------------------------------------------------------
# extract_channel
# ---------------------------------------------------------------------------

class TestExtractChannel:
    def test_twitch(self):
        assert extract_channel("https://twitch.tv/shroud") == "shroud"

    def test_kick(self):
        assert extract_channel("https://kick.com/xqc") == "xqc"

    def test_youtube_at(self):
        assert extract_channel("https://youtube.com/@pewdiepie") == "pewdiepie"

    def test_youtube_watch(self):
        # M1 fix: extract video ID from watch URL instead of literal "watch"
        assert extract_channel("https://youtube.com/watch?v=dQw4w9WgXcQ") == "dQw4w9WgXcQ"

    def test_youtube_shorts(self):
        # Round-2 fix: Shorts have ID in path, not query param
        assert extract_channel("https://youtube.com/shorts/abc123") == "abc123"

    def test_youtube_embed(self):
        assert extract_channel("https://youtube.com/embed/xyz789") == "xyz789"

    def test_youtube_live(self):
        assert extract_channel("https://youtube.com/live/liveID") == "liveID"

    def test_tiktok(self):
        assert extract_channel("https://tiktok.com/@charli") == "charli"

    def test_empty_path(self):
        assert extract_channel("https://twitch.tv/") == "unknown"


# ---------------------------------------------------------------------------
# is_live_url
# ---------------------------------------------------------------------------

class TestIsLiveUrl:
    def test_channel_page_is_live(self):
        assert is_live_url("https://twitch.tv/shroud") is True

    def test_channel_with_vod_keyword_in_name(self):
        # C1 fix: /videogamedunkey should not match /video substring
        assert is_live_url("https://twitch.tv/videogamedunkey") is True

    def test_vod_is_not_live(self):
        assert is_live_url("https://twitch.tv/videos/123") is False

    def test_youtube_watch_is_not_live(self):
        assert is_live_url("https://youtube.com/watch?v=abc") is False

    def test_kick_video_is_not_live(self):
        assert is_live_url("https://kick.com/video/abc") is False


# ---------------------------------------------------------------------------
# safe_filename
# ---------------------------------------------------------------------------

class TestSafeFilename:
    def test_basic(self):
        name = safe_filename(Platform.TWITCH, "shroud")
        assert name.startswith("twitch_shroud_")
        assert name.endswith(".mp4")

    def test_string_platform(self):
        name = safe_filename("tiktok", "user")
        assert name.startswith("tiktok_user_")

    def test_special_chars_cleaned(self):
        name = safe_filename(Platform.KICK, "user/with<bad>chars")
        assert "/" not in name
        assert "<" not in name
        # m5 fix: ensure sanitized channel name is actually present
        assert "user_with_bad_chars" in name


# ---------------------------------------------------------------------------
# validate_crop
# ---------------------------------------------------------------------------

class TestValidateCrop:
    def test_valid_crop(self):
        assert validate_crop("640:480:1280:0") == "640:480:1280:0"
        assert validate_crop(" 1920:1080:0:0 ") == "1920:1080:0:0"

    @pytest.mark.parametrize("bad_val", [
        "640:480:0",
        "640:480:0:0:0",
        "640:480:0:0[cam];",
        "invalid",
        "-1:0:0:0",
        "640:480:x:y",
    ])
    def test_invalid_crop_raises(self, bad_val):
        with pytest.raises(ValueError, match="Invalid crop format"):
            validate_crop(bad_val)
