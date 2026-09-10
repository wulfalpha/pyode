# SPDX-License-Identifier: GPL-2.0-or-later
"""Tests for the CSV-backed station library.

The file is user-editable by design, so most of these cover what a hand edit or
a spreadsheet round trip can do to it.
"""

import csv
from dataclasses import replace

import pytest

from pyode.stations import ENV_VAR, Station, StationLibrary, default_path

JAZZ = Station(
    name="Smooth Jazz",
    url="https://example.com/jazz.aac",
    stationuuid="510506e7-6bc0-4b91-b6a1-fc024ccad1a8",
    codec="AAC+",
    bitrate=48,
    country="Mexico",
    tags="jazz,smooth jazz,piano",
    homepage="https://example.com/",
    added="2026-08-21",
)


@pytest.fixture
def csv_path(tmp_path):
    return tmp_path / "stations.csv"


# -- round tripping -------------------------------------------------------


def test_round_trip_preserves_every_field(csv_path):
    StationLibrary([JAZZ], path=csv_path).save()
    assert StationLibrary.load(csv_path)[0] == JAZZ


def test_round_trip_preserves_non_ascii(csv_path):
    station = Station(name="Radio Ciudad de México", url="https://e.com/s", country="México")
    StationLibrary([station], path=csv_path).save()
    loaded = StationLibrary.load(csv_path)[0]
    assert (loaded.name, loaded.country) == ("Radio Ciudad de México", "México")


def test_commas_in_tags_survive(csv_path):
    """Tags are comma-separated inside a comma-separated file."""
    StationLibrary([JAZZ], path=csv_path).save()
    loaded = StationLibrary.load(csv_path)[0]
    assert loaded.tags == "jazz,smooth jazz,piano"
    assert loaded.tag_list == ["jazz", "smooth jazz", "piano"]


def test_quoted_field_is_one_column_on_disk(csv_path):
    StationLibrary([JAZZ], path=csv_path).save()
    with csv_path.open(newline="", encoding="utf-8") as fh:
        rows = list(csv.reader(fh))
    assert len(rows[1]) == len(rows[0])


def test_embedded_newline_survives(csv_path):
    station = Station(name="Line\nBreak", url="https://e.com/s")
    StationLibrary([station], path=csv_path).save()
    assert StationLibrary.load(csv_path)[0].name == "Line\nBreak"


# -- tolerating hand edits ------------------------------------------------


def test_missing_file_is_an_empty_library(csv_path):
    library = StationLibrary.load(csv_path)
    assert len(library) == 0
    assert library.path == csv_path


def test_byte_order_mark_is_stripped(csv_path):
    """A spreadsheet round trip can leave a BOM on the first header cell."""
    csv_path.write_text("﻿name,url\nBOM Radio,https://e.com/s\n", encoding="utf-8")
    assert StationLibrary.load(csv_path)[0].name == "BOM Radio"


def test_unknown_columns_are_ignored(csv_path):
    csv_path.write_text("name,url,rating\nX,https://e.com/s,5\n", encoding="utf-8")
    assert StationLibrary.load(csv_path)[0].name == "X"


def test_absent_columns_get_defaults(csv_path):
    csv_path.write_text("name,url\nX,https://e.com/s\n", encoding="utf-8")
    station = StationLibrary.load(csv_path)[0]
    assert station.codec == "" and station.bitrate is None


def test_bad_rows_are_skipped_without_losing_good_ones(csv_path):
    csv_path.write_text(
        "name,url\n"
        "Good One,https://e.com/1\n"
        ",https://e.com/2\n"  # no name
        "No URL,\n"  # no url
        "Good Two,https://e.com/3\n",
        encoding="utf-8",
    )
    assert [s.name for s in StationLibrary.load(csv_path)] == ["Good One", "Good Two"]


def test_non_numeric_bitrate_becomes_none(csv_path):
    csv_path.write_text("name,url,bitrate\nX,https://e.com/s,lots\n", encoding="utf-8")
    assert StationLibrary.load(csv_path)[0].bitrate is None


