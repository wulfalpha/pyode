# SPDX-License-Identifier: GPL-2.0-or-later
"""The pyode interface.

The UI polls the player rather than subscribing to it.  `PlayerStatus` is a
pure snapshot of cached state, so reading it ten times a second is cheap, and
polling keeps every mpv call on the main thread -- which is the invariant that
keeps `player` safe (see its module docstring).
"""

from __future__ import annotations

from pathlib import Path

from textual import events, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.css.query import NoMatches
from textual.message import Message
from textual.screen import ModalScreen
from textual.timer import Timer
from textual.widgets import DataTable, Input, Label, Static

from pyode.browser import RadioBrowser, RadioBrowserError
from pyode.config import Preferences
from pyode.keys import CONTROLS, bindings, help_text, table_action
from pyode.player import MpvPlayer, PlayerState, PlayerStatus
from pyode.stations import Station, StationLibrary
from pyode.widgets import ControlBar, DialRule, NowPlaying, SignalMeter, TopBar

TICK = 0.1  # seconds between meter samples

# Below these, a pane cannot say anything useful, so it is dropped rather than
# squeezed into an unreadable sliver.
NARROW_WIDTH = 68
SHORT_HEIGHT = 20


def _quality(station: Station) -> str:
    parts = (station.codec.upper(), f"{station.bitrate}k" if station.bitrate else "")
    return " ".join(part for part in parts if part)


def _row(station: Station, width: int) -> str:
    """Lay a station out as one fixed-width line.

    The list owns its own column arithmetic so names truncate rather than
    growing the table past its pane and summoning a scrollbar.
    """
    mark = "♥ " if station.favorite else "  "
    quality = _quality(station)
    room = max(4, width - len(mark) - len(quality) - 1)
    name = station.name if len(station.name) <= room else f"{station.name[: room - 1]}…"
    gap = max(1, width - len(mark) - len(name) - len(quality))
    return f"{mark}{name}{' ' * gap}{quality}"


class NavigationTable(DataTable):
    """Handle list motions locally, leaving Input editing untouched."""

    def on_key(self, event: events.Key) -> None:
        action = table_action(event.key, self.app.keymap, search=self.id == "results")
        if action is None:
            return
        event.stop()
        event.prevent_default()
        if action == "cursor_up":
            self.action_cursor_up()
        elif action == "cursor_down":
            self.action_cursor_down()
        elif action == "play":
            self.app.action_play()
        elif action == "stop":
            self.app.action_stop()


class HelpScreen(ModalScreen):
    BINDINGS = [Binding("escape", "dismiss", "Close")]

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="help-box"):
            yield Static(help_text(self.app.keymap))


class StationTable(NavigationTable):
    """A station list that rebuilds when its own width changes.

    Rows are laid out to an exact character width, so they are only correct for
    the width the table actually has.  That is not known during `on_mount` --
    layout has not run yet -- so the rebuild is driven by the table's own
    resize rather than by the app's.
    """

    class Resized(Message):
        pass

    def on_resize(self) -> None:
        self.post_message(self.Resized())


