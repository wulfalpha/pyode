# SPDX-License-Identifier: GPL-2.0-or-later
import pytest

from pyode.config import Preferences


def test_missing_preferences_use_defaults(tmp_path):
    assert Preferences.load(tmp_path / "missing") == Preferences()


def test_preferences_round_trip(tmp_path):
    path = tmp_path / "nested/config.toml"
    prefs = Preferences("vim", 35, False)
    prefs.save(path)
    assert Preferences.load(path) == prefs


@pytest.mark.parametrize(
    "content",
    [
        'keymap = "emacs"',
        "volume = 101",
        "volume = true",
        "volume = 5.5",
        'visualiser = "false"',
        "invalid toml",
    ],
)
def test_invalid_preferences_are_rejected_without_rewriting(tmp_path, content):
    path = tmp_path / "config.toml"
    path.write_text(content)
    with pytest.raises(ValueError):
        Preferences.load(path)
    assert path.read_text() == content


def test_failed_save_keeps_existing_preferences(tmp_path, monkeypatch):
    import pyode.config

    path = tmp_path / "config.toml"
    Preferences().save(path)

    def fail(*args):
        raise OSError("disk full")

    monkeypatch.setattr(pyode.config.os, "replace", fail)
    with pytest.raises(OSError):
        Preferences("vim").save(path)
    assert Preferences.load(path) == Preferences()
    assert list(tmp_path.iterdir()) == [path]
