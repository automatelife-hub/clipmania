"""Tests for clipmania.ingestion.metadata."""

import json
from unittest.mock import MagicMock, patch

import pytest

from clipmania.ingestion.metadata import (
    SourceMetadata,
    VideoChapter,
    fetch_metadata,
    find_chapter,
)


class TestVideoChapter:
    def test_duration(self):
        ch = VideoChapter(index=1, title="Intro", start_time=10.0, end_time=45.5)
        assert ch.duration == 35.5

    def test_zero_duration(self):
        ch = VideoChapter(index=1, title="Marker", start_time=10.0, end_time=5.0)
        assert ch.duration == 0.0


class TestFindChapter:
    @pytest.fixture
    def chapters(self):
        return [
            VideoChapter(index=1, title="Introduction & Setup", start_time=0.0, end_time=60.0),
            VideoChapter(index=2, title="Boss Fight Phase 1", start_time=60.0, end_time=180.0),
            VideoChapter(index=3, title="Victory & Outro", start_time=180.0, end_time=240.0),
        ]

    def test_find_by_index_int(self, chapters):
        assert find_chapter(chapters, 2) == chapters[1]

    def test_find_by_index_str(self, chapters):
        assert find_chapter(chapters, "3") == chapters[2]

    def test_find_by_title_keyword(self, chapters):
        assert find_chapter(chapters, "Boss") == chapters[1]
        assert find_chapter(chapters, "intro") == chapters[0]

    def test_not_found(self, chapters):
        assert find_chapter(chapters, "NonExistent") is None
        assert find_chapter(chapters, 99) is None

    def test_empty_list(self):
        assert find_chapter([], "1") is None


class TestFetchMetadata:
    @patch("subprocess.run")
    def test_fetch_success(self, mock_run):
        mock_output = {
            "title": "Epic Gameplay Highlight",
            "uploader": "Gamer123",
            "duration": 300.0,
            "view_count": 50000,
            "upload_date": "20260901",
            "thumbnail": "https://example.com/thumb.jpg",
            "chapters": [
                {"title": "Start", "start_time": 0.0, "end_time": 100.0},
                {"title": "Climax", "start_time": 100.0, "end_time": 300.0},
            ],
            "formats": [
                {"height": 1080, "fps": 60},
                {"height": 720, "fps": 30},
                {"height": 480, "fps": 30},
            ],
            "is_live": False,
        }
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps(mock_output),
            stderr="",
        )

        meta = fetch_metadata("https://youtube.com/watch?v=123")
        assert meta.title == "Epic Gameplay Highlight"
        assert meta.uploader == "Gamer123"
        assert meta.duration == 300.0
        assert meta.view_count == 50000
        assert len(meta.chapters) == 2
        assert meta.chapters[0].title == "Start"
        assert "1080p60" in meta.available_resolutions
        assert "720p" in meta.available_resolutions

    @patch("subprocess.run")
    def test_fetch_failure_raises(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=1,
            stdout="",
            stderr="Video unavailable",
        )
        with pytest.raises(RuntimeError, match="Failed to fetch metadata"):
            fetch_metadata("https://youtube.com/watch?v=bad")
