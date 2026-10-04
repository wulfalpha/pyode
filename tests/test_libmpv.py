# SPDX-License-Identifier: GPL-2.0-or-later
"""Tests for libmpv discovery.

The whole point of the module is to work on platforms the test machine is not,
so each test reloads it with `sys.platform` patched and a fake install tree on
disk.  Loading the library itself is not exercised -- that needs a real libmpv,
and mocking ctypes would only assert that the mock behaves like the mock.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

import pyode.libmpv


@pytest.fixture
def on_platform(monkeypatch):
    """Reload the module as if it had been imported on another platform."""

    def _reload(name: str):
        monkeypatch.setattr(sys, "platform", name)
        return importlib.reload(pyode.libmpv)

    yield _reload
    monkeypatch.undo()
    importlib.reload(pyode.libmpv)


def touch(directory: Path, name: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_bytes(b"")
    return path


# -- the environment override -------------------------------------------


def test_env_var_takes_a_file(tmp_path, monkeypatch, on_platform):
    libmpv = on_platform("linux")
    target = touch(tmp_path, "libmpv.so.2")
    monkeypatch.setenv(libmpv.ENV_VAR, str(target))
    assert libmpv.find() == target


def test_env_var_takes_a_directory(tmp_path, monkeypatch, on_platform):
    libmpv = on_platform("linux")
    target = touch(tmp_path / "lib", "libmpv.so.2")
    monkeypatch.setenv(libmpv.ENV_VAR, str(tmp_path / "lib"))
    assert libmpv.find() == target


def test_env_var_pointing_nowhere_falls_through(tmp_path, monkeypatch, on_platform):
    """A stale override should not be fatal; the normal search still runs."""
    libmpv = on_platform("linux")
    monkeypatch.setenv(libmpv.ENV_VAR, str(tmp_path / "gone"))
    monkeypatch.setattr(libmpv, "search_dirs", list)
    assert libmpv.find() is None


# -- scanning a directory -----------------------------------------------


def test_scan_prefers_the_newest_abi(tmp_path, on_platform):
    libmpv = on_platform("linux")
    touch(tmp_path, "libmpv.so.1")
    touch(tmp_path, "libmpv.so.2")
    assert libmpv._scan(tmp_path).name == "libmpv.so.2"


def test_scan_falls_back_to_a_fully_versioned_name(tmp_path, on_platform):
    """Some builds ship only libmpv.so.2.5.0, with no unversioned symlink."""
    libmpv = on_platform("linux")
    touch(tmp_path, "libmpv.so.2.5.0")
    assert libmpv._scan(tmp_path).name == "libmpv.so.2.5.0"


def test_scan_ignores_a_missing_directory(tmp_path, on_platform):
    libmpv = on_platform("linux")
    assert libmpv._scan(tmp_path / "nope") is None


# -- macOS ---------------------------------------------------------------


def test_finds_apple_silicon_homebrew(tmp_path, monkeypatch, on_platform):
    """The M1/M4 case: dyld does not search /opt/homebrew/lib, so we must."""
    libmpv = on_platform("darwin")
    target = touch(tmp_path / "opt/homebrew/lib", "libmpv.2.dylib")
    monkeypatch.setenv("HOMEBREW_PREFIX", str(tmp_path / "opt/homebrew"))
    monkeypatch.setattr(libmpv.shutil, "which", lambda _: None)
    assert libmpv.find() == target


def test_finds_intel_homebrew_without_the_env_var(tmp_path, monkeypatch, on_platform):
    libmpv = on_platform("darwin")
    monkeypatch.delenv("HOMEBREW_PREFIX", raising=False)
    monkeypatch.setattr(libmpv.shutil, "which", lambda _: None)
    assert Path("/usr/local/lib") in libmpv.search_dirs()
    assert Path("/opt/homebrew/lib") in libmpv.search_dirs()
    assert Path("/opt/local/lib") in libmpv.search_dirs()  # MacPorts


def test_finds_the_lib_dir_beside_the_mpv_binary(tmp_path, monkeypatch, on_platform):
    libmpv = on_platform("darwin")
    target = touch(tmp_path / "prefix/lib", "libmpv.2.dylib")
    (tmp_path / "prefix/bin").mkdir(parents=True)
    (tmp_path / "prefix/bin/mpv").write_bytes(b"")
    monkeypatch.setattr(libmpv.shutil, "which", lambda _: str(tmp_path / "prefix/bin/mpv"))
    assert libmpv.find() == target


# -- Windows -------------------------------------------------------------


def test_finds_a_dll_in_the_scoop_app_dir(tmp_path, monkeypatch, on_platform):
    libmpv = on_platform("win32")
    target = touch(tmp_path / "scoop/apps/mpv/current", "libmpv-2.dll")
    monkeypatch.setenv("SCOOP", str(tmp_path / "scoop"))
    monkeypatch.setattr(libmpv.shutil, "which", lambda _: None)
    assert libmpv.find() == target


def test_finds_a_dll_in_the_chocolatey_tools_dir(tmp_path, monkeypatch, on_platform):
    libmpv = on_platform("win32")
    target = touch(tmp_path / "choco/lib/mpv.install/tools", "libmpv-2.dll")
    monkeypatch.setenv("ChocolateyInstall", str(tmp_path / "choco"))
    monkeypatch.setattr(libmpv.shutil, "which", lambda _: None)
    assert libmpv.find() == target


def test_follows_a_scoop_shim_to_the_real_install(tmp_path, monkeypatch, on_platform):
    """Scoop's shims are not symlinks -- the real path is in a .shim file."""
    libmpv = on_platform("win32")
    app = tmp_path / "scoop/apps/mpv/current"
    target = touch(app, "libmpv-2.dll")
    (app / "mpv.exe").write_bytes(b"")
    shims = tmp_path / "scoop/shims"
    shims.mkdir(parents=True)
    (shims / "mpv.exe").write_bytes(b"")
    (shims / "mpv.shim").write_text(f"path = {app / 'mpv.exe'}\n")

    monkeypatch.delenv("SCOOP", raising=False)
    monkeypatch.setattr(libmpv.shutil, "which", lambda _: str(shims / "mpv.exe"))
    assert libmpv.find() == target


