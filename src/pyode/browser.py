# SPDX-License-Identifier: GPL-2.0-or-later
"""A client for the radio-browser.info community station directory.

The API is a free, volunteer-run service, so this client tries to be a decent
citizen: it identifies itself with a real User-Agent, asks for broken stations
to be filtered server-side, and reports plays back to the directory so the
community's click counts stay meaningful.

Server discovery goes through DNS rather than an API call, because finding a
server via the API would require already having one.  ``all.api.radio-browser.info``
resolves to the mirror pool; reverse-resolving those addresses yields the real
hostnames.  The pool has been a single machine recently, so `DEFAULT_SERVER` is
the fallback that actually carries the load.
"""

from __future__ import annotations

import asyncio
import logging
import random
import socket
from dataclasses import dataclass
from typing import Any

import httpx

from pyode import __version__
from pyode.stations import Station

log = logging.getLogger(__name__)

DISCOVERY_HOST = "all.api.radio-browser.info"
DEFAULT_SERVER = "https://de1.api.radio-browser.info"
USER_AGENT = f"pyode/{__version__}"


class RadioBrowserError(RuntimeError):
    """Raised when the directory cannot be reached or returns nonsense."""


@dataclass(frozen=True, slots=True)
class Country:
    name: str
    code: str
    station_count: int


@dataclass(frozen=True, slots=True)
class Tag:
    name: str
    station_count: int


def _resolve_mirrors() -> list[str]:
    """Blocking DNS work, kept separate so it can be pushed to a thread."""
    try:
        addresses = {info[4][0] for info in socket.getaddrinfo(DISCOVERY_HOST, 80)}
    except OSError:
        return []
    names: set[str] = set()
    for address in addresses:
        try:
            names.add(socket.gethostbyaddr(address)[0])
        except OSError:
            continue  # a mirror without a PTR record is not usable by name
    return sorted(names)


async def discover_servers(timeout: float = 3.0) -> list[str]:
    """Best-effort mirror list.  Returns an empty list rather than raising."""
    try:
        names = await asyncio.wait_for(asyncio.to_thread(_resolve_mirrors), timeout)
    except (TimeoutError, OSError):
        return []
    return [f"https://{name}" for name in names]


def _params(**kwargs: Any) -> dict[str, str]:
    """Drop unset values and render booleans the way the API expects."""
    out: dict[str, str] = {}
    for key, value in kwargs.items():
        if value is None or value == "":
            continue
        out[key] = "true" if value is True else "false" if value is False else str(value)
    return out


class RadioBrowser:
    """Async client for the station directory.

    Usage::

        async with RadioBrowser() as api:
            for station in await api.search(tag="jazz", limit=20):
                ...
    """

    def __init__(
        self,
        base_url: str | None = None,
        *,
        user_agent: str = USER_AGENT,
        timeout: float = 10.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/") if base_url else None
        self._user_agent = user_agent
        self._timeout = timeout
        self._client = client
        self._owns_client = client is None
        self._resolving = asyncio.Lock()

    # -- plumbing ---------------------------------------------------------

    async def base_url(self) -> str:
        """The mirror in use, discovered on first need and then reused."""
        if self._base_url is None:
            async with self._resolving:
                if self._base_url is None:  # another caller may have won the race
                    mirrors = await discover_servers()
                    self._base_url = random.choice(mirrors) if mirrors else DEFAULT_SERVER
                    log.debug("using radio-browser mirror %s", self._base_url)
        return self._base_url

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=self._timeout,
                follow_redirects=True,
                headers={"User-Agent": self._user_agent},
            )
        return self._client

    async def _get(self, path: str, params: dict[str, str] | None = None) -> Any:
        url = f"{await self.base_url()}{path}"
        try:
            response = await self._get_client().get(
                url, params=params, headers={"User-Agent": self._user_agent}
            )
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as exc:
            raise RadioBrowserError(
                f"{exc.response.status_code} from radio-browser for {path}"
            ) from exc
        except httpx.HTTPError as exc:
            raise RadioBrowserError(f"could not reach radio-browser: {exc}") from exc
        except ValueError as exc:  # includes json.JSONDecodeError
            raise RadioBrowserError(f"radio-browser returned invalid JSON for {path}") from exc

    async def _stations(self, path: str, params: dict[str, str] | None = None) -> list[Station]:
        payload = await self._get(path, params)
        if not isinstance(payload, list):
            raise RadioBrowserError(f"expected a list of stations from {path}")
        return _to_stations(payload)

    # -- queries ----------------------------------------------------------

    async def search(
        self,
        *,
        name: str | None = None,
        tag: str | None = None,
        country_code: str | None = None,
        language: str | None = None,
        limit: int = 50,
        offset: int = 0,
        order: str = "clickcount",
        reverse: bool = True,
        hide_broken: bool = True,
    ) -> list[Station]:
        """Search the directory.  With no criteria, returns the most popular."""
        return await self._stations(
            "/json/stations/search",
            _params(
                name=name,
                tag=tag,
                countrycode=country_code.upper() if country_code else None,
                language=language,
                limit=limit,
                offset=offset,
                order=order,
                reverse=reverse,
                hidebroken=hide_broken,
            ),
        )

    async def top_clicked(self, limit: int = 50) -> list[Station]:
        return await self._stations(
            f"/json/stations/topclick/{int(limit)}", _params(hidebroken=True)
        )

    async def top_voted(self, limit: int = 50) -> list[Station]:
        return await self._stations(
            f"/json/stations/topvote/{int(limit)}", _params(hidebroken=True)
        )

    async def by_uuid(self, *uuids: str) -> list[Station]:
        """Re-fetch known stations, e.g. to refresh a stale saved URL."""
        if not uuids:
            return []
        return await self._stations("/json/stations/byuuid", _params(uuids=",".join(uuids)))

    async def countries(self) -> list[Country]:
        payload = await self._get("/json/countries")
        return [
            Country(
                name=str(row.get("name") or ""),
                code=str(row.get("iso_3166_1") or ""),
                station_count=int(row.get("stationcount") or 0),
            )
            for row in payload
            if isinstance(row, dict) and row.get("name")
        ]

    async def tags(self, limit: int = 100) -> list[Tag]:
        payload = await self._get(
            "/json/tags", _params(limit=limit, order="stationcount", reverse=True)
        )
        return [
            Tag(name=str(row.get("name") or ""), station_count=int(row.get("stationcount") or 0))
            for row in payload
            if isinstance(row, dict) and row.get("name")
        ]

    # -- courtesy ---------------------------------------------------------

    async def register_click(self, stationuuid: str) -> bool:
        """Tell the directory a station was played.

        This is how the community's popularity ranking stays useful, but it is
        never worth failing playback over, so errors are swallowed.
        """
        if not stationuuid:
            return False
        try:
            payload = await self._get(f"/json/url/{stationuuid}")
        except RadioBrowserError:
            log.debug("click registration failed for %s", stationuuid, exc_info=True)
            return False
        return bool(isinstance(payload, dict) and payload.get("ok"))

    # -- lifecycle --------------------------------------------------------

    async def close(self) -> None:
        if self._client is not None and self._owns_client:
            await self._client.aclose()
            self._client = None

    async def __aenter__(self) -> RadioBrowser:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.close()


def _to_stations(payload: list[Any]) -> list[Station]:
    """Convert API rows to stations, dropping unplayable and duplicate ones."""
    stations: list[Station] = []
    seen: set[str] = set()
    for row in payload:
        if not isinstance(row, dict):
            continue
        station = Station.from_api(row)
        if not station.url or station.key in seen:
            continue
        seen.add(station.key)
        stations.append(station)
    return stations
