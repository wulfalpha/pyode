# SPDX-License-Identifier: GPL-2.0-or-later
import pathlib

import httpx
import pytest
from fakes import FakeMediaSession, FakePlayer

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


@pytest.fixture(autouse=True)
def isolated_history(tmp_path, monkeypatch):
    monkeypatch.setenv("PYODE_HISTORY_FILE", str(tmp_path / "history.csv"))


@pytest.fixture(autouse=True)
def clipboard(monkeypatch):
    """Record copies instead of overwriting the developer's clipboard."""
    import pyode.app

    copied: list[str] = []
    monkeypatch.setattr(pyode.app, "_native_copy", lambda text: copied.append(text) or True)
    return copied


@pytest.fixture(autouse=True)
def media(monkeypatch):
    """Keep the tests out of the developer's Now Playing and media keys.

    Returns a list that receives each app's session as it mounts.
    """
    import pyode.mediacontrols

    sessions: list[FakeMediaSession] = []

    def create(commands, *, enabled=True):
        if not enabled:
            return pyode.mediacontrols.NullSession()
        sessions.append(FakeMediaSession(commands))
        return sessions[-1]

    monkeypatch.setattr(pyode.mediacontrols, "create", create)
    return sessions
