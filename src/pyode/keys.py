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
    Control("shift+up", "move(-1)", "Move station up", "Shift+↑", "K"),
    Control("shift+down", "move(1)", "Move station down", "Shift+↓", "J"),
    Control("f", "toggle_favourite", "Favourite", "f"),
    Control("F", "filter_favourites", "Favourites only / all", "F"),
    Control("d", "remove", "Remove", "d"),
    Control("u", "undo_remove", "Undo last removal", "u"),
    Control("m", "toggle_mute", "Mute", "m"),
    Control("plus,equals_sign", "volume(5)", "Louder", "+ / ="),
    Control("minus", "volume(-5)", "Quieter", "-"),
    Control("t", "history", "Track history", "t"),
    Control("c", "copy_title", "Copy track title", "c"),
    Control("v", "toggle_visualiser", "Visualiser", "v"),
    Control("slash", "search", "Search directory", "/"),
    Control("question_mark", "help", "Help", "?"),
    Control("q", "quit", "Quit", "q"),
)


# Handled by the focused table itself; see `bindings`.
TABLE_ACTIONS = ("cursor_up", "cursor_down", "play", "stop")


def bindings() -> list[Binding]:
    # Table navigation and activation are handled at the focused table so
    # DataTable's own arrow bindings cannot swallow transport controls.
    return [Binding(c.keys, c.action, c.label) for c in CONTROLS]


def table_action(key: str, keymap: str, *, search: bool = False) -> str | None:
    for control in CONTROLS:
        if control.action not in TABLE_ACTIONS:
            continue
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
            "History: Enter or c copies the highlighted title.",
            "j/k move through results in Vim mode. Typing edits the search.",
            "Escape closes dialogs. Playback controls apply to the main screen.",
        ]
    )
    return "\n".join(lines)


def key_for(action: str, keymap: str) -> str:
    """The key to show for an action: the Vim key in Vim mode, else the main one."""
    control = next(c for c in CONTROLS if c.action == action)
    return control.vim if keymap == "vim" and control.vim else control.display.split(" / ")[0]


def footer_text(keymap: str) -> str:
    actions = ("play", "stop", "toggle_pause", "help")
    return " · ".join(
        f"{key_for(c.action, keymap)} {c.label.split(' / ')[0].lower()}"
        for c in CONTROLS
        if c.action in actions
    )
