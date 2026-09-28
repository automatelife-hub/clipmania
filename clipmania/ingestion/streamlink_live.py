"""Streamlink-based live stream capture with rolling HLS buffer."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from rich.console import Console

from ..utils import detect_platform, extract_channel, ensure_dir, safe_filename

console = Console()

STATE_FILENAME = "buffer_state.json"
LOCK_FILENAME = "buffer.lock"


@dataclass
class BufferState:
    """Persisted state of a running live buffer."""

    url: str
    channel: str
    platform: str
    buffer_dir: str
    playlist_path: str
    streamlink_pid: int | None = None
    ffmpeg_pid: int | None = None
    started_at: str = ""
    # C6: Store process creation timestamps to detect PID reuse after reboot
    streamlink_create_time: float | None = None
    ffmpeg_create_time: float | None = None

    def save(self, path: Path) -> None:
        """Persist state to a JSON file."""
        path.parent.mkdir(parents=True, exist_ok=True)
        # Write atomically: write to temp then rename
        tmp = path.with_suffix(".tmp")
        with open(tmp, "w") as f:
            json.dump(asdict(self), f, indent=2)
        tmp.replace(path)

    @classmethod
    def load(cls, path: Path) -> BufferState:
        """Load state from a JSON file.

        Handles corrupted JSON gracefully instead of crashing.
        """
        try:
            with open(path) as f:
                data = json.load(f)
        except (json.JSONDecodeError, ValueError):
            # Corrupted state file — treat as stale
            console.print("[yellow]⚠ Corrupted buffer state file, treating as stale.[/]")
            path.unlink(missing_ok=True)
            raise FileNotFoundError("Corrupted buffer state file")
        # Filter out unknown keys for forward compatibility
        valid_keys = {f.name for f in cls.__dataclass_fields__.values()}
        filtered = {k: v for k, v in data.items() if k in valid_keys}
        return cls(**filtered)


def get_default_buffer_dir() -> Path:
    """Platform-appropriate default buffer directory."""
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    else:
        import tempfile
        base = Path(tempfile.gettempdir())
    return base / "clipmania" / "buffer"


def _state_path(buffer_dir: Path) -> Path:
    return buffer_dir / STATE_FILENAME


def _lock_path(buffer_dir: Path) -> Path:
    return buffer_dir / LOCK_FILENAME


def _get_process_create_time(pid: int) -> float | None:
    """Get the creation time of a process by PID, or None if unavailable."""
    try:
        if sys.platform == "win32":
            import ctypes
            from ctypes import wintypes
            kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
            handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
            if not handle:
                return None
            try:
                creation = wintypes.FILETIME()
                exit_t = wintypes.FILETIME()
                kernel_t = wintypes.FILETIME()
                user_t = wintypes.FILETIME()
                if kernel32.GetProcessTimes(
                    handle,
                    ctypes.byref(creation), ctypes.byref(exit_t),
                    ctypes.byref(kernel_t), ctypes.byref(user_t),
                ):
                    # Convert FILETIME to float timestamp
                    ct = (creation.dwHighDateTime << 32) | creation.dwLowDateTime
                    return ct / 10_000_000.0  # 100-ns intervals
                return None
            finally:
                kernel32.CloseHandle(handle)
        else:
            # Unix: read process start time from /proc
            stat_path = Path(f"/proc/{pid}/stat")
            if stat_path.exists():
                return stat_path.stat().st_mtime
            return None
    except (OSError, PermissionError, ValueError):
        return None


def _acquire_lock(buffer_dir: Path) -> Path:
    """Acquire a file-based lock to prevent concurrent buffer starts (C5)."""
    lock_file = _lock_path(buffer_dir)
    lock_file.parent.mkdir(parents=True, exist_ok=True)

    try:
        # Use exclusive create to make it atomic
        fd = os.open(str(lock_file), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
        return lock_file
    except FileExistsError:
        # Check if the lock is stale (process that created it is dead)
        try:
            with open(lock_file) as f:
                lock_pid = int(f.read().strip())
            if not _is_pid_alive(lock_pid):
                # Stale lock — remove and retry
                lock_file.unlink(missing_ok=True)
                return _acquire_lock(buffer_dir)
        except (ValueError, OSError):
            lock_file.unlink(missing_ok=True)
            return _acquire_lock(buffer_dir)
        raise RuntimeError(
            "Another clipmania instance is currently starting a live buffer. "
            "Wait for it to finish, or remove the lock file: " + str(lock_file)
        )


def _release_lock(buffer_dir: Path) -> None:
    """Release the file-based lock."""
    _lock_path(buffer_dir).unlink(missing_ok=True)


def start_live_buffer(
    url: str,
    buffer_dir: Path | None = None,
    segment_duration: int = 5,
    playlist_size: int = 24,
    quality: str = "best",
) -> BufferState:
    """Start a rolling HLS buffer for a live stream.

    Spawns ``streamlink <url> <quality> -O | ffmpeg … -f hls …`` as
    detached subprocesses so they persist after the CLI exits.

    The buffer holds ``segment_duration * playlist_size`` seconds of content
    (default 120 s).
    """
    bdir = buffer_dir or get_default_buffer_dir()
    state_file = _state_path(bdir)

    # C5: Acquire lock to prevent concurrent buffer starts
    lock = _acquire_lock(bdir)

    try:
        # Check for already-running buffer
        if state_file.exists():
            try:
                existing = BufferState.load(state_file)
                if (_is_pid_alive(existing.streamlink_pid, existing.streamlink_create_time)
                        or _is_pid_alive(existing.ffmpeg_pid, existing.ffmpeg_create_time)):
                    raise RuntimeError(
                        f"A live buffer is already running for [{existing.channel}]. "
                        "Stop it first with 'clipmania live-stop'."
                    )
            except FileNotFoundError:
                pass  # Corrupted state was already cleaned up
            # Stale state — clean up
            state_file.unlink(missing_ok=True)

        channel = extract_channel(url)
        platform = detect_platform(url).value
        seg_dir = ensure_dir(bdir / channel)
        playlist = seg_dir / "live.m3u8"
        seg_pattern = str(seg_dir / "segment_%03d.ts")
        stderr_log = seg_dir / "ffmpeg_stderr.log"

        console.print(f"[bold blue]📡 Starting live buffer[/] for [cyan]{channel}[/] ({platform})")
        console.print(f"   Buffer: {seg_dir}")
        console.print(f"   Window: {segment_duration * playlist_size}s "
                      f"({playlist_size} × {segment_duration}s segments)")

        # Build commands
        sl_cmd = ["streamlink", url, quality, "-O"]

        ff_cmd = [
            "ffmpeg", "-y",
            "-i", "pipe:0",
            "-c", "copy",
            "-f", "hls",
            "-hls_time", str(segment_duration),
            "-hls_list_size", str(playlist_size),
            "-hls_flags", "delete_segments+append_list",
            "-hls_segment_filename", seg_pattern,
            str(playlist),
        ]

        # M9: Platform-specific process detachment
        popen_kwargs: dict[str, object] = {}
        if sys.platform == "win32":
            popen_kwargs["creationflags"] = (
                subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
            )
        else:
            popen_kwargs["start_new_session"] = True

        sl_proc = subprocess.Popen(
            sl_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            **popen_kwargs,  # type: ignore[arg-type]
        )

        # Save streamlink PID immediately in case ffmpeg spawn fails (M8)
        partial_state = BufferState(
            url=url, channel=channel, platform=platform,
            buffer_dir=str(bdir), playlist_path=str(playlist),
            streamlink_pid=sl_proc.pid,
            streamlink_create_time=_get_process_create_time(sl_proc.pid),
            started_at=datetime.now(timezone.utc).isoformat(),
        )
        partial_state.save(state_file)

        # M7: Log FFmpeg stderr to a file instead of DEVNULL
        stderr_fh = open(stderr_log, "w")

        ff_proc = subprocess.Popen(
            ff_cmd,
            stdin=sl_proc.stdout,
            stdout=subprocess.DEVNULL,
            stderr=stderr_fh,
            **popen_kwargs,  # type: ignore[arg-type]
        )

        # Allow streamlink's stdout to be closed in this process so SIGPIPE works
        if sl_proc.stdout:
            sl_proc.stdout.close()

        # Close the stderr log handle in the parent — the child process owns it now
        stderr_fh.close()

        state = BufferState(
            url=url,
            channel=channel,
            platform=platform,
            buffer_dir=str(bdir),
            playlist_path=str(playlist),
            streamlink_pid=sl_proc.pid,
            ffmpeg_pid=ff_proc.pid,
            started_at=datetime.now(timezone.utc).isoformat(),
            streamlink_create_time=_get_process_create_time(sl_proc.pid),
            ffmpeg_create_time=_get_process_create_time(ff_proc.pid),
        )
        state.save(state_file)

        # Give processes a moment to start
        time.sleep(2)

        if sl_proc.poll() is not None:
            console.print("[red]✗ Streamlink exited immediately.[/] Is the stream live?")
            console.print(f"   FFmpeg log: {stderr_log}")
            state_file.unlink(missing_ok=True)
            stderr_fh.close()
            raise RuntimeError("Streamlink failed to start. Check the URL and stream status.")

        console.print(f"[green]✓ Live buffer running[/] (PIDs: sl={sl_proc.pid}, ff={ff_proc.pid})")
        console.print(f"   FFmpeg log: {stderr_log}")
        return state
    finally:
        _release_lock(bdir)


def clip_from_buffer(
    seconds: int = 60,
    output_path: Path | None = None,
    buffer_dir: Path | None = None,
) -> Path:
    """Extract the last *seconds* from the active live buffer.

    Reads the local HLS playlist to calculate total buffered duration,
    then uses ``-ss`` with an absolute offset for reliable seeking.
    """
    if seconds <= 0:
        raise ValueError("--seconds must be a positive integer.")

    bdir = buffer_dir or get_default_buffer_dir()
    state_file = _state_path(bdir)

    if not state_file.exists():
        raise RuntimeError(
            "No active live buffer found. Start one with 'clipmania live-start <URL>'."
        )

    state = BufferState.load(state_file)
    playlist = Path(state.playlist_path)

    if not playlist.exists():
        raise RuntimeError(f"Playlist not found: {playlist}")

    if output_path is None:
        output_path = Path.cwd() / safe_filename(state.platform, state.channel)

    output_path.parent.mkdir(parents=True, exist_ok=True)

    console.print(f"[bold blue]✂  Clipping[/] last {seconds}s from live buffer…")

    # M5: Calculate total buffer duration from playlist segments instead of -sseof
    total_duration = _get_playlist_duration(playlist)
    if total_duration is not None and total_duration > 0:
        start_offset = max(0, total_duration - seconds)
        cmd = [
            "ffmpeg", "-y",
            "-ss", str(start_offset),
            "-i", str(playlist),
            "-t", str(seconds),
            "-c", "copy",
            "-movflags", "+faststart",
            str(output_path),
        ]
    else:
        # Fallback to -sseof if we can't parse the playlist
        cmd = [
            "ffmpeg", "-y",
            "-sseof", f"-{seconds}",
            "-i", str(playlist),
            "-c", "copy",
            "-movflags", "+faststart",
            str(output_path),
        ]

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"FFmpeg clip extraction failed:\n{result.stderr[-500:]}")

    console.print(f"[green]✓[/] Live clip saved: {output_path}")
    return output_path


def _get_playlist_duration(playlist_path: Path) -> float | None:
    """Parse an HLS playlist to get total buffered duration in seconds."""
    try:
        total = 0.0
        with open(playlist_path) as f:
            for line in f:
                line = line.strip()
                if line.startswith("#EXTINF:"):
                    # Format: #EXTINF:<duration>,
                    dur_str = line.split(":")[1].split(",")[0]
                    total += float(dur_str)
        return total if total > 0 else None
    except (OSError, ValueError):
        return None


def stop_live_buffer(
    buffer_dir: Path | None = None,
    cleanup: bool = False,
) -> None:
    """Stop the running live buffer and optionally remove segment files."""
    bdir = buffer_dir or get_default_buffer_dir()
    state_file = _state_path(bdir)

    if not state_file.exists():
        console.print("[yellow]No active live buffer to stop.[/]")
        return

    try:
        state = BufferState.load(state_file)
    except FileNotFoundError:
        console.print("[yellow]Buffer state was corrupted and has been cleaned up.[/]")
        return

    console.print(f"[bold red]⏹  Stopping[/] live buffer for [cyan]{state.channel}[/]…")

    for pid in (state.streamlink_pid, state.ffmpeg_pid):
        _kill_pid(pid)

    state_file.unlink(missing_ok=True)

    if cleanup:
        seg_dir = Path(state.buffer_dir) / state.channel
        if seg_dir.exists():
            import shutil
            shutil.rmtree(seg_dir, ignore_errors=True)
            console.print(f"   Cleaned up: {seg_dir}")

    console.print("[green]✓ Live buffer stopped.[/]")


def get_buffer_status(buffer_dir: Path | None = None) -> BufferState | None:
    """Return the current buffer state, or *None* if nothing is running."""
    bdir = buffer_dir or get_default_buffer_dir()
    state_file = _state_path(bdir)
    if not state_file.exists():
        return None
    try:
        state = BufferState.load(state_file)
    except FileNotFoundError:
        return None
    # Verify at least one process is alive (with creation time check)
    if (not _is_pid_alive(state.streamlink_pid, state.streamlink_create_time)
            and not _is_pid_alive(state.ffmpeg_pid, state.ffmpeg_create_time)):
        # Stale state
        state_file.unlink(missing_ok=True)
        return None
    return state


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _is_pid_alive(pid: int | None, expected_create_time: float | None = None) -> bool:
    """Check if a process is alive.

    When *expected_create_time* is provided, also verifies the process
    creation time matches to detect PID reuse after reboot (C6).
    """
    if pid is None:
        return False
    try:
        if sys.platform == "win32":
            import ctypes
            kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
            handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
            if handle:
                kernel32.CloseHandle(handle)
                # C6: Verify creation time if available
                if expected_create_time is not None:
                    actual = _get_process_create_time(pid)
                    if actual is not None and abs(actual - expected_create_time) > 1.0:
                        return False  # PID was reused by a different process
                return True
            return False
        else:
            os.kill(pid, 0)
            # C6: Verify creation time if available
            if expected_create_time is not None:
                actual = _get_process_create_time(pid)
                if actual is not None and abs(actual - expected_create_time) > 1.0:
                    return False
            return True
    except (OSError, PermissionError):
        return False


def _kill_pid(pid: int | None) -> None:
    if pid is None:
        return
    try:
        if sys.platform == "win32":
            subprocess.run(
                ["taskkill", "/F", "/PID", str(pid)],
                capture_output=True,
                timeout=5,
            )
        else:
            import signal
            os.kill(pid, signal.SIGTERM)
            # Wait briefly, then escalate to SIGKILL if still alive
            time.sleep(1)
            if _is_pid_alive(pid):
                os.kill(pid, signal.SIGKILL)
    except (OSError, subprocess.TimeoutExpired):
        pass
