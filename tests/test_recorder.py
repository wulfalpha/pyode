# SPDX-License-Identifier: GPL-2.0-or-later
from dataclasses import replace
from datetime import datetime
from pathlib import Path

import pytest

from pyode import recorder
from pyode.app import PyodeApp
from pyode.player import PlayerState
from pyode.recorder import Recording, extension_for, safe_name
from pyode.widgets import TopBar

WHEN = datetime(2026, 11, 8, 21, 30)

# -- naming ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("codec", "extension"),
    [("mp3", ".mp3"), ("aac", ".aac"), ("AAC", ".aac"), ("opus", ".ogg"), ("", ".mka")],
)
def test_extension_follows_the_codec(codec, extension):
    assert extension_for(codec) == extension


def test_names_are_safe_on_every_platform():
    assert safe_name('AC/DC: "Live" <radio>?') == "AC DC Live radio"
    assert safe_name("...") == "pyode"


def test_file_is_named_for_station_and_time(tmp_path):
    recording = Recording.begin("SomaFM Groove Salad", ".mp3", 0.0, directory=tmp_path, when=WHEN)
    assert recording.path == tmp_path / "SomaFM Groove Salad 2026-11-08 21.30.mp3"
    assert recording.cue_path.suffix == ".cue"


def test_never_overwrites_an_earlier_recording(tmp_path):
    first = Recording.begin("Jazz", ".mp3", 0.0, directory=tmp_path, when=WHEN)
    first.path.write_bytes(b"audio")
    second = Recording.begin("Jazz", ".mp3", 0.0, directory=tmp_path, when=WHEN)
    assert second.path.name == "Jazz 2026-11-08 21.30 (2).mp3"


def test_elapsed_label():
    recording = Recording("Jazz", Path("x.mp3"), started=100.0)
    assert recording.label(100.0) == "0:00"
    assert recording.label(100.0 + 754) == "12:34"
    assert recording.label(100.0 + 3 * 3600 + 5) == "3:00:05"


# -- the cue sheet -----------------------------------------------------------------


def test_cue_sheet_marks_each_new_title():
    recording = Recording("Groove Salad", Path("/r/Groove Salad 2026.mp3"), started=0.0)
    assert recording.mark("Tycho - Awake", 2.0)
    assert not recording.mark("Tycho - Awake", 50.0)  # repeats are not new tracks
    assert not recording.mark("", 60.0)
    assert recording.mark('Station ID "jingle"', 61.4)
    assert recording.cue_sheet() == (
        'PERFORMER "Groove Salad"\n'
        'TITLE "Groove Salad 2026"\n'
        'FILE "Groove Salad 2026.mp3" MP3\n'
        "  TRACK 01 AUDIO\n"
        '    TITLE "Awake"\n'
        '    PERFORMER "Tycho"\n'
        "    INDEX 01 00:00:00\n"
        "  TRACK 02 AUDIO\n"
        "    TITLE \"Station ID 'jingle'\"\n"
        '    PERFORMER "Groove Salad"\n'
        "    INDEX 01 01:01:30\n"
    )


def test_cue_sheet_without_titles_still_has_a_track():
    recording = Recording("Jazz", Path("Jazz.aac"), started=0.0)
    sheet = recording.cue_sheet()
    assert 'FILE "Jazz.aac" WAVE' in sheet
    assert 'TITLE "Jazz"' in sheet and "INDEX 01 00:00:00" in sheet


# -- in the app ----------------------------------------------------------------------


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def app(library, player, api, clock):
    app = PyodeApp(library=library, player=player, api=api)
    app._clock = clock
    return app


def _playing(player, title="", codec="mp3"):
    now = replace(player.status.now_playing, title=title, codec=codec)
    player.status = replace(player.status, state=PlayerState.PLAYING, now_playing=now)


def _readout(app) -> str:
    rec = app.query_one(TopBar).query_one("#rec")
    return str(rec.render()) if rec.display else ""