def test_surrounding_whitespace_is_trimmed(csv_path):
    csv_path.write_text("name,url\n  Spaced  ,  https://e.com/s  \n", encoding="utf-8")
    station = StationLibrary.load(csv_path)[0]
    assert (station.name, station.url) == ("Spaced", "https://e.com/s")


# -- atomic writes --------------------------------------------------------


def test_failed_write_leaves_the_original_intact(csv_path, monkeypatch):
    StationLibrary([JAZZ], path=csv_path).save()
    original = csv_path.read_bytes()

    library = StationLibrary([JAZZ, Station(name="Second", url="https://e.com/2")], path=csv_path)

    def explode(self, row):
        raise OSError("disk full")

    monkeypatch.setattr(csv.DictWriter, "writerow", explode)
    with pytest.raises(OSError):
        library.save()

    assert csv_path.read_bytes() == original
    assert not [p for p in csv_path.parent.iterdir() if p.name.startswith(".stations-")]


def test_save_creates_missing_parent_directories(tmp_path):
    path = tmp_path / "deep" / "nested" / "stations.csv"
    StationLibrary([JAZZ], path=path).save()
    assert StationLibrary.load(path)[0] == JAZZ


def test_save_leaves_no_temporary_files(csv_path):
    StationLibrary([JAZZ], path=csv_path).save()
    assert [p.name for p in csv_path.parent.iterdir()] == ["stations.csv"]


def test_editing_rolls_back_memory_when_save_fails(csv_path, monkeypatch):
    library = StationLibrary([JAZZ], path=csv_path)

    def explode():
        raise OSError("disk full")

    monkeypatch.setattr(library, "save", explode)
    with pytest.raises(OSError, match="disk full"), library.editing():
        library.remove(JAZZ.key)

    assert list(library) == [JAZZ]


# -- collection behaviour -------------------------------------------------


def test_duplicate_uuid_is_rejected():
    library = StationLibrary([JAZZ])
    duplicate = Station(name="Renamed", url="https://other", stationuuid=JAZZ.stationuuid)
    assert library.add(duplicate) is False
    assert len(library) == 1


def test_hand_added_stations_deduplicate_on_url():
    """Stations added by hand have no UUID, so the URL is their identity."""
    a = Station(name="One", url="https://e.com/s/")
    b = Station(name="Two", url="https://e.com/s")
    library = StationLibrary([a])
    assert library.add(b) is False


def test_add_stamps_the_added_date():
    library = StationLibrary([Station(name="X", url="https://e.com/s")])
    assert library[0].added != ""


def test_add_preserves_an_existing_added_date():
    assert StationLibrary([JAZZ])[0].added == "2026-08-21"


def test_remove():
    library = StationLibrary([JAZZ])
    assert library.remove(JAZZ.key) is True
    assert library.remove(JAZZ.key) is False
    assert len(library) == 0


def test_remove_frees_the_key_for_reuse():
    library = StationLibrary([JAZZ])
    library.remove(JAZZ.key)
    assert library.add(JAZZ) is True


def test_move_reorders_and_clamps():
    stations = [Station(name=n, url=f"https://e.com/{n}") for n in "ABC"]
    library = StationLibrary(stations)
    assert library.move(0, 1) == 1
    assert [s.name for s in library] == ["B", "A", "C"]
    assert library.move(0, -5) == 0  # clamped at the top
    assert library.move(2, 99) == 2  # clamped at the bottom
    assert [s.name for s in library] == ["B", "A", "C"]


def test_move_on_empty_library_does_not_raise():
    assert StationLibrary().move(0, 1) == 0


def test_find():
    library = StationLibrary([JAZZ])
    assert library.find(JAZZ.key) == JAZZ
    assert library.find("nope") is None


def test_search_matches_name_country_and_tags():
    library = StationLibrary([JAZZ])
    assert library.search("smooth") == [JAZZ]
    assert library.search("MEXICO") == [JAZZ]
    assert library.search("piano") == [JAZZ]
    assert library.search("polka") == []
    assert library.search("  ") == [JAZZ]  # blank term means everything


# -- location -------------------------------------------------------------


def test_env_var_overrides_the_default_path(monkeypatch, tmp_path):
    monkeypatch.setenv(ENV_VAR, str(tmp_path / "custom.csv"))
    assert default_path() == tmp_path / "custom.csv"


