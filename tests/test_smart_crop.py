"""Tests for OpenCV smart facecam and layout detection."""

from pathlib import Path
from unittest.mock import MagicMock, patch

from clipmania.processing.layouts import Layout, build_filter_for_layout
from clipmania.processing.smart_crop import (
    FacecamDetectionResult,
    detect_facecam,
    has_opencv,
)


class TestLayoutEnumSmart:
    def test_from_string_hyphen(self):
        assert Layout.from_string("vertical-smart") == Layout.VERTICAL_SMART

    def test_from_string_underscore(self):
        assert Layout.from_string("vertical_smart") == Layout.VERTICAL_SMART

    def test_build_filter_for_smart_layout(self):
        filter_str, is_complex = build_filter_for_layout(Layout.VERTICAL_SMART)
        assert is_complex is True
        assert "boxblur" in filter_str


class TestSmartCropDetection:
    def test_has_opencv_returns_bool(self):
        assert isinstance(has_opencv(), bool)

    @patch("clipmania.processing.smart_crop.has_opencv", return_value=False)
    def test_detect_facecam_without_opencv_returns_none(self, _mock):
        result = detect_facecam(Path("dummy.mp4"))
        assert result is None

    @patch("clipmania.processing.smart_crop.has_opencv", return_value=True)
    @patch("clipmania.processing.smart_crop.probe")
    @patch("clipmania.processing.smart_crop.cv2")
    def test_detect_facecam_mocked_success(self, mock_cv2, mock_probe, _mock_has_cv):
        mock_probe.return_value = MagicMock(width=1920, height=1080, duration=20.0)

        mock_cascade = MagicMock()
        mock_cascade.empty.return_value = False
        # Return face in top-right corner: x=1400, y=100, w=150, h=150
        mock_cascade.detectMultiScale.return_value = [(1400, 100, 150, 150)]
        mock_cv2.CascadeClassifier.return_value = mock_cascade

        mock_cap = MagicMock()
        mock_cap.isOpened.return_value = True
        mock_frame = MagicMock()
        mock_cap.read.return_value = (True, mock_frame)
        mock_cv2.VideoCapture.return_value = mock_cap
        mock_cv2.data.haarcascades = ""

        res = detect_facecam(Path("dummy.mp4"), num_samples=3)
        assert res is not None
        assert isinstance(res, FacecamDetectionResult)
        assert res.position == "top-right"
        assert res.cam_crop.count(":") == 3
        assert res.game_crop == "1920:1080:0:0"
        assert res.confidence > 0.0

    @patch("clipmania.processing.smart_crop.has_opencv", return_value=True)
    @patch("clipmania.processing.smart_crop.probe")
    @patch("clipmania.processing.smart_crop.cv2")
    def test_detect_facecam_no_faces_found(self, mock_cv2, mock_probe, _mock_has_cv):
        mock_probe.return_value = MagicMock(width=1920, height=1080, duration=20.0)

        mock_cascade = MagicMock()
        mock_cascade.empty.return_value = False
        mock_cascade.detectMultiScale.return_value = []
        mock_cv2.CascadeClassifier.return_value = mock_cascade

        mock_cap = MagicMock()
        mock_cap.isOpened.return_value = True
        mock_cap.read.return_value = (True, MagicMock())
        mock_cv2.VideoCapture.return_value = mock_cap
        mock_cv2.data.haarcascades = ""

        res = detect_facecam(Path("dummy.mp4"), num_samples=3)
        assert res is None
