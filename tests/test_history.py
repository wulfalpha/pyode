# SPDX-License-Identifier: GPL-2.0-or-later
"""Tests for the track history log."""

from datetime import UTC, datetime, timedelta

import pytest

from pyode.history import KEEP, Track, TrackHistory, default_path

NOON = datetime(2026, 11, 7, 12, 0, tzinfo=UTC)


@pytest.fixture
def path(tmp_path):
    return tmp_path / "history.csv"


def test_records_are_saved_and_read_back(path):
    history = TrackHistory(path)
    history.record("Groove Salad", "Artist - Song", NOON)
    history.record("Groove Salad", "Artist - Next", NOON + timedelta(minutes=4))
    loaded = TrackHistory.load(path)
    assert [(t.station, t.title) for t in loaded] == [
        ("Groove Salad", "Artist - Song"),
        ("Groove Salad", "Artist - Next"),
    ]
    assert list(loaded)[0].time == NOON


def test_recent_is_newest_first(path):
    history = TrackHistory(path)
    history.record("S", "One", NOON)
    history.record("S", "Two", NOON)
    assert [t.title for t in history.recent()] == ["Two", "One"]


def test_a_repeated_title_is_not_logged_twice(path):
    history = TrackHistory(path)
    assert history.record("S", "Song", NOON) is not None
    assert history.record("S", "Song", NOON) is None  # metadata resent
    assert history.record("S", " Song ", NOON) is None
    assert len(TrackHistory.load(path)) == 1


def test_the_same_title_on_another_station_is_logged(path):
    history = TrackHistory(path)
    history.record("A", "Song", NOON)
    assert history.record("B", "Song", NOON) is not None


def test_blank_titles_are_ignored(path):
    history = TrackHistory(path)
    assert history.record("S", "   ", NOON) is None
    assert not path.exists()


def test_the_header_is_written_once(path):
    history = TrackHistory(path)
    history.record("S", "One", NOON)
    history.record("S", "Two", NOON)
    assert path.read_text().splitlines()[0] == "time,station,title"
    assert path.read_text().count("time,station,title") == 1


def test_commas_and_quotes_survive(path):
    TrackHistory(path).record("S", 'Artist, The - "Song"', NOON)
    assert list(TrackHistory.load(path))[0].title == 'Artist, The - "Song"'


def test_without_persistence_nothing_is_written(path):
    history = TrackHistory(path, persist=False)
    history.record("S", "Song", NOON)
    assert len(history) == 1
    assert not path.exists()


def test_a_session_only_history_does_not_read_the_file(path):
    TrackHistory(path).record("S", "Song", NOON)
    assert len(TrackHistory.load(path, persist=False)) == 0


def test_only_the_most_recent_entries_are_kept_in_memory(path):
    history = TrackHistory(path)
    for number in range(KEEP + 5):
        history.record("S", f"Song {number}", NOON)
    loaded = TrackHistory.load(path)
    assert len(loaded) == KEEP
    assert loaded.recent()[0].title == f"Song {KEEP + 4}"
    assert len(path.read_text().splitlines()) == KEEP + 6  # the file is never trimmed


def test_bad_rows_are_skipped(path):
    path.write_text(
        "time,station,title\n"
        "2026-11-07T12:00:00+00:00,S,Good\n"
        "not a time,S,Bad time\n"
        "2026-11-07T12:01:00+00:00,S,\n"
        "2026-11-07T12:02:00+00:00,S,Also good\n"
    )
    assert [t.title for t in TrackHistory.load(path)] == ["Good", "Also good"]


def test_an_unreadable_file_is_an_empty_history(path):
    path.write_bytes(b"time,station,title\n\xff\xfe\n")
    assert len(TrackHistory.load(path)) == 0


def test_a_failed_write_is_kept_in_memory(tmp_path):
    blocker = tmp_path / "not-a-directory"
    blocker.write_text("")
    history = TrackHistory(blocker / "history.csv")
    with pytest.raises(OSError):
        history.record("S", "Song", NOON)
    assert [t.title for t in history] == ["Song"]


def test_a_time_without_a_zone_is_taken_as_local(path):
    path.write_text("time,station,title\n2026-11-07T12:00:00,S,Song\n")
    assert list(TrackHistory.load(path))[0].time.tzinfo is not None


@pytest.mark.parametrize(
    "when,expected",
    [
        (NOON - timedelta(hours=2), "10:00"),
        (NOON - timedelta(days=3), "Nov 4 12:00"),
        (NOON - timedelta(days=400), "2025-10-03 12:00"),
    ],
)
def test_times_are_shortened_relative_to_now(when, expected):
    assert Track(when, "S", "T").when(now=NOON) == expected


def test_environment_overrides_the_path(monkeypatch, tmp_path):
    monkeypatch.setenv("PYODE_HISTORY_FILE", str(tmp_path / "h.csv"))
    assert default_path() == tmp_path / "h.csv"
