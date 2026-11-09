# SPDX-License-Identifier: GPL-2.0-or-later
"""The pyode interface.

The UI polls the player rather than subscribing to it.  `PlayerStatus` is a
pure snapshot of cached state, so reading it ten times a second is cheap, and
polling keeps every mpv call on the main thread -- which is the invariant that
keeps `player` safe (see its module docstring).
"""

from __future__ import annotations

import contextlib
import logging
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
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

from pyode import mediacontrols, recorder
from pyode.browser import RadioBrowser, RadioBrowserError
from pyode.config import Preferences
from pyode.history import TrackHistory
from pyode.keys import CONTROLS, bindings, help_text, table_action
from pyode.player import MpvPlayer, PlayerState, PlayerStatus
from pyode.sleep import SleepTimer, next_step
from pyode.stations import LibraryConflictError, Station, StationLibrary
from pyode.widgets import ControlBar, DialRule, NowPlaying, SignalMeter, TopBar

log = logging.getLogger(__name__)

TICK = 0.1  # seconds between meter samples
# Holding + or - repeats the key many times a second; the volume follows every
# press, but the preferences file is written once the keys have settled.
VOLUME_SAVE_DELAY = 0.5
LIBRARY_POLL = 2.0  # seconds between checks for outside edits to the CSV

# Reconnecting after a stream drops.  Each entry is the wait before one
# attempt, so its length is the number of attempts before giving up.
RECONNECT_DELAYS = (1, 2, 4, 8, 15)
# A connection that held this long counts as a recovery, and a later drop gets
# a full set of attempts again.  Shorter than this and the station is
# flapping, so drops keep using up the same allowance.
STABLE_SECONDS = 30.0

# Below these, a pane cannot say anything useful, so it is dropped rather than
# squeezed into an unreadable sliver.
NARROW_WIDTH = 68
SHORT_HEIGHT = 20


def _row(station: Station, width: int) -> str:
    """Lay a station out as one fixed-width line.

    The list owns its own column arithmetic so names truncate rather than
    growing the table past its pane and summoning a scrollbar.
    """
    mark = "♥ " if station.favorite else "  "
    quality = station.quality
    room = max(4, width - len(mark) - len(quality) - 1)
    name = station.name if len(station.name) <= room else f"{station.name[: room - 1]}…"
    gap = max(1, width - len(mark) - len(name) - len(quality))
    return f"{mark}{name}{' ' * gap}{quality}"


class NavigationTable(DataTable):
    """Handle list motions locally, leaving Input editing untouched."""

    def on_key(self, event: events.Key) -> None:
        action = table_action(event.key, self.app.keymap, search=self.id != "station-table")
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


class HistoryScreen(ModalScreen):
    """Recently heard tracks, newest first."""

    BINDINGS = [
        Binding("escape", "dismiss", "Close", show=False),
        Binding("c", "copy", "Copy", show=False),
    ]

    def __init__(self, history: TrackHistory) -> None:
        super().__init__()
        self._tracks = history.recent()

    def compose(self) -> ComposeResult:
        with Vertical(id="history-box"):
            yield Label("TRACK HISTORY", classes="eyebrow")
            yield NavigationTable(id="history", cursor_type="row")
            yield Static("", id="history-status")

    def on_mount(self) -> None:
        table = self.query_one("#history", DataTable)
        table.add_columns("Time", "Title", "Station")
        for track in self._tracks:
            table.add_row(track.when(), track.title, track.station)
        self.query_one("#history-status", Static).update(
            "Enter or c copies the highlighted title. Escape closes."
            if self._tracks
            else "No tracks yet. Titles appear here as stations announce them."
        )
        table.focus()

    def action_copy(self) -> None:
        row = self.query_one("#history", DataTable).cursor_row
        if 0 <= row < len(self._tracks):
            self.app.copy_title(self._tracks[row].title)

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        event.stop()
        self.action_copy()


def _native_copy(text: str) -> bool:
    """Copy with the platform's clipboard tool, if there is one."""
    if sys.platform == "darwin":
        candidates = [["pbcopy"]]
    elif sys.platform == "win32":
        candidates = [["clip"]]
    else:
        candidates = [["wl-copy"], ["xclip", "-selection", "clipboard"], ["xsel", "-ib"]]
    for command in candidates:
        if shutil.which(command[0]) is None:
            continue
        try:
            subprocess.run(command, input=text.encode(), check=True, timeout=2)
        except (OSError, subprocess.SubprocessError):
            continue
        return True
    return False


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