class SearchScreen(ModalScreen[int]):
    """Search the directory and add stations to the library."""

    BINDINGS = [Binding("escape", "close", "Close", show=False)]

    def __init__(self, api: RadioBrowser, library: StationLibrary) -> None:
        super().__init__()
        self._api = api
        self._library = library
        self._results: list[Station] = []
        self._added = 0

    def compose(self) -> ComposeResult:
        with Vertical(id="search-box"):
            yield Label("SEARCH THE DIRECTORY", classes="eyebrow")
            yield Input(placeholder="Station name or tag")
            yield NavigationTable(id="results", cursor_type="row")
            yield Static("Type a search and press Enter.", id="search-status")

    def on_mount(self) -> None:
        table = self.query_one("#results", DataTable)
        table.add_columns("Station", "Country", "Quality")
        self.query_one(Input).focus()

    def _status(self, message: str) -> None:
        self.query_one("#search-status", Static).update(message)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        query = event.value.strip()
        if query:
            self._search(query)

    @work(exclusive=True)
    async def _search(self, query: str) -> None:
        self._status(f"Searching for {query}…")
        try:
            found = await self._api.search(name=query, limit=60)
            if not found:
                # A word that finds nothing as a name is usually a genre.
                found = await self._api.search(tag=query, limit=60)
                if found:
                    self._status(f"No station named {query}. Showing the {query} tag instead.")
        except RadioBrowserError as exc:
            self._status(str(exc))
            return

        self._results = found
        table = self.query_one("#results", DataTable)
        table.clear()
        for station in found:
            table.add_row(station.name, station.country, _quality(station))
        if not found:
            self._status(f"Nothing found for {query}.")
            return
        self._status(f"{len(found)} found. Enter adds the highlighted station.")
        table.focus()

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        if not 0 <= event.cursor_row < len(self._results):
            return
        station = self._results[event.cursor_row]
        try:
            with self._library.editing():
                added = self._library.add(station)
        except OSError as exc:
            self._status(f"Could not save {station.name}: {exc}")
            return
        if not added:
            self._status(f"{station.name} is already in your library.")
            return
        self._added += 1
        self._status(f"Added {station.name}.")

    def action_close(self) -> None:
        self.dismiss(self._added)


