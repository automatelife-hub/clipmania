"""Tests for autocrop module and CropRegion data structures."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from clipmania.processing.autocrop import (
    CropRegion,
    auto_detect_crops,
    detect_facecam,
    detect_gameplay,
    has_opencv,
)


class TestCropRegion:
    def test_crop_str(self):
        cr = CropRegion(w=640, h=480, x=1280, y=0)
        assert cr.crop_str == "640:480:1280:0"

    def test_from_str_valid(self):
        cr = CropRegion.from_str("1920:1080:0:0")
        assert cr.w == 1920
        assert cr.h == 1080
        assert cr.x == 0
        assert cr.y == 0

    def test_from_str_invalid_raises(self):
        with pytest.raises(ValueError):
            CropRegion.from_str("1920:1080:0")


class TestAutoCropFunctions:
    def test_has_opencv_bool(self):
        assert isinstance(has_opencv(), bool)

    @patch("clipmania.processing.autocrop.has_opencv", return_value=False)
    def test_detect_facecam_no_cv_returns_none(self, _mock):
        assert detect_facecam(Path("dummy.mp4")) is None

    @patch("clipmania.processing.autocrop.probe")
    def test_detect_gameplay_returns_dimensions(self, mock_probe):
        mock_probe.return_value = MagicMock(width=1920, height=1080)
        region = detect_gameplay(Path("dummy.mp4"))
        assert region.w == 1920
        assert region.h == 1080
        assert region.x == 0
        assert region.y == 0

    @patch("clipmania.processing.autocrop.probe")
    def test_detect_gameplay_odd_dimensions_even_aligned(self, mock_probe):
        mock_probe.return_value = MagicMock(width=1921, height=1081)
        region = detect_gameplay(Path("dummy.mp4"))
        assert region.w == 1920
        assert region.h == 1080

    @patch("clipmania.processing.autocrop.detect_facecam", return_value=None)
    @patch("clipmania.processing.autocrop.detect_gameplay")
    @patch("clipmania.processing.autocrop.probe")
    def test_auto_detect_crops_fallback_1080p(self, mock_probe, mock_gameplay, _mock_facecam):
        """Fallback cam crop fits within 1920x1080 source."""
        mock_probe.return_value = MagicMock(width=1920, height=1080)
        mock_gameplay.return_value = CropRegion(1920, 1080, 0, 0)
        cam, game = auto_detect_crops(Path("dummy.mp4"))
        # Cam should be within source bounds
        assert cam.x + cam.w <= 1920
        assert cam.y + cam.h <= 1080
        assert game.crop_str == "1920:1080:0:0"

    @patch("clipmania.processing.autocrop.detect_facecam", return_value=None)
    @patch("clipmania.processing.autocrop.detect_gameplay")
    @patch("clipmania.processing.autocrop.probe")
    def test_auto_detect_crops_fallback_720p(self, mock_probe, mock_gameplay, _mock_facecam):
        """Fallback cam crop is clamped to 1280x720 source — never exceeds bounds."""
        mock_probe.return_value = MagicMock(width=1280, height=720)
        mock_gameplay.return_value = CropRegion(1280, 720, 0, 0)
        cam, game = auto_detect_crops(Path("dummy.mp4"))
        assert cam.x + cam.w <= 1280, f"Cam extends beyond right edge: {cam}"
        assert cam.y + cam.h <= 720, f"Cam extends beyond bottom edge: {cam}"
        assert game.crop_str == "1280:720:0:0"

    @patch("clipmania.processing.autocrop.detect_facecam", return_value=None)
    @patch("clipmania.processing.autocrop.detect_gameplay")
    @patch("clipmania.processing.autocrop.probe")
    def test_auto_detect_crops_fallback_small_source(self, mock_probe, mock_gameplay, _mock_facecam):
        """Fallback cam crop handles very small source (e.g. 480x360)."""
        mock_probe.return_value = MagicMock(width=480, height=360)
        mock_gameplay.return_value = CropRegion(480, 360, 0, 0)
        cam, game = auto_detect_crops(Path("dummy.mp4"))
        assert cam.x + cam.w <= 480
        assert cam.y + cam.h <= 360
        # Dimensions should be even-aligned
        assert cam.w % 2 == 0
        assert cam.h % 2 == 0
        assert cam.x % 2 == 0

    @patch("clipmania.processing.autocrop.detect_facecam")
    @patch("clipmania.processing.autocrop.detect_gameplay")
    def test_auto_detect_crops_uses_detected_cam(self, mock_gameplay, mock_facecam):
        """When detection succeeds, the detected cam is used (no fallback)."""
        detected = CropRegion(w=400, h=300, x=100, y=50)
        mock_facecam.return_value = detected
        mock_gameplay.return_value = CropRegion(1920, 1080, 0, 0)
        cam, game = auto_detect_crops(Path("dummy.mp4"))
        assert cam == detected
