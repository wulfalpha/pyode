# SPDX-License-Identifier: GPL-2.0-or-later
"""A player stand-in, so the interface can be tested without audio hardware."""

from __future__ import annotations

from dataclasses import replace

from pyode.player import NowPlaying, PlayerState, PlayerStatus


class FakePlayer:
    """Implements the `Player` protocol against in-memory state."""

    def __init__(self, level: float | None = 0.5) -> None:
        self.status = PlayerStatus()
        self.calls: list[tuple] = []
        self._level = level
        self.closed = False
        self.recording: str | None = None
        self.relay_retries = 0  # how many failures retry_through_relay rescues
        self.relay_asked = 0

    # -- Player -----------------------------------------------------------

    def play(self, url: str, station_name: str = "") -> None:
        self.recording = None  # as in MpvPlayer: a recording ends with its stream
        self.calls.append(("play", url, station_name))
        self.status = replace(
            self.status,
            state=PlayerState.PLAYING,
            now_playing=NowPlaying(station=station_name),
            error="",
        )

    def stop(self) -> None:
        self.recording = None
        self.calls.append(("stop",))
        self.status = replace(self.status, state=PlayerState.IDLE, now_playing=NowPlaying())

    def toggle_pause(self) -> None:
        self.calls.append(("toggle_pause",))
        paused = self.status.state is PlayerState.PAUSED
        self.status = replace(
            self.status, state=PlayerState.PLAYING if paused else PlayerState.PAUSED
        )

    def set_volume(self, value: int) -> None:
        self.calls.append(("set_volume", value))
        self.status = replace(self.status, volume=max(0, min(100, value)))

    def adjust_volume(self, delta: int) -> None:
        self.set_volume(self.status.volume + delta)

    def toggle_mute(self) -> None:
        self.calls.append(("toggle_mute",))
        self.status = replace(self.status, muted=not self.status.muted)

    def retry_through_relay(self) -> bool:
        # Counted apart from `calls`: asking is not a transport action, and
        # tests that list the actions should not have to mention it.
        self.relay_asked += 1
        if self.relay_retries <= 0 or self.status.state is not PlayerState.ERROR:
            return False
        self.relay_retries -= 1
        self.status = replace(self.status, state=PlayerState.PLAYING, error="")
        return True

    def record(self, path: str | None) -> None:
        self.calls.append(("record", path))
        self.recording = path

    def close(self) -> None:
        self.recording = None
        self.closed = True

    def level(self) -> float | None:
        return self._level


class FakeMediaSession:
    """Stands in for the system's media controls, recording what it is shown.

    `press` plays the part of a media key: the real session delivers commands
    from inside `pump`, on the app's tick, and so does this one.
    """

    def __init__(self, commands) -> None:
        self.commands = commands
        self.shown: list = []
        self.pending: list[str] = []
        self.closed = False

    def press(self, name: str, *args) -> None:
        self.pending.append((name, args))

    def update(self, info, volume: int = 100) -> None:
        self.volume = volume
        if not self.shown or self.shown[-1] != info:
            self.shown.append(info)

    def pump(self) -> None:
        while self.pending:
            name, args = self.pending.pop(0)
            getattr(self.commands, name)(*args)

    def close(self) -> None:
        self.closed = True
