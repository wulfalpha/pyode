# SPDX-License-Identifier: GPL-2.0-or-later
"""A localhost relay for SHOUTcast v1 servers that modern ffmpeg refuses.

Old SHOUTcast servers answer a request with ``ICY 200 OK`` where every other
server says ``HTTP/1.1 200 OK``.  Everything after that first line is ordinary
HTTP.  libcurl has always refused it, and since August 2026 ffmpeg does too
("avformat/http: reject malformed response status lines"), so builds of
libmpv against current ffmpeg cannot play these stations at all -- SLAY Radio
among them.

So pyode can fetch such a station itself: mpv is pointed at
``http://127.0.0.1:<port>/<token>``, the relay makes the real request,
replaces ``ICY`` with ``HTTP/1.0`` on the status line, and copies everything
else through untouched -- headers, audio, and the interleaved ICY titles.

It runs on plain sockets in background threads, so it needs no event loop and
serves the headless ``--play`` mode as well as the interface.  It only ever
listens on the loopback interface, and only relays URLs pyode registered.
"""

from __future__ import annotations

import logging
import secrets
import socket
import socketserver
import threading
from urllib.parse import urlsplit

log = logging.getLogger(__name__)

CONNECT_TIMEOUT = 10.0
# A live stream sends constantly.  This long without a byte means it has
# stalled; closing lets ffmpeg's own reconnect logic take over.
STALL_TIMEOUT = 30.0
MAX_REQUEST = 16 * 1024
CHUNK = 16 * 1024
# Headers the relay sets itself rather than passing on from mpv.
_OWN_HEADERS = {b"host", b"connection", b"proxy-connection", b"keep-alive"}


def rewrite_status(line: bytes) -> bytes:
    """``ICY 200 OK`` becomes ``HTTP/1.0 200 OK``; anything else is unchanged."""
    if line[:4].upper() == b"ICY ":
        return b"HTTP/1.0" + line[3:]
    return line


class _Handler(socketserver.StreamRequestHandler):
    server: _Server

    def handle(self) -> None:
        try:
            request_line, headers = self._read_request()
        except (OSError, ValueError):
            return
        parts = request_line.split()
        token = parts[1].lstrip(b"/").decode("ascii", "replace") if len(parts) >= 2 else ""
        upstream = self.server.relay.lookup(token)
        if upstream is None:
            self._reply(b"404 Not Found")
            return
        try:
            self._relay(upstream, headers)
        except OSError as exc:
            log.debug("relay for %s ended: %s", upstream, exc)

    def _read_request(self) -> tuple[bytes, list[bytes]]:
        request_line = self.rfile.readline(MAX_REQUEST)
        headers: list[bytes] = []
        size = len(request_line)
        while True:
            line = self.rfile.readline(MAX_REQUEST)
            size += len(line)
            if size > MAX_REQUEST:
                raise ValueError("request too large")
            if line in (b"\r\n", b"\n", b""):
                return request_line, headers
            headers.append(line)

    def _reply(self, status: bytes) -> None:
        self.wfile.write(b"HTTP/1.0 " + status + b"\r\nContent-Length: 0\r\n\r\n")

    def _relay(self, upstream: str, client_headers: list[bytes]) -> None:
        parts = urlsplit(upstream)
        port = parts.port or 80
        path = parts.path or "/"
        if parts.query:
            path += "?" + parts.query
        host = parts.hostname or ""
        host_header = host if port == 80 else f"{host}:{port}"
        # HTTP/1.0, so the answer is never chunked: SHOUTcast would not chunk
        # anyway, and the relay can then copy bytes without parsing them.
        request = [f"GET {path} HTTP/1.0\r\n".encode(), f"Host: {host_header}\r\n".encode()]
        for header in client_headers:
            name = header.split(b":", 1)[0].strip().lower()
            if name not in _OWN_HEADERS:
                request.append(header if header.endswith(b"\r\n") else header.rstrip() + b"\r\n")
        request.append(b"Connection: close\r\n\r\n")

        try:
            remote = socket.create_connection((host, port), timeout=CONNECT_TIMEOUT)
        except OSError as exc:
            log.info("relay could not reach %s: %s", upstream, exc)
            self._reply(b"502 Bad Gateway")
            return
        with remote:
            remote.settimeout(STALL_TIMEOUT)
            remote.sendall(b"".join(request))
            reader = remote.makefile("rb")
            status = reader.readline(MAX_REQUEST)
            if not status:
                return
            self.wfile.write(rewrite_status(status))
            while chunk := reader.read1(CHUNK):
                self.wfile.write(chunk)


class _Server(socketserver.ThreadingTCPServer):
    daemon_threads = True  # a stream being relayed must not hold up quitting
    allow_reuse_address = True

    def __init__(self, relay: IcyRelay) -> None:
        super().__init__(("127.0.0.1", 0), _Handler)
        self.relay = relay


class IcyRelay:
    """The relay itself.  Started on first use; `close` stops it."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._server: _Server | None = None
        self._routes: dict[str, str] = {}

    def url_for(self, upstream: str) -> str:
        """The local URL that plays `upstream` through the relay."""
        with self._lock:
            if self._server is None:
                self._server = _Server(self)
                # Shutting down waits for the loop to notice, which it checks
                # this often; the default half second would hold up quitting.
                thread = threading.Thread(
                    target=self._server.serve_forever,
                    kwargs={"poll_interval": 0.1},
                    name="pyode-icy-relay",
                    daemon=True,
                )
                thread.start()
            token = next((t for t, u in self._routes.items() if u == upstream), None)
            if token is None:
                # Unguessable, so nothing else on the machine can use the
                # relay to make requests on pyode's behalf.
                token = secrets.token_urlsafe(12)
                self._routes[token] = upstream
            port = self._server.server_address[1]
        return f"http://127.0.0.1:{port}/{token}"

    def lookup(self, token: str) -> str | None:
        with self._lock:
            return self._routes.get(token)

    def close(self) -> None:
        with self._lock:
            server, self._server = self._server, None
        if server is not None:
            server.shutdown()
            server.server_close()


def relayable(url: str) -> bool:
    """Whether a URL could be a SHOUTcast v1 stream the relay can help with:
    plain http only, since SHOUTcast v1 never spoke TLS."""
    return urlsplit(url).scheme.lower() == "http"
