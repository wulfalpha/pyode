# SPDX-License-Identifier: GPL-2.0-or-later
"""MPRIS: media keys and now-playing panels on Linux desktops.

GNOME, KDE Plasma, waybar, playerctl and the rest all find media players the
same way: on the D-Bus session bus, under a name beginning
``org.mpris.MediaPlayer2.``, implementing the interfaces in the MPRIS spec
(https://specifications.freedesktop.org/mpris-spec/latest/).  This module is
that player, built on dbus-fast's asyncio bus so it shares Textual's event
loop: D-Bus method calls arrive as ordinary callbacks on the main thread, and
nothing needs pumping.

No ``from __future__ import annotations`` here: dbus-fast reads D-Bus type
signatures from annotations such as ``-> "s"``, and needs them as written.
"""

import asyncio
import logging
import os

from dbus_fast import BusType, NameFlag, RequestNameReply, Variant
from dbus_fast.aio import MessageBus
from dbus_fast.service import PropertyAccess, ServiceInterface, dbus_property, method

from pyode.mediacontrols import Commands, NowPlayingInfo

log = logging.getLogger(__name__)

OBJECT_PATH = "/org/mpris/MediaPlayer2"
BUS_NAME = "org.mpris.MediaPlayer2.pyode"
NO_TRACK = "/org/mpris/MediaPlayer2/TrackList/NoTrack"


def _status(info: NowPlayingInfo | None) -> str:
    if info is None:
        return "Stopped"
    return "Paused" if info.paused else "Playing"


class _Root(ServiceInterface):
    """org.mpris.MediaPlayer2: who the player is.  pyode lives in a terminal,
    so there is no window to raise, and quitting is left to the terminal."""

    def __init__(self) -> None:
        super().__init__("org.mpris.MediaPlayer2")

    @method()
    def Raise(self):  # noqa: N802 -- D-Bus member names are CamelCase
        pass

    @method()
    def Quit(self):  # noqa: N802
        pass

    @dbus_property(access=PropertyAccess.READ)
    def CanQuit(self) -> "b":  # noqa: N802, F821
        return False

    @dbus_property(access=PropertyAccess.READ)
    def CanRaise(self) -> "b":  # noqa: N802, F821
        return False

    @dbus_property(access=PropertyAccess.READ)
    def HasTrackList(self) -> "b":  # noqa: N802, F821
        return False

    @dbus_property(access=PropertyAccess.READ)
    def Identity(self) -> "s":  # noqa: N802, F821
        return "pyode"

    @dbus_property(access=PropertyAccess.READ)
    def SupportedUriSchemes(self) -> "as":  # noqa: N802, F722
        return []

    @dbus_property(access=PropertyAccess.READ)
    def SupportedMimeTypes(self) -> "as":  # noqa: N802, F722
        return []


