# SPDX-License-Identifier: GPL-2.0-or-later
"""Audio playback for pyode, backed by libmpv.

There is deliberately only one backend.  libmpv is the only widely available
player that offers live volume control, ICY stream metadata and AAC decoding
through a single programmatic API; ffplay has no control channel and mpg123
cannot decode AAC, which rules both out for internet radio.  Everything the
rest of the app touches goes through the `Player` protocol, so adding a second
backend later (subprocess mpv over ``--input-ipc-server``, for platforms that
ship the binary but not the shared library) stays additive rather than a
refactor.

Threading
---------
libmpv delivers property changes on its own event thread, so ``on_change``
does **not** run on the caller's thread.  Textual callers must marshal it with
``App.call_from_thread``.

One invariant keeps that safe: **the mpv handle is only ever touched from the
caller's thread.**  The event thread does nothing but update cached Python
state.  Reading an mpv property from inside an observer callback segfaults the
interpreter at teardown, so `status` is assembled purely from that cache and
performs no FFI at all.
"""

from __future__ import annotations

import logging
import math
import threading
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import Protocol

from pyode import libmpv

# Re-exported: callers catch this around MpvPlayer(), and should not need to
# know that finding the library is a separate concern from playing audio.
from pyode.libmpv import LibmpvNotFoundError as LibmpvNotFoundError

log = logging.getLogger(__name__)

# libmpv is loaded on first use rather than at import, so `pyode --list` and
# the tests do not pay for the search -- and so that a machine without libmpv
# can still run everything that does not make a sound.
_mpv = None


def _backend():
    """The `mpv` module, imported on first use.

    Raises `LibmpvNotFoundError` when libmpv cannot be found; see
    `pyode.libmpv` for where it looks and why looking is necessary.
    """
    global _mpv
    if _mpv is None:
        _mpv = libmpv.load()
    return _mpv


# python-mpv raises plain stdlib exceptions rather than a single base class.
# ShutdownError subclasses SystemError and PropertyUnavailableError subclasses
# AttributeError, so this tuple covers those too.
_MPV_ERRORS = (AttributeError, RuntimeError, ValueError, TypeError, SystemError, MemoryError)

# The metering filter is inserted ahead of mpv's volume stage, so levels track
# the decoded stream rather than what reaches the speakers.  That is the point:
# the meter shows the stream is alive even when the player is muted.
METER_LABEL = "pyodemeter"
METER_FLOOR_DB = -55.0


