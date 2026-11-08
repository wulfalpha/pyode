# SPDX-License-Identifier: GPL-2.0-or-later
"""Interface tests, driven through Textual's Pilot against a fake player."""

import pytest
from textual.widgets import DataTable, Input, Static

from pyode.app import PyodeApp, SearchScreen, StationTable, _row
from pyode.player import PlayerState
from pyode.stations import StationLibrary
from pyode.widgets import ControlBar, DialRule, SignalMeter, _volume_readout


@pytest.fixture
def app(library, player, api):
    return PyodeApp(library=library, player=player, api=api)


# -- layout ---------------------------------------------------------------


async def test_library_fills_the_station_list(app, library):
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.query_one("#station-table", DataTable).row_count == len(library)


async def test_empty_library_invites_a_search(empty_library, player, api):
    async with PyodeApp(library=empty_library, player=player, api=api).run_test() as pilot:
        await pilot.pause()
        empty = pilot.app.query_one("#stations-empty", Static)
        assert empty.display
        assert "search" in str(empty.content).lower()
        assert not pilot.app.query_one("#station-table", DataTable).display


async def test_rows_are_hidden_once_stations_exist(app):
    async with app.run_test() as pilot:
        await pilot.pause()
        assert not pilot.app.query_one("#stations-empty", Static).display


# -- playback -------------------------------------------------------------


async def test_play_starts_the_selected_station(app, library, player):
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("p")
        assert player.calls[0] == ("play", library[0].url, library[0].name)


async def test_play_on_an_empty_library_does_not_call_the_player(empty_library, player, api):
    async with PyodeApp(library=empty_library, player=player, api=api).run_test() as pilot:
        await pilot.pause()
        await pilot.press("p")
        assert player.calls == []


async def test_selecting_a_row_plays_it(app, library, player):
    async with app.run_test() as pilot:
        await pilot.pause()
        pilot.app.query_one("#station-table", DataTable).move_cursor(row=1)
        await pilot.press("enter")
        await pilot.pause()
        assert player.calls[-1] == ("play", library[1].url, library[1].name)


async def test_stop_clears_the_playing_station(app, player):
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("p")
        await pilot.press("s")
        assert ("stop",) in player.calls
        assert pilot.app._playing_key is None


async def test_pause_and_volume_and_mute_reach_the_player(app, player):
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("p", "space", "minus", "m")
        kinds = [call[0] for call in player.calls]
        assert kinds == ["play", "toggle_pause", "set_volume", "toggle_mute"]


async def test_volume_is_clamped_at_the_bottom(app, player):
    async with app.run_test() as pilot:
        await pilot.pause()
        for _ in range(25):
            await pilot.press("minus")
        assert player.status.volume == 0


# -- the dial needle ------------------------------------------------------


async def test_needle_is_absent_until_something_plays(app):
    async with app.run_test() as pilot:
        await pilot.pause()
        assert pilot.app.query_one(DialRule).position is None


async def test_needle_tracks_position_in_the_library(app):
    async with app.run_test() as pilot:
        await pilot.pause()
        pilot.app.query_one("#station-table", DataTable).move_cursor(row=2)
        await pilot.press("p")
        # Third of three stations sits at the bottom of the scale.
        assert pilot.app.query_one(DialRule).position == 1.0


async def test_needle_clears_on_stop(app):
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("p", "s")
        assert pilot.app.query_one(DialRule).position is None


# -- library editing ------------------------------------------------------


async def test_favourite_toggles_and_persists(app, library):
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("f")
        assert library[0].favorite is False  # seeded as a favourite, so this unsets it
        assert StationLibrary.load(library.path)[0].favorite is False


async def test_remove_deletes_and_persists(app, library):
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("d")
        assert len(library) == 2
        assert len(StationLibrary.load(library.path)) == 2


async def test_removing_the_playing_station_stops_it(app, player):
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("p", "d")
        assert ("stop",) in player.calls
        assert pilot.app._playing_key is None


async def test_failed_favourite_save_rolls_back_and_notifies(app, library, monkeypatch):
    def explode():
        raise OSError("disk full")

    monkeypatch.setattr(library, "save", explode)
    async with app.run_test() as pilot:
        await pilot.pause()
        toasts = []
        pilot.app.notify = lambda message, **kwargs: toasts.append((message, kwargs))
        await pilot.press("f")
        assert library[0].favorite is True
        assert "disk full" in toasts[0][0]
        assert toasts[0][1]["severity"] == "error"


async def test_failed_remove_save_keeps_station_and_playback(app, library, player, monkeypatch):
    def explode():
        raise OSError("read only")

    monkeypatch.setattr(library, "save", explode)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("p", "d")
        assert len(library) == 3
        assert ("stop",) not in player.calls


# -- visualiser -----------------------------------------------------------


async def test_visualiser_toggles(app):
    async with app.run_test() as pilot:
        await pilot.pause()
        meter = pilot.app.query_one(SignalMeter)
        assert meter.enabled is True
        await pilot.press("v")
        assert meter.enabled is False


async def test_visualiser_can_start_hidden(library, player, api):
    app = PyodeApp(library=library, player=player, api=api, visualiser=False)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert pilot.app.query_one(SignalMeter).enabled is False


async def test_meter_is_fed_while_playing(app):
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("p")
        for _ in range(5):
            pilot.app._tick()
        assert pilot.app.query_one(SignalMeter)._history


# -- search ---------------------------------------------------------------


async def test_search_adds_a_station_to_the_library(app, library):
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("slash")
        await pilot.pause()
        screen = pilot.app.screen
        assert isinstance(screen, SearchScreen)

        screen.query_one(Input).value = "jazz"
        screen._search("jazz")
        for _ in range(40):
            await pilot.pause()
            if screen._level.kind == "stations":
                break
        assert screen._level.items

        before = len(library)
        screen.query_one("#results", DataTable).move_cursor(row=0)
        await pilot.press("enter")
        await pilot.pause()
        assert len(library) == before + 1
        assert library[-1].url == "https://stream/1"  # the resolved URL, not the redirect


async def test_search_reports_a_duplicate_instead_of_adding_twice(app, library):
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("slash")
        await pilot.pause()
        screen = pilot.app.screen
        screen._search("jazz")
        for _ in range(40):
            await pilot.pause()
            if screen._level.kind == "stations":
                break
        screen.query_one("#results", DataTable).move_cursor(row=0)
        await pilot.press("enter")
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        assert screen._added == 1
        assert "already" in str(screen.query_one("#search-status", Static).content)


