# SPDX-License-Identifier: GPL-2.0-or-later
"""The ICY relay, against a fake SHOUTcast server on loopback."""

import socket
import socketserver
import threading
from dataclasses import replace

import pytest

from pyode.app import PyodeApp
from pyode.icyrelay import IcyRelay, relayable, rewrite_status
from pyode.player import PlayerState

AUDIO = bytes(range(256)) * 64  # 16 KiB standing in for the stream


class FakeShoutcast(socketserver.ThreadingTCPServer):
    """Answers the way a SHOUTcast v1 server does, and remembers the request."""

    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, status: bytes = b"ICY 200 OK\r\n") -> None:
        self.status = status
        self.requests: list[bytes] = []
        server = self

        class Handler(socketserver.StreamRequestHandler):
            def handle(self) -> None:
                request = b""
                while not request.endswith(b"\r\n\r\n"):
                    chunk = self.request.recv(4096)
                    if not chunk:
                        break
                    request += chunk
                server.requests.append(request)
                self.wfile.write(
                    server.status
                    + b"icy-name: Fake Radio\r\nicy-metaint: 8192\r\n"
                    + b"content-type: audio/aacp\r\n\r\n"
                    + AUDIO
                )

        super().__init__(("127.0.0.1", 0), Handler)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server_address[1]}/stream?id=7"


@pytest.fixture
def upstream():
    server = FakeShoutcast()
    thread = threading.Thread(target=server.serve_forever, args=(0.05,), daemon=True)
    thread.start()
    yield server
    server.shutdown()
    server.server_close()


@pytest.fixture
def relay():
    relay = IcyRelay()
    yield relay
    relay.close()


def fetch(url: str, extra: bytes = b"Icy-MetaData: 1\r\n") -> bytes:
    """GET `url` the way ffmpeg does and return the whole response."""
    host_port, path = url.removeprefix("http://").split("/", 1)
    host, port = host_port.split(":")
    with socket.create_connection((host, int(port)), timeout=5) as sock:
        sock.sendall(
            f"GET /{path} HTTP/1.1\r\nHost: {host_port}\r\n".encode()
            + b"User-Agent: Lavf/62\r\nConnection: close\r\n"
            + extra
            + b"\r\n"
        )
        response = b""
        while chunk := sock.recv(65536):
            response += chunk
    return response


def test_status_line_rewrite():
    assert rewrite_status(b"ICY 200 OK\r\n") == b"HTTP/1.0 200 OK\r\n"
    assert rewrite_status(b"icy 404 Not Found\r\n") == b"HTTP/1.0 404 Not Found\r\n"
    assert rewrite_status(b"HTTP/1.1 200 OK\r\n") == b"HTTP/1.1 200 OK\r\n"


def test_only_plain_http_is_relayable():
    assert relayable("http://relay1.slayradio.org:8300/")
    assert not relayable("https://ice1.somafm.com/groovesalad-128-mp3")


def test_relays_an_icy_station_as_http(relay, upstream):
    response = fetch(relay.url_for(upstream.url))
    head, _, body = response.partition(b"\r\n\r\n")
    lines = head.split(b"\r\n")
    assert lines[0] == b"HTTP/1.0 200 OK"
    assert b"icy-name: Fake Radio" in lines
    assert b"icy-metaint: 8192" in lines
    assert body == AUDIO  # every byte, untouched


def test_forwards_the_request_to_the_real_path(relay, upstream):
    fetch(relay.url_for(upstream.url))
    request = upstream.requests[0]
    first, *headers = request.split(b"\r\n")
    assert first == b"GET /stream?id=7 HTTP/1.0"
    port = upstream.server_address[1]
    assert f"Host: 127.0.0.1:{port}".encode() in headers
    assert b"Icy-MetaData: 1" in headers  # so titles keep coming
    assert b"User-Agent: Lavf/62" in headers
    assert b"Connection: close" in headers
    assert sum(h.lower().startswith(b"host:") for h in headers) == 1


def test_ordinary_http_passes_through_unchanged(relay):
    server = FakeShoutcast(status=b"HTTP/1.1 200 OK\r\n")
    threading.Thread(target=server.serve_forever, args=(0.05,), daemon=True).start()
    try:
        assert fetch(relay.url_for(server.url)).startswith(b"HTTP/1.1 200 OK\r\n")
    finally:
        server.shutdown()
        server.server_close()


def test_same_station_gets_the_same_address(relay, upstream):
    assert relay.url_for(upstream.url) == relay.url_for(upstream.url)
    assert relay.url_for(upstream.url) != relay.url_for(upstream.url + "&x=1")


def test_unknown_paths_are_refused(relay, upstream):
    relay_url = relay.url_for(upstream.url)
    base = relay_url.rsplit("/", 1)[0]
    assert fetch(f"{base}/not-a-token").startswith(b"HTTP/1.0 404")
    assert upstream.requests == []  # nothing was fetched on a stranger's behalf


def test_unreachable_station_is_a_bad_gateway(relay):
    with socket.socket() as probe:  # a port with nothing listening
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    url = relay.url_for(f"http://127.0.0.1:{port}/")
    assert fetch(url).startswith(b"HTTP/1.0 502")


def test_listens_on_loopback_only(relay, upstream):
    assert relay.url_for(upstream.url).startswith("http://127.0.0.1:")


def test_close_stops_it(relay, upstream):
    url = relay.url_for(upstream.url)
    relay.close()
    with pytest.raises(OSError):
        fetch(url)


# -- in the app ---------------------------------------------------------------


@pytest.fixture
def app(library, player, api):
    return PyodeApp(library=library, player=player, api=api)


# These tick by hand: the fake player reports PLAYING the instant it is asked
# to play, and a tick in between would count the station as having started.


async def test_a_station_that_never_starts_is_retried_through_the_relay(app, player):
    async with app.run_test() as pilot:
        toasts = []
        app.notify = lambda message, **kwargs: toasts.append(message)
        app._timer.stop()
        player.relay_retries = 1
        await pilot.press("p")
        player.status = replace(player.status, state=PlayerState.ERROR, error="loading failed")
        app._tick()
        assert player.relay_asked == 1
        assert player.status.state is PlayerState.PLAYING
        assert toasts == []  # rescued quietly


async def test_a_dropped_stream_reconnects_rather_than_relays(app, player, monkeypatch):
    import pyode.app

    monkeypatch.setattr(pyode.app, "RECONNECT_DELAYS", (5,))
    async with app.run_test() as pilot:
        player.relay_retries = 1
        await pilot.press("p")
        await pilot.pause(0.2)
        app._playing_since = 0.0  # it had been playing
        player.status = replace(player.status, state=PlayerState.ERROR, error="dropped")
        await pilot.pause(0.2)
        assert player.relay_asked == 0
        assert app._reconnect_timer is not None


async def test_when_the_relay_cannot_help_the_failure_is_reported(app, player):
    async with app.run_test() as pilot:
        toasts = []
        app.notify = lambda message, **kwargs: toasts.append(message)
        app._timer.stop()
        await pilot.press("p")
        player.status = replace(player.status, state=PlayerState.ERROR, error="loading failed")
        app._tick()
        assert player.relay_asked == 1
        assert any("loading failed" in toast for toast in toasts)