class _Player(ServiceInterface):
    """org.mpris.MediaPlayer2.Player: what is on air, and the transport."""

    def __init__(self, commands: Commands) -> None:
        super().__init__("org.mpris.MediaPlayer2.Player")
        self._commands = commands
        self._info: NowPlayingInfo | None = None
        self._volume = 1.0
        self._track = 0  # bumped per title, so clients see a new track

    # -- state from the app ---------------------------------------------------

    def show(self, info: NowPlayingInfo | None, volume: int) -> None:
        changed: dict[str, object] = {}
        if info != self._info:
            if info is None or self._info is None or info.title != self._info.title:
                self._track += 1
            if _status(info) != _status(self._info):
                changed["PlaybackStatus"] = _status(info)
            self._info = info
            changed["Metadata"] = self._metadata()
        level = max(0, min(100, volume)) / 100
        if level != self._volume:
            self._volume = level
            changed["Volume"] = level
        if changed:
            self.emit_properties_changed(changed)

    def _metadata(self) -> dict[str, Variant]:
        info = self._info
        if info is None:
            return {"mpris:trackid": Variant("o", NO_TRACK)}
        metadata = {
            "mpris:trackid": Variant("o", f"/org/pyode/track/{self._track}"),
            "xesam:title": Variant("s", info.title),
        }
        if info.artist:
            metadata["xesam:artist"] = Variant("as", [info.artist])
        if info.genre:
            metadata["xesam:genre"] = Variant("as", [info.genre])
        return metadata

    # -- methods ----------------------------------------------------------------

    @method()
    def Play(self):  # noqa: N802
        self._commands.play()

    @method()
    def Pause(self):  # noqa: N802
        self._commands.pause()

    @method()
    def PlayPause(self):  # noqa: N802
        self._commands.toggle()

    @method()
    def Stop(self):  # noqa: N802
        self._commands.stop()

    @method()
    def Next(self):  # noqa: N802
        self._commands.next()

    @method()
    def Previous(self):  # noqa: N802
        self._commands.previous()

    # Live radio cannot seek, and pyode plays its own library, not URIs it is
    # handed.  The spec says to ignore these rather than fail.
    @method()
    def Seek(self, offset: "x"):  # noqa: N802, F821
        pass

    @method()
    def SetPosition(self, track_id: "o", position: "x"):  # noqa: N802, F821
        pass

    @method()
    def OpenUri(self, uri: "s"):  # noqa: N802, F821
        pass

    # -- properties ---------------------------------------------------------------

    @dbus_property(access=PropertyAccess.READ)
    def PlaybackStatus(self) -> "s":  # noqa: N802, F821
        return _status(self._info)

    @dbus_property(access=PropertyAccess.READ)
    def Metadata(self) -> "a{sv}":  # noqa: N802, F722
        return self._metadata()

    @dbus_property()
    def Volume(self) -> "d":  # noqa: N802, F821
        return self._volume

    @Volume.setter
    def Volume(self, value: "d"):  # noqa: N802, F821
        self._commands.set_volume(round(max(0.0, min(1.0, value)) * 100))

    @dbus_property(access=PropertyAccess.READ)
    def Position(self) -> "x":  # noqa: N802, F821
        return 0

    @dbus_property(access=PropertyAccess.READ)
    def Rate(self) -> "d":  # noqa: N802, F821
        return 1.0

    @dbus_property(access=PropertyAccess.READ)
    def MinimumRate(self) -> "d":  # noqa: N802, F821
        return 1.0

    @dbus_property(access=PropertyAccess.READ)
    def MaximumRate(self) -> "d":  # noqa: N802, F821
        return 1.0

    @dbus_property(access=PropertyAccess.READ)
    def CanGoNext(self) -> "b":  # noqa: N802, F821
        return True

    @dbus_property(access=PropertyAccess.READ)
    def CanGoPrevious(self) -> "b":  # noqa: N802, F821
        return True

    @dbus_property(access=PropertyAccess.READ)
    def CanPlay(self) -> "b":  # noqa: N802, F821
        return True

    @dbus_property(access=PropertyAccess.READ)
    def CanPause(self) -> "b":  # noqa: N802, F821
        return True

    @dbus_property(access=PropertyAccess.READ)
    def CanSeek(self) -> "b":  # noqa: N802, F821
        return False

    @dbus_property(access=PropertyAccess.READ)
    def CanControl(self) -> "b":  # noqa: N802, F821
        return True


class MprisSession:
    """pyode on the session bus.  Connecting happens in the background: until
    it finishes, or if there is no session bus at all (a bare SSH login, say),
    updates are simply remembered and nothing is published."""

    def __init__(self, commands: Commands, *, bus_address: str | None = None) -> None:
        self._player = _Player(commands)
        self._root = _Root()
        self._bus: MessageBus | None = None
        self._address = bus_address
        self.name: str | None = None  # the bus name, once it is held
        self._ready = asyncio.get_running_loop().create_task(self._connect())

    async def _connect(self) -> None:
        try:
            if self._address:
                bus = await MessageBus(bus_address=self._address).connect()
            else:
                bus = await MessageBus(bus_type=BusType.SESSION).connect()
        except Exception as exc:  # no bus, no permission, a malformed address...
            log.info("no D-Bus session bus, so no MPRIS: %s", exc)
            return
        bus.export(OBJECT_PATH, self._root)
        bus.export(OBJECT_PATH, self._player)
        # A second pyode cannot have the plain name, so it takes the spec's
        # per-instance form instead and both stay controllable.
        for name in (BUS_NAME, f"{BUS_NAME}.instance{os.getpid()}"):
            reply = await bus.request_name(name, NameFlag.DO_NOT_QUEUE)
            if reply in (RequestNameReply.PRIMARY_OWNER, RequestNameReply.ALREADY_OWNER):
                self.name = name
                break
        self._bus = bus

    async def ready(self) -> None:
        """Wait for the connection attempt to finish, for tests."""
        await self._ready

    def update(self, info: NowPlayingInfo | None, volume: int = 100) -> None:
        self._player.show(info, volume)

    def pump(self) -> None:
        pass

    def close(self) -> None:
        self._ready.cancel()
        if self._bus is not None:
            self._bus.disconnect()
            self._bus = None