def test_default_path_lands_in_a_pyode_directory(monkeypatch):
    monkeypatch.delenv(ENV_VAR, raising=False)
    path = default_path()
    assert path.name == "stations.csv" and path.parent.name == "pyode"


# -- API payloads ---------------------------------------------------------


def test_from_api_prefers_the_resolved_url():
    station = Station.from_api(
        {"name": "X", "url": "https://redirector/", "url_resolved": "https://real/stream.aac"}
    )
    assert station.url == "https://real/stream.aac"


def test_from_api_falls_back_when_unresolved():
    assert Station.from_api({"name": "X", "url": "https://only"}).url == "https://only"


def test_from_api_handles_nulls_and_zero_bitrate():
    station = Station.from_api(
        {"name": "X", "url": "https://e.com/s", "bitrate": 0, "homepage": None, "codec": None}
    )
    assert station.bitrate is None and station.homepage == "" and station.codec == ""


def test_from_api_names_the_unnamed():
    assert Station.from_api({"url": "https://e.com/s"}).name == "(unnamed)"


# -- favorites ------------------------------------------------------------


def test_favorite_round_trips(csv_path):
    StationLibrary([replace(JAZZ, favorite=True)], path=csv_path).save()
    assert StationLibrary.load(csv_path)[0].favorite is True


def test_favorite_defaults_to_false(csv_path):
    StationLibrary([JAZZ], path=csv_path).save()
    assert StationLibrary.load(csv_path)[0].favorite is False


def test_files_written_before_the_column_existed_still_load(csv_path):
    """The favorite column was appended later; older files must survive."""
    csv_path.write_text("name,url,added\nOld,https://e.com/s,2026-01-01\n", encoding="utf-8")
    station = StationLibrary.load(csv_path)[0]
    assert station.favorite is False and station.added == "2026-01-01"


@pytest.mark.parametrize("written", ["true", "TRUE", "yes", "Y", "1", "x", "*", " true "])
def test_hand_typed_truthy_values_are_accepted(csv_path, written):
    csv_path.write_text(f"name,url,favorite\nX,https://e.com/s,{written}\n", encoding="utf-8")
    assert StationLibrary.load(csv_path)[0].favorite is True


@pytest.mark.parametrize("written", ["", "false", "no", "0", "maybe"])
def test_anything_else_is_not_a_favorite(csv_path, written):
    csv_path.write_text(f"name,url,favorite\nX,https://e.com/s,{written}\n", encoding="utf-8")
    assert StationLibrary.load(csv_path)[0].favorite is False


def test_toggle_favorite_returns_new_state():
    library = StationLibrary([JAZZ])
    assert library.toggle_favorite(JAZZ.key) is True
    assert library.toggle_favorite(JAZZ.key) is False


def test_toggle_favorite_on_unknown_key_returns_none():
    assert StationLibrary([JAZZ]).toggle_favorite("nope") is None


def test_toggle_favorite_keeps_list_position():
    stations = [Station(name=n, url=f"https://e.com/{n}") for n in "ABC"]
    library = StationLibrary(stations)
    library.toggle_favorite(stations[1].key)
    assert [s.name for s in library] == ["A", "B", "C"]
    assert library[1].favorite is True


def test_set_favorite_is_idempotent():
    library = StationLibrary([JAZZ])
    assert library.set_favorite(JAZZ.key) is True
    assert library.set_favorite(JAZZ.key) is True
    assert library[0].favorite is True
    assert library.set_favorite("nope") is False


def test_favorites_filters_in_library_order():
    stations = [Station(name=n, url=f"https://e.com/{n}") for n in "ABC"]
    library = StationLibrary(stations)
    library.set_favorite(stations[2].key)
    library.set_favorite(stations[0].key)
    assert [s.name for s in library.favorites()] == ["A", "C"]


def test_favoriting_does_not_change_identity():
    """Toggling must not orphan the station from its own key."""
    library = StationLibrary([JAZZ])
    library.toggle_favorite(JAZZ.key)
    assert library.find(JAZZ.key) is not None
