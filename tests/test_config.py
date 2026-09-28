"""Tests for clipmania.config."""

import textwrap
from pathlib import Path

import pytest

from clipmania.config import ClipManiaConfig, load_config


class TestDefaults:
    """Default config values are sensible."""

    def test_default_output_dir(self):
        cfg = ClipManiaConfig()
        assert cfg.output_dir == Path("./output")

    def test_default_layout(self):
        cfg = ClipManiaConfig()
        assert cfg.default_layout == "vertical-blur"

    def test_default_audio(self):
        cfg = ClipManiaConfig()
        assert cfg.audio_target_lufs == -14.0
        assert cfg.audio_lra == 11.0
        assert cfg.audio_true_peak == -1.5

    def test_default_encoding(self):
        cfg = ClipManiaConfig()
        assert cfg.crf == 20
        assert cfg.preset == "fast"


class TestLoadConfig:
    """Config loading from TOML files."""

    def test_load_nonexistent_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError, match="Config file not found"):
            load_config(config_path=tmp_path / "does_not_exist.toml")

    def test_load_from_toml(self, tmp_path):
        config_file = tmp_path / "test.toml"
        config_file.write_text(textwrap.dedent("""\
            [output]
            dir = "/custom/output"

            [encoding]
            crf = 18
            preset = "slow"

            [audio]
            target_lufs = -16
        """))

        cfg = load_config(config_path=config_file)
        assert cfg.output_dir == Path("/custom/output")
        assert cfg.crf == 18
        assert cfg.preset == "slow"
        assert cfg.audio_target_lufs == -16.0
        # Unset values keep defaults
        assert cfg.audio_lra == 11.0

    def test_cli_overrides(self, tmp_path):
        config_file = tmp_path / "test.toml"
        config_file.write_text("[encoding]\ncrf = 18\n")

        cfg = load_config(
            config_path=config_file,
            overrides={"crf": 15, "output_dir": "/override"},
        )
        assert cfg.crf == 15  # override wins
        assert cfg.output_dir == Path("/override")

    def test_buffer_dir_override_path_type(self):
        # M2 fix: buffer_dir default is None, ensure override converts string to Path
        cfg = load_config(overrides={"buffer_dir": "/custom/buffer"})
        assert isinstance(cfg.buffer_dir, Path)
        assert cfg.buffer_dir == Path("/custom/buffer")

    def test_none_overrides_ignored(self):
        cfg = load_config(overrides={"crf": None})
        assert cfg.crf == 20  # None doesn't override


class TestLoadConfigEnv:
    """Config via environment variable."""

    def test_env_var_config(self, tmp_path, monkeypatch):
        config_file = tmp_path / "env.toml"
        config_file.write_text("[encoding]\ncrf = 22\n")
        monkeypatch.setenv("CLIPMANIA_CONFIG", str(config_file))

        cfg = load_config()
        assert cfg.crf == 22
