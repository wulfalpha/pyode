# SPDX-License-Identifier: GPL-2.0-or-later
"""The station library, persisted as a plain CSV file.

CSV is the storage format on purpose: the library is meant to be editable in a
spreadsheet or a text editor without going through pyode at all.  That choice
drives most of what follows -- reading tolerates a byte-order mark and unknown
or missing columns, because a round trip through Excel or LibreOffice adds the
former and a hand edit can produce the latter.

Writes are atomic.  A truncated station list is worse than a stale one, so the
file is written to a temporary sibling, flushed to disk, and moved into place
with ``os.replace``.
"""

from __future__ import annotations

import contextlib
import csv
import logging
import os
import tempfile
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path

from platformdirs import user_config_path

log = logging.getLogger(__name__)

ENV_VAR = "PYODE_STATIONS_FILE"

# The on-disk column order.  Append new columns to the end; readers key off the
# header rather than position, so an older file stays loadable.
FIELDNAMES = (
    "name",
    "url",
    "stationuuid",
    "codec",
    "bitrate",
    "country",
    "tags",
    "homepage",
    "favicon",
    "added",
    "favorite",
)

# Hand-edited files will contain whatever the user felt like typing, so reading
# is generous and writing is canonical.
_TRUTHY = frozenset({"1", "true", "yes", "y", "t", "x", "*"})


def default_path() -> Path:
    """Where the library lives, unless the environment says otherwise."""
    override = os.environ.get(ENV_VAR)
    if override:
        return Path(override).expanduser()
    return user_config_path("pyode") / "stations.csv"


def _utc_today() -> str:
    return datetime.now(UTC).date().isoformat()


@dataclass(frozen=True, slots=True)
class Station:
    name: str
    url: str
    stationuuid: str = ""
    codec: str = ""
    bitrate: int | None = None
    country: str = ""
    tags: str = ""
    homepage: str = ""
    favicon: str = ""
    added: str = ""
    favorite: bool = False

    @property
    def key(self) -> str:
        """Identity for de-duplication.

        Stations from radio-browser carry a UUID.  Hand-added ones do not, so
        they fall back to their URL.
        """
        return self.stationuuid or self.url.strip().rstrip("/")

    @property
    def tag_list(self) -> list[str]:
        return [t.strip() for t in self.tags.split(",") if t.strip()]

    def to_row(self) -> dict[str, str]:
        return {
            "name": self.name,
            "url": self.url,
            "stationuuid": self.stationuuid,
            "codec": self.codec,
            "bitrate": "" if self.bitrate is None else str(self.bitrate),
            "country": self.country,
            "tags": self.tags,
            "homepage": self.homepage,
            "favicon": self.favicon,
            "added": self.added,
            "favorite": "true" if self.favorite else "false",
        }

    @classmethod
    def from_row(cls, row: dict[str, str | None]) -> Station:
        """Build from a CSV row, ignoring unknown columns.

        Raises ``ValueError`` if the row has no name or no URL; a station
        without either is not playable and not worth keeping.
        """
        get = lambda field: (row.get(field) or "").strip()  # noqa: E731
        name, url = get("name"), get("url")
        if not name or not url:
            raise ValueError("row is missing a name or a url")
        bitrate = get("bitrate")
        return cls(
            name=name,
            url=url,
            stationuuid=get("stationuuid"),
            codec=get("codec"),
            bitrate=int(bitrate) if bitrate.isdigit() else None,
            country=get("country"),
            tags=get("tags"),
            homepage=get("homepage"),
            favicon=get("favicon"),
            added=get("added"),
            favorite=get("favorite").lower() in _TRUTHY,
        )

    @classmethod
    def from_api(cls, payload: dict) -> Station:
        """Build from a radio-browser.info search result.

        Prefers ``url_resolved`` -- the post-redirect stream -- because that is
        what actually plays; ``url`` is frequently a redirector.
        """
        get = lambda field: str(payload.get(field) or "").strip()  # noqa: E731
        bitrate = payload.get("bitrate")
        return cls(
            name=get("name") or "(unnamed)",
            url=get("url_resolved") or get("url"),
            stationuuid=get("stationuuid"),
            codec=get("codec"),
            bitrate=int(bitrate) if isinstance(bitrate, int) and bitrate > 0 else None,
            country=get("country"),
            tags=get("tags"),
            homepage=get("homepage"),
            favicon=get("favicon"),
            added=_utc_today(),
        )


