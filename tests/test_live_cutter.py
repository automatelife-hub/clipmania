"""Tests for live highlight cutter engine."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from clipmania.processing.layouts import Layout
from clipmania.processing.live_cutter import LiveCutter, LiveCutterConfig


@patch("clipmania.processing.live_cutter.clip_from_buffer")
@patch("clipmania.processing.live_cutter.process_clip")
def test_interval_timer_triggers_clip(mock_process, mock_clip, tmp_path):
    mock_clip.return_value = tmp_path / "raw.mp4"
    mock_process.return_value = tmp_path / "final.mp4"

    cfg = LiveCutterConfig(interval_seconds=10, cooldown_seconds=5)
    cutter = LiveCutter(buffer_dir=tmp_path / "buffer", output_dir=tmp_path / "out", config=cfg)

    cutter.last_clip_time = 0.0
    cutter.last_interval_time = 0.0
    result = cutter.check_interval_timer(current_time=15.0)

    assert result == tmp_path / "final.mp4"
    mock_clip.assert_called_once()
    mock_process.assert_called_once()
    assert cutter.last_clip_time == 15.0


def test_cooldown_prevents_duplicate_clipping(tmp_path):
    cfg = LiveCutterConfig(cooldown_seconds=60)
    cutter = LiveCutter(buffer_dir=tmp_path / "buffer", output_dir=tmp_path / "out", config=cfg)
    cutter.last_clip_time = 100.0
    assert cutter.can_clip(current_time=120.0) is False
    assert cutter.can_clip(current_time=160.0) is True


@patch("clipmania.processing.live_cutter.measure_segment_volume")
@patch("clipmania.processing.live_cutter.clip_from_buffer")
@patch("clipmania.processing.live_cutter.process_clip")
def test_audio_energy_trigger_detects_spike(mock_process, mock_clip, mock_volume, tmp_path):
    mock_clip.return_value = tmp_path / "raw.mp4"
    mock_process.return_value = tmp_path / "final.mp4"

    cfg = LiveCutterConfig(
        interval_seconds=300,
        cooldown_seconds=60,
        enable_audio_trigger=True,
        energy_threshold_multiplier=2.0,
    )
    cutter = LiveCutter(buffer_dir=tmp_path / "buffer", output_dir=tmp_path / "out", config=cfg)
    cutter.last_clip_time = 0.0

    # Prime running average with normal background speech (-25 dB)
    mock_volume.return_value = -25.0
    cutter.evaluate_new_segment(tmp_path / "seg1.ts", current_time=10.0)
    cutter.evaluate_new_segment(tmp_path / "seg2.ts", current_time=20.0)
    assert mock_clip.call_count == 0

    # Shout / loud scream event: -15 dB (10 dB higher, which is > 3x linear amplitude / > 2x threshold)
    mock_volume.return_value = -15.0
    result = cutter.evaluate_new_segment(tmp_path / "seg3.ts", current_time=70.0)

    assert result == tmp_path / "final.mp4"
    assert mock_clip.call_count == 1
    assert cutter.last_clip_time == 70.0


@patch("clipmania.processing.live_cutter.measure_segment_volume")
def test_audio_energy_trigger_ignores_below_threshold(mock_volume, tmp_path):
    cfg = LiveCutterConfig(
        interval_seconds=300,
        cooldown_seconds=60,
        enable_audio_trigger=True,
        energy_threshold_multiplier=2.0,
    )
    cutter = LiveCutter(buffer_dir=tmp_path / "buffer", output_dir=tmp_path / "out", config=cfg)
    cutter.last_clip_time = 0.0

    mock_volume.return_value = -25.0
    cutter.evaluate_new_segment(tmp_path / "seg1.ts", current_time=10.0)
    mock_volume.return_value = -24.0
    result = cutter.evaluate_new_segment(tmp_path / "seg2.ts", current_time=70.0)

    assert result is None
    assert cutter.last_clip_time == 0.0


@patch("clipmania.processing.live_cutter.clip_from_buffer")
def test_trigger_clip_handles_buffer_error(mock_clip, tmp_path):
    mock_clip.side_effect = RuntimeError("Buffer playlist not found")
    cfg = LiveCutterConfig(interval_seconds=10)
    cutter = LiveCutter(buffer_dir=tmp_path / "buffer", output_dir=tmp_path / "out", config=cfg)

    result = cutter.trigger_clip(reason="test", current_time=10.0)
    assert result is None


def test_measure_segment_volume_missing_file(tmp_path):
    from clipmania.processing.live_cutter import measure_segment_volume
    assert measure_segment_volume(tmp_path / "nonexistent.ts") is None


@patch("subprocess.run")
def test_measure_segment_volume_ffmpeg_parsing(mock_run, tmp_path):
    from clipmania.processing.live_cutter import measure_segment_volume
    test_seg = tmp_path / "test.ts"
    test_seg.write_text("dummy")

    mock_run.return_value = MagicMock(
        stderr="[Parsed_volumedetect_0 @ 000001] max_volume: -2.1 dB\n[Parsed_volumedetect_0 @ 000001] mean_volume: -18.7 dB\n",
        returncode=0,
    )
    vol = measure_segment_volume(test_seg)
    assert vol == -18.7


@patch("clipmania.processing.live_cutter.clip_from_buffer")
@patch("clipmania.processing.live_cutter.process_clip_single_pass")
def test_trigger_clip_fast_single_pass(mock_single_pass, mock_clip, tmp_path):
    mock_clip.return_value = tmp_path / "raw.mp4"
    mock_single_pass.return_value = tmp_path / "out_fast.mp4"

    cfg = LiveCutterConfig(fast_single_pass=True, channel="shroud")
    cutter = LiveCutter(buffer_dir=tmp_path / "buffer", output_dir=tmp_path / "out", config=cfg)

    result = cutter.trigger_clip(reason="manual", current_time=100.0)
    assert result == tmp_path / "out_fast.mp4"
    mock_single_pass.assert_called_once()

