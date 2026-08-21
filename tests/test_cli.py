# SPDX-License-Identifier: GPL-2.0-or-later
"""Tests for the command line, including first-run seeding."""

import pytest

from pyode import cli
from pyode.stations import SEED_STATIONS, Station, StationLibrary


@pytest.fixture
def path(tmp_path):
    return tmp_path / "stations.csv"


# -- seeding --------------------------------------------------------------


def test_first_run_seeds_the_library(path):
    library = cli.load_library(path)
    assert len(library) == len(SEED_STATIONS)
    assert path.exists()


def test_seeding_is_written_to_disk(path):
    cli.load_library(path)
    assert len(StationLibrary.load(path)) == len(SEED_STATIONS)


def test_an_emptied_library_is_not_refilled(path):
    """Deleting every station is a decision, not a corruption to repair."""
    cli.load_library(path)
    library = StationLibrary.load(path)
    for station in list(library):
        library.remove(station.key)
    library.save()

    assert len(cli.load_library(path)) == 0


def test_an_existing_library_is_left_alone(path):
    StationLibrary([Station(name="Mine", url="https://e.com/1")], path=path).save()
    library = cli.load_library(path)
    assert [s.name for s in library] == ["Mine"]


def test_seed_stations_are_well_formed():
    assert all(s.name and s.url and s.stationuuid for s in SEED_STATIONS)


def test_seed_stations_are_distinct():
    keys = [s.key for s in SEED_STATIONS]
    assert len(set(keys)) == len(keys)


def test_seed_stations_are_not_favourites():
    """Favouriting is the user's call, not a shipped default."""
    assert not any(s.favorite for s in SEED_STATIONS)


# -- station matching -----------------------------------------------------


@pytest.fixture
def library(tmp_path):
    return StationLibrary(
        [
            Station(name="Jazz Radio Blues", url="https://e.com/1", country="France", tags="jazz"),
            Station(name="Dance Wave!", url="https://e.com/2", country="Hungary", tags="dance"),
        ],
        path=tmp_path / "stations.csv",
    )


def test_exact_name_wins(library):
    assert cli.match_station(library, "Dance Wave!").url == "https://e.com/2"


def test_matching_ignores_case(library):
    assert cli.match_station(library, "jazz radio blues") is not None


def test_partial_names_match(library):
    assert cli.match_station(library, "blues").name == "Jazz Radio Blues"


def test_tags_and_countries_match(library):
    assert cli.match_station(library, "Hungary").name == "Dance Wave!"


def test_no_match_returns_none(library):
    assert cli.match_station(library, "polka") is None


# -- argument handling ----------------------------------------------------


def test_list_prints_every_station(path, capsys):
    cli.load_library(path)
    assert cli.main(["--list", "--stations", str(path)]) == 0
    printed = capsys.readouterr().out
    for station in SEED_STATIONS:
        assert station.name in printed


def test_list_of_an_empty_library_says_what_to_do(path, capsys):
    StationLibrary(path=path).save()
    cli.main(["--list", "--stations", str(path)])
    assert "press /" in capsys.readouterr().out.lower()


def test_unknown_station_name_fails_with_guidance(path, capsys):
    assert cli.main(["--station", "polka", "--stations", str(path)]) == 1
    assert "--list" in capsys.readouterr().err


def test_named_station_is_handed_to_the_interface(path, monkeypatch):
    seen = {}

    def fake_run(library, autoplay, *, volume, visualiser):
        seen.update(autoplay=autoplay, volume=volume, visualiser=visualiser)
        return 0

    monkeypatch.setattr(cli, "_run_tui", fake_run)
    assert cli.main(["--station", "jazz", "--stations", str(path), "--volume", "55"]) == 0
    assert seen["autoplay"].name == "Jazz Radio Blues"
    assert seen["volume"] == 55
    assert seen["visualiser"] is True


def test_visualiser_can_be_suppressed(path, monkeypatch):
    seen = {}
    monkeypatch.setattr(cli, "_run_tui", lambda lib, auto, **kw: seen.update(kw) or 0)
    cli.main(["--no-visualiser", "--stations", str(path)])
    assert seen["visualiser"] is False


@pytest.mark.parametrize("volume", ["-5", "101"])
def test_volume_outside_the_range_is_rejected(volume, path):
    with pytest.raises(SystemExit):
        cli.main(["--volume", volume, "--stations", str(path)])


def test_version_exits_cleanly():
    with pytest.raises(SystemExit) as exit_info:
        cli.main(["--version"])
    assert exit_info.value.code == 0