# A small, deliberately varied starting library, used only when the file has
# never existed.  Every URL here was checked against a live decoder; shipping a
# default station that does not play is worse than shipping none.
SEED_STATIONS = (
    Station(
        name="BBC World Service",
        url="http://stream.live.vc.bbcmedia.co.uk/bbc_world_service",
        stationuuid="98adecf7-2683-4408-9be7-02d3f9098eb8",
        codec="MP3",
        bitrate=56,
        country="The United Kingdom Of Great Britain And Northern Ireland",
        tags="news,talk",
        homepage="https://www.bbc.co.uk/programmes/w172xzjgf6lxp7y",
    ),
    Station(
        name="Jazz Radio Blues",
        url="http://jazzblues.ice.infomaniak.ch/jazzblues-high.mp3",
        stationuuid="96102c22-0601-11e8-ae97-52543be04c81",
        codec="MP3",
        bitrate=128,
        country="France",
        tags="blues,jazz",
        homepage="http://www.jazzradio.fr/radio/webradio/3/blues",
    ),
    Station(
        name="Classic Vinyl HD",
        url="https://icecast.walmradio.com:8443/classic",
        stationuuid="d1a54d2e-623e-4970-ab11-35f7b56c5ec3",
        codec="MP3",
        bitrate=320,
        country="The United States Of America",
        tags="big band,classic hits,crooners,easy listening,oldies,swing",
        homepage="https://walmradio.com/classic",
    ),
    Station(
        name="SomaFM Groove Salad",
        url="https://ice2.somafm.com/groovesalad-128-mp3",
        stationuuid="960cf833-0601-11e8-ae97-52543be04c81",
        codec="MP3",
        bitrate=128,
        country="The United States Of America",
        tags="ambient,chillout,downtempo,groove,lounge",
        homepage="https://somafm.com/groovesalad/",
    ),
    Station(
        name="Ambient Sleeping Pill",
        url="http://radio.stereoscenic.com/asp-h",
        stationuuid="961fa288-0601-11e8-ae97-52543be04c81",
        codec="MP3",
        bitrate=256,
        country="The United States Of America",
        tags="ambient,meditation,sleep",
        homepage="http://ambientsleepingpill.com/",
    ),
    Station(
        name="Dance Wave!",
        url="https://dancewave.online/dance.mp3",
        stationuuid="962cc6df-0601-11e8-ae97-52543be04c81",
        country="Hungary",
        codec="MP3",
        tags="club,dance,electronic,house,trance",
        homepage="https://dancewave.online/",
    ),
)


class StationLibrary:
    """An ordered, de-duplicated collection of stations backed by a CSV file."""

    def __init__(self, stations: Iterable[Station] = (), path: Path | None = None) -> None:
        self.path = path or default_path()
        self._stations: list[Station] = []
        self._keys: set[str] = set()
        for station in stations:
            self.add(station)

    # -- persistence ------------------------------------------------------

    @classmethod
    def load(cls, path: Path | None = None) -> StationLibrary:
        """Read the library.  A missing file is an empty library, not an error."""
        path = path or default_path()
        library = cls(path=path)
        if not path.exists():
            return library

        # utf-8-sig transparently strips a BOM if a spreadsheet added one, and
        # behaves like plain utf-8 when there is none.
        with path.open("r", encoding="utf-8-sig", newline="") as fh:
            for number, row in enumerate(csv.DictReader(fh), start=2):
                try:
                    library.add(Station.from_row(row))
                except ValueError as exc:
                    # One bad hand edit should not cost the user the rest of
                    # their stations.
                    log.warning("%s line %d skipped: %s", path, number, exc)
        return library

    def save(self) -> None:
        """Write the library atomically."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp: str | None = None
        try:
            fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".stations-", suffix=".csv")
            with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
                writer = csv.DictWriter(fh, fieldnames=FIELDNAMES)
                writer.writeheader()
                for station in self._stations:
                    writer.writerow(station.to_row())
                fh.flush()
                os.fsync(fh.fileno())  # rename is only atomic if the data landed
            os.replace(tmp, self.path)
            tmp = None
        finally:
            if tmp is not None:
                with contextlib.suppress(OSError):
                    os.unlink(tmp)

    # -- mutation ---------------------------------------------------------

    def add(self, station: Station) -> bool:
        """Append a station.  Returns False if it is already in the library."""
        if station.key in self._keys:
            return False
        if not station.added:
            station = replace(station, added=_utc_today())
        self._stations.append(station)
        self._keys.add(station.key)
        return True

    def remove(self, key: str) -> bool:
        for index, station in enumerate(self._stations):
            if station.key == key:
                del self._stations[index]
                self._keys.discard(key)
                return True
        return False

    def move(self, index: int, delta: int) -> int:
        """Shift a station within the list, returning its new index."""
        if not self._stations:
            return 0
        index = max(0, min(index, len(self._stations) - 1))
        target = max(0, min(index + delta, len(self._stations) - 1))
        if target != index:
            self._stations.insert(target, self._stations.pop(index))
        return target

    def set_favorite(self, key: str, value: bool = True) -> bool:
        """Mark or unmark a station, keeping its position in the list."""
        for index, station in enumerate(self._stations):
            if station.key == key:
                if station.favorite != value:
                    self._stations[index] = replace(station, favorite=value)
                return True
        return False

    def toggle_favorite(self, key: str) -> bool | None:
        """Flip a station's favorite flag, returning the new state.

        Returns None if the key is not in the library.
        """
        for index, station in enumerate(self._stations):
            if station.key == key:
                flipped = replace(station, favorite=not station.favorite)
                self._stations[index] = flipped
                return flipped.favorite
        return None

    # -- access -----------------------------------------------------------

    def favorites(self) -> list[Station]:
        return [s for s in self._stations if s.favorite]

    def find(self, key: str) -> Station | None:
        return next((s for s in self._stations if s.key == key), None)

    def search(self, term: str) -> list[Station]:
        """Case-insensitive match across name, country and tags."""
        term = term.strip().lower()
        if not term:
            return list(self._stations)
        return [
            s
            for s in self._stations
            if term in s.name.lower() or term in s.country.lower() or term in s.tags.lower()
        ]

    def __iter__(self) -> Iterator[Station]:
        return iter(self._stations)

    def __len__(self) -> int:
        return len(self._stations)

    def __getitem__(self, index: int) -> Station:
        return self._stations[index]

    def __repr__(self) -> str:
        return f"StationLibrary({len(self._stations)} stations, path={self.path})"
