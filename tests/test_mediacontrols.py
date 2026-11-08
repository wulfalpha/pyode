# SPDX-License-Identifier: GPL-2.0-or-later
from dataclasses import replace

import pytest

from pyode import mediacontrols
from pyode.app import PyodeApp
from pyode.mediacontrols import NowPlayingInfo, describe
from pyode.player import NowPlaying, PlayerState, PlayerStatus

# Captured at import, before conftest swaps in a fake for each test.
real_create = mediacontrols.create

# -- describe ---------------------------------------------------------------


def _status(state=PlayerState.PLAYING, **now) -> PlayerStatus:
    return PlayerStatus(state=state, now_playing=NowPlaying(**now))


def test_track_title_leads_with_the_station_beneath():
    info = describe(_status(title="Lisboa Dreaming", genre="ambient"), "Groove Salad")
    assert info == NowPlayingInfo(title="Lisboa Dreaming", artist="Groove Salad", genre="ambient")


def test_without_a_title_the_station_is_the_headline():
    assert describe(_status(), "Groove Salad") == NowPlayingInfo(title="Groove Salad")


def test_stream_name_stands_in_for_a_missing_station():
    info = describe(_status(title="Song", stream_name="SomaFM"), None)
    assert (info.title, info.artist) == ("Song", "SomaFM")


def test_paused_is_reported():
    assert describe(_status(PlayerState.PAUSED), "Groove Salad").paused


@pytest.mark.parametrize("state", [PlayerState.IDLE, PlayerState.ERROR])
def test_nothing_on_air_clears_the_panel(state):
    assert describe(_status(state), "Groove Salad") is None


def test_buffering_still_shows_the_station():
    assert describe(_status(PlayerState.BUFFERING), "Groove Salad").title == "Groove Salad"


def test_disabled_or_unsupported_platforms_get_a_null_session(monkeypatch):
    commands = mediacontrols.Commands(*(lambda: None,) * 6)
    assert isinstance(real_create(commands, enabled=False), mediacontrols.NullSession)
    monkeypatch.setattr(mediacontrols.sys, "platform", "linux")
    assert isinstance(real_create(commands), mediacontrols.NullSession)


# -- the app ----------------------------------------------------------------


@pytest.fixture
def app(library, player, api):
    return PyodeApp(library=library, player=player, api=api)


async def test_panel_follows_playback(app, library, player, media):
    async with app.run_test() as pilot:
        await pilot.press("p")
        await pilot.pause(0.2)
        assert media[0].shown[-1] == NowPlayingInfo(title=library[0].name)
        player.status = replace(
            player.status, now_playing=NowPlaying(station=library[0].name, title="So What")
        )
        await pilot.pause(0.2)
        assert media[0].shown[-1].title == "So What"
        assert media[0].shown[-1].artist == library[0].name
        await pilot.press("s")
        await pilot.pause(0.2)
        assert media[0].shown[-1] is None


async def test_play_pause_keys(app, library, player, media):
    async with app.run_test() as pilot:
        media[0].press("toggle")  # nothing playing: starts the selected station
        await pilot.pause(0.2)
        assert player.calls[-1] == ("play", library[0].url, library[0].name)
        media[0].press("toggle")
        await pilot.pause(0.2)
        assert player.status.state is PlayerState.PAUSED
        assert media[0].shown[-1].paused
        media[0].press("pause")  # already paused: no change
        media[0].press("play")
        await pilot.pause(0.2)
        assert player.status.state is PlayerState.PLAYING
        assert player.calls.count(("toggle_pause",)) == 2


async def test_play_key_restarts_a_failed_stream(app, library, player, media):
    async with app.run_test() as pilot:
        await pilot.press("down", "p")
        player.status = replace(player.status, state=PlayerState.ERROR, error="dropped")
        await pilot.pause(0.2)
        media[0].press("play")
        await pilot.pause(0.2)
        assert player.calls[-1] == ("play", library[1].url, library[1].name)


async def test_stop_key(app, player, media):
    async with app.run_test() as pilot:
        await pilot.press("p")
        media[0].press("stop")
        await pilot.pause(0.2)
        assert player.status.state is PlayerState.IDLE


async def test_next_and_previous_step_through_stations_and_wrap(app, library, player, media):
    async with app.run_test() as pilot:
        await pilot.press("down", "p")
        media[0].press("next")
        await pilot.pause(0.2)
        assert player.calls[-1][1] == library[2].url
        media[0].press("next")
        await pilot.pause(0.2)
        assert player.calls[-1][1] == library[0].url
        media[0].press("previous")
        await pilot.pause(0.2)
        assert player.calls[-1][1] == library[2].url
        assert app.selected == library[2]


async def test_next_with_nothing_playing_plays_the_selection(app, library, player, media):
    async with app.run_test() as pilot:
        await pilot.press("down")
        media[0].press("next")
        await pilot.pause(0.2)
        assert player.calls[-1][1] == library[1].url


async def test_session_closes_on_quit(app, media):
    async with app.run_test() as pilot:
        await pilot.press("q")
    assert media[0].closed


async def test_media_keys_can_be_turned_off(library, player, api, media):
    app = PyodeApp(library=library, player=player, api=api, media_controls=False)
    async with app.run_test():
        pass
    assert media == []
