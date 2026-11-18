"""Tests for clipmania.deps dependency checks."""

from unittest.mock import MagicMock, patch

import click
import pytest

from clipmania.deps import _get_version, check_all_dependencies, check_dependency


class TestCheckDependency:
    @patch("shutil.which", return_value="/usr/bin/ffmpeg")
    @patch("clipmania.deps._get_version", return_value="ffmpeg version 6.0")
    def test_found_dependency(self, mock_ver, mock_which):
        info = check_dependency("ffmpeg", required=True)
        assert info.name == "ffmpeg"
        assert info.path == "/usr/bin/ffmpeg"
        assert info.version == "ffmpeg version 6.0"
        assert info.required is True

    @patch("shutil.which", return_value=None)
    def test_missing_dependency(self, mock_which):
        info = check_dependency("ffmpeg", required=True)
        assert info.name == "ffmpeg"
        assert info.path is None
        assert info.version is None
        assert info.required is True

    @patch("subprocess.run")
    def test_get_version_success(self, mock_run):
        mock_run.return_value = MagicMock(stdout="tool v1.2.3\nadditional info")
        assert _get_version("mytool") == "tool v1.2.3"

    @patch("subprocess.run", side_effect=FileNotFoundError)
    def test_get_version_missing_binary(self, mock_run):
        assert _get_version("nonexistent") is None


class TestCheckAllDependencies:
    @patch("clipmania.deps.check_dependency")
    def test_all_found_succeeds(self, mock_check):
        from clipmania.deps import DepInfo
        mock_check.side_effect = lambda name, required: DepInfo(
            name=name, path=f"/bin/{name}", version="1.0", required=required
        )
        results = check_all_dependencies(verbose=False)
        assert len(results) == 4
        assert all(dep.path is not None for dep in results)

    @patch("clipmania.deps.check_dependency")
    def test_missing_required_raises(self, mock_check):
        from clipmania.deps import DepInfo

        def fake_check(name, required):
            path = None if name == "ffmpeg" else f"/bin/{name}"
            return DepInfo(name=name, path=path, version=None, required=required)

        mock_check.side_effect = fake_check
        with pytest.raises(click.ClickException, match="Required dependencies not found: ffmpeg"):
            check_all_dependencies(verbose=False)

    @patch("clipmania.deps.check_dependency")
    def test_missing_optional_does_not_raise(self, mock_check):
        from clipmania.deps import DepInfo

        def fake_check(name, required):
            # streamlink is optional (required=False)
            path = None if name == "streamlink" else f"/bin/{name}"
            return DepInfo(name=name, path=path, version="1.0", required=required)

        mock_check.side_effect = fake_check
        results = check_all_dependencies(verbose=True)
        assert len(results) == 4