# -- shutdown -------------------------------------------------------------


async def test_quitting_while_the_timer_runs_does_not_crash(app):
    """Widgets unmount before the app does, so a tick can land on a dead tree."""
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("p")
        for _ in range(3):
            pilot.app._tick()
        await pilot.press("q")
        await pilot.pause()
    # run_test re-raises anything the timer swallowed; reaching here is the test.


async def test_player_is_closed_on_exit(app, player):
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("q")
        await pilot.pause()
    assert player.closed


# -- row rendering --------------------------------------------------------


def test_row_is_exactly_the_requested_width(library):
    assert len(_row(library[0], 40)) == 40


def test_row_marks_favourites(library):
    assert _row(library[0], 40).startswith("♥")
    assert _row(library[1], 40).startswith("  ")


def test_row_truncates_long_names_rather_than_overflowing():
    from pyode.stations import Station

    station = Station(name="A" * 200, url="https://e/1", codec="MP3", bitrate=128)
    rendered = _row(station, 30)
    assert len(rendered) == 30
    assert "…" in rendered


def test_row_keeps_quality_visible_when_the_name_is_long():
    from pyode.stations import Station

    station = Station(name="B" * 200, url="https://e/1", codec="AAC+", bitrate=64)
    assert _row(station, 32).endswith("AAC+ 64k")


@pytest.mark.parametrize("volume,expected", [(0, "░" * 8), (100, "█" * 8), (50, "█" * 4 + "░" * 4)])
def test_volume_bar_fills_proportionally(volume, expected):
    from dataclasses import replace

    from pyode.player import PlayerStatus

    assert _volume_readout(replace(PlayerStatus(), volume=volume)).startswith(expected)


def test_muted_volume_says_so():
    from dataclasses import replace

    from pyode.player import PlayerStatus

    assert _volume_readout(replace(PlayerStatus(), muted=True)) == "muted"


@pytest.mark.parametrize("state", list(PlayerState))
def test_every_player_state_has_a_transport_label(state):
    from pyode.widgets import TRANSPORT

    assert state in TRANSPORT


# -- meter scaling --------------------------------------------------------


def _scale(values):
    return SignalMeter()._scaled(values)


def test_scaling_leaves_an_empty_chart_alone():
    assert _scale([None, None]) == [None, None]


def test_scaling_preserves_gaps():
    scaled = _scale([None, 0.5, None, 0.9])
    assert scaled[0] is None and scaled[2] is None


def test_scaling_stretches_a_varied_signal_across_the_height():
    scaled = _scale([0.50, 0.65, 0.90])
    assert scaled[0] == pytest.approx(0.0)
    assert scaled[-1] == pytest.approx(1.0)


def test_scaling_does_not_amplify_a_steady_signal_into_a_fake_dance():
    """A flat signal must not be stretched into full-height noise."""
    scaled = [v for v in _scale([0.800, 0.801, 0.802]) if v is not None]
    assert max(scaled) - min(scaled) < 0.1


def test_silence_stays_flat():
    assert _scale([0.0, 0.01, 0.0]) == [0.0, 0.0, 0.0]


def test_scaling_never_leaves_the_unit_range():
    scaled = [v for v in _scale([0.0, 0.2, 0.55, 1.0]) if v is not None]
    assert all(0.0 <= v <= 1.0 for v in scaled)


# -- launch and failure surfacing -----------------------------------------


async def test_autoplay_starts_the_named_station(library, player, api):
    target = library[1]
    app = PyodeApp(library=library, player=player, api=api, autoplay=target)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert player.calls[0] == ("play", target.url, target.name)
        assert pilot.app._playing_key == target.key


async def test_autoplay_moves_the_cursor_to_that_station(library, player, api):
    app = PyodeApp(library=library, player=player, api=api, autoplay=library[2])
    async with app.run_test() as pilot:
        await pilot.pause()
        assert pilot.app.query_one("#station-table", DataTable).cursor_row == 2


async def test_a_failing_stream_raises_a_toast_once(app, player):
    from dataclasses import replace

    async with app.run_test() as pilot:
        await pilot.pause()
        toasts = []
        pilot.app.notify = lambda message, **kwargs: toasts.append((message, kwargs))
        pilot.app._timer.stop()  # never observed playing: a failed start, not a drop
        await pilot.press("p")
        player.status = replace(player.status, state=PlayerState.ERROR, error="loading failed")
        pilot.app._tick()
        pilot.app._tick()
        assert len(toasts) == 1
        assert "loading failed" in toasts[0][0]
        assert toasts[0][1]["severity"] == "error"


async def test_retrying_after_a_failure_can_toast_again(app, player):
    from dataclasses import replace

    async with app.run_test() as pilot:
        await pilot.pause()
        toasts = []
        pilot.app.notify = lambda message, **kwargs: toasts.append(message)
        pilot.app._timer.stop()  # never observed playing: a failed start, not a drop
        await pilot.press("p")
        player.status = replace(player.status, state=PlayerState.ERROR, error="loading failed")
        pilot.app._tick()
        await pilot.press("p")  # retry resets the state
        player.status = replace(player.status, state=PlayerState.ERROR, error="loading failed")
        pilot.app._tick()
        assert len(toasts) == 2


# -- sizing ---------------------------------------------------------------


@pytest.mark.parametrize("size", [(40, 12), (56, 16), (72, 22), (100, 30), (140, 40)])
async def test_rows_match_the_pane_width_at_any_terminal_size(library, player, api, size):
    """Regression: the column was fixed at a fallback width.

    refresh_stations() first runs from on_mount, before layout, when the table
    still reports no width.  The rebuild has to be driven by the table's own
    resize or every terminal that is not the fallback size clips its rows.
    """
    app = PyodeApp(library=library, player=player, api=api)
    async with app.run_test(size=size) as pilot:
        await pilot.pause()
        table = pilot.app.query_one("#station-table", StationTable)
        column = next(iter(table.columns.values()))
        assert column.width == table.size.width
        assert len(table.get_row_at(0)[0]) == table.size.width


async def test_quality_stays_visible_when_the_pane_is_narrow(library, player, api):
    app = PyodeApp(library=library, player=player, api=api)
    async with app.run_test(size=(72, 22)) as pilot:
        await pilot.pause()
        table = pilot.app.query_one("#station-table", StationTable)
        assert table.get_row_at(0)[0].rstrip().endswith("128k")


