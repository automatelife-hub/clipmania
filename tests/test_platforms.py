"""Tests for clipmania.platforms."""

import pytest

from clipmania.platforms import (
    PLATFORM_SPECS,
    get_spec,
    list_platforms,
    validate_output,
)


class TestPlatformRegistry:
    """Platform specs are complete and consistent."""

    def test_all_expected_platforms_exist(self):
        expected = {"tiktok", "youtube", "youtube_shorts", "kick", "twitch", "x", "rumble"}
        assert expected == set(PLATFORM_SPECS.keys())

    def test_all_specs_have_required_fields(self):
        for key, spec in PLATFORM_SPECS.items():
            assert spec.name, f"{key} has no name"
            assert spec.aspect_ratios, f"{key} has no aspect ratios"
            assert spec.width > 0, f"{key} has invalid width"
            assert spec.height > 0, f"{key} has invalid height"
            assert spec.codec, f"{key} has no codec"
            assert spec.container, f"{key} has no container"
            assert spec.audio_lufs < 0, f"{key} has invalid LUFS"

    def test_all_use_h264_mp4(self):
        for key, spec in PLATFORM_SPECS.items():
            assert spec.codec == "libx264", f"{key} codec is {spec.codec}"
            assert spec.container == "mp4", f"{key} container is {spec.container}"


class TestGetSpec:
    def test_valid_platform(self):
        spec = get_spec("tiktok")
        assert spec.name == "TikTok"

    def test_rumble_spec_exists(self):
        spec = get_spec("rumble")
        assert spec.name == "Rumble"
        assert spec.width == 1920
        assert spec.height == 1080

    def test_case_insensitive(self):
        spec = get_spec("TikTok")
        assert spec.name == "TikTok"

    def test_unknown_raises(self):
        with pytest.raises(ValueError, match="Unknown platform"):
            get_spec("instagram")


class TestValidateOutput:
    def test_valid_tiktok(self):
        spec = get_spec("tiktok")
        warnings = validate_output(
            {"duration": 45.0, "width": 1080, "height": 1920},
            spec,
        )
        assert warnings == []

    def test_duration_exceeded(self):
        spec = get_spec("tiktok")
        warnings = validate_output(
            {"duration": 120.0, "width": 1080, "height": 1920},
            spec,
        )
        assert any("Duration" in w for w in warnings)

    def test_wrong_aspect_ratio(self):
        spec = get_spec("tiktok")
        warnings = validate_output(
            {"duration": 30.0, "width": 1920, "height": 1080},
            spec,
        )
        assert any("Aspect ratio" in w for w in warnings)

    def test_unlimited_duration(self):
        spec = get_spec("twitch")  # max_duration_seconds is None
        warnings = validate_output(
            {"duration": 99999.0, "width": 1920, "height": 1080},
            spec,
        )
        assert warnings == []

    def test_min_width_warning(self):
        spec = get_spec("tiktok")
        warnings = validate_output(
            {"duration": 30.0, "width": 480, "height": 854},
            spec,
        )
        assert any("minimum requirement" in w for w in warnings)

    def test_file_size_limit_exceeded(self):
        spec = get_spec("kick")
        large_size = 300 * 1024 * 1024  # 300MB > 250MB
        warnings = validate_output(
            {"duration": 30.0, "width": 1920, "height": 1080},
            spec,
            file_size_bytes=large_size,
        )
        assert any("File size" in w for w in warnings)


class TestRecommendPlatform:
    def test_recommend_vertical_short(self):
        from clipmania.platforms import recommend_platform
        result = recommend_platform(1080, 1920, 45.0)
        assert "tiktok" in result
        assert "youtube_shorts" in result

    def test_recommend_vertical_long(self):
        from clipmania.platforms import recommend_platform
        result = recommend_platform(1080, 1920, 90.0)
        assert "x" in result
        # TikTok and YouTube Shorts are 60s max, should be excluded
        assert "tiktok" not in result
        assert "youtube_shorts" not in result

    def test_recommend_horizontal_short(self):
        from clipmania.platforms import recommend_platform
        result = recommend_platform(1920, 1080, 45.0)
        assert "kick" in result
        assert "twitch" in result

    def test_recommend_horizontal_long(self):
        from clipmania.platforms import recommend_platform
        result = recommend_platform(1920, 1080, 120.0)
        assert "twitch" in result
        # kick is 60s max
        assert "kick" not in result

    def test_recommend_vertical_over_140s(self):
        """A 141s vertical video exceeds X's 140s limit — no platform fits."""
        from clipmania.platforms import recommend_platform
        result = recommend_platform(1080, 1920, 141.0)
        assert "x" not in result

    def test_recommend_returns_empty_for_zero_dimensions(self):
        from clipmania.platforms import recommend_platform
        assert recommend_platform(0, 0, 30.0) == []

    def test_recommend_returns_list(self):
        from clipmania.platforms import recommend_platform
        result = recommend_platform(1080, 1920, 30.0)
        assert isinstance(result, list)


class TestListPlatforms:
    def test_returns_sorted(self):
        platforms = list_platforms()
        assert platforms == sorted(platforms)

    def test_count(self):
        assert len(list_platforms()) == 7
