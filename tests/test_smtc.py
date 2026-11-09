# SPDX-License-Identifier: GPL-2.0-or-later
"""The Windows media controls, against stand-ins for the winrt modules -- so
the logic is tested everywhere -- and, on Windows, against the real thing."""

import asyncio
import enum
import importlib
import sys
import threading
import types
from dataclasses import replace

import pytest

from pyode.mediacontrols import Commands, NowPlayingInfo

# -- stand-ins for winrt ----------------------------------------------------------


class MediaPlaybackStatus(enum.IntEnum):
    CLOSED = 0
    CHANGING = 1
    STOPPED = 2
    PLAYING = 3
    PAUSED = 4


class MediaPlaybackType(enum.IntEnum):
    UNKNOWN = 0
    MUSIC = 1


class Button(enum.IntEnum):
    PLAY = 0
    PAUSE = 1
    STOP = 2
    RECORD = 3
    FAST_FORWARD = 4
    REWIND = 5
    NEXT = 6
    PREVIOUS = 7


class Music:
    def __init__(self) -> None:
        self.title = ""
        self.artist = ""
        self.genres: list[str] = []


class Updater:
    def __init__(self) -> None:
        self.type = MediaPlaybackType.UNKNOWN
        self.music_properties = Music()
        self.published: list[tuple] = []

    def clear_all(self) -> None:
        self.type = MediaPlaybackType.UNKNOWN
        self.music_properties = Music()

    def update(self) -> None:
        music = self.music_properties
        self.published.append((music.title, music.artist, list(music.genres)))


class Controls:
    def __init__(self) -> None:
        self.display_updater = Updater()
        self.playback_status = MediaPlaybackStatus.CLOSED
        self.handlers: dict[int, object] = {}
        for name in ("enabled", "play", "pause", "stop", "next", "previous"):
            setattr(self, f"is_{name}_enabled" if name != "enabled" else "is_enabled", False)

    def add_button_pressed(self, handler):
        token = len(self.handlers) + 1
        self.handlers[token] = handler
        return token

    def remove_button_pressed(self, token) -> None:
        del self.handlers[token]

    def press(self, button: Button) -> None:
        """What Windows does, from a thread of its own."""
        args = types.SimpleNamespace(button=button)
        for handler in list(self.handlers.values()):
            handler(self, args)


class FakeMediaPlayer:
    def __init__(self) -> None:
        self.command_manager = types.SimpleNamespace(is_enabled=True)
        self.system_media_transport_controls = Controls()
        self.closed = False

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def smtc(monkeypatch):
    """pyode.smtc, imported against the stand-ins."""
    media = types.ModuleType("winrt.windows.media")
    media.MediaPlaybackStatus = MediaPlaybackStatus
    media.MediaPlaybackType = MediaPlaybackType
    media.SystemMediaTransportControlsButton = Button
    playback = types.ModuleType("winrt.windows.media.playback")
    playback.MediaPlayer = FakeMediaPlayer
    for name, module in {
        "winrt": types.ModuleType("winrt"),
        "winrt.windows": types.ModuleType("winrt.windows"),
        "winrt.windows.media": media,
        "winrt.windows.media.playback": playback,
    }.items():
        monkeypatch.setitem(sys.modules, name, module)
    monkeypatch.delitem(sys.modules, "pyode.smtc", raising=False)
    module = importlib.import_module("pyode.smtc")
    yield module
    sys.modules.pop("pyode.smtc", None)


class Recorder:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.threads: set[int] = set()

    def _record(self, name: str) -> None:
        self.calls.append(name)
        self.threads.add(threading.get_ident())

    def commands(self) -> Commands:
        names = ("play", "pause", "toggle", "stop", "next", "previous")
        return Commands(
            *(lambda n=n: self._record(n) for n in names),
            set_volume=lambda v: self._record(f"volume {v}"),
        )


async def test_registers_with_every_button_it_answers(smtc):
    session = smtc.SmtcSession(Recorder().commands())
    player = session._player
    controls = player.system_media_transport_controls
    assert player.command_manager.is_enabled is False  # pyode answers, not the player
    assert controls.is_enabled and controls.is_play_enabled and controls.is_next_enabled
    assert controls.playback_status is MediaPlaybackStatus.CLOSED


async def test_shows_what_is_playing(smtc):
    session = smtc.SmtcSession(Recorder().commands())
    controls = session._player.system_media_transport_controls
    session.update(NowPlayingInfo("So What", "Jazz Radio", "jazz"))
    assert controls.display_updater.published[-1] == ("So What", "Jazz Radio", ["jazz"])
    assert controls.display_updater.type is MediaPlaybackType.MUSIC
    assert controls.playback_status is MediaPlaybackStatus.PLAYING
    session.update(NowPlayingInfo("So What", "Jazz Radio", "jazz", paused=True))
    assert controls.playback_status is MediaPlaybackStatus.PAUSED
    session.update(NowPlayingInfo("Jazz Radio"))  # no genre now: the old one goes
    assert controls.display_updater.published[-1] == ("Jazz Radio", "", [])
    session.update(None)
    assert controls.playback_status is MediaPlaybackStatus.STOPPED
    assert controls.display_updater.published[-1] == ("", "", [])


async def test_unchanged_info_is_not_republished(smtc):
    session = smtc.SmtcSession(Recorder().commands())
    updater = session._player.system_media_transport_controls.display_updater
    session.update(NowPlayingInfo("A"))
    session.update(NowPlayingInfo("A"), volume=50)
    assert len(updater.published) == 1


async def test_buttons_reach_the_app_on_the_loop(smtc):
    recorder = Recorder()
    session = smtc.SmtcSession(recorder.commands())
    controls = session._player.system_media_transport_controls
    loop = asyncio.get_running_loop()
    # Windows calls from its thread pool; nothing may run there.
    for button in (Button.PLAY, Button.PAUSE, Button.STOP, Button.NEXT, Button.PREVIOUS):
        await loop.run_in_executor(None, controls.press, button)
    controls.press(Button.RECORD)  # not one pyode answers
    await asyncio.sleep(0)
    assert recorder.calls == ["play", "pause", "stop", "next", "previous"]
    assert recorder.threads == {threading.get_ident()}  # all on the loop's thread


async def test_a_failing_action_does_not_escape(smtc):
    def boom():
        raise RuntimeError("boom")

    session = smtc.SmtcSession(replace(Recorder().commands(), play=boom))
    session._player.system_media_transport_controls.press(Button.PLAY)
    await asyncio.sleep(0)  # logged, not raised


async def test_close_lets_go(smtc):
    session = smtc.SmtcSession(Recorder().commands())
    player = session._player
    controls = player.system_media_transport_controls
    session.close()
    assert controls.handlers == {}
    assert controls.playback_status is MediaPlaybackStatus.CLOSED
    assert not controls.is_enabled
    assert player.closed


# -- the real thing ----------------------------------------------------------------


@pytest.mark.skipif(sys.platform != "win32", reason="Windows only")
async def test_real_windows_media_controls_register():
    session = importlib.import_module("pyode.smtc").SmtcSession(Recorder().commands())
    try:
        session.update(NowPlayingInfo("So What", "Jazz Radio", "jazz"))
        session.update(NowPlayingInfo("So What", "Jazz Radio", "jazz", paused=True))
        session.update(None)
    finally:
        session.close()
