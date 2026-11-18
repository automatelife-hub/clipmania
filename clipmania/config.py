"""Configuration loading and management."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

if sys.version_info >= (3, 11):
    import tomllib
else:
    try:
        import tomllib  # type: ignore[import]
    except ModuleNotFoundError:
        import tomli as tomllib  # type: ignore[no-redef]


@dataclass
class ClipManiaConfig:
    """Merged configuration from file, env vars, and CLI flags."""

    # Output
    output_dir: Path = field(default_factory=lambda: Path("./output"))

    # Defaults
    default_layout: str = "vertical-blur"

    # Encoding
    crf: int = 20
    preset: str = "fast"

    # Audio
    audio_target_lufs: float = -14.0
    audio_lra: float = 11.0
    audio_true_peak: float = -1.5
    audio_bitrate: str = "192k"

    # Live buffer
    buffer_dir: Path | None = None
    segment_duration: int = 5
    playlist_size: int = 24
    quality: str = "best"


def _resolve_buffer_dir(raw: str) -> Path | None:
    """Return *None* (auto-detect) when the value is empty."""
    if not raw:
        return None
    return Path(raw)


def load_config(
    config_path: Path | None = None,
    overrides: dict[str, Any] | None = None,
) -> ClipManiaConfig:
    """Load configuration from a TOML file, with optional CLI overrides.

    Search order for the config file (first match wins):
      1. Explicit *config_path* argument
      2. ``CLIPMANIA_CONFIG`` environment variable
      3. ``./clipmania.toml`` in the current working directory
      4. Fall back to defaults
    """
    cfg = ClipManiaConfig()

    # Locate config file
    path: Path | None = None
    if config_path:
        path = config_path
        if not path.is_file():
            raise FileNotFoundError(
                f"Config file not found: {path}"
            )
    elif env := os.environ.get("CLIPMANIA_CONFIG"):
        path = Path(env)
        if not path.is_file():
            raise FileNotFoundError(
                f"Config file specified by CLIPMANIA_CONFIG not found: {path}"
            )
    else:
        cwd_config = Path("clipmania.toml")
        if cwd_config.is_file():
            path = cwd_config

    # Parse TOML
    if path and path.is_file():
        try:
            with open(path, "rb") as f:
                data = tomllib.load(f)
        except Exception as e:
            raise ValueError(
                f"Failed to parse config file {path}: {e}\n"
                "Ensure the file contains valid TOML syntax."
            ) from e

        output = data.get("output", {})
        defaults = data.get("defaults", {})
        encoding = data.get("encoding", {})
        audio = data.get("audio", {})
        live = data.get("live_buffer", {})

        if "dir" in output:
            cfg.output_dir = Path(output["dir"])
        if "layout" in defaults:
            cfg.default_layout = defaults["layout"]
        if "crf" in encoding:
            cfg.crf = int(encoding["crf"])
        if "preset" in encoding:
            cfg.preset = encoding["preset"]
        if "target_lufs" in audio:
            cfg.audio_target_lufs = float(audio["target_lufs"])
        if "lra" in audio:
            cfg.audio_lra = float(audio["lra"])
        if "true_peak" in audio:
            cfg.audio_true_peak = float(audio["true_peak"])
        if "bitrate" in audio:
            cfg.audio_bitrate = audio["bitrate"]
        if "dir" in live:
            cfg.buffer_dir = _resolve_buffer_dir(live["dir"])
        if "segment_duration" in live:
            cfg.segment_duration = int(live["segment_duration"])
        if "playlist_size" in live:
            cfg.playlist_size = int(live["playlist_size"])
        if "quality" in live:
            cfg.quality = live["quality"]

    # Apply CLI overrides
    if overrides:
        # Fields that should be Path type
        _path_fields = {"output_dir", "buffer_dir"}
        for key, value in overrides.items():
            if value is not None and hasattr(cfg, key):
                if key in _path_fields:
                    setattr(cfg, key, Path(value))
                else:
                    setattr(cfg, key, value)

    return cfg
