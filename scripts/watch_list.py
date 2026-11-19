"""Launch one ``clipmania watch`` process per channel in a watchlist file.

Usage:
    python scripts/watch_list.py watchlists/gta6.txt [--limit N] [watch options...]

Each watcher polls its channel and starts buffering/clipping when it goes live.
Logs go to ``logs/watch_<channel>.log``. Press Ctrl+C to stop all watchers
(each one stops its own live buffer on exit).

Note: every *live* channel encodes its own clips, so CPU load scales with how
many watched channels are live at once. Use --limit, a longer --clip-interval,
or --fast when watching many channels.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def read_watchlist(path: Path) -> list[str]:
    urls: list[str] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if line and line not in urls:
            urls.append(line)
    return urls


def slug(url: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "_", url.split("://", 1)[-1]).strip("_")[:80]


def main(argv: list[str]) -> int:
    if not argv or argv[0] in {"-h", "--help"}:
        print(__doc__)
        return 0

    watchlist = Path(argv[0])
    rest = argv[1:]
    limit: int | None = None
    if "--limit" in rest:
        i = rest.index("--limit")
        limit = int(rest[i + 1])
        rest = rest[:i] + rest[i + 2:]

    urls = read_watchlist(watchlist)[:limit]
    if not urls:
        print(f"No URLs in {watchlist}")
        return 1

    logs = ROOT / "logs"
    logs.mkdir(exist_ok=True)
    env = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
    scripts_dir = str(Path(sys.executable).parent)
    env["PATH"] = scripts_dir + os.pathsep + env.get("PATH", "")

    procs: list[tuple[str, subprocess.Popen]] = []
    for url in urls:
        log = open(logs / f"watch_{slug(url)}.log", "a", encoding="utf-8")
        p = subprocess.Popen(
            [sys.executable, "-m", "clipmania", "watch", url, *rest],
            cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, env=env,
        )
        procs.append((url, p))
        print(f"watching {url}  (pid {p.pid}, log logs/watch_{slug(url)}.log)")
        time.sleep(1)  # stagger startup probes

    print(f"\n{len(procs)} watchers running. Ctrl+C to stop all.")
    try:
        while any(p.poll() is None for _, p in procs):
            time.sleep(5)
    except KeyboardInterrupt:
        print("\nStopping watchers...")
    finally:
        for _, p in procs:
            if p.poll() is None:
                p.terminate()
        for url, p in procs:
            try:
                p.wait(timeout=30)
            except subprocess.TimeoutExpired:
                p.kill()
            print(f"stopped {url} (exit {p.returncode})")
        _stop_buffers(urls)
    return 0


def _stop_buffers(urls: list[str]) -> None:
    """Hard-terminated watchers can't run their cleanup, and their streamlink/
    ffmpeg buffer processes are detached, so stop each watch buffer here."""
    sys.path.insert(0, str(ROOT))
    from clipmania.config import load_config
    from clipmania.ingestion.streamlink_live import get_default_buffer_dir, stop_live_buffer
    from clipmania.utils import extract_channel

    cfg = load_config()
    root = Path(cfg.buffer_dir) if cfg.buffer_dir else get_default_buffer_dir()
    for url in urls:
        bdir = root / "watch" / extract_channel(url)
        if (bdir / "buffer_state.json").exists():
            try:
                stop_live_buffer(buffer_dir=bdir)
            except Exception as exc:  # keep stopping the rest
                print(f"could not stop buffer for {url}: {exc}")


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