async def test_narrow_terminals_drop_the_metadata_pane(library, player, api):
    app = PyodeApp(library=library, player=player, api=api)
    async with app.run_test(size=(50, 24)) as pilot:
        await pilot.pause()
        assert pilot.app.screen.has_class("-narrow")
        assert not pilot.app.query_one("#right").display


async def test_short_terminals_drop_the_signal_meter(library, player, api):
    app = PyodeApp(library=library, player=player, api=api)
    async with app.run_test(size=(100, 16)) as pilot:
        await pilot.pause()
        assert pilot.app.screen.has_class("-short")
        assert not pilot.app.query_one("#signal-pane").display


async def test_a_roomy_terminal_keeps_everything(library, player, api):
    app = PyodeApp(library=library, player=player, api=api)
    async with app.run_test(size=(110, 34)) as pilot:
        await pilot.pause()
        assert not pilot.app.screen.has_class("-narrow")
        assert pilot.app.query_one("#right").display
        assert pilot.app.query_one("#signal-pane").display


async def test_the_controls_survive_every_size(library, player, api):
    """Whatever else goes, you must still be able to see the transport."""
    for size in [(40, 10), (56, 16), (110, 34)]:
        app = PyodeApp(library=library, player=player, api=api)
        async with app.run_test(size=size) as pilot:
            await pilot.pause()
            assert pilot.app.query_one(ControlBar).display


@pytest.mark.parametrize(
    "keymap,down,up,play,stop",
    [
        ("standard", "down", "up", "right", "left"),
        ("vim", "j", "k", "l", "h"),
        ("vim", "down", "up", "right", "left"),
    ],
)
async def test_navigation_and_transport(library, player, api, keymap, down, up, play, stop):
    app = PyodeApp(library=library, player=player, api=api, keymap=keymap)
    async with app.run_test() as pilot:
        await pilot.press(down, down, up, play, "space", stop)
        assert player.calls[0] == ("play", library[1].url, library[1].name)
        assert [c[0] for c in player.calls] == ["play", "toggle_pause", "stop"]


async def test_vim_keys_are_opt_in(app, player):
    async with app.run_test() as pilot:
        await pilot.press("j", "l", "h")
        assert app.selected == app.library[0]
        assert not player.calls


async def test_search_typing_and_results_do_not_control_player(library, player, api):
    app = PyodeApp(library=library, player=player, api=api, keymap="vim")
    async with app.run_test() as pilot:
        await pilot.press("slash", "h", "j", "k", "l", "space", "d", "f", "q")
        screen = app.screen
        assert screen.query_one(Input).value == "hjkl dfq"
        from pyode.app import _Level

        table = screen.query_one("#results", DataTable)
        screen._open(_Level("Test", "stations", list(library)))
        table.focus()
        await pilot.press("j", "j", "k")
        assert table.cursor_row == 1
        # Space is left out: in a station list it previews (tested below).
        await pilot.press("h", "l", "right", "left", "d", "f", "p", "s", "q")
        assert not player.calls
        assert len(library) == 3
        assert library[0].favorite
        await pilot.press("escape")  # back to the menu, still in the dialog
        assert screen._level.kind == "menu"
        assert not player.calls
        await pilot.press("escape", "l")
        assert player.calls[0][0] == "play"


async def test_filter_uses_visible_station_identity_and_undo_restores_order(app, library, player):
    library.set_favorite(library[0].key, False)
    library.set_favorite(library[2].key)
    original = list(library)
    async with app.run_test() as pilot:
        await pilot.press("F", "right")
        assert player.calls[0] == ("play", original[2].url, original[2].name)
        await pilot.press("d")
        assert app.selected is None
        await pilot.press("u")
        assert list(library) == original
        assert list(StationLibrary.load(library.path)) == original
        assert app.selected == original[2]
        await pilot.press("F")
        assert app.selected == original[2]


async def test_unfavourite_last_filtered_station_and_stop(app, player):
    async with app.run_test() as pilot:
        await pilot.press("F", "right", "f")
        assert app.selected is None
        assert app.query_one("#stations-empty").display
        await pilot.press("left")
        assert player.calls[-1] == ("stop",)
        await pilot.press("F")
        assert app.query_one("#station-table", DataTable).row_count == 3


async def test_failed_undo_can_be_retried(app, library, monkeypatch):
    original = list(library)
    async with app.run_test() as pilot:
        await pilot.press("d")
        save = library.save

        def fail():
            raise OSError("disk full")

        monkeypatch.setattr(library, "save", fail)
        await pilot.press("u")
        assert len(library) == 2
        assert app._removed is not None
        monkeypatch.setattr(library, "save", save)
        await pilot.press("u")
        assert list(library) == original


async def test_help_shows_keymap_and_closes(library, player, api):
    from pyode.app import HelpScreen

    app = PyodeApp(library=library, player=player, api=api, keymap="vim")
    async with app.run_test(size=(40, 16)) as pilot:
        await pilot.press("question_mark")
        assert isinstance(app.screen, HelpScreen)
        assert "vim" in str(app.screen.query_one(Static).content)
        await pilot.press("d", "l")
        assert len(library) == 3 and not player.calls
        await pilot.press("escape")
        assert not isinstance(app.screen, HelpScreen)


async def test_preferences_save_after_changes(library, player, api, tmp_path):
    from pyode.config import Preferences

    path = tmp_path / "prefs.toml"
    app = PyodeApp(library=library, player=player, api=api, keymap="vim", config_path=path)
    async with app.run_test() as pilot:
        await pilot.press("minus", "v")
        assert Preferences.load(path) == Preferences("vim", player.status.volume, False)


async def test_tick_survives_partial_control_bar_teardown(app):
    async with app.run_test() as pilot:
        await pilot.pause()
        app._timer.stop()
        await app.query_one("#transport").remove()
        app._last_signature = None
        app._tick()


async def test_undo_does_not_duplicate_a_readded_station(app, library):
    station = library[0]
    async with app.run_test() as pilot:
        await pilot.press("d")
        with library.editing():
            library.add(station)
        await pilot.press("u")
        assert len(library) == 3
        assert sum(item.key == station.key for item in library) == 1


