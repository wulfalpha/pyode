# SPDX-License-Identifier: GPL-2.0-or-later
"""Tests for the pure parts of the player.

Playback itself is verified by hand against real streams; mocking libmpv would
only assert that the mock behaves like the mock.
"""

from pyode.player import NowPlaying, PlayerState, PlayerStatus, _clamp, _parse_metadata


def test_display_prefers_track_title():
    now = NowPlaying(station="Saved Name", title="Artist - Song", stream_name="Stream")
    assert now.display == "Artist - Song"


def test_display_falls_back_to_stream_name():
    # Plenty of stations send station-level ICY headers but never a track title.
    now = NowPlaying(station="Saved Name", stream_name="Smooth Jazz")
    assert now.display == "Smooth Jazz"


def test_display_falls_back_to_saved_station_name():
    assert NowPlaying(station="Saved Name").display == "Saved Name"


def test_display_never_blank():
    assert NowPlaying().display == "Nothing playing"


def test_parse_metadata_reads_icy_fields():
    now = _parse_metadata(
        {"icy-name": "Smooth Jazz", "icy-genre": "Jazz", "icy-br": "48"}, "Saved Name"
    )
    assert now.stream_name == "Smooth Jazz"
    assert now.genre == "Jazz"
    assert now.bitrate == 48
    assert now.station == "Saved Name"


def test_parse_metadata_is_case_insensitive():
    assert _parse_metadata({"ICY-Title": "Song"}, "").title == "Song"


def test_parse_metadata_tolerates_missing_and_junk():
    assert _parse_metadata(None, "Fallback").display == "Fallback"
    assert _parse_metadata({"icy-br": "not-a-number"}, "").bitrate is None


def test_parse_metadata_treats_null_fields_as_missing():
    now = _parse_metadata({"icy-title": None, "icy-name": None}, "Fallback")
    assert now.display == "Fallback"


def test_parse_metadata_strips_whitespace():
    assert _parse_metadata({"icy-title": "  Song  "}, "").title == "Song"


def test_clamp_bounds_volume():
    assert (_clamp(-10), _clamp(50), _clamp(500)) == (0, 50, 100)


def test_is_active_excludes_idle_and_error():
    assert PlayerStatus(state=PlayerState.BUFFERING).is_active
    assert PlayerStatus(state=PlayerState.PAUSED).is_active
    assert not PlayerStatus(state=PlayerState.IDLE).is_active
    assert not PlayerStatus(state=PlayerState.ERROR).is_active
