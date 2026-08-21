# SPDX-License-Identifier: GPL-2.0-or-later
"""Tests for the radio-browser.info client.

These run against an httpx MockTransport rather than the live directory: it is
a free volunteer-run service, and a test suite is not a good reason to send it
traffic. Live behaviour is checked by hand instead.
"""

import httpx
import pytest

from pyode.browser import (
    DEFAULT_SERVER,
    Country,
    RadioBrowser,
    RadioBrowserError,
    Tag,
    _params,
    discover_servers,
)

BASE = "https://test.invalid"

STATION_ROW = {
    "stationuuid": "96102c22-0601-11e8-ae97-52543be04c81",
    "name": "Jazz Radio Blues",
    "url": "http://redirector/blues",
    "url_resolved": "http://jazzblues.ice.infomaniak.ch/jazzblues-high.mp3",
    "codec": "MP3",
    "bitrate": 128,
    "country": "France",
    "countrycode": "FR",
    "tags": "jazz,blues",
    "homepage": "https://jazzradio.fr/",
    "favicon": "",
}


def make_api(payload=None, *, status=200, text=None):
    """A client wired to a fake transport, plus the list of requests it saw."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if text is not None:
            return httpx.Response(status, text=text)
        return httpx.Response(status, json=payload)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return RadioBrowser(BASE, client=client), seen


# -- parameter building ---------------------------------------------------


def test_params_renders_booleans_the_api_way():
    assert _params(a=True, b=False) == {"a": "true", "b": "false"}


def test_params_drops_unset_values():
    assert _params(a=None, b="", c=0, d="x") == {"c": "0", "d": "x"}


async def test_search_sends_expected_query():
    api, seen = make_api([])
    await api.search(name="jazz", tag="blues", country_code="fr", limit=5, hide_broken=True)
    query = dict(seen[0].url.params)
    assert query["name"] == "jazz"
    assert query["tag"] == "blues"
    assert query["countrycode"] == "FR"  # the API matches on upper case
    assert query["limit"] == "5"
    assert query["hidebroken"] == "true"


async def test_search_omits_criteria_that_were_not_given():
    api, seen = make_api([])
    await api.search(name="jazz")
    assert "tag" not in seen[0].url.params
    assert "countrycode" not in seen[0].url.params


async def test_requests_identify_pyode():
    api, seen = make_api([])
    await api.search()
    assert seen[0].headers["user-agent"].startswith("pyode/")


async def test_top_clicked_and_top_voted_hit_their_endpoints():
    api, seen = make_api([])
    await api.top_clicked(7)
    await api.top_voted(3)
    assert seen[0].url.path == "/json/stations/topclick/7"
    assert seen[1].url.path == "/json/stations/topvote/3"


async def test_by_uuid_joins_ids():
    api, seen = make_api([])
    await api.by_uuid("a", "b")
    assert dict(seen[0].url.params)["uuids"] == "a,b"


async def test_by_uuid_with_no_ids_makes_no_request():
    api, seen = make_api([])
    assert await api.by_uuid() == []
    assert seen == []


# -- station conversion ---------------------------------------------------


async def test_search_returns_stations_using_the_resolved_url():
    api, _ = make_api([STATION_ROW])
    station = (await api.search())[0]
    assert station.name == "Jazz Radio Blues"
    assert station.url == STATION_ROW["url_resolved"]
    assert station.bitrate == 128


async def test_duplicate_stations_are_collapsed():
    api, _ = make_api([STATION_ROW, dict(STATION_ROW)])
    assert len(await api.search()) == 1


async def test_stations_without_a_url_are_dropped():
    api, _ = make_api([{"name": "Silent", "url": "", "url_resolved": ""}, STATION_ROW])
    assert [s.name for s in await api.search()] == ["Jazz Radio Blues"]


async def test_junk_rows_are_skipped():
    api, _ = make_api(["nonsense", None, 42, STATION_ROW])
    assert len(await api.search()) == 1


# -- facets ---------------------------------------------------------------


async def test_countries_are_parsed():
    api, _ = make_api([{"name": "Andorra", "iso_3166_1": "AD", "stationcount": 11}])
    assert await api.countries() == [Country(name="Andorra", code="AD", station_count=11)]


async def test_tags_are_parsed_and_ordered_by_popularity():
    api, seen = make_api([{"name": "pop", "stationcount": 6022}])
    assert await api.tags(limit=3) == [Tag(name="pop", station_count=6022)]
    query = dict(seen[0].url.params)
    assert (query["order"], query["reverse"], query["limit"]) == ("stationcount", "true", "3")


async def test_nameless_facets_are_ignored():
    api, _ = make_api([{"name": "", "stationcount": 1}, {"stationcount": 2}])
    assert await api.tags() == []


# -- failure modes --------------------------------------------------------


@pytest.mark.parametrize("status", [404, 429, 500, 503])
async def test_http_errors_become_radiobrowser_errors(status):
    api, _ = make_api([], status=status)
    with pytest.raises(RadioBrowserError, match=str(status)):
        await api.search()


async def test_unreachable_server_becomes_a_radiobrowser_error():
    def handler(request):
        raise httpx.ConnectError("no route to host")

    api = RadioBrowser(BASE, client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    with pytest.raises(RadioBrowserError, match="could not reach"):
        await api.search()


async def test_invalid_json_becomes_a_radiobrowser_error():
    api, _ = make_api(text="<html>down for maintenance</html>")
    with pytest.raises(RadioBrowserError, match="invalid JSON"):
        await api.search()


async def test_unexpected_payload_shape_becomes_a_radiobrowser_error():
    api, _ = make_api({"error": "nope"})
    with pytest.raises(RadioBrowserError, match="expected a list"):
        await api.search()


# -- click registration ---------------------------------------------------


async def test_register_click_reports_success():
    api, seen = make_api({"ok": True, "message": "retrieved station url"})
    assert await api.register_click("abc") is True
    assert seen[0].url.path == "/json/url/abc"


async def test_register_click_never_raises():
    """A courtesy call must not be able to break playback."""
    api, _ = make_api([], status=500)
    assert await api.register_click("abc") is False


async def test_register_click_ignores_an_empty_uuid():
    api, seen = make_api({"ok": True})
    assert await api.register_click("") is False
    assert seen == []


# -- server discovery -----------------------------------------------------


async def test_discovery_failure_falls_back_to_the_default_server(monkeypatch):
    monkeypatch.setattr("pyode.browser._resolve_mirrors", lambda: [])
    api = RadioBrowser()
    assert await api.base_url() == DEFAULT_SERVER


async def test_discovered_mirror_is_used(monkeypatch):
    monkeypatch.setattr("pyode.browser._resolve_mirrors", lambda: ["de1.api.radio-browser.info"])
    api = RadioBrowser()
    assert await api.base_url() == "https://de1.api.radio-browser.info"


async def test_discovery_survives_a_dns_outage(monkeypatch):
    def explode(*_args, **_kwargs):
        raise OSError("name resolution failed")

    monkeypatch.setattr("socket.getaddrinfo", explode)
    assert await discover_servers() == []


async def test_explicit_base_url_skips_discovery(monkeypatch):
    def explode():
        raise AssertionError("discovery should not run")

    monkeypatch.setattr("pyode.browser._resolve_mirrors", explode)
    assert await RadioBrowser(BASE).base_url() == BASE


async def test_base_url_is_resolved_only_once(monkeypatch):
    calls = []

    def counted():
        calls.append(1)
        return ["de1.api.radio-browser.info"]

    monkeypatch.setattr("pyode.browser._resolve_mirrors", counted)
    api = RadioBrowser()
    await api.base_url()
    await api.base_url()
    assert len(calls) == 1


# -- lifecycle ------------------------------------------------------------


async def test_context_manager_closes_an_owned_client(monkeypatch):
    monkeypatch.setattr("pyode.browser._resolve_mirrors", lambda: [])
    async with RadioBrowser(BASE) as api:
        client = api._get_client()
    assert client.is_closed


async def test_an_injected_client_is_left_open():
    """The caller owns what the caller passed in."""
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=[]))
    client = httpx.AsyncClient(transport=transport)
    async with RadioBrowser(BASE, client=client):
        pass
    assert not client.is_closed
    await client.aclose()