async def test_preference_write_failure_keeps_controls_working(library, player, api, tmp_path):
    blocker = tmp_path / "not-a-directory"
    blocker.write_text("keep")
    app = PyodeApp(library=library, player=player, api=api, config_path=blocker / "config.toml")
    notices = []
    app.notify = lambda message, **kwargs: notices.append(message)
    async with app.run_test() as pilot:
        await pilot.press("minus", "v", "right")
        assert player.calls[-1][0] == "play"
        assert not app._visualiser
        assert any("Could not save preferences" in notice for notice in notices)
        assert blocker.read_text() == "keep"


# -- reordering -------------------------------------------------------------


@pytest.mark.parametrize(
    "keymap,down,up", [("standard", "shift+down", "shift+up"), ("vim", "J", "K")]
)
async def test_move_reorders_and_persists(library, player, api, keymap, down, up):
    names = [s.name for s in library]
    app = PyodeApp(library=library, player=player, api=api, keymap=keymap)
    async with app.run_test() as pilot:
        await pilot.press(down)
        assert [s.name for s in library] == [names[1], names[0], names[2]]
        assert app.selected.name == names[0]  # the cursor follows the station
        assert [s.name for s in StationLibrary.load(library.path)] == [s.name for s in library]
        await pilot.press(up)
        assert [s.name for s in library] == names


async def test_move_stops_at_the_ends(app, library):
    names = [s.name for s in library]
    async with app.run_test() as pilot:
        await pilot.press("shift+up")
        assert [s.name for s in library] == names


async def test_vim_move_keys_are_opt_in(app, library):
    names = [s.name for s in library]
    async with app.run_test() as pilot:
        await pilot.press("J")
        assert [s.name for s in library] == names


async def test_move_with_favourites_shown_jumps_hidden_stations(app, library):
    library.set_favorite(library[2].key)  # favourites are now stations 0 and 2
    names = [s.name for s in library]
    async with app.run_test() as pilot:
        await pilot.press("F", "shift+down")
        assert [s.name for s in library] == [names[1], names[2], names[0]]
        assert app.selected.name == names[0]


async def test_moving_the_playing_station_moves_the_needle(app):
    async with app.run_test() as pilot:
        await pilot.press("p", "shift+down", "shift+down")
        assert app.query_one(DialRule).position == 1.0


# -- recovery ---------------------------------------------------------------


def _set_state(player, state, error=""):
    from dataclasses import replace

    player.status = replace(player.status, state=state, error=error)


async def _drop(app, player):
    """Play the first station, let the app see it playing, then lose it."""
    app._timer.stop()  # drive ticks by hand
    await app.run_action("play")
    app._tick()  # observes PLAYING
    _set_state(player, PlayerState.ERROR, "stream ended")
    app._tick()


async def test_a_dropped_stream_is_reconnected(app, library, player):
    async with app.run_test() as pilot:
        toasts = []
        app.notify = lambda message, **kwargs: toasts.append((message, kwargs))
        await _drop(app, player)
        assert app._reconnect_timer is not None
        assert "Reconnecting" in toasts[-1][0]
        assert toasts[-1][1]["severity"] == "warning"
        assert "Reconnecting" in str(app.query_one("#transport", Static).content)
        assert "Reconnecting" in str(app.query_one("#np-error", Static).content)

        app.query_one("#station-table", DataTable).move_cursor(row=2)
        app._reconnect_timer.stop()
        app._reconnect()
        await pilot.pause()
        assert player.calls[-1] == ("play", library[0].url, library[0].name)
        assert app.selected == library[2]  # a retry leaves the cursor alone
        app._tick()
        assert "Reconnecting" not in str(app.query_one("#transport", Static).content)


async def test_reconnecting_gives_up_eventually(app, player, monkeypatch):
    import pyode.app as app_module

    monkeypatch.setattr(app_module, "RECONNECT_DELAYS", (1, 1))
    async with app.run_test():
        toasts = []
        app.notify = lambda message, **kwargs: toasts.append((message, kwargs))
        await _drop(app, player)
        for _ in range(2):
            app._reconnect_timer.stop()
            app._reconnect()
            _set_state(player, PlayerState.ERROR, "loading failed")  # retry never starts
            app._tick()
        assert app._reconnect_timer is None
        assert "Gave up" in toasts[-1][0]
        assert toasts[-1][1]["severity"] == "error"
        assert player.calls.count(("play", app.library[0].url, app.library[0].name)) == 3


async def test_a_long_stable_connection_earns_fresh_attempts(app, player, monkeypatch):
    import pyode.app as app_module

    monkeypatch.setattr(app_module, "RECONNECT_DELAYS", (1,))
    async with app.run_test():
        await _drop(app, player)
        app._reconnect_timer.stop()
        app._reconnect()
        app._tick()  # playing again
        app._playing_since -= app_module.STABLE_SECONDS
        _set_state(player, PlayerState.ERROR, "stream ended")
        app._tick()
        assert app._reconnect_timer is not None


async def test_a_flapping_stream_uses_up_its_attempts(app, player, monkeypatch):
    import pyode.app as app_module

    monkeypatch.setattr(app_module, "RECONNECT_DELAYS", (1,))
    async with app.run_test():
        await _drop(app, player)
        app._reconnect_timer.stop()
        app._reconnect()
        app._tick()  # plays briefly, then drops again
        _set_state(player, PlayerState.ERROR, "stream ended")
        app._tick()
        assert app._reconnect_timer is None


async def test_stop_cancels_a_pending_reconnect(app, player):
    async with app.run_test() as pilot:
        await _drop(app, player)
        await pilot.press("s")
        assert app._reconnect_timer is None
        assert app._notice == ""


async def test_a_stream_that_drops_while_paused_waits_for_space(app, library, player):
    async with app.run_test() as pilot:
        toasts = []
        app.notify = lambda message, **kwargs: toasts.append(message)
        app._timer.stop()
        await pilot.press("p", "space")
        app._tick()  # observes PAUSED
        _set_state(player, PlayerState.ERROR, "stream ended")
        app._tick()
        assert app._reconnect_timer is None
        assert "space" in toasts[-1]
        await pilot.press("space")
        assert player.calls[-1] == ("play", library[0].url, library[0].name)


def _directory(rows, requests=None):
    import httpx

    from pyode.browser import RadioBrowser

    def handle(request):
        if requests is not None:
            requests.append(request.url.path)
        return httpx.Response(200, json=rows)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
    return RadioBrowser("https://test.invalid", client=client)


