"""Tests for clipmania.platforms."""

import pytest

from clipmania.platforms import (
    PLATFORM_SPECS,
    AspectRatio,
    get_spec,
    list_platforms,
    validate_output,
)


class TestPlatformRegistry:
    """Platform specs are complete and consistent."""

    def test_all_expected_platforms_exist(self):
        expected = {"tiktok", "youtube_shorts", "kick", "twitch", "x"}
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


class TestListPlatforms:
    def test_returns_sorted(self):
        platforms = list_platforms()
        assert platforms == sorted(platforms)

    def test_count(self):
        assert len(list_platforms()) == 5