class PyodeApp(App[None]):
    CSS_PATH = "pyode.tcss"
    TITLE = "pyode"

    BINDINGS = bindings()

    def __init__(
        self,
        library: StationLibrary | None = None,
        player: MpvPlayer | None = None,
        api: RadioBrowser | None = None,
        *,
        visualiser: bool = True,
        autoplay: Station | None = None,
        keymap: str = "standard",
        config_path: Path | None = None,
    ) -> None:
        super().__init__()
        self.library = library if library is not None else StationLibrary.load()
        self.player = player if player is not None else MpvPlayer()
        self.api = api if api is not None else RadioBrowser()
        self.keymap = keymap
        if keymap == "vim":
            for control in CONTROLS:
                if control.vim:
                    self.bind(control.vim, control.action)
        self._config_path = config_path
        self._favourites_only = False
        self._visible: list[Station] = []
        self._removed: tuple[Station, int] | None = None
        self._visualiser = visualiser
        self._autoplay = autoplay
        self._playing_key: str | None = None
        self._last_state: PlayerState | None = None
        self._last_signature: tuple | None = None
        self._timer: Timer | None = None

    # -- layout -----------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield TopBar()
        with Horizontal(id="body"):
            with Vertical(id="stations"):
                yield Label("STATIONS", id="stations-title", classes="eyebrow")
                yield StationTable(
                    id="station-table",
                    cursor_type="row",
                    show_header=False,
                    cell_padding=0,
                )
                yield Static("", id="stations-empty")
            yield DialRule(id="dial")
            with Vertical(id="right"):
                yield NowPlaying(id="meta")
                with Vertical(id="signal-pane"):
                    yield Label("SIGNAL", classes="eyebrow")
                    yield SignalMeter(id="signal")
        yield ControlBar(keymap=self.keymap, id="controls")

    def on_mount(self) -> None:
        self.query_one(SignalMeter).enabled = self._visualiser
        self.refresh_stations()
        self.query_one("#station-table", StationTable).focus()
        self._save_preferences()
        self._timer = self.set_interval(TICK, self._tick)
        if self._autoplay is not None:
            self._start(self._autoplay)

    def on_station_table_resized(self, _event: StationTable.Resized) -> None:
        self.refresh_stations()

    def on_resize(self, event: events.Resize) -> None:
        """Drop panes that no longer have room to be legible."""
        if not self.screen_stack:
            return
        screen = self.screen
        screen.set_class(event.size.width < NARROW_WIDTH, "-narrow")
        screen.set_class(event.size.height < SHORT_HEIGHT, "-short")

    # -- state ------------------------------------------------------------

    def refresh_stations(self, cursor: int | None = None) -> None:
        table = self.query_one("#station-table", StationTable)
        empty = self.query_one("#stations-empty", Static)
        # An empty library is an invitation, not a blank pane.
        self._visible = self.library.favorites() if self._favourites_only else list(self.library)
        self.query_one("#stations-title", Label).update(
            "FAVOURITES" if self._favourites_only else "STATIONS"
        )
        empty.display = not self._visible
        empty.update(
            "No favourites yet.\nPress F to show all stations."
            if self._favourites_only
            else "No stations yet.\nPress / to search the directory."
        )
        table.display = bool(self._visible)
        keep = table.cursor_row if cursor is None else cursor
        # cell_padding is 0, so the column is exactly the row string we build.
        width = max(20, table.size.width)
        table.clear(columns=True)
        table.add_column("Station", width=width)
        for station in self._visible:
            table.add_row(_row(station, width))
        if self._visible:
            table.move_cursor(row=max(0, min(keep, len(self._visible) - 1)))
        self._sync_dial()

    def _sync_dial(self) -> None:
        """Point the needle at the playing station's place in the library."""
        rule = self.query_one(DialRule)
        keys = [s.key for s in self.library]
        if self._playing_key not in keys or len(keys) < 2:
            rule.position = 0.0 if self._playing_key in keys else None
            return
        rule.position = keys.index(self._playing_key) / (len(keys) - 1)

    @property
    def selected(self) -> Station | None:
        row = self.query_one("#station-table", StationTable).cursor_row
        return self._visible[row] if 0 <= row < len(self._visible) else None

    def _playing_station(self) -> Station | None:
        return self.library.find(self._playing_key) if self._playing_key else None

    def _tick(self) -> None:
        try:
            meter = self.query_one(SignalMeter)
        except NoMatches:
            # Widgets are torn down before the app's unmount handler runs, so a
            # tick can land after they are gone.  Nothing to draw on.
            return

        status = self.player.status
        if self._visualiser:
            meter.push(self.player.level())

        # Buffer seconds drift constantly; comparing the rounded value keeps
        # the text panes from re-rendering ten times a second.
        signature = (
            status.state,
            status.now_playing,
            status.volume,
            status.muted,
            status.error,
            round(status.cache_seconds),
        )
        if signature != self._last_signature:
            try:
                self._apply(status)
            except NoMatches:
                # Children can already be removed while their parent widgets
                # are still queryable during shutdown.
                return
            self._last_signature = signature

    def _apply(self, status: PlayerStatus) -> None:
        station = self._playing_station()
        # The side panel carries the detail; a toast is what gets noticed when
        # a station dies while you are looking at the list.
        if status.state is PlayerState.ERROR and self._last_state is not PlayerState.ERROR:
            name = station.name if station else "The stream"
            self.notify(f"{name}: {status.error}.", title="Stream failed", severity="error")
        self._last_state = status.state
        self.query_one(NowPlaying).update(status, station)
        self.query_one(ControlBar).update(status, station)
        self.query_one(TopBar).update(
            station_count=len(self.library), live=status.state.name == "PLAYING"
        )

    # -- actions ----------------------------------------------------------

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        # Main-screen shortcuts must never edit the library or transport while
        # a modal owns focus (including its results table).
        return not isinstance(self.screen, ModalScreen)

    def _save_preferences(self) -> None:
        if self._config_path is None:
            return
        try:
            Preferences(self.keymap, self.player.status.volume, self._visualiser).save(
                self._config_path
            )
        except OSError as exc:
            self.notify(f"Could not save preferences: {exc}", severity="error")

    def action_cursor_up(self) -> None:
        self.query_one("#station-table", StationTable).action_cursor_up()

    def action_cursor_down(self) -> None:
        self.query_one("#station-table", StationTable).action_cursor_down()

    def action_help(self) -> None:
        self.push_screen(HelpScreen())

    def action_filter_favourites(self) -> None:
        selected = self.selected
        self._favourites_only = not self._favourites_only
        self.refresh_stations()
        if selected is not None:
            self._select_key(selected.key)
        if self._visible:
            self.query_one("#station-table", StationTable).focus()

    def _select_key(self, key: str) -> None:
        keys = [s.key for s in self._visible]
        if key in keys:
            self.query_one("#station-table", StationTable).move_cursor(row=keys.index(key))

    def action_play(self) -> None:
        station = self.selected
        if station is None:
            self.notify("No station selected. Press / to search the directory.")
            return
        self._start(station)

    def _start(self, station: Station) -> None:
        self._select_key(station.key)
        self.player.play(station.url, station.name)
        self._playing_key = station.key
        # Starting a station always deserves a redraw.  Without this, retrying
        # a station that fails the same way twice produces an identical status
        # signature, and the second failure would pass unreported.
        self._last_state = None
        self._last_signature = None
        self.query_one(SignalMeter).clear()
        self._sync_dial()
        if station.stationuuid:
            self._register_click(station.stationuuid)

    @work
    async def _register_click(self, stationuuid: str) -> None:
        """Report the play to the directory so its rankings stay useful."""
        await self.api.register_click(stationuuid)

    def action_toggle_pause(self) -> None:
        self.player.toggle_pause()

    def action_stop(self) -> None:
        self.player.stop()
        self._playing_key = None
        self.query_one(SignalMeter).clear()
        self._sync_dial()

    def action_toggle_mute(self) -> None:
        self.player.toggle_mute()

    def action_volume(self, delta: int) -> None:
        self.player.adjust_volume(delta)
        self._save_preferences()

    def action_toggle_favourite(self) -> None:
        station = self.selected
        if station is None:
            return
        try:
            with self.library.editing():
                self.library.toggle_favorite(station.key)
        except OSError as exc:
            self.notify(f"Could not save favourites: {exc}", severity="error")
        self.refresh_stations()

    def action_remove(self) -> None:
        station = self.selected
        if station is None:
            return
        index = next(i for i, item in enumerate(self.library) if item.key == station.key)
        try:
            with self.library.editing():
                self.library.remove(station.key)
        except OSError as exc:
            self.notify(f"Could not remove {station.name}: {exc}", severity="error")
            self.refresh_stations()
            return
        self._removed = (station, index)
        if station.key == self._playing_key:
            self.action_stop()
        self.refresh_stations()
        self.notify(f"Removed {station.name}. Press u to undo.")

    def action_undo_remove(self) -> None:
        if self._removed is None:
            self.notify("Nothing to restore.")
            return
        station, index = self._removed
        if self.library.find(station.key) is not None:
            self.notify(f"{station.name} is already in your library.")
            self._removed = None
            return
        try:
            with self.library.editing():
                self.library.add(station)
                last = len(self.library) - 1
                self.library.move(last, index - last)
        except OSError as exc:
            self.notify(f"Could not restore {station.name}: {exc}", severity="error")
            return
        self._removed = None
        self.refresh_stations()
        self._select_key(station.key)
        if self._visible:
            self.query_one("#station-table", StationTable).focus()
        self.notify(f"Restored {station.name}.")

    def action_toggle_visualiser(self) -> None:
        self._visualiser = not self._visualiser
        self.query_one(SignalMeter).enabled = self._visualiser
        self._save_preferences()

    def action_search(self) -> None:
        self.push_screen(SearchScreen(self.api, self.library), self._after_search)

    def _after_search(self, added: int | None) -> None:
        if added:
            self.refresh_stations()
            self.notify(f"Added {added} station{'s' if added > 1 else ''}.")

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        if event.data_table.id == "station-table":
            self.action_play()

    async def on_unmount(self) -> None:
        # The timer outlives the widgets it queries, so it has to go first.
        if self._timer is not None:
            self._timer.stop()
            self._timer = None
        self.player.close()
        await self.api.close()
