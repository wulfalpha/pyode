# SPDX-License-Identifier: GPL-2.0-or-later
"""The operating system's media controls: Now Playing panels and media keys.

On Linux that is MPRIS, in `pyode.mpris`; on Windows, the System Media
Transport Controls, in `pyode.smtc`.  The rest of this module is macOS.

libmpv has its own macOS integration (the ``media-controls`` option), but it
only registers when mpv itself is the application; embedded through libmpv it
stays silent.  So pyode talks to Apple's MediaPlayer framework directly, via
PyObjC: `MPNowPlayingInfoCenter` for what Control Center shows, and
`MPRemoteCommandCenter` for the play/pause, next and previous keys.

Run loop
--------
MediaPlayer delivers remote commands on the main dispatch queue, which only
drains while the main thread's run loop runs.  Textual owns the main thread
with asyncio, so nothing would ever run it.  Instead the app calls `pump` from
its tick: one non-blocking pass of the run loop, which delivers any pending
commands on the main thread -- where the app's actions may be called safely.

Everywhere else, or when PyObjC is missing, `create` returns a session that
does nothing, so callers never need to know which platform they are on.
"""

from __future__ import annotations

import logging
import sys
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from pyode.player import PlayerState, PlayerStatus

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class NowPlayingInfo:
    """What the system panel should show, independent of any platform API."""

    title: str
    artist: str = ""  # the station, when a track title fills `title`
    genre: str = ""
    paused: bool = False


def describe(status: PlayerStatus, station_name: str | None) -> NowPlayingInfo | None:
    """What to show for this status, or None when nothing is on air.

    The track title is the headline when the station sends one, with the
    station beneath it, the way a music player shows song over artist.  Without
    a title the station name moves up to the headline.
    """
    if status.state not in (PlayerState.PLAYING, PlayerState.BUFFERING, PlayerState.PAUSED):
        return None
    now = status.now_playing
    station = station_name or now.stream_name or now.station
    if now.title:
        title, artist = now.title, station
    else:
        title, artist = station or now.display, ""
    return NowPlayingInfo(
        title=title,
        artist=artist,
        genre=now.genre,
        paused=status.state is PlayerState.PAUSED,
    )


@dataclass(frozen=True, slots=True)
class Commands:
    """What the media keys do.  Each runs on the main thread, inside `pump`."""

    play: Callable[[], None]
    pause: Callable[[], None]
    toggle: Callable[[], None]
    stop: Callable[[], None]
    next: Callable[[], None]
    previous: Callable[[], None]
    set_volume: Callable[[int], None]  # 0-100; macOS has no use for it


class MediaSession(Protocol):
    def update(self, info: NowPlayingInfo | None, volume: int = 100) -> None: ...
    def pump(self) -> None: ...
    def close(self) -> None: ...


class NullSession:
    """Media controls on a platform pyode has no integration for."""

    def update(self, info: NowPlayingInfo | None, volume: int = 100) -> None:
        pass

    def pump(self) -> None:
        pass

    def close(self) -> None:
        pass


class MacSession:
    """Now Playing and media keys through Apple's MediaPlayer framework."""

    def __init__(self, commands: Commands) -> None:
        import MediaPlayer as mp  # noqa: N813 -- the framework's own name
        from Foundation import (
            NSDate,
            NSDefaultRunLoopMode,
            NSMutableDictionary,
            NSNumber,
            NSRunLoop,
            NSString,
        )

        self._mp = mp
        self._dictionary = NSMutableDictionary.dictionary
        self._number = NSNumber
        self._string = NSString.stringWithString_
        self._run_loop = NSRunLoop.mainRunLoop()
        self._mode = NSDefaultRunLoopMode
        self._now = NSDate.date
        self._center = mp.MPNowPlayingInfoCenter.defaultCenter()
        self._remote = mp.MPRemoteCommandCenter.sharedCommandCenter()
        self._last: NowPlayingInfo | None = None
        self._targets: list[tuple[object, object]] = []

        remote = self._remote
        self._bind(remote.playCommand(), commands.play)
        self._bind(remote.pauseCommand(), commands.pause)
        self._bind(remote.togglePlayPauseCommand(), commands.toggle)
        self._bind(remote.stopCommand(), commands.stop)
        self._bind(remote.nextTrackCommand(), commands.next)
        self._bind(remote.previousTrackCommand(), commands.previous)

    def _bind(self, command, action: Callable[[], None]) -> None:
        success = self._mp.MPRemoteCommandHandlerStatusSuccess
        failed = self._mp.MPRemoteCommandHandlerStatusCommandFailed

        def handler(_event):
            # An exception escaping into Objective-C would take the app down.
            try:
                action()
            except Exception:
                log.exception("media key handler failed")
                return failed
            return success

        command.setEnabled_(True)
        self._targets.append((command, command.addTargetWithHandler_(handler)))

    def update(self, info: NowPlayingInfo | None, volume: int = 100) -> None:
        if info == self._last:
            return
        self._last = info
        mp = self._mp
        if info is None:
            self._center.setNowPlayingInfo_(None)
            self._center.setPlaybackState_(mp.MPNowPlayingPlaybackStateStopped)
            return
        # Native Foundation objects only.  A Python dict, str or float would
        # cross as a PyObjC proxy, and MediaRemote reads these values on its
        # own queue -- which, if it happens while the interpreter is shutting
        # down, needs the GIL and crashes the process on the way out.
        number, string = self._number, self._string
        details = self._dictionary()
        details[mp.MPMediaItemPropertyTitle] = string(info.title)
        details[mp.MPNowPlayingInfoPropertyIsLiveStream] = number.numberWithBool_(True)
        details[mp.MPNowPlayingInfoPropertyMediaType] = number.numberWithUnsignedInteger_(
            mp.MPNowPlayingInfoMediaTypeAudio
        )
        details[mp.MPNowPlayingInfoPropertyPlaybackRate] = number.numberWithDouble_(
            0.0 if info.paused else 1.0
        )
        if info.artist:
            details[mp.MPMediaItemPropertyArtist] = string(info.artist)
        if info.genre:
            details[mp.MPMediaItemPropertyGenre] = string(info.genre)
        self._center.setNowPlayingInfo_(details)
        paused, playing = mp.MPNowPlayingPlaybackStatePaused, mp.MPNowPlayingPlaybackStatePlaying
        self._center.setPlaybackState_(paused if info.paused else playing)

    def pump(self) -> None:
        # A date already passed makes this a single pass that never blocks.
        self._run_loop.runMode_beforeDate_(self._mode, self._now())

    def close(self) -> None:
        for command, target in self._targets:
            command.removeTarget_(target)
            command.setEnabled_(False)
        self._targets.clear()
        self._center.setNowPlayingInfo_(None)
        self._center.setPlaybackState_(self._mp.MPNowPlayingPlaybackStateStopped)


def create(commands: Commands, *, enabled: bool = True) -> MediaSession:
    """The best media session this platform supports."""
    if not enabled:
        return NullSession()
    try:
        if sys.platform == "darwin":
            return MacSession(commands)
        if sys.platform.startswith("linux"):
            from pyode.mpris import MprisSession

            return MprisSession(commands)
        if sys.platform == "win32":
            from pyode.smtc import SmtcSession

            return SmtcSession(commands)
    except ImportError as exc:
        log.info("no media controls, a binding is missing: %s", exc)
    except Exception:
        log.exception("could not register with the system's media controls")
    return NullSession()
