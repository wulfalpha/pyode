# SPDX-License-Identifier: GPL-2.0-or-later
"""Tests for the shared control definitions."""

import pytest

from pyode.keys import CONTROLS, footer_text, key_for


@pytest.mark.parametrize(
    "action,keymap,expected",
    [
        ("play", "standard", "→"),
        ("play", "vim", "l"),
        ("stop", "vim", "h"),
        ("toggle_pause", "vim", "Space"),  # no Vim key, so the main one
        ("remove", "standard", "d"),
    ],
)
def test_key_for_names_the_key_to_show(action, keymap, expected):
    assert key_for(action, keymap) == expected


def test_every_control_has_a_key_to_show():
    for control in CONTROLS:
        assert key_for(control.action, "standard")
        assert key_for(control.action, "vim")


@pytest.mark.parametrize(
    "keymap,expected",
    [
        ("standard", "→ play · ← stop · Space pause · ? help"),
        ("vim", "l play · h stop · Space pause · ? help"),
    ],
)
def test_footer_uses_the_same_keys(keymap, expected):
    assert footer_text(keymap) == expected