async def test_a_station_that_moved_is_repaired_and_retried(library, player):
    first = library[0]
    moved = {
        "stationuuid": first.stationuuid,
        "name": "Whatever the directory calls it",
        "url_resolved": "https://new.example/stream",
        "codec": "AAC",
        "bitrate": 64,
    }
    app = PyodeApp(library=library, player=player, api=_directory([moved]))
    async with app.run_test() as pilot:
        app._timer.stop()
        await pilot.press("p")
        _set_state(player, PlayerState.ERROR, "loading failed")
        app._tick()
        await app.workers.wait_for_complete()
        await pilot.pause()

        repaired = library.find(first.key)
        assert (repaired.url, repaired.codec, repaired.bitrate) == (
            "https://new.example/stream",
            "AAC",
            64,
        )
        assert repaired.name == first.name  # the user's name is kept
        assert StationLibrary.load(library.path).find(first.key).url == repaired.url
        assert player.calls[-1] == ("play", "https://new.example/stream", first.name)


async def test_a_station_at_the_same_address_is_left_alone(library, player):
    first = library[0]
    same = {"stationuuid": first.stationuuid, "name": "x", "url_resolved": first.url}
    app = PyodeApp(library=library, player=player, api=_directory([same]))
    async with app.run_test() as pilot:
        toasts = []
        app.notify = lambda message, **kwargs: toasts.append(message)
        app._timer.stop()
        await pilot.press("p")
        _set_state(player, PlayerState.ERROR, "loading failed")
        app._tick()
        await app.workers.wait_for_complete()
        assert library.find(first.key) == first
        assert [c[0] for c in player.calls] == ["play"]
        assert len(toasts) == 1  # just the failure


async def test_repair_is_tried_once_per_play(library, player):
    first = library[0]
    requests = []
    same = {"stationuuid": first.stationuuid, "name": "x", "url_resolved": first.url}
    app = PyodeApp(library=library, player=player, api=_directory([same], requests))
    async with app.run_test() as pilot:
        app._timer.stop()
        await pilot.press("p")
        for _ in range(2):
            _set_state(player, PlayerState.ERROR, "loading failed")
            app._tick()
            _set_state(player, PlayerState.BUFFERING)
            app._tick()
        await app.workers.wait_for_complete()
        assert requests.count("/json/stations/byuuid") == 1


async def test_retries_are_not_reported_to_the_directory_as_plays(library, player):
    requests = []
    app = PyodeApp(library=library, player=player, api=_directory([], requests))
    async with app.run_test():
        await _drop(app, player)
        app._reconnect_timer.stop()
        app._reconnect()
        await app.workers.wait_for_complete()
        assert sum(path.startswith("/json/url/") for path in requests) == 1


# -- outside edits ----------------------------------------------------------


async def test_outside_edits_appear_in_the_list(app, library):
    import os

    from pyode.stations import Station

    library.save()
    async with app.run_test() as pilot:
        await pilot.press("down")
        selected = app.selected
        edited = StationLibrary(
            [Station(name="Typed In", url="https://e.com/typed"), *library],
            path=library.path.with_name("other.csv"),
        )
        edited.save()
        edited.path.replace(library.path)
        os.utime(library.path, ns=(1, 1))

        app._poll_library()
        assert app.query_one("#station-table", DataTable).row_count == 4
        assert library[0].name == "Typed In"
        assert app.selected == selected  # same station, now one row lower


async def test_an_unreadable_outside_edit_is_reported_once(app, library):
    import os

    library.save()
    async with app.run_test():
        toasts = []
        app.notify = lambda message, **kwargs: toasts.append(message)
        library.path.write_bytes(b"name,url\n\xff\xfe\n")
        os.utime(library.path, ns=(1, 1))
        app._poll_library()
        app._poll_library()
        assert len(toasts) == 1
        assert "not overwriting" in toasts[0]
        assert app.query_one("#station-table", DataTable).row_count == 3


def _remove_outside(library, key):
    """Drop a station from the CSV as another program would."""
    import os

    edited = StationLibrary(
        [s for s in library if s.key != key], path=library.path.with_name("other.csv")
    )
    edited.save()
    edited.path.replace(library.path)
    os.utime(library.path, ns=(1, 1))


async def test_a_station_removed_outside_keeps_logging_titles(app, library, player):
    library.save()
    async with app.run_test():
        app._timer.stop()
        await app.run_action("play")
        playing = library[0]
        _remove_outside(library, playing.key)
        app._poll_library()
        assert library.find(playing.key) is None
        _announce(app, player, "Artist - Song")
        assert [(t.station, t.title) for t in app.history] == [(playing.name, "Artist - Song")]
        assert app._playing_station() == playing


async def test_a_station_removed_outside_is_still_reconnected(app, library, player):
    library.save()
    async with app.run_test():
        playing = library[0]
        app._timer.stop()
        await app.run_action("play")
        app._tick()  # observes PLAYING
        _remove_outside(library, playing.key)
        app._poll_library()
        _set_state(player, PlayerState.ERROR, "stream ended")
        app._tick()
        assert app._reconnect_timer is not None
        app._reconnect_timer.stop()
        app._reconnect()
        assert player.calls[-1] == ("play", playing.url, playing.name)


async def test_a_station_renamed_outside_shows_the_new_name(app, library, player):
    import os

    library.save()
    async with app.run_test():
        app._timer.stop()
        await app.run_action("play")
        with library.editing():
            library.update(library[0].key, name="Renamed")
        os.utime(library.path, ns=(1, 1))
        _announce(app, player, "Artist - Song")
        assert app._playing_station().name == "Renamed"
        assert app.history.recent()[0].station == "Renamed"


# -- track history ----------------------------------------------------------


def _announce(app, player, title):
    from dataclasses import replace

    player.status = replace(
        player.status, now_playing=replace(player.status.now_playing, title=title)
    )
    app._tick()


async def test_announced_titles_are_logged_once(app, library, player):
    from pyode.history import TrackHistory

    async with app.run_test() as pilot:
        app._timer.stop()
        await pilot.press("p")
        _announce(app, player, "Artist - Song")
        app.action_toggle_mute()
        app._tick()  # a redraw for another reason must not log it again
        _announce(app, player, "Artist - Next")
        titles = [(t.station, t.title) for t in app.history]
        assert titles == [(library[0].name, "Artist - Song"), (library[0].name, "Artist - Next")]
        assert len(TrackHistory.load()) == 2


