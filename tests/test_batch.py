"""Tests for clipmania.processing.batch."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from clipmania.processing.batch import (
    ClipJob,
    load_batch_config,
    process_batch,
)


class TestLoadBatchConfig:
    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            load_batch_config(tmp_path / "missing.json")

    def test_unsupported_extension_raises(self, tmp_path):
        bad = tmp_path / "queue.yaml"
        bad.write_text("clips: []")
        with pytest.raises(ValueError, match="Unsupported batch file format"):
            load_batch_config(bad)

    def test_load_json_valid(self, tmp_path):
        cfg_file = tmp_path / "batch.json"
        data = {
            "clips": [
                {"url": "https://youtube.com/watch?v=1", "start": "0:10", "end": "0:30", "platform": "tiktok"},
                {"url": "https://youtube.com/watch?v=2", "chapter": "Boss", "layout": "vertical-blur"},
            ]
        }
        cfg_file.write_text(json.dumps(data), encoding="utf-8")

        jobs = load_batch_config(cfg_file)
        assert len(jobs) == 2
        assert jobs[0].url == "https://youtube.com/watch?v=1"
        assert jobs[0].start == "0:10"
        assert jobs[0].platform == "tiktok"
        assert jobs[1].chapter == "Boss"
        assert jobs[1].layout == "vertical-blur"

    def test_empty_json_raises(self, tmp_path):
        cfg_file = tmp_path / "empty.json"
        cfg_file.write_text(json.dumps({"clips": []}), encoding="utf-8")
        with pytest.raises(ValueError, match="No valid clipping jobs found"):
            load_batch_config(cfg_file)


class TestProcessBatch:
    @patch("clipmania.processing.batch._execute_single_job")
    def test_process_batch_success(self, mock_exec, tmp_path):
        out_clip = tmp_path / "clip1.mp4"
        out_clip.write_text("dummy")
        mock_exec.return_value = out_clip

        jobs = [
            ClipJob(url="https://youtube.com/watch?v=1", start="0:10", end="0:20"),
            ClipJob(url="https://youtube.com/watch?v=2", start="1:00", end="1:30"),
        ]

        results = process_batch(jobs, workers=2, output_dir=tmp_path)
        assert len(results) == 2
        assert mock_exec.call_count == 2


class TestClipJob:
    def test_output_filename_field(self):
        """output_filename is accepted and stored on ClipJob."""
        job = ClipJob(url="https://example.com", output_filename="my_clip.mp4")
        assert job.output_filename == "my_clip.mp4"

    def test_output_filename_defaults_none(self):
        job = ClipJob(url="https://example.com")
        assert job.output_filename is None

