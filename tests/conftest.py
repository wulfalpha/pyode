# SPDX-License-Identifier: GPL-2.0-or-later
import pathlib

import httpx
import pytest
from fakes import FakePlayer

from pyode.browser import RadioBrowser
from pyode.stations import Station, StationLibrary

SEEDS = [
    ("Jazz Radio Blues", "France", "MP3", 128, "jazz,blues", True),
    ("Classic Vinyl HD", "United States", "MP3", 320, "swing", False),
    ("Adroit Jazz Underground", "United States", "MP3", 320, "jazz", False),
]

API_ROWS = [
    {
        "stationuuid": "found-1",
        "name": "TSF Jazz",
        "url": "https://redirect/1",
        "url_resolved": "https://stream/1",
        "codec": "MP3",
        "bitrate": 128,
        "country": "France",
        "tags": "jazz",
    }
]


@pytest.fixture
def library(tmp_path: pathlib.Path) -> StationLibrary:
    stations = [
        Station(
            name=name,
            url=f"https://example.com/{index}",
            country=country,
            codec=codec,
            bitrate=bitrate,
            tags=tags,
            favorite=favorite,
            stationuuid=f"uuid-{index}",
        )
        for index, (name, country, codec, bitrate, tags, favorite) in enumerate(SEEDS)
    ]
    return StationLibrary(stations, path=tmp_path / "stations.csv")


@pytest.fixture
def empty_library(tmp_path: pathlib.Path) -> StationLibrary:
    return StationLibrary(path=tmp_path / "stations.csv")


@pytest.fixture
def player() -> FakePlayer:
    return FakePlayer()


@pytest.fixture
def api() -> RadioBrowser:
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=API_ROWS))
    return RadioBrowser("https://test.invalid", client=httpx.AsyncClient(transport=transport))


@pytest.fixture(autouse=True)
def isolated_preferences(tmp_path, monkeypatch):
    monkeypatch.setenv("PYODE_CONFIG_FILE", str(tmp_path / "config.toml"))