async def test_history_screen_lists_newest_first_and_copies(app, player, clipboard):
    from pyode.app import HistoryScreen

    async with app.run_test() as pilot:
        app._timer.stop()
        await pilot.press("p")
        _announce(app, player, "First")
        _announce(app, player, "Second")
        await pilot.press("t")
        assert isinstance(app.screen, HistoryScreen)
        table = app.screen.query_one("#history", DataTable)
        assert table.get_row_at(0)[1] == "Second"
        await pilot.press("down", "enter")
        assert clipboard[-1] == "First"
        await pilot.press("up", "c")
        assert clipboard[-1] == "Second"
        assert app.clipboard == "Second"


async def test_history_screen_does_not_drive_the_player(library, player, api, clipboard):
    from pyode.app import HistoryScreen

    app = PyodeApp(library=library, player=player, api=api, keymap="vim")
    async with app.run_test() as pilot:
        await pilot.press("t")
        assert isinstance(app.screen, HistoryScreen)
        await pilot.press("l", "h", "right", "left", "p", "space", "d", "f")
        assert not player.calls
        assert len(library) == 3
        await pilot.press("escape")
        assert not isinstance(app.screen, HistoryScreen)


async def test_empty_history_says_so(app):
    async with app.run_test() as pilot:
        await pilot.press("t")
        status = app.screen.query_one("#history-status", Static)
        assert "No tracks yet" in str(status.content)


async def test_copy_takes_the_current_title(app, player, clipboard):
    async with app.run_test() as pilot:
        notices = []
        app.notify = lambda message, **kwargs: notices.append(message)
        await pilot.press("c")
        assert clipboard == []
        assert "not announced" in notices[-1]
        app._timer.stop()
        await pilot.press("p")
        _announce(app, player, "Artist - Song")
        await pilot.press("c")
        assert clipboard == ["Artist - Song"]
        assert notices[-1] == "Copied: Artist - Song"


async def test_a_history_write_failure_is_reported_once(library, player, api, tmp_path):
    from pyode.history import TrackHistory

    blocker = tmp_path / "not-a-directory"
    blocker.write_text("")
    history = TrackHistory(blocker / "history.csv")
    app = PyodeApp(library=library, player=player, api=api, history=history)
    async with app.run_test() as pilot:
        notices = []
        app.notify = lambda message, **kwargs: notices.append(message)
        app._timer.stop()
        await pilot.press("p")
        _announce(app, player, "One")
        _announce(app, player, "Two")
        assert sum("track history" in n for n in notices) == 1
        assert [t.title for t in history] == ["One", "Two"]


async def test_session_only_history_is_preserved_in_preferences(library, player, api, tmp_path):
    from pyode.config import Preferences
    from pyode.history import TrackHistory

    path = tmp_path / "prefs.toml"
    app = PyodeApp(library=library, player=player, api=api, config_path=path, save_history=False)
    async with app.run_test() as pilot:
        app._timer.stop()
        await pilot.press("p")
        _announce(app, player, "Song")
        await pilot.press("minus")
        assert Preferences.load(path).history is False
        assert len(app.history) == 1
        assert len(TrackHistory.load()) == 0


# -- retry hint ---------------------------------------------------------------


@pytest.mark.parametrize("keymap,key", [("standard", "→"), ("vim", "l")])
async def test_retry_hint_names_the_play_key_for_the_keymap(library, player, api, keymap, key):
    app = PyodeApp(library=library, player=player, api=api, keymap=keymap)
    async with app.run_test() as pilot:
        app._timer.stop()
        await pilot.press("enter")
        _set_state(player, PlayerState.ERROR, "loading failed")
        app._tick()
        hint = str(app.query_one("#np-error", Static).content)
        assert hint == f"loading failed. Press {key} to try again."


# -- volume saves -------------------------------------------------------------


def _count_saves(monkeypatch):
    from pyode.config import Preferences

    saved = []
    original = Preferences.save

    def save(self, path):
        saved.append(self.volume)
        original(self, path)

    monkeypatch.setattr(Preferences, "save", save)
    return saved


async def test_held_volume_keys_do_not_write_on_every_press(
    library, player, api, tmp_path, monkeypatch
):
    import pyode.app as app_module

    monkeypatch.setattr(app_module, "VOLUME_SAVE_DELAY", 60)
    app = PyodeApp(library=library, player=player, api=api, config_path=tmp_path / "p.toml")
    async with app.run_test() as pilot:
        saved = _count_saves(monkeypatch)
        for _ in range(6):
            await pilot.press("minus")
        assert player.status.volume == 70  # the player follows every press
        assert saved == []
        assert app._volume_save_timer is not None


async def test_a_volume_change_is_saved_once_the_keys_settle(
    library, player, api, tmp_path, monkeypatch
):
    import pyode.app as app_module
    from pyode.config import Preferences

    monkeypatch.setattr(app_module, "VOLUME_SAVE_DELAY", 0.05)
    path = tmp_path / "prefs.toml"
    app = PyodeApp(library=library, player=player, api=api, config_path=path)
    async with app.run_test() as pilot:
        saved = _count_saves(monkeypatch)
        await pilot.press("minus")
        await pilot.pause(0.3)
        assert saved == [95]
        assert Preferences.load(path).volume == 95
        assert app._volume_save_timer is None


async def test_quitting_straight_after_a_volume_change_keeps_it(
    library, player, api, tmp_path, monkeypatch
):
    import pyode.app as app_module
    from pyode.config import Preferences

    monkeypatch.setattr(app_module, "VOLUME_SAVE_DELAY", 60)
    path = tmp_path / "prefs.toml"
    app = PyodeApp(library=library, player=player, api=api, config_path=path)
    async with app.run_test() as pilot:
        await pilot.press("minus", "minus")
        assert Preferences.load(path).volume == 100  # still waiting
    assert Preferences.load(path).volume == 90


async def test_another_preference_save_takes_the_pending_volume_with_it(
    library, player, api, tmp_path, monkeypatch
):
    import pyode.app as app_module
    from pyode.config import Preferences

    monkeypatch.setattr(app_module, "VOLUME_SAVE_DELAY", 60)
    path = tmp_path / "prefs.toml"
    app = PyodeApp(library=library, player=player, api=api, config_path=path)
    async with app.run_test() as pilot:
        saved = _count_saves(monkeypatch)
        await pilot.press("minus", "v")
        assert saved == [95]
        assert app._volume_save_timer is None
        assert Preferences.load(path) == Preferences("standard", 95, False)


