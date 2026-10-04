# SPDX-License-Identifier: GPL-2.0-or-later
"""Widgets for the pyode interface.

The visual idea is a receiver's illuminated dial: warm amber for the chrome you
operate, paper white for the content you read, and a single cold cyan reserved
for the live signal.  `DialRule` and `SignalMeter` draw themselves line by line
so they adapt to whatever height the layout gives them.
"""

from __future__ import annotations

from collections import deque

from rich.segment import Segment
from textual.containers import Horizontal, Vertical
from textual.reactive import reactive
from textual.strip import Strip
from textual.widget import Widget
from textual.widgets import Label, Static

from pyode.keys import footer_text
from pyode.player import PlayerState, PlayerStatus
from pyode.stations import Station

# Eighth-block ramp, used to give the meter sub-character resolution.
BLOCKS = " ▁▂▃▄▅▆▇█"

TRANSPORT = {
    PlayerState.IDLE: ("□", "Off air", ""),
    PlayerState.BUFFERING: ("◌", "Buffering", ""),
    PlayerState.PLAYING: ("▶", "On air", "live"),
    PlayerState.PAUSED: ("‖", "Paused", "paused"),
    PlayerState.ERROR: ("△", "Stream failed", "error"),
}


class TopBar(Horizontal):
    """Wordmark and the on-air lamp."""

    def compose(self):
        yield Static("pyode", id="wordmark")
        yield Static("○ off air", id="lamp")

    def update(self, *, station_count: int, live: bool) -> None:
        stations = "station" if station_count == 1 else "stations"
        self.query_one("#wordmark", Static).update(f"pyode  [dim]{station_count} {stations}[/]")
        lamp = self.query_one("#lamp", Static)
        lamp.update("◉ on air" if live else "○ off air")
        lamp.set_class(live, "live")


class DialRule(Widget):
    """The divider between the list and the metadata, drawn as a tuning scale.

    The needle sits at the playing station's position within the library, so
    the rule reports where in the band you are tuned rather than decorating the
    gap between two panes.
    """

    COMPONENT_CLASSES = {"dial--tick", "dial--major", "dial--needle"}

    position: reactive[float | None] = reactive(None)

    def watch_position(self) -> None:
        self.refresh()

    def render_line(self, y: int) -> Strip:
        height = self.size.height
        needle = None
        if self.position is not None and height > 1:
            needle = round(self.position * (height - 1))
        if y == needle:
            return Strip([Segment("─◀ ", self.get_component_rich_style("dial--needle"))])
        if y % 5 == 0:
            return Strip([Segment(" ┤ ", self.get_component_rich_style("dial--major"))])
        return Strip([Segment(" │ ", self.get_component_rich_style("dial--tick"))])


class SignalMeter(Widget):
    """A strip chart of signal level, newest at the right.

    This plots RMS over time rather than a frequency spectrum, because time is
    what the player actually measures.  Levels are taken ahead of the volume
    stage, so the chart keeps moving while muted -- the point is to show the
    stream is alive, not that sound is coming out.  A stalled stream flatlines.

    The chart auto-ranges over what is on screen.  Broadcast audio is heavily
    compressed, so absolute levels sit near the top of the scale and a fixed
    range draws a near-solid block with all the movement crushed into the top
    few percent.  The player reports absolute level; deciding how to show it is
    the display's job.
    """

    # The narrowest span stretched across the full height.  Without a floor,
    # a dead-steady signal would have its noise amplified into a fake dance.
    WINDOW = 0.12
    SILENCE = 0.04

    COMPONENT_CLASSES = {"signal--bar", "signal--peak", "signal--flat", "signal--off"}

    enabled: reactive[bool] = reactive(True)

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._history: deque[float | None] = deque(maxlen=240)

    def watch_enabled(self) -> None:
        self.refresh()

    def push(self, level: float | None) -> None:
        self._history.append(level)
        self.refresh()

    def clear(self) -> None:
        self._history.clear()
        self.refresh()

    def _scaled(self, values: list[float | None]) -> list[float | None]:
        """Stretch the on-screen values across the full height."""
        live = [value for value in values if value is not None and value > 0.0]
        if not live:
            return values
        top = max(live)
        if top < self.SILENCE:
            return [None if value is None else 0.0 for value in values]
        span = max(top - min(live), self.WINDOW)
        base = top - span
        return [
            None if value is None else max(0.0, min(1.0, (value - base) / span)) for value in values
        ]

    def render_line(self, y: int) -> Strip:
        width, height = self.size.width, self.size.height
        # Every segment carries an explicit style: an unstyled one does not
        # take the widget's background with it, and renders as a grey hole.
        base = self.rich_style
        if not self.enabled:
            if y == height // 2:
                text = "visualiser off — press v".center(width)[:width]
                return Strip([Segment(text, self.get_component_rich_style("signal--off"))])
            return Strip([Segment(" " * width, base)])

        bar = self.get_component_rich_style("signal--bar")
        peak = self.get_component_rich_style("signal--peak")
        flat = self.get_component_rich_style("signal--flat")

        values = list(self._history)[-width:]
        values = self._scaled([None] * (width - len(values)) + values)

        segments = []
        for value in values:
            if value is None:
                segments.append(Segment(" ", base))
                continue
            if value <= 0.0:
                # Dead air reads as a flatline along the bottom, not as a gap.
                segments.append(Segment("─" if y == height - 1 else " ", flat))
                continue
            filled = value * height * 8
            remaining = filled - (height - 1 - y) * 8
            index = max(0, min(8, round(remaining)))
            style = peak if 0 < remaining <= 8 else bar
            segments.append(Segment(BLOCKS[index], style))
        return Strip(segments, width)