async def test_records_with_a_track_list(app, library, player, clock, recordings):
    async with app.run_test() as pilot:
        await pilot.press("p")
        _playing(player, "Miles Davis - So What")
        await pilot.pause(0.2)
        await pilot.press("r")
        await pilot.pause(0.2)
        path = Path(player.recording)
        assert path.parent == recordings
        assert path.name.startswith(library[0].name) and path.suffix == ".mp3"
        assert _readout(app) == "● REC 0:00"
        clock.now += 75
        _playing(player, "Miles Davis - Blue in Green")
        await pilot.pause(0.2)
        assert _readout(app) == "● REC 1:15"
        cue = path.with_suffix(".cue").read_text()
        assert '"So What"' in cue and '"Blue in Green"' in cue and "01:15:00" in cue
        await pilot.press("r")
        await pilot.pause(0.2)
        assert player.recording is None
        assert app._recording is None
        assert _readout(app) == ""
        assert player.status.state is PlayerState.PLAYING  # stopping a recording is not stopping


async def test_nothing_to_record_until_playing(app, player):
    async with app.run_test() as pilot:
        await pilot.press("r")
        await pilot.pause()
        assert player.recording is None
        assert not any(call[0] == "record" for call in player.calls)


async def test_aac_streams_record_to_aac(app, player):
    async with app.run_test() as pilot:
        await pilot.press("p")
        _playing(player, codec="aac")
        await pilot.press("r")
        assert player.recording.endswith(".aac")


@pytest.mark.parametrize("keys", [("s",), ("down", "p")])
async def test_stopping_or_changing_station_ends_it(app, player, keys):
    async with app.run_test() as pilot:
        await pilot.press("p")
        _playing(player)
        await pilot.press("r")
        await pilot.press(*keys)
        await pilot.pause(0.2)
        assert app._recording is None
        assert player.recording is None


async def test_a_reconnect_continues_in_a_new_file(app, player, clock, monkeypatch):
    import pyode.app

    monkeypatch.setattr(pyode.app, "RECONNECT_DELAYS", (0.01,))
    async with app.run_test() as pilot:
        await pilot.press("p")
        _playing(player)
        await pilot.pause(0.2)
        await pilot.press("r")
        first = player.recording
        app._playing_since = 0.0  # it had been playing a while
        player.status = replace(player.status, state=PlayerState.ERROR, error="dropped")
        await pilot.pause(0.3)
        assert player.recording is not None
        assert player.recording != first
        assert Path(player.recording).stem.endswith("(2)")


async def test_giving_up_on_a_dropped_stream_ends_it(app, player, monkeypatch):
    import pyode.app

    monkeypatch.setattr(pyode.app, "RECONNECT_DELAYS", ())
    async with app.run_test() as pilot:
        await pilot.press("p")
        _playing(player)
        await pilot.pause(0.2)
        await pilot.press("r")
        app._playing_since = 0.0
        player.status = replace(player.status, state=PlayerState.ERROR, error="dropped")
        await pilot.pause(0.2)
        assert app._recording is None


async def test_quitting_closes_the_recording(app, player):
    async with app.run_test() as pilot:
        await pilot.press("p")
        _playing(player)
        await pilot.press("r", "q")
    assert ("record", None) in player.calls