def test_the_bundled_directory_is_searched_last(tmp_path, monkeypatch, on_platform):
    """A real installation should always beat a copy we were handed."""
    libmpv = on_platform("win32")
    installed = touch(tmp_path / "scoop/apps/mpv/current", "libmpv-2.dll")
    bundled = touch(tmp_path / "data/lib", "libmpv-2.dll")

    monkeypatch.setenv("SCOOP", str(tmp_path / "scoop"))
    monkeypatch.setattr(libmpv.shutil, "which", lambda _: None)
    monkeypatch.setattr(libmpv, "bundled_dir", lambda: bundled.parent)

    assert libmpv.find() == installed
    assert libmpv.search_dirs()[-1] == bundled.parent


def test_windows_looks_for_the_dll_names_python_mpv_uses(on_platform):
    libmpv = on_platform("win32")
    assert libmpv.LIB_NAMES[:3] == ("libmpv-2.dll", "mpv-2.dll", "mpv-1.dll")


# -- the failure message -------------------------------------------------


def test_windows_hint_explains_that_mpv_exe_is_not_enough(on_platform):
    """Scoop and Chocolatey ship a static mpv.exe and no DLL, so saying
    "install mpv" to a Windows user is actively misleading."""
    libmpv = on_platform("win32")
    hint = libmpv.install_hint()
    assert "Scoop" in hint and "statically linked" in hint
    assert "mpv-dev" in hint
    assert str(libmpv.bundled_dir()) in hint


def test_macos_hint_names_homebrew(on_platform):
    assert "brew install mpv" in on_platform("darwin").install_hint()


def test_every_hint_mentions_the_escape_hatch(on_platform):
    for name in ("win32", "darwin", "linux"):
        libmpv = on_platform(name)
        assert libmpv.ENV_VAR in libmpv.install_hint()


def test_report_runs_on_every_platform(on_platform, monkeypatch):
    for name in ("win32", "darwin", "linux"):
        libmpv = on_platform(name)
        # Faking the platform confuses the real find_library, which consults
        # the actual host; the report only needs it to answer something.
        monkeypatch.setattr(libmpv.ctypes.util, "find_library", lambda _: None)
        monkeypatch.setattr(libmpv.shutil, "which", lambda _: None)
        report = libmpv.report()
        assert "searched:" in report
        assert libmpv.ENV_VAR in report
        assert "result:" in report


def test_load_honours_override_before_system_loader(tmp_path, monkeypatch):
    import builtins
    from types import SimpleNamespace

    libmpv = pyode.libmpv
    target = touch(tmp_path, "libmpv.so.2")
    monkeypatch.setenv(libmpv.ENV_VAR, str(target))
    monkeypatch.delitem(sys.modules, "mpv", raising=False)
    monkeypatch.setattr(libmpv, "_loader_can_find_it", lambda: True)
    original_import = builtins.__import__
    original_find = libmpv.ctypes.util.find_library
    backend = SimpleNamespace()

    def import_backend(name, *args, **kwargs):
        if name == "mpv":
            assert libmpv.ctypes.util.find_library("mpv") == str(target)
            return backend
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", import_backend)
    assert libmpv.load() is backend
    assert libmpv.ctypes.util.find_library is original_find
