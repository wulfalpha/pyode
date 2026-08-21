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

    # -- Player -----------------------------------------------------------

    def play(self, url: str, station_name: str = "") -> None:
        self.calls.append(("play", url, station_name))
        self.status = replace(
            self.status,
            state=PlayerState.PLAYING,
            now_playing=NowPlaying(station=station_name),
            error="",
        )

    def stop(self) -> None:
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

    def close(self) -> None:
        self.closed = True

    def level(self) -> float | None:
        return self._level