# -- browsing the directory -----------------------------------------------------


def _row_for(name, uuid):
    return {"stationuuid": uuid, "name": name, "url_resolved": f"https://stream/{uuid}"}


class FakeDirectory:
    """Answers each radio-browser endpoint with its own canned rows."""

    def __init__(self, fail=()):
        self.requests = []
        self.fail = set(fail)

    def __call__(self, request):
        import httpx

        path, params = request.url.path, dict(request.url.params)
        self.requests.append((path, params))
        if any(path.startswith(prefix) for prefix in self.fail):
            return httpx.Response(503)
        if path.startswith("/json/stations/topclick/"):
            return httpx.Response(200, json=[_row_for("Popular One", "p1")])
        if path.startswith("/json/stations/topvote/"):
            return httpx.Response(200, json=[_row_for("Voted One", "v1")])
        if path == "/json/tags":
            return httpx.Response(
                200,
                json=[{"name": "jazz", "stationcount": 900}, {"name": "rock", "stationcount": 800}],
            )
        if path == "/json/countries":
            return httpx.Response(
                200,
                json=[
                    {"name": "sweden", "iso_3166_1": "SE", "stationcount": 300},
                    {"name": "Austria", "iso_3166_1": "AT", "stationcount": 200},
                    {"name": "Nowhere", "iso_3166_1": "", "stationcount": 5},
                    {"name": "Empty", "iso_3166_1": "XX", "stationcount": 0},
                ],
            )
        if path == "/json/stations/search":
            name = params.get("tag") or params.get("countrycode") or params.get("name")
            return httpx.Response(200, json=[_row_for(f"{name} station", f"s-{name}")])
        return httpx.Response(200, json=[])

    def api(self):
        import httpx

        from pyode.browser import RadioBrowser

        client = httpx.AsyncClient(transport=httpx.MockTransport(self))
        return RadioBrowser("https://test.invalid", client=client)


async def _open_directory(pilot):
    await pilot.press("slash")
    await pilot.pause()
    return pilot.app.screen


async def _settle(pilot, screen):
    await screen.workers.wait_for_complete()
    await pilot.pause()


def _rows(screen):
    table = screen.query_one("#results", DataTable)
    return [table.get_row_at(index)[0] for index in range(table.row_count)]


async def test_the_directory_opens_on_the_browse_menu(app):
    async with app.run_test() as pilot:
        screen = await _open_directory(pilot)
        assert _rows(screen) == ["Popular stations", "Top voted", "By genre…", "By country…"]
        assert screen.focused is screen.query_one(Input)
        await pilot.press("down")  # into the list, as in a menu
        assert screen.focused is screen.query_one("#results", DataTable)


@pytest.mark.parametrize("row,expected", [(0, "Popular One"), (1, "Voted One")])
async def test_popular_and_top_voted_lists_can_be_added_from(library, player, row, expected):
    directory = FakeDirectory()
    app = PyodeApp(library=library, player=player, api=directory.api())
    async with app.run_test() as pilot:
        screen = await _open_directory(pilot)
        await pilot.press("down", *(["down"] * row), "enter")
        await _settle(pilot, screen)
        assert _rows(screen) == [expected]
        await pilot.press("enter")
        assert library[-1].name == expected
        await pilot.press("escape", "escape")
        assert not isinstance(app.screen, SearchScreen)
        assert app.query_one("#station-table", DataTable).row_count == 4


async def test_browsing_by_genre_matches_the_tag_exactly(library, player):
    directory = FakeDirectory()
    app = PyodeApp(library=library, player=player, api=directory.api())
    async with app.run_test() as pilot:
        screen = await _open_directory(pilot)
        await pilot.press("down", "down", "down", "enter")
        await _settle(pilot, screen)
        assert _rows(screen) == ["jazz", "rock"]
        await pilot.press("down", "enter")
        await _settle(pilot, screen)
        assert _rows(screen) == ["rock station"]
        path, params = directory.requests[-1]
        assert (path, params["tag"], params["tagExact"]) == (
            "/json/stations/search",
            "rock",
            "true",
        )


async def test_countries_are_alphabetical_and_searched_by_code(library, player):
    directory = FakeDirectory()
    app = PyodeApp(library=library, player=player, api=directory.api())
    async with app.run_test() as pilot:
        screen = await _open_directory(pilot)
        await pilot.press("down", *(["down"] * 3), "enter")
        await _settle(pilot, screen)
        # No code to search by, or no stations: left out.
        assert _rows(screen) == ["Austria", "sweden"]
        await pilot.press("down", "enter")
        await _settle(pilot, screen)
        assert directory.requests[-1][1]["countrycode"] == "SE"


async def test_escape_goes_back_a_level_and_keeps_the_place(library, player):
    directory = FakeDirectory()
    app = PyodeApp(library=library, player=player, api=directory.api())
    async with app.run_test() as pilot:
        screen = await _open_directory(pilot)
        await pilot.press("down", "down", "down", "enter")  # genres
        await _settle(pilot, screen)
        await pilot.press("down", "enter")  # rock
        await _settle(pilot, screen)
        await pilot.press("escape")
        assert screen._level.kind == "tags"
        assert screen.query_one("#results", DataTable).cursor_row == 1  # still on rock
        await pilot.press("escape")
        assert screen._level.kind == "menu"
        assert screen.query_one("#results", DataTable).cursor_row == 2  # still on genres
        assert isinstance(app.screen, SearchScreen)


async def test_a_failed_list_stays_put_and_says_why(library, player):
    directory = FakeDirectory(fail=["/json/tags"])
    app = PyodeApp(library=library, player=player, api=directory.api())
    async with app.run_test() as pilot:
        screen = await _open_directory(pilot)
        await pilot.press("down", "down", "down", "enter")
        await _settle(pilot, screen)
        assert screen._level.kind == "menu"
        assert "503" in str(screen.query_one("#search-status", Static).content)