@dataclass
class _Level:
    """One level of the directory dialog: what the table is showing."""

    title: str
    kind: str  # "menu", "stations", "tags" or "countries"
    items: list = field(default_factory=list)
    cursor: int = 0  # restored when coming back to this level


# The dialog's opening menu: label and what choosing it does.
BROWSE_MENU = (
    ("Popular stations", "popular"),
    ("Top voted", "voted"),
    ("By genre…", "tags"),
    ("By country…", "countries"),
)

DIRECTORY_LIMIT = 60  # stations per list; enough to choose from, quick to fetch
GENRE_LIMIT = 100


class SearchScreen(ModalScreen[int]):
    """Browse or search the directory and add stations to the library.

    The table shows one level at a time, kept on a stack: the menu at the
    bottom, then whatever was opened from it.  Enter opens a level or adds a
    station, and Escape goes back one level before it closes the dialog.
    Space plays the highlighted station as a preview; the app decides what to
    play once the dialog closes (see `PyodeApp.end_preview`).
    """

    BINDINGS = [
        Binding("escape", "back", "Back", show=False),
        Binding("down", "focus_results", show=False),
        # Space reaches the screen only from the list: the search box types it.
        Binding("space", "preview", "Listen", show=False),
    ]

    def __init__(self, api: RadioBrowser, library: StationLibrary) -> None:
        super().__init__()
        self._api = api
        self._library = library
        self._levels = [_Level("Browse the directory", "menu", list(BROWSE_MENU))]
        self._added = 0

    def compose(self) -> ComposeResult:
        with Vertical(id="search-box"):
            yield Label("THE DIRECTORY", classes="eyebrow")
            yield Input(placeholder="Search by station name or tag, or browse below")
            yield NavigationTable(id="results", cursor_type="row")
            yield Static("", id="search-status")

    def on_mount(self) -> None:
        self._show_level()
        self.query_one(Input).focus()

    @property
    def _level(self) -> _Level:
        return self._levels[-1]

    def _status(self, message: str) -> None:
        self.query_one("#search-status", Static).update(message)

    def _show_level(self, message: str = "") -> None:
        """Show the top level, with a status line saying what Enter will do."""
        level = self._level
        table = self.query_one("#results", DataTable)
        table.clear(columns=True)
        if level.kind == "menu":
            table.add_column("Browse")
            for label, _action in level.items:
                table.add_row(label)
            hint = "Type a search and press Enter, or choose a list."
        elif level.kind == "stations":
            table.add_columns("Station", "Country", "Quality")
            for station in level.items:
                table.add_row(station.name, station.country, station.quality)
            hint = "Space listens, Enter adds. Escape goes back."
            if not level.items:
                hint = "Nothing found. Escape goes back."
        else:
            table.add_columns("Genre" if level.kind == "tags" else "Country", "Stations")
            for item in level.items:
                table.add_row(item.name, str(item.station_count))
            hint = "Enter lists its stations. Escape goes back."
        if level.items:
            table.move_cursor(row=min(level.cursor, len(level.items) - 1))
        prefix = "" if level.kind == "menu" else f"{level.title}. "
        self._status(message or f"{prefix}{hint}")

    def _open(self, level: _Level, message: str = "") -> None:
        self._level.cursor = self.query_one("#results", DataTable).cursor_row
        self._levels.append(level)
        self._show_level(message)
        self.query_one("#results", DataTable).focus()

    # -- searching ----------------------------------------------------------

    def on_input_submitted(self, event: Input.Submitted) -> None:
        query = event.value.strip()
        if query:
            self._search(query)

    @work(exclusive=True, group="directory")
    async def _search(self, query: str) -> None:
        self._status(f"Searching for {query}…")
        message = ""
        try:
            found = await self._api.search(name=query, limit=DIRECTORY_LIMIT)
            if not found:
                # A word that finds nothing as a name is usually a genre.
                found = await self._api.search(tag=query, limit=DIRECTORY_LIMIT)
                if found:
                    message = f"No station named {query}. Showing the {query} tag instead."
        except RadioBrowserError as exc:
            self._status(str(exc))
            return
        # A new search replaces whatever was being browsed, so Escape leads
        # straight back to the menu rather than through old results.
        del self._levels[1:]
        if not found:
            message = f"Nothing found for {query}. Escape goes back."
        self._open(_Level(f"Results for {query}", "stations", found), message)

    # -- browsing -----------------------------------------------------------

    @work(exclusive=True, group="directory")
    async def _browse(self, title: str, kind: str, fetch) -> None:
        self._status(f"Loading {title.lower().rstrip('…')}…")
        try:
            items = await fetch()
        except RadioBrowserError as exc:
            self._status(str(exc))
            return
        self._open(_Level(title, kind, items))

    def _choose(self, row: int) -> None:
        level = self._level
        if not 0 <= row < len(level.items):
            return
        item = level.items[row]
        api = self._api
        if level.kind == "stations":
            self._add(item)
        elif level.kind == "menu":
            label, action = item
            if action == "popular":
                self._browse(label, "stations", lambda: api.top_clicked(DIRECTORY_LIMIT))
            elif action == "voted":
                self._browse(label, "stations", lambda: api.top_voted(DIRECTORY_LIMIT))
            elif action == "tags":
                self._browse("Genres", "tags", lambda: api.tags(limit=GENRE_LIMIT))
            elif action == "countries":
                self._browse("Countries", "countries", self._countries)
        elif level.kind == "tags":
            self._browse(
                item.name,
                "stations",
                lambda: api.search(tag=item.name, tag_exact=True, limit=DIRECTORY_LIMIT),
            )
        elif level.kind == "countries":
            self._browse(
                item.name,
                "stations",
                lambda: api.search(country_code=item.code, limit=DIRECTORY_LIMIT),
            )

    async def _countries(self) -> list:
        # Alphabetical is what anyone scanning for a country expects; empty
        # entries and ones without a code to search by are left out.
        countries = await self._api.countries()
        usable = [c for c in countries if c.code and c.station_count]
        return sorted(usable, key=lambda c: c.name.casefold())

    def _add(self, station: Station) -> None:
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

    # -- keys ---------------------------------------------------------------

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        event.stop()
        self._choose(event.cursor_row)

    def action_focus_results(self) -> None:
        # Down from the search box moves into the list, as it would in a
        # menu; Input has no use for it.
        self.query_one("#results", DataTable).focus()

    def action_preview(self) -> None:
        level = self._level
        row = self.query_one("#results", DataTable).cursor_row
        if level.kind != "stations" or not 0 <= row < len(level.items):
            return
        station = level.items[row]
        previewing = self.app.previewing
        if previewing is not None and previewing.key == station.key:
            self.app.stop_preview()
            self._status(f"Stopped {station.name}.")
            return
        self.app.start_preview(station)
        self._status(f"Listening to {station.name}. Enter adds it; Space stops.")

    def action_back(self) -> None:
        if len(self._levels) == 1:
            self.app.end_preview()
            self.dismiss(self._added)
            return
        self.workers.cancel_group(self, "directory")
        self._levels.pop()
        self._show_level()
        self.query_one("#results", DataTable).focus()


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
        history: TrackHistory | None = None,
        save_history: bool = True,
        media_controls: bool = True,
        sleep_minutes: float | None = None,
    ) -> None:
        super().__init__()
        self.library = library if library is not None else StationLibrary.load()
        self.history = history if history is not None else TrackHistory.load(persist=save_history)
        self._history_error = False
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
        # The station itself, not just its key: an outside edit can remove it
        # from the library while it plays, and it should play on regardless.
        self._playing: Station | None = None
        self._last_state: PlayerState | None = None
        self._last_signature: tuple | None = None
        self._timer: Timer | None = None
        self._library_timer: Timer | None = None
        self._volume_save_timer: Timer | None = None
        self._library_conflict = False
        # Recovery state for the station that is playing.
        self._playing_since: float | None = None
        self._reconnect_attempt = 0
        self._reconnect_timer: Timer | None = None
        self._notice = ""
        self._repair_tried = False
        # Listening from the directory dialog.  `_preview` is what is playing
        # now, if anything; `_resume_key` is what was playing when the first
        # preview started, to go back to when the dialog closes.
        self._preview: Station | None = None
        self._preview_session = False
        self._resume_key: str | None = None
        # Now Playing and the media keys.  Registered on mount, so a test that
        # never mounts the app never touches the system's media controls.
        self._media_controls = media_controls
        self._media: mediacontrols.MediaSession = mediacontrols.NullSession()
        # The sleep timer.  `_sleep_base` is the volume from before the fade
        # began, to put back afterwards; it is set only while fading.
        self._clock = time.monotonic
        self._sleep_minutes = sleep_minutes
        self._sleep: SleepTimer | None = None
        self._sleep_base: int | None = None
        self._recording: recorder.Recording | None = None
        self._cue_error = False

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
                yield NowPlaying(keymap=self.keymap, id="meta")
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
        self._library_timer = self.set_interval(LIBRARY_POLL, self._poll_library)
        self._media = mediacontrols.create(
            mediacontrols.Commands(
                play=self._media_play,
                pause=self._media_pause,
                toggle=self._media_toggle,
                stop=self._media_stop,
                next=lambda: self._step_station(1),
                previous=lambda: self._step_station(-1),
                set_volume=self._media_volume,
            ),
            enabled=self._media_controls,
        )
        if self._sleep_minutes:
            self._set_sleep(self._sleep_minutes)
        if self._autoplay is not None:
            self._start(self._autoplay)

    def on_station_table_resized(self, _event: StationTable.Resized) -> None:
        # A resize can be queued during shutdown, after the table has gone.
        with contextlib.suppress(NoMatches):
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

    @property
    def _playing_key(self) -> str | None:
        return self._playing.key if self._playing is not None else None

    def _playing_station(self) -> Station | None:
        """What is playing: the library's copy if it has one, which carries any
        outside edit to its name, or else the station as it was started."""
        if self._preview is not None:
            return self._preview
        if self._playing is None:
            return None
        return self.library.find(self._playing.key) or self._playing

    def _tick(self) -> None:
        # Deliver any media keys pressed since the last tick.
        self._media.pump()
        try:
            self._sleep_tick()
            self._recording_tick()
        except NoMatches:
            return
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
        if status.state is PlayerState.PLAYING and self._playing_since is None:
            self._playing_since = time.monotonic()
        if status.state is PlayerState.ERROR and self._last_state is not PlayerState.ERROR:
            self._stream_failed(status, station)
        self._last_state = status.state
        if station is not None and status.now_playing.title:
            self._record(station.name, status.now_playing.title)
        if self._recording is not None:
            if status.state is PlayerState.ERROR and self._reconnect_timer is None:
                self._finish_recording()  # dropped for good: nothing more is coming
            elif self._recording.mark(status.now_playing.title, self._clock()):
                self._write_cue()
        reconnecting = self._reconnect_timer is not None
        self.query_one(NowPlaying).update(status, station, self._notice)
        self._media.update(
            mediacontrols.describe(status, station.name if station else None), status.volume
        )
        self.query_one(ControlBar).update(status, station, reconnecting=reconnecting)
        self.query_one(TopBar).update(
            station_count=len(self.library), live=status.state.name == "PLAYING"
        )

    def _record(self, station: str, title: str) -> None:
        try:
            self.history.record(station, title)
        except OSError as exc:
            if not self._history_error:  # say so once, not on every track
                self._history_error = True
                self.notify(f"Could not save track history: {exc}", severity="error")

    def copy_title(self, title: str) -> None:
        # OSC 52 reaches the clipboard through most terminals, even over SSH,
        # but not macOS Terminal; the platform tool covers that locally.
        self.copy_to_clipboard(title)
        _native_copy(title)
        self.notify(f"Copied: {title}")

    # -- recovery ---------------------------------------------------------

    def _stream_failed(self, status: PlayerStatus, station: Station | None) -> None:
        """Decide what a failure means and what to do about it.

        A stream that was playing and stopped has dropped, which on internet
        radio is usually the network, so it is retried with backoff.  A stream
        that never started is more likely a dead address; that gets reported,
        and a directory station is looked up again in case it has moved.
        """
        played_for = (
            time.monotonic() - self._playing_since if self._playing_since is not None else None
        )
        self._playing_since = None
        name = station.name if station else "The stream"

        if station is None:
            self.notify(f"{name}: {status.error}.", title="Stream failed", severity="error")
            return
        if self._preview is not None:
            # A preview is a quick listen, not a station to nurse back: no
            # reconnecting, and no repairing an entry that is not in the library.
            self.notify(f"{name}: {status.error}.", title="Preview failed", severity="error")
            return
        if self._last_state is PlayerState.PAUSED:
            # Reconnecting would start the audio the user just paused.
            self.notify(f"{name} dropped while paused. Press space to resume.")
            return

        if played_for is not None or self._reconnect_attempt:
            if played_for is not None and played_for >= STABLE_SECONDS:
                self._reconnect_attempt = 0
            if self._reconnect_attempt < len(RECONNECT_DELAYS):
                self._schedule_reconnect(name)
                return
            self._reconnect_attempt = 0
            self.notify(
                f"{name}: {status.error}. Gave up after {len(RECONNECT_DELAYS)} attempts.",
                title="Stream failed",
                severity="error",
            )
            return

        # The side panel carries the detail; a toast is what gets noticed when
        # a station dies while you are looking at the list.
        self.notify(f"{name}: {status.error}.", title="Stream failed", severity="error")
        if station.stationuuid and not self._repair_tried:
            self._repair_tried = True
            self._repair(station)

    def _schedule_reconnect(self, name: str) -> None:
        delay = RECONNECT_DELAYS[self._reconnect_attempt]
        self._reconnect_attempt += 1
        total = len(RECONNECT_DELAYS)
        self._notice = f"Connection lost. Reconnecting ({self._reconnect_attempt}/{total})…"
        self._reconnect_timer = self.set_timer(delay, self._reconnect)
        if self._reconnect_attempt == 1:
            # Later attempts are counted in the side panel, not toasted.
            self.notify(f"Lost {name}. Reconnecting in {delay}s.", severity="warning")

    def _reconnect(self) -> None:
        self._reconnect_timer = None
        station = self._playing_station()
        if station is not None:
            self._start(station, user=False)

    def _cancel_reconnect(self) -> None:
        if self._reconnect_timer is not None:
            self._reconnect_timer.stop()
            self._reconnect_timer = None
        self._reconnect_attempt = 0
        self._notice = ""

    @work(group="repair")
    async def _repair(self, station: Station) -> None:
        """Look a failed directory station up again, in case its address moved.

        Station addresses go stale constantly, and the directory keeps the
        current one under the same UUID.  If it differs from the saved address,
        the library is updated and the station retried.
        """
        try:
            found = await self.api.by_uuid(station.stationuuid)
        except RadioBrowserError:
            return
        fresh = next((s for s in found if s.stationuuid == station.stationuuid), None)
        if fresh is None or fresh.url.rstrip("/") == station.url.rstrip("/"):
            return

        changes: dict[str, object] = {"url": fresh.url}
        if fresh.codec:
            changes["codec"] = fresh.codec
        if fresh.bitrate:
            changes["bitrate"] = fresh.bitrate
        try:
            with self.library.editing():
                updated = self.library.update(station.key, **changes)
        except OSError as exc:
            self.notify(f"Could not save the new address for {station.name}: {exc}")
            return
        if updated is None:
            return  # removed while we were asking
        self.refresh_stations()

        still_failed = self.player.status.state is PlayerState.ERROR
        if self._playing_key == station.key and still_failed:
            self.notify(f"{station.name} has moved. Saved its new address; retrying.")
            self._start(updated, user=False)
        else:
            self.notify(f"{station.name} has moved. Saved its new address.")

    # -- outside edits ----------------------------------------------------

    def _poll_library(self) -> None:
        """Pick up edits made to the CSV while pyode is open."""
        selected = self.selected
        try:
            changed = self.library.reload_if_changed()
        except LibraryConflictError as exc:
            if not self._library_conflict:
                self._library_conflict = True
                self.notify(str(exc), title="Station library", severity="error")
            return
        self._library_conflict = False
        if not changed:
            return
        try:
            self.refresh_stations()
        except NoMatches:
            return  # shutting down
        if selected is not None:
            self._select_key(selected.key)
        self.notify(f"Reloaded {self.library.path.name}; it changed on disk.")

    # -- actions ----------------------------------------------------------

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        # Main-screen shortcuts must never edit the library or transport while
        # a modal owns focus (including its results table).
        return not isinstance(self.screen, ModalScreen)

    def _save_preferences(self, *, report: bool = True) -> None:
        """Write every preference now.

        This includes the current volume, so it also covers any volume save
        still waiting to happen.
        """
        if self._volume_save_timer is not None:
            self._volume_save_timer.stop()
            self._volume_save_timer = None
        if self._config_path is None:
            return
        try:
            Preferences(
                self.keymap, self._resting_volume, self._visualiser, self.history.persist
            ).save(self._config_path)
        except OSError as exc:
            if report:
                self.notify(f"Could not save preferences: {exc}", severity="error")
            else:
                log.warning("could not save preferences: %s", exc)

    def _save_volume_later(self) -> None:
        if self._volume_save_timer is not None:
            self._volume_save_timer.stop()
        self._volume_save_timer = self.set_timer(VOLUME_SAVE_DELAY, self._save_preferences)

    def action_cursor_up(self) -> None:
        self.query_one("#station-table", StationTable).action_cursor_up()

    def action_cursor_down(self) -> None:
        self.query_one("#station-table", StationTable).action_cursor_down()

    def action_help(self) -> None:
        self.push_screen(HelpScreen())

    def action_history(self) -> None:
        self.push_screen(HistoryScreen(self.history))

    def action_copy_title(self) -> None:
        title = self.player.status.now_playing.title if self._playing_key else ""
        if not title:
            self.notify("This station has not announced a track title.")
            return
        self.copy_title(title)

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

    def _start(self, station: Station, *, user: bool = True) -> None:
        """Play a station.

        `user` is False for pyode's own retries: those keep the recovery state,
        leave the cursor where the user put it, and are not reported to the
        directory as another play.
        """
        if user:
            self._cancel_reconnect()
            self._repair_tried = False
            self._select_key(station.key)
        else:
            if self._reconnect_timer is not None:
                self._reconnect_timer.stop()
                self._reconnect_timer = None
            self._notice = ""
        self._playing_since = None
        # A reconnect carries a recording on into a new file; anything the
        # user starts ends it, since the player closes it with the stream.
        carried = self._recording if not user else None
        if carried is None:
            self._finish_recording()
        self.player.play(station.url, station.name)
        self._playing = station
        if carried is not None:
            self._continue_recording(carried)
        # Starting a station always deserves a redraw.  Without this, retrying
        # a station that fails the same way twice produces an identical status
        # signature, and the second failure would pass unreported.
        self._last_state = None
        self._last_signature = None
        self.query_one(SignalMeter).clear()
        self._sync_dial()
        if user and station.stationuuid:
            self._register_click(station.stationuuid)

    # -- previews -----------------------------------------------------------

    @property
    def previewing(self) -> Station | None:
        return self._preview

    def start_preview(self, station: Station) -> None:
        """Play a directory station without adding it to the library."""
        if not self._preview_session:
            self._preview_session = True
            self._resume_key = self._playing_key
        self._cancel_reconnect()
        self._finish_recording()
        self._preview = station
        self._playing = None
        self._playing_since = None
        self._last_state = None
        self._last_signature = None
        self.player.play(station.url, station.name)
        self.query_one(SignalMeter).clear()
        self._sync_dial()
        if station.stationuuid:
            self._register_click(station.stationuuid)  # a preview is still a listen

    def stop_preview(self) -> None:
        if self._preview is None:
            return
        self._preview = None
        self.player.stop()
        self.query_one(SignalMeter).clear()

    def end_preview(self) -> None:
        """Settle what plays once the directory dialog closes.

        A previewed station that was added keeps playing, now as a library
        station.  Otherwise whatever was playing before the first preview comes
        back, or playback stops if nothing was.
        """
        if not self._preview_session:
            return
        preview, resume_key = self._preview, self._resume_key
        self._preview, self._preview_session, self._resume_key = None, False, None
        added = self.library.find(preview.key) if preview is not None else None
        if added is not None:
            self._playing = added
            self._last_signature = None
            self._sync_dial()
            return
        resume = self.library.find(resume_key) if resume_key else None
        if resume is not None:
            self._start(resume, user=False)
            return
        self.player.stop()
        self._playing = None
        self.query_one(SignalMeter).clear()
        self._sync_dial()

    @work
    async def _register_click(self, stationuuid: str) -> None:
        """Report the play to the directory so its rankings stay useful."""
        await self.api.register_click(stationuuid)

    def action_toggle_pause(self) -> None:
        station = self._playing_station()
        if station is not None and self.player.status.state is PlayerState.ERROR:
            # Nothing to resume once the stream has gone, so start it again.
            self._start(station)
            return
        self.player.toggle_pause()

    def action_stop(self) -> None:
        self._cancel_reconnect()
        self._finish_recording()
        self.player.stop()
        self._playing = None
        self._playing_since = None
        self.query_one(SignalMeter).clear()
        self._sync_dial()

    # -- media keys ---------------------------------------------------------

    def _media_play(self) -> None:
        state = self.player.status.state
        if state is PlayerState.PAUSED:
            self.player.toggle_pause()
        elif state in (PlayerState.IDLE, PlayerState.ERROR) and not self._preview_session:
            station = self._playing_station()
            if station is not None:
                self._start(station)
            else:
                self.action_play()

    def _media_pause(self) -> None:
        if self.player.status.state in (PlayerState.PLAYING, PlayerState.BUFFERING):
            self.player.toggle_pause()

    def _media_toggle(self) -> None:
        if self.player.status.state in (PlayerState.PLAYING, PlayerState.BUFFERING):
            self.player.toggle_pause()
        else:
            self._media_play()

    def _media_stop(self) -> None:
        if self._preview_session:
            self.stop_preview()
        else:
            self.action_stop()

    def _media_volume(self, value: int) -> None:
        self._wake()
        self.player.set_volume(value)
        self._save_volume_later()

    def _step_station(self, delta: int) -> None:
        """Play the next or previous station in the list, wrapping at the ends.

        Steps from the playing station when it is in view; otherwise plays
        the station under the cursor.  Ignored while the directory dialog is open, where a library
        station would trample the preview.
        """
        if self._preview_session or not self._visible:
            return
        keys = [s.key for s in self._visible]
        if self._playing_key in keys:
            here = keys.index(self._playing_key)
        else:
            here = self.query_one("#station-table", StationTable).cursor_row - delta
        self._start(self._visible[(here + delta) % len(self._visible)])

    # -- recording ------------------------------------------------------------

    def action_record(self) -> None:
        if self._recording is not None:
            self._finish_recording()
            return
        station = self._playing_station()
        status = self.player.status
        if station is None or status.state is not PlayerState.PLAYING:
            self.notify("Recording starts once a station is playing.")
            return
        directory = recorder.default_dir()
        try:
            directory.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            self.notify(f"Could not create {directory}: {exc}", severity="error")
            return
        extension = recorder.extension_for(status.now_playing.codec)
        self._begin_recording(station.name, extension, directory)
        self._recording.mark(status.now_playing.title, self._clock())
        self._write_cue()
        self.notify(f"Recording to {self._recording.path}")

    def _begin_recording(self, station: str, extension: str, directory: Path) -> None:
        self._recording = recorder.Recording.begin(
            station, extension, self._clock(), directory=directory, when=datetime.now()
        )
        self._cue_error = False
        self.player.record(str(self._recording.path))

    def _continue_recording(self, previous: recorder.Recording) -> None:
        """After a drop, carry on recording the same station into a new file."""
        self._begin_recording(previous.station, previous.path.suffix, previous.path.parent)
        self._write_cue()
        self.notify(f"Stream reconnected; recording continues in {self._recording.path.name}")

    def _finish_recording(self, *, report: bool = True) -> None:
        recording, self._recording = self._recording, None
        if recording is None:
            return
        self.player.record(None)
        if report:
            length = recording.label(self._clock())
            self.notify(f"Saved {recording.path.name} ({length}).", title="Recording")

    def _write_cue(self) -> None:
        if self._recording is None:
            return
        try:
            self._recording.write_cue()
        except OSError as exc:
            if not self._cue_error:  # once per recording, not per track
                self._cue_error = True
                self.notify(f"Could not write the track list: {exc}", severity="warning")

    def _recording_tick(self) -> None:
        recording = self._recording
        label = recording.label(self._clock()) if recording is not None else ""
        self.query_one(TopBar).set_recording(label)

    # -- sleep timer --------------------------------------------------------

    @property
    def _resting_volume(self) -> int:
        """The volume to save: never a point partway through the fade."""
        return self._sleep_base if self._sleep_base is not None else self.player.status.volume

    def action_sleep(self) -> None:
        minutes = next_step(self._sleep.minutes if self._sleep else None)
        self._set_sleep(minutes)
        self.notify(f"Sleeping in {minutes} minutes." if minutes else "Sleep timer off.")

    def _set_sleep(self, minutes: float | None) -> None:
        self._end_fade()
        self._sleep = SleepTimer.start(minutes, self._clock()) if minutes else None
        self._sleep_tick()

    def _end_fade(self) -> None:
        """Put the volume back where it was before the fade, if one began."""
        if self._sleep_base is not None:
            self.player.set_volume(self._sleep_base)
            self._sleep_base = None

    def _wake(self) -> None:
        """Touching the volume during the fade means someone is still awake."""
        if self._sleep_base is not None:
            self._set_sleep(None)
            self.notify("Sleep timer off.")

    def _sleep_tick(self) -> None:
        top = self.query_one(TopBar)
        timer = self._sleep
        if timer is None:
            top.set_sleep("")
            return
        now = self._clock()
        if timer.expired(now):
            self._set_sleep(None)  # also restores the volume, ready for next time
            if self._preview_session:
                self.stop_preview()
            elif self._playing is not None:
                self.action_stop()
            self.notify("Sleep timer: stopped playback.")
            return
        top.set_sleep(timer.label(now))
        if timer.fading(now) and self.player.status.is_active:
            if self._sleep_base is None:
                self._sleep_base = self.player.status.volume
            volume = timer.volume(self._sleep_base, now)
            if volume != self.player.status.volume:
                self.player.set_volume(volume)

    def action_toggle_mute(self) -> None:
        self.player.toggle_mute()

    def action_volume(self, delta: int) -> None:
        self._wake()
        self.player.adjust_volume(delta)
        self._save_volume_later()

    def action_move(self, delta: int) -> None:
        """Shift the selected station past its visible neighbour.

        With favourites filtered, the neighbour is the next favourite, which
        may be several places away in the full library.
        """
        station = self.selected
        if station is None:
            return
        target = self.query_one("#station-table", StationTable).cursor_row + delta
        if not 0 <= target < len(self._visible):
            return
        neighbour = self._visible[target]
        try:
            with self.library.editing():
                keys = [s.key for s in self.library]
                if station.key in keys and neighbour.key in keys:
                    index = keys.index(station.key)
                    self.library.move(index, keys.index(neighbour.key) - index)
        except OSError as exc:
            self.notify(f"Could not save the new order: {exc}", severity="error")
        self.refresh_stations()
        self._select_key(station.key)

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
        index = 0
        try:
            with self.library.editing():
                keys = [item.key for item in self.library]
                index = keys.index(station.key) if station.key in keys else 0
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
        # Adding may also have pulled in outside edits, so refresh regardless.
        self.refresh_stations()
        if added:
            self.notify(f"Added {added} station{'s' if added > 1 else ''}.")

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        if event.data_table.id == "station-table":
            self.action_play()

    async def on_unmount(self) -> None:
        # The timer outlives the widgets it queries, so it has to go first.
        for timer in (self._timer, self._library_timer, self._reconnect_timer):
            if timer is not None:
                timer.stop()
        self._timer = self._library_timer = self._reconnect_timer = None
        if self._volume_save_timer is not None:
            # Quitting right after a volume change must not lose it.  There is
            # no screen left to show an error on, so a failure is logged.
            self._save_preferences(report=False)
        self._finish_recording(report=False)
        self._media.close()
        self.player.close()
        await self.api.close()
