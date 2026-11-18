# Live Stream Channel Watcher & Live Clipping Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enable automated multi-platform channel monitoring (YouTube, Twitch, Kick, Rumble) with automated live highlight clipping, smart vertical formatting, and export.

**Architecture:** A lightweight polling watcher monitors creator channels via `streamlink --json`. When a channel transitions to live, it starts ClipMania's rolling HLS buffer with Streamlink reconnect resilience, activates an event-driven live cutter (evaluating audio energy peaks and interval schedules), and passes extracted clips through the existing `Layout.VERTICAL_SMART` pipeline.

**Tech Stack:** Python 3.12, Streamlink CLI, FFmpeg, Click, Rich, OpenCV, Pytest.

**Spec:** [docs/superpowers/specs/2026-11-17-live-clipping-watcher-design.md](file:///c:/Users/Neste/Desktop/clipmania/docs/superpowers/specs/2026-11-17-live-clipping-watcher-design.md)

## Global Constraints
- Target platforms: YouTube, Twitch, Kick, Rumble.
- No direct platform API keys required (relies on Streamlink plugins).
- Windows filesystem safety: all file operations use `Path.replace()` instead of `Path.rename()`, sanitized filenames via `safe_filename`.
- Subprocess safety: all spawned `Popen` instances must be wrapped in `try/finally` with `proc.kill()` and `proc.wait(timeout=5)`.
- Existing test suite (196 tests) must remain 100% green at every task boundary.

## Review Focus
1. Broadcaster goes offline mid-stream: watcher must catch stream termination, kill buffer processes, and return cleanly to polling.
2. Rumble URL variations: handle `rumble.com/c/ChannelName`, `rumble.com/user/UserName`, and direct `rumble.com/v...` stream URLs.
3. Streamlink returns unparseable or error JSON when offline: must not raise unhandled exception or crash the daemon loop.
4. Audio-less stream segment: live cutter energy spike detector must handle silent or missing audio without crashing.
5. Concurrent clip triggers: prevent duplicate clipping of the same live window if interval timer and audio peak fire simultaneously.

---

### Task 1: Rumble Platform Integration

**Files:**
- Modify: `clipmania/utils.py:12-21, 88-105, 120-155`
- Modify: `clipmania/platforms.py:41-115`
- Test: `tests/test_platforms.py`
- Test: `tests/test_utils.py`

**Interfaces:**
- Consumes: None
- Produces: `Platform.RUMBLE`, `PLATFORM_SPECS["rumble"]`, `detect_platform("https://rumble.com/...")`, `extract_channel("https://rumble.com/c/Channel")`

- [ ] **Step 1: Write failing tests for Rumble URL detection and channel extraction**

In `tests/test_utils.py`:
```python
def test_detect_rumble():
    assert detect_platform("https://rumble.com/c/Bongino") == Platform.RUMBLE
    assert detect_platform("https://www.rumble.com/user/GlennGreenwald") == Platform.RUMBLE

def test_extract_channel_rumble():
    assert extract_channel("https://rumble.com/c/Bongino") == "Bongino"
    assert extract_channel("https://rumble.com/user/GlennGreenwald") == "GlennGreenwald"
    assert extract_channel("https://rumble.com/v12345-live-stream.html") == "v12345-live-stream.html"
```

In `tests/test_platforms.py`:
```python
def test_rumble_spec_exists():
    spec = get_spec("rumble")
    assert spec.name == "Rumble"
    assert spec.width == 1920
    assert spec.height == 1080
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_utils.py -k rumble tests/test_platforms.py -k rumble -v`  
Expected: FAIL with `AttributeError: type object 'Platform' has no attribute 'RUMBLE'`

- [ ] **Step 3: Implement Rumble platform support**

In `clipmania/utils.py`:
1. Add `RUMBLE = "rumble"` to `Platform` enum.
2. Add `"rumble.com": Platform.RUMBLE`, `"www.rumble.com": Platform.RUMBLE` to `_PLATFORM_DOMAINS`.
3. In `extract_channel()`:
   If `platform == Platform.RUMBLE`:
   - `/c/<channel>` $\rightarrow$ `<channel>`
   - `/user/<channel>` $\rightarrow$ `<channel>`
   - otherwise return first path segment.

In `clipmania/platforms.py`:
Add `"rumble"` spec to `PLATFORM_SPECS` (1920x1080 landscape, unlimited duration, H.264/AAC).

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_utils.py tests/test_platforms.py -v`  
Expected: PASS (all tests pass, update expected platform count from 6 to 7 in `test_platforms.py`).

- [ ] **Step 5: Commit**

```bash
git add clipmania/utils.py clipmania/platforms.py tests/test_utils.py tests/test_platforms.py
git commit -m "feat(platforms): add native Rumble platform support"
```

---

### Task 2: Stream Status Prober (`stream_probe.py`)

**Files:**
- Create: `clipmania/ingestion/stream_probe.py`
- Test: `tests/test_stream_probe.py`

**Interfaces:**
- Consumes: None
- Produces:
  ```python
  @dataclass(frozen=True)
  class StreamStatus:
      is_live: bool
      url: str
      title: str | None = None
      author: str | None = None
      category: str | None = None
      qualities: tuple[str, ...] = ()
      error: str | None = None

  def probe_stream_status(url: str, timeout: int = 15) -> StreamStatus: ...
  ```

- [ ] **Step 1: Write the failing tests for `probe_stream_status`**

In `tests/test_stream_probe.py`:
```python
from unittest.mock import MagicMock, patch
from clipmania.ingestion.stream_probe import StreamStatus, probe_stream_status

@patch("subprocess.run")
def test_probe_stream_live_success(mock_run):
    mock_run.return_value = MagicMock(
        returncode=0,
        stdout='{"plugin": "twitch", "metadata": {"title": "Live Gaming", "author": "shroud", "category": "Valorant"}, "streams": {"720p": {}, "1080p": {}, "best": {}}}',
        stderr="",
    )
    status = probe_stream_status("https://twitch.tv/shroud")
    assert status.is_live is True
    assert status.title == "Live Gaming"
    assert status.author == "shroud"
    assert "best" in status.qualities
    assert status.error is None

@patch("subprocess.run")
def test_probe_stream_offline(mock_run):
    mock_run.return_value = MagicMock(
        returncode=1,
        stdout='{"error": "No playable streams found on this URL: https://twitch.tv/shroud"}',
        stderr="",
    )
    status = probe_stream_status("https://twitch.tv/shroud")
    assert status.is_live is False
    assert status.qualities == ()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/test_stream_probe.py -v`  
Expected: FAIL with `ModuleNotFoundError: No module named 'clipmania.ingestion.stream_probe'`

- [ ] **Step 3: Implement `probe_stream_status` in `clipmania/ingestion/stream_probe.py`**

Execute `["streamlink", "--json", url]` with `timeout=timeout`. Parse stdout JSON:
- If `"streams"` in data and bool(data["streams"]): `is_live=True`, extract metadata and sorted available streams.
- If `"error"` in data or `returncode != 0`: `is_live=False`.
- Handle `subprocess.TimeoutExpired`, `json.JSONDecodeError`, and `FileNotFoundError` gracefully returning `is_live=False` with error message.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv\Scripts\python.exe -m pytest tests/test_stream_probe.py -v`  
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add clipmania/ingestion/stream_probe.py tests/test_stream_probe.py
git commit -m "feat(ingestion): add Streamlink JSON stream status prober"
```

---

### Task 3: Streamlink Live Buffer Reconnect Resilience

**Files:**
- Modify: `clipmania/ingestion/streamlink_live.py:206-220`
- Test: `tests/test_streamlink_live.py`

**Interfaces:**
- Consumes: `streamlink` CLI
- Produces: `start_live_buffer(url, ..., retry_streams=5, retry_max=5)`

- [ ] **Step 1: Write the failing test for resilient buffer startup**

In `tests/test_streamlink_live.py`:
```python
from unittest.mock import MagicMock, patch
from clipmania.ingestion.streamlink_live import start_live_buffer

@patch("subprocess.Popen")
@patch("clipmania.ingestion.streamlink_live._acquire_lock")
@patch("clipmania.ingestion.streamlink_live._release_lock")
def test_start_live_buffer_includes_retry_flags(mock_rel, mock_acq, mock_popen, tmp_path):
    mock_sl = MagicMock(pid=1001, poll=lambda: None)
    mock_ff = MagicMock(pid=1002, poll=lambda: None)
    mock_popen.side_effect = [mock_sl, mock_ff]

    start_live_buffer("https://kick.com/xqc", buffer_dir=tmp_path, retry_streams=5, retry_max=3)

    sl_cmd = mock_popen.call_args_list[0][0][0]
    assert "--retry-streams" in sl_cmd
    assert "5" in sl_cmd
    assert "--retry-max" in sl_cmd
    assert "3" in sl_cmd
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/test_streamlink_live.py -k test_start_live_buffer_includes_retry_flags -v`  
Expected: FAIL with `TypeError: start_live_buffer() got an unexpected keyword argument 'retry_streams'`

- [ ] **Step 3: Update `start_live_buffer` parameters and command building**

In `clipmania/ingestion/streamlink_live.py`:
Add `retry_streams: int = 5` and `retry_max: int = 0` (0 = infinite retries if stream drops) to `start_live_buffer()`.
Add `["--retry-streams", str(retry_streams), "--retry-max", str(retry_max)]` to `sl_cmd`.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv\Scripts\python.exe -m pytest tests/test_streamlink_live.py -v`  
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add clipmania/ingestion/streamlink_live.py tests/test_streamlink_live.py
git commit -m "feat(ingestion): add stream retry resilience to live buffer startup"
```

---

### Task 4: Live Highlight Cutter Engine (`live_cutter.py`)

**Files:**
- Create: `clipmania/processing/live_cutter.py`
- Test: `tests/test_live_cutter.py`

**Interfaces:**
- Consumes:
  - `clipmania.ingestion.streamlink_live.clip_from_buffer`
  - `clipmania.processing.pipeline.process_clip`
  - `clipmania.processing.layouts.Layout`
- Produces:
  ```python
  @dataclass
  class LiveCutterConfig:
      clip_window_seconds: int = 60
      interval_seconds: int = 300  # 5 minutes
      enable_audio_trigger: bool = True
      energy_threshold_multiplier: float = 2.0  # multiplier over running average RMS
      cooldown_seconds: int = 120  # min time between clips

  class LiveCutter:
      def __init__(self, buffer_dir: Path, output_dir: Path, config: LiveCutterConfig, ...): ...
      def evaluate_new_segment(self, segment_path: Path) -> Path | None: ...
      def check_interval_timer(self) -> Path | None: ...
  ```

- [ ] **Step 1: Write the failing tests for interval and audio energy clipping**

In `tests/test_live_cutter.py`:
```python
from unittest.mock import MagicMock, patch
from pathlib import Path
from clipmania.processing.live_cutter import LiveCutter, LiveCutterConfig

@patch("clipmania.processing.live_cutter.clip_from_buffer")
@patch("clipmania.processing.live_cutter.process_clip")
def test_interval_timer_triggers_clip(mock_process, mock_clip, tmp_path):
    mock_clip.return_value = tmp_path / "raw.mp4"
    mock_process.return_value = tmp_path / "final.mp4"

    cfg = LiveCutterConfig(interval_seconds=10, cooldown_seconds=5)
    cutter = LiveCutter(buffer_dir=tmp_path / "buffer", output_dir=tmp_path / "out", config=cfg)

    # First check: initialized
    cutter.last_clip_time = 0.0  # Simulate 10s elapsed
    result = cutter.check_interval_timer(current_time=15.0)
    assert result == tmp_path / "final.mp4"
    mock_clip.assert_called_once()
    mock_process.assert_called_once()

def test_cooldown_prevents_duplicate_clipping(tmp_path):
    cfg = LiveCutterConfig(cooldown_seconds=60)
    cutter = LiveCutter(buffer_dir=tmp_path / "buffer", output_dir=tmp_path / "out", config=cfg)
    cutter.last_clip_time = 100.0
    assert cutter.can_clip(current_time=120.0) is False
    assert cutter.can_clip(current_time=165.0) is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/test_live_cutter.py -v`  
Expected: FAIL with `ModuleNotFoundError: No module named 'clipmania.processing.live_cutter'`

- [ ] **Step 3: Implement `LiveCutter` in `clipmania/processing/live_cutter.py`**

1. Maintain running average audio volume (via `ffprobe` or quick RMS measurement on incoming `.ts` audio packet).
2. Maintain `last_clip_time` and enforce `cooldown_seconds` so interval timer and audio peaks don't clip redundant windows.
3. On trigger, call `clip_from_buffer(seconds=cfg.clip_window_seconds)` and `process_clip(layout=Layout.VERTICAL_SMART)`.
4. Return Path of final clip, or `None` if no trigger fired.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv\Scripts\python.exe -m pytest tests/test_live_cutter.py -v`  
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add clipmania/processing/live_cutter.py tests/test_live_cutter.py
git commit -m "feat(processing): add live highlight cutter with interval and audio energy trigger"
```

---

### Task 5: Live Channel Watcher Daemon (`live_watcher.py`)

**Files:**
- Create: `clipmania/ingestion/live_watcher.py`
- Test: `tests/test_live_watcher.py`

**Interfaces:**
- Consumes:
  - `clipmania.ingestion.stream_probe.probe_stream_status`
  - `clipmania.ingestion.streamlink_live.start_live_buffer`
  - `clipmania.ingestion.streamlink_live.stop_live_buffer`
  - `clipmania.processing.live_cutter.LiveCutter`
- Produces:
  ```python
  class WatcherState(Enum):
      OFFLINE = "offline"
      STARTING = "starting"
      LIVE = "live"
      STOPPED = "stopped"

  class LiveWatcher:
      def __init__(self, url: str, check_interval: int = 30, ...): ...
      def poll_once(self) -> WatcherState: ...
      def run(self, max_iterations: int | None = None) -> None: ...
      def stop(self) -> None: ...
  ```

- [ ] **Step 1: Write the failing tests for watcher state transitions**

In `tests/test_live_watcher.py`:
```python
from unittest.mock import MagicMock, patch
from clipmania.ingestion.live_watcher import LiveWatcher, WatcherState
from clipmania.ingestion.stream_probe import StreamStatus

@patch("clipmania.ingestion.live_watcher.probe_stream_status")
@patch("clipmania.ingestion.live_watcher.start_live_buffer")
@patch("clipmania.ingestion.live_watcher.stop_live_buffer")
def test_watcher_transitions_offline_to_live(mock_stop, mock_start, mock_probe, tmp_path):
    mock_probe.side_effect = [
        StreamStatus(is_live=False, url="https://twitch.tv/user"),
        StreamStatus(is_live=True, url="https://twitch.tv/user", title="Stream Live", qualities=("best",)),
    ]
    watcher = LiveWatcher("https://twitch.tv/user", check_interval=1, buffer_dir=tmp_path)

    # First poll: offline
    state1 = watcher.poll_once()
    assert state1 == WatcherState.OFFLINE
    mock_start.assert_not_called()

    # Second poll: went live!
    state2 = watcher.poll_once()
    assert state2 == WatcherState.LIVE
    mock_start.assert_called_once()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/test_live_watcher.py -v`  
Expected: FAIL with `ModuleNotFoundError: No module named 'clipmania.ingestion.live_watcher'`

- [ ] **Step 3: Implement `LiveWatcher` in `clipmania/ingestion/live_watcher.py`**

- Polling loop tracking `WatcherState`.
- Spawns `start_live_buffer()` and initializes `LiveCutter` upon detecting live stream.
- In `poll_once()` during `LIVE` state: checks `get_buffer_status()` and `probe_stream_status()`. If stream has ended, executes `stop_live_buffer()` and reverts to `OFFLINE`.
- Graceful shutdown hook on `stop()` ensuring buffer processes are cleanly terminated.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv\Scripts\python.exe -m pytest tests/test_live_watcher.py -v`  
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add clipmania/ingestion/live_watcher.py tests/test_live_watcher.py
git commit -m "feat(ingestion): add live channel watcher daemon"
```

---

### Task 6: CLI `watch` Subcommand & Interactive Dashboard

**Files:**
- Modify: `clipmania/cli.py`
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: `clipmania.ingestion.live_watcher.LiveWatcher`
- Produces: CLI subcommand `clipmania watch <URL>`

- [ ] **Step 1: Write failing CLI tests for `watch` command**

In `tests/test_cli.py`:
```python
@patch("clipmania.ingestion.live_watcher.LiveWatcher.run")
def test_watch_cli_invocation(mock_run, runner):
    result = runner.invoke(main, [
        "watch", "https://rumble.com/c/Bongino",
        "--check-interval", "15",
        "--clip-interval", "300",
    ])
    assert result.exit_code == 0
    mock_run.assert_called_once()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/test_cli.py -k test_watch_cli_invocation -v`  
Expected: FAIL with `No such command 'watch'`

- [ ] **Step 3: Add `watch` command in `clipmania/cli.py`**

```python
@main.command("watch")
@click.argument("url")
@click.option("--check-interval", default=30, help="Seconds between live status checks.")
@click.option("--clip-interval", default=300, help="Seconds between auto-cut clips.")
@click.option("--window", default=60, help="Duration of clips extracted from live buffer.")
@click.option("--layout", type=click.Choice([l.value for l in Layout]), default="vertical-smart")
@click.option("--platform", default="tiktok")
@click.option("--audio-trigger/--no-audio-trigger", default=True, help="Trigger on audio energy peaks.")
@click.pass_context
def watch(ctx: click.Context, url: str, check_interval: int, clip_interval: int, window: int, layout: str, platform: str, audio_trigger: bool) -> None:
    ...
```

- [ ] **Step 4: Run full test suite to verify 100% pass rate**

Run: `.venv\Scripts\python.exe -m pytest tests/ -v`  
Expected: PASS (all tests pass, 0 failures)

- [ ] **Step 5: Commit**

```bash
git add clipmania/cli.py tests/test_cli.py
git commit -m "feat(cli): add clipmania watch command for automated live clipping"
```
