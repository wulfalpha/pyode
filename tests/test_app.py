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
            if screen._results:
                break
        assert screen._results

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
            if screen._results:
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
        table = screen.query_one("#results", DataTable)
        screen._results = list(library)
        for station in library:
            table.add_row(station.name, station.country, "")
        table.focus()
        await pilot.press("j", "j", "k")
        assert table.cursor_row == 1
        await pilot.press("h", "l", "right", "left", "space", "d", "f", "p", "s", "q")
        assert not player.calls
        assert len(library) == 3
        assert library[0].favorite
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
