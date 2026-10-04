# SPDX-License-Identifier: GPL-2.0-or-later
"""Shared controls for bindings, the footer, and help."""

from dataclasses import dataclass

from textual.binding import Binding


@dataclass(frozen=True)
class Control:
    keys: str
    action: str
    label: str
    display: str
    vim: str = ""


CONTROLS = (
    Control("up", "cursor_up", "Previous station", "↑", "k"),
    Control("down", "cursor_down", "Next station", "↓", "j"),
    Control("right,enter,p", "play", "Play", "→ / Enter / p", "l"),
    Control("left,s", "stop", "Stop", "← / s", "h"),
    Control("space", "toggle_pause", "Pause / resume", "Space"),
    Control("f", "toggle_favourite", "Favourite", "f"),
    Control("F", "filter_favourites", "Favourites only / all", "F"),
    Control("d", "remove", "Remove", "d"),
    Control("u", "undo_remove", "Undo last removal", "u"),
    Control("m", "toggle_mute", "Mute", "m"),
    Control("plus,equals_sign", "volume(5)", "Louder", "+ / ="),
    Control("minus", "volume(-5)", "Quieter", "-"),
    Control("v", "toggle_visualiser", "Visualiser", "v"),
    Control("slash", "search", "Search directory", "/"),
    Control("question_mark", "help", "Help", "?"),
    Control("q", "quit", "Quit", "q"),
)


def bindings() -> list[Binding]:
    # Table navigation and activation are handled at the focused table so
    # DataTable's own arrow bindings cannot swallow transport controls.
    return [Binding(c.keys, c.action, c.label) for c in CONTROLS]


def table_action(key: str, keymap: str, *, search: bool = False) -> str | None:
    for control in CONTROLS[:4]:
        keys = control.keys.split(",") + ([control.vim] if keymap == "vim" else [])
        if key in keys:
            if search and control.action in ("play", "stop"):
                return None
            return control.action
    return None


def help_text(keymap: str) -> str:
    lines = [f"CONTROLS — {keymap}", ""]
    for control in CONTROLS:
        keys = control.display + (f" / {control.vim}" if keymap == "vim" and control.vim else "")
        lines.append(f"{keys:22} {control.label}")
    lines.extend(
        [
            "",
            "Search: Enter searches / adds; Tab changes focus.",
            "j/k move through results in Vim mode. Typing edits the search.",
            "Escape closes dialogs. Playback controls apply to the main screen.",
        ]
    )
    return "\n".join(lines)


def footer_text(keymap: str) -> str:
    actions = ("play", "stop", "toggle_pause", "help")
    return " · ".join(
        f"{c.vim if keymap == 'vim' and c.vim else c.display.split(' / ')[0]} "
        f"{c.label.split(' / ')[0].lower()}"
        for c in CONTROLS
        if c.action in actions
    )