def _normalise_db(raw: object) -> float | None:
    """Map an astats RMS reading in dBFS onto 0.0-1.0."""
    try:
        decibels = float(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if math.isnan(decibels):
        return None
    if decibels >= 0.0:
        return 1.0
    if decibels <= METER_FLOOR_DB:
        return 0.0  # also catches -inf, which astats reports for digital silence
    return (decibels - METER_FLOOR_DB) / -METER_FLOOR_DB


class PlayerState(StrEnum):
    IDLE = "idle"
    BUFFERING = "buffering"
    PLAYING = "playing"
    PAUSED = "paused"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class NowPlaying:
    """What is currently on air.

    Not every station sends per-track titles -- many send only station-level
    ICY headers, and some send nothing at all -- so `display` falls back
    through progressively less specific names rather than going blank.
    """

    station: str = ""  # name from our own library; the last resort
    title: str = ""  # icy-title, per track, frequently absent
    stream_name: str = ""  # icy-name, station-level
    genre: str = ""  # icy-genre
    bitrate: int | None = None  # kbps
    codec: str = ""

    @property
    def display(self) -> str:
        return self.title or self.stream_name or self.station or "Nothing playing"


@dataclass(frozen=True, slots=True)
class PlayerStatus:
    state: PlayerState = PlayerState.IDLE
    now_playing: NowPlaying = field(default_factory=NowPlaying)
    volume: int = 100
    muted: bool = False
    cache_seconds: float = 0.0
    error: str = ""

    @property
    def is_active(self) -> bool:
        return self.state in (PlayerState.PLAYING, PlayerState.PAUSED, PlayerState.BUFFERING)


class Player(Protocol):
    """The surface the UI is allowed to depend on."""

    def play(self, url: str, station_name: str = "") -> None: ...
    def stop(self) -> None: ...
    def toggle_pause(self) -> None: ...
    def set_volume(self, value: int) -> None: ...
    def adjust_volume(self, delta: int) -> None: ...
    def toggle_mute(self) -> None: ...
    def close(self) -> None: ...

    @property
    def status(self) -> PlayerStatus: ...


def _parse_metadata(raw: dict | None, station: str) -> NowPlaying:
    meta = {str(k).lower(): str(v) for k, v in (raw or {}).items()}
    bitrate = meta.get("icy-br", "").strip()
    return NowPlaying(
        station=station,
        title=meta.get("icy-title", "").strip(),
        stream_name=meta.get("icy-name", "").strip(),
        genre=meta.get("icy-genre", "").strip(),
        bitrate=int(bitrate) if bitrate.isdigit() else None,
    )


class MpvPlayer:
    """A libmpv-backed `Player`."""

    def __init__(
        self,
        on_change: Callable[[PlayerStatus], None] | None = None,
        volume: int = 100,
        network_timeout: int = 10,
        meter: bool = True,
        **mpv_options,
    ) -> None:
        mpv = _backend()

        self._on_change = on_change
        self._lock = threading.RLock()
        self._url: str | None = None
        self._station = ""
        self._paused = False
        self._core_idle = True
        self._error = ""
        self._now = NowPlaying()
        self._volume = _clamp(volume)
        self._codec = ""
        self._bitrate_bps: float | None = None
        self._muted = False
        self._cache_seconds = 0.0
        self._closing = False
        self._meter = meter

        self._mpv = mpv.MPV(
            video=False,
            ytdl=False,
            terminal=False,  # keep mpv's own output off our TUI
            idle=True,  # stay alive between stations
            volume=self._volume,
            network_timeout=network_timeout,
            # Internet radio drops connections routinely; let ffmpeg reconnect
            # rather than treating a blip as end-of-stream.
            stream_lavf_o="reconnect=1,reconnect_streamed=1,reconnect_delay_max=5",
            **mpv_options,
        )
        self._register_observers()
        if self._meter:
            self._enable_meter()

    def _enable_meter(self) -> None:
        """Insert the level-metering filter into the audio chain."""
        try:
            self._mpv.command("af", "set", f"@{METER_LABEL}:lavfi=[astats=metadata=1:reset=1]")
        except _MPV_ERRORS:
            # No metering is a cosmetic loss; refusing to play is not.
            log.debug("could not install the level meter", exc_info=True)
            self._meter = False

    def level(self) -> float | None:
        """Current signal level as 0.0-1.0, or None when unavailable.

        Must be called from the caller's thread, never from an observer.
        """
        if self._meter is False or self._closing:
            return None
        try:
            # python-mpv maps both attribute and item access onto options
            # rather than properties, and neither can express a slash-
            # delimited property path, so this reaches for the raw getter.
            meta = self._mpv._get_property(f"af-metadata/{METER_LABEL}")
        except _MPV_ERRORS:
            return None
        if not isinstance(meta, dict):
            return None
        return _normalise_db(meta.get("lavfi.astats.Overall.RMS_level"))

    # -- mpv event thread -------------------------------------------------

    def _register_observers(self) -> None:
        """Observers run on mpv's thread and may only touch cached state."""

        @self._mpv.property_observer("metadata")
        def _on_metadata(_name: str, value: dict | None) -> None:
            with self._lock:
                self._now = _parse_metadata(value, self._station)
            self._emit()

        @self._mpv.property_observer("core-idle")
        def _on_core_idle(_name: str, value: bool | None) -> None:
            with self._lock:
                self._core_idle = bool(value)
            self._emit()

        @self._mpv.property_observer("pause")
        def _on_pause(_name: str, value: bool | None) -> None:
            with self._lock:
                self._paused = bool(value)
            self._emit()

        @self._mpv.property_observer("mute")
        def _on_mute(_name: str, value: bool | None) -> None:
            with self._lock:
                self._muted = bool(value)
            self._emit()

        @self._mpv.property_observer("audio-codec-name")
        def _on_codec(_name: str, value: str | None) -> None:
            with self._lock:
                self._codec = value or ""
            self._emit()

        # These two update several times a second.  They are cached for
        # `status` but deliberately do not notify, or the UI would redraw
        # continuously for a buffer gauge that barely moves.
        @self._mpv.property_observer("audio-bitrate")
        def _on_bitrate(_name: str, value: float | None) -> None:
            with self._lock:
                self._bitrate_bps = value

        @self._mpv.property_observer("demuxer-cache-duration")
        def _on_cache(_name: str, value: float | None) -> None:
            with self._lock:
                self._cache_seconds = float(value or 0.0)

        @self._mpv.event_callback("end-file")
        def _on_end_file(event) -> None:
            data = event.data
            if data is None or data.reason != _backend().MpvEventEndFile.ERROR:
                return  # our own stop() also ends the file; only errors matter
            with self._lock:
                self._error = _backend().ErrorCode.human_readable(data.error)
                self._url = None
            self._emit()

    def _emit(self) -> None:
        # Tearing down unloads the file, which fires observers one last time.
        # Listeners should not see the player flicker back to BUFFERING on the
        # way out.
        if self._on_change is None or self._closing:
            return
        try:
            self._on_change(self.status)
        except Exception:
            # Raising here would kill mpv's event thread and freeze every
            # subsequent property update, so a broken listener is logged
            # rather than propagated.
            log.exception("player status listener failed")

    # -- state ------------------------------------------------------------

    def _state(self) -> PlayerState:
        """Caller must hold the lock."""
        if self._error:
            return PlayerState.ERROR
        if self._url is None:
            return PlayerState.IDLE
        if self._paused:
            return PlayerState.PAUSED
        if self._core_idle:
            return PlayerState.BUFFERING
        return PlayerState.PLAYING

    @property
    def status(self) -> PlayerStatus:
        """A snapshot of cached state.  Safe to call from any thread."""
        with self._lock:
            now = self._now
            bitrate = now.bitrate  # icy-br wins; it is what the station claims
            if bitrate is None and self._bitrate_bps:
                bitrate = round(self._bitrate_bps / 1000)
            return PlayerStatus(
                state=self._state(),
                now_playing=replace(now, station=self._station, codec=self._codec, bitrate=bitrate),
                volume=self._volume,
                muted=self._muted,
                cache_seconds=self._cache_seconds,
                error=self._error,
            )

    # -- controls ---------------------------------------------------------

    def play(self, url: str, station_name: str = "") -> None:
        with self._lock:
            self._url = url
            self._station = station_name
            self._error = ""
            self._paused = False
            self._core_idle = True
            self._now = NowPlaying(station=station_name)
            self._codec = ""
            self._bitrate_bps = None
            self._cache_seconds = 0.0
        self._mpv.play(url)
        self._mpv.pause = False  # a previous pause would otherwise carry over
        self._emit()

    def stop(self) -> None:
        with self._lock:
            self._url = None
            self._station = ""
            self._now = NowPlaying()
            self._error = ""
            self._codec = ""
            self._bitrate_bps = None
            self._cache_seconds = 0.0
        try:
            self._mpv.stop()
        except _MPV_ERRORS:
            log.debug("stop failed", exc_info=True)
        self._emit()

    def toggle_pause(self) -> None:
        with self._lock:
            if self._url is None:
                return
            paused = self._paused
        self._mpv.pause = not paused
        # the `pause` observer emits for us

    def set_volume(self, value: int) -> None:
        value = _clamp(value)
        with self._lock:
            self._volume = value
        self._mpv.volume = value
        self._emit()

    def adjust_volume(self, delta: int) -> None:
        with self._lock:
            current = self._volume
        self.set_volume(current + delta)

    def toggle_mute(self) -> None:
        with self._lock:
            muted = self._muted
        self._mpv.mute = not muted
        # the `mute` observer emits for us

    def close(self) -> None:
        with self._lock:
            self._closing = True
        try:
            self._mpv.terminate()
        except _MPV_ERRORS:
            log.debug("terminate failed", exc_info=True)

    def __enter__(self) -> MpvPlayer:
        return self

    def __exit__(self, *_exc) -> None:
        self.close()


def _clamp(value: int) -> int:
    return max(0, min(100, int(value)))