async def test_a_search_from_deep_in_a_list_backs_out_to_the_menu(library, player):
    directory = FakeDirectory()
    app = PyodeApp(library=library, player=player, api=directory.api())
    async with app.run_test() as pilot:
        screen = await _open_directory(pilot)
        await pilot.press("down", "down", "down", "enter")  # genres
        await _settle(pilot, screen)
        screen.query_one(Input).focus()
        await pilot.press(*"soma", "enter")
        await _settle(pilot, screen)
        assert _rows(screen) == ["soma station"]
        await pilot.press("escape")
        assert screen._level.kind == "menu"


# -- listening before adding ------------------------------------------------------


async def _popular(pilot):
    """Open the directory on its Popular list, with the list focused."""
    screen = await _open_directory(pilot)
    await pilot.press("down", "enter")
    await _settle(pilot, screen)
    return screen


async def test_space_previews_a_station_without_adding_it(library, player):
    directory = FakeDirectory()
    app = PyodeApp(library=library, player=player, api=directory.api())
    async with app.run_test() as pilot:
        screen = await _popular(pilot)
        await pilot.press("space")
        assert player.calls[-1] == ("play", "https://stream/p1", "Popular One")
        assert app.previewing.name == "Popular One"
        assert len(library) == 3
        assert "Listening to Popular One" in str(screen.query_one("#search-status").content)
        app._tick()  # the control bar redraws on the next tick
        assert "Popular One" in str(app.query_one("#ctl-station", Static).content)
        await pilot.press("space")  # same station again: stop
        assert player.calls[-1] == ("stop",)
        assert app.previewing is None


async def test_closing_goes_back_to_what_was_playing(library, player):
    directory = FakeDirectory()
    app = PyodeApp(library=library, player=player, api=directory.api())
    async with app.run_test() as pilot:
        await pilot.press("down", "p")  # library station 1 is playing
        screen = await _popular(pilot)
        await pilot.press("space", "escape", "escape")
        assert not isinstance(app.screen, SearchScreen)
        assert player.calls[-1] == ("play", library[1].url, library[1].name)
        assert app._playing_key == library[1].key
        assert app.query_one(DialRule).position == 0.5
        assert screen.app.previewing is None


async def test_closing_stops_a_preview_when_nothing_was_playing(library, player):
    directory = FakeDirectory()
    app = PyodeApp(library=library, player=player, api=directory.api())
    async with app.run_test() as pilot:
        await _popular(pilot)
        await pilot.press("space", "escape", "escape")
        assert player.calls[-1] == ("stop",)
        assert app._playing_key is None


async def test_an_added_preview_keeps_playing(library, player):
    directory = FakeDirectory()
    app = PyodeApp(library=library, player=player, api=directory.api())
    async with app.run_test() as pilot:
        await pilot.press("p")
        await _popular(pilot)
        await pilot.press("space", "enter", "escape", "escape")
        added = library[-1]
        assert added.name == "Popular One"
        assert player.calls[-1] == ("play", "https://stream/p1", "Popular One")  # no restart
        assert app._playing_key == added.key
        assert app.query_one(DialRule).position == 1.0


async def test_closing_without_a_preview_leaves_playback_alone(library, player):
    async with PyodeApp(
        library=library, player=player, api=FakeDirectory().api()
    ).run_test() as pilot:
        await pilot.press("p")
        await _open_directory(pilot)
        await pilot.press("escape")
        assert [c[0] for c in player.calls] == ["play"]


async def test_space_in_the_search_box_types(library, player):
    async with PyodeApp(
        library=library, player=player, api=FakeDirectory().api()
    ).run_test() as pilot:
        screen = await _open_directory(pilot)
        await pilot.press(*"lo fi")
        assert screen.query_one(Input).value == "lo fi"
        assert not player.calls


async def test_space_on_a_menu_or_genre_list_does_nothing(library, player):
    directory = FakeDirectory()
    app = PyodeApp(library=library, player=player, api=directory.api())
    async with app.run_test() as pilot:
        screen = await _open_directory(pilot)
        await pilot.press("down", "space")
        await pilot.press("down", "down", "enter")
        await _settle(pilot, screen)
        await pilot.press("space")
        assert not player.calls


async def test_a_failed_preview_is_reported_not_retried(library, player):
    from dataclasses import replace

    directory = FakeDirectory()
    app = PyodeApp(library=library, player=player, api=directory.api())
    async with app.run_test() as pilot:
        toasts = []
        app.notify = lambda message, **kwargs: toasts.append((message, kwargs))
        app._timer.stop()
        await _popular(pilot)
        await pilot.press("space")
        app._tick()  # observed playing, so a real station would reconnect
        player.status = replace(player.status, state=PlayerState.ERROR, error="stream ended")
        app._tick()
        assert app._reconnect_timer is None
        assert toasts[-1][1]["title"] == "Preview failed"
        await app.workers.wait_for_complete()
        assert not any(path == "/json/stations/byuuid" for path, _ in directory.requests)


async def test_previews_are_logged_to_history(library, player):
    from dataclasses import replace

    app = PyodeApp(library=library, player=player, api=FakeDirectory().api())
    async with app.run_test() as pilot:
        app._timer.stop()
        await _popular(pilot)
        await pilot.press("space")
        player.status = replace(
            player.status, now_playing=replace(player.status.now_playing, title="Artist - Song")
        )
        app._tick()
        assert [(t.station, t.title) for t in app.history] == [("Popular One", "Artist - Song")]


async def test_closing_does_not_resume_a_station_removed_meanwhile(library, player):
    """Removed by an outside edit to the CSV while the dialog was open."""
    directory = FakeDirectory()
    app = PyodeApp(library=library, player=player, api=directory.api())
    async with app.run_test() as pilot:
        await pilot.press("p")  # library station 0 is playing
        playing = library[0]
        await _popular(pilot)
        await pilot.press("space")
        with library.editing():
            library.remove(playing.key)
        await pilot.press("escape", "escape")
        assert player.calls[-1] == ("stop",)
        assert app._playing_key is None


async def test_an_added_preview_removed_meanwhile_falls_back(library, player):
    directory = FakeDirectory()
    app = PyodeApp(library=library, player=player, api=directory.api())
    async with app.run_test() as pilot:
        await pilot.press("p")
        await _popular(pilot)
        await pilot.press("space", "enter")
        with library.editing():
            library.remove(library[-1].key)  # the station just added
        await pilot.press("escape", "escape")
        assert player.calls[-1] == ("play", library[0].url, library[0].name)
        assert app._playing_key == library[0].key