async def test_unwritable_directory_is_reported(app, player, monkeypatch, tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("not a directory")
    monkeypatch.setattr(recorder, "default_dir", lambda: blocker / "recordings")
    async with app.run_test() as pilot:
        await pilot.press("p")
        _playing(player)
        await pilot.press("r")
        await pilot.pause()
        assert app._recording is None
        assert player.recording is None


# -- one track, from its start ----------------------------------------------------


def test_a_track_recording_is_named_after_the_track(tmp_path):
    first = Recording.for_track("Tycho - Awake", "Groove Salad", ".mp3", 0.0, directory=tmp_path)
    assert first.path == tmp_path / "Tycho - Awake.mp3"
    assert first.track == "Tycho - Awake"
    first.path.write_bytes(b"audio")
    again = Recording.for_track("Tycho - Awake", "Groove Salad", ".mp3", 0.0, directory=tmp_path)
    assert again.path.name == "Tycho - Awake (2).mp3"


async def test_keeps_the_track_from_its_start_until_the_next(app, player, clock, recordings):
    async with app.run_test() as pilot:
        await pilot.press("p")
        _playing(player, "Tycho - Awake")
        player.track_played = 95.0  # a minute and a half in already
        await pilot.pause(0.2)
        await pilot.press("R")
        await pilot.pause(0.2)
        assert player.track == "Tycho - Awake"
        assert _readout(app) == "● REC TRACK 1:35"  # counted from the track's start
        assert player.recording is None  # nothing streams to disk meanwhile
        clock.now += 120
        _playing(player, "Boards of Canada - Dayvan Cowboy")
        await pilot.pause(0.2)
        assert player.saved_tracks == [str(recordings / "Tycho - Awake.mp3")]
        assert app._recording is None
        assert _readout(app) == ""
        assert not (recordings / "Tycho - Awake.cue").exists()  # one track needs no list


async def test_stopping_by_hand_saves_what_has_played(app, player):
    async with app.run_test() as pilot:
        await pilot.press("p")
        _playing(player, "Tycho - Awake")
        await pilot.press("R")
        await pilot.press("R")  # either key stops it
        await pilot.pause(0.2)
        assert len(player.saved_tracks) == 1
        assert player.status.state is PlayerState.PLAYING


@pytest.mark.parametrize("keys", [("s",), ("down", "p")])
async def test_stopping_or_changing_station_saves_the_track_so_far(app, player, keys):
    async with app.run_test() as pilot:
        await pilot.press("p")
        _playing(player, "Tycho - Awake")
        await pilot.press("R", *keys)
        await pilot.pause(0.2)
        assert len(player.saved_tracks) == 1
        assert app._recording is None


async def test_r_stops_a_track_recording_too(app, player):
    async with app.run_test() as pilot:
        await pilot.press("p")
        _playing(player, "Tycho - Awake")
        await pilot.press("R", "r")
        await pilot.pause(0.2)
        assert len(player.saved_tracks) == 1
        assert player.recording is None  # it stopped, rather than starting the other kind


async def test_stations_without_titles_point_to_plain_recording(app, player):
    async with app.run_test() as pilot:
        toasts = []
        app.notify = lambda message, **kwargs: toasts.append(message)
        await pilot.press("p")
        _playing(player, "")
        await pilot.press("R")
        await pilot.pause()
        assert app._recording is None
        assert any("Press r" in toast for toast in toasts)


async def test_a_failed_save_is_reported(app, player):
    async with app.run_test() as pilot:
        toasts = []
        app.notify = lambda message, **kwargs: toasts.append(message)
        player.save_works = False
        await pilot.press("p")
        _playing(player, "Tycho - Awake")
        await pilot.press("R", "R")
        await pilot.pause()
        assert any("Could not save Tycho - Awake" in toast for toast in toasts)


async def test_a_stream_failure_saves_at_once_and_does_not_carry_on(app, player, monkeypatch):
    import pyode.app

    monkeypatch.setattr(pyode.app, "RECONNECT_DELAYS", (0.01,))
    async with app.run_test() as pilot:
        await pilot.press("p")
        _playing(player, "Tycho - Awake")
        await pilot.pause(0.2)
        await pilot.press("R")
        app._playing_since = 0.0  # it had been playing a while
        player.status = replace(player.status, state=PlayerState.ERROR, error="dropped")
        await pilot.pause(0.3)
        assert len(player.saved_tracks) == 1
        assert app._recording is None
        assert player.recording is None  # the reconnect did not start a new file