class NowPlaying(Vertical):
    """Everything known about what is currently on air."""

    def compose(self):
        yield Label("NOW PLAYING", classes="eyebrow")
        yield Static("Nothing playing", id="np-title")
        yield Static("", id="np-station")
        yield Static("", id="np-detail")
        yield Label("TAGS", classes="eyebrow", id="np-tags-label")
        yield Static("", id="np-tags")
        yield Static("", id="np-error")

    def update(self, status: PlayerStatus, station: Station | None) -> None:
        now = status.now_playing
        self.query_one("#np-title", Static).update(now.display)

        # The title line already shows whichever name we have; only repeat the
        # station underneath when it is telling us something different.  Empty
        # lines are hidden rather than left as gaps in the panel.
        station_name = station.name if station else now.station
        _set(
            self.query_one("#np-station", Static),
            station_name if station_name != now.display else "",
        )

        details = []
        if station and station.country:
            details.append(station.country)
        if now.codec:
            details.append(now.codec.upper())
        if now.bitrate:
            details.append(f"{now.bitrate} kbps")
        if status.cache_seconds:
            details.append(f"buffer {status.cache_seconds:.0f}s")
        _set(self.query_one("#np-detail", Static), " · ".join(details))

        tags = station.tag_list[:8] if station else []
        self.query_one("#np-tags-label", Label).display = bool(tags)
        self.query_one("#np-tags", Static).update("  ".join(tags))

        _set(
            self.query_one("#np-error", Static),
            f"{status.error}. Press p to try again." if status.error else "",
        )


class ControlBar(Horizontal):
    """Transport state, volume, and the keys that drive them."""

    def __init__(self, *, keymap: str = "standard", **kwargs):
        super().__init__(**kwargs)
        self.keymap = keymap

    def compose(self):
        yield Static("□ Off air", id="transport")
        yield Static("", id="ctl-station")
        yield Static("", id="ctl-volume")
        yield Static(footer_text(self.keymap), id="ctl-keys")

    def update(self, status: PlayerStatus, station: Station | None) -> None:
        glyph, label, css_class = TRANSPORT[status.state]
        transport = self.query_one("#transport", Static)
        transport.update(f"{glyph} {label}")
        for candidate in ("live", "paused", "error"):
            transport.set_class(candidate == css_class, candidate)

        name = station.name if station else status.now_playing.station
        self.query_one("#ctl-station", Static).update(name if status.is_active else "")
        self.query_one("#ctl-volume", Static).update(_volume_readout(status))


def _set(widget: Static, text: str) -> None:
    """Show a line only when it has something to say."""
    widget.update(text)
    widget.display = bool(text)


def _volume_readout(status: PlayerStatus) -> str:
    if status.muted:
        return "muted"
    filled = round(status.volume / 100 * 8)
    # Block elements only: geometric shapes like U+25B0 are missing from many
    # monospace fonts and get substituted at the wrong width, which shears the
    # whole control bar sideways.
    return f"{'█' * filled}{'░' * (8 - filled)} {status.volume:>3}"
