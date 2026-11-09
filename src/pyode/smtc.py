# SPDX-License-Identifier: GPL-2.0-or-later
"""Windows' System Media Transport Controls: the media flyout and media keys.

SMTC normally belongs to a window, and pyode has none of its own -- the
terminal's window is someone else's.  A WinRT `MediaPlayer` carries its own
SMTC, though, with no window needed.  pyode never gives it anything to play:
it switches off the player's automatic handling and drives the controls by
hand, the same way it drives Now Playing on macOS.

Button presses arrive on a Windows thread-pool thread.  Each one is passed to
the asyncio loop the session was created on, so the app's actions run on the
main thread as everywhere else.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable

from winrt.windows.media import (
    MediaPlaybackStatus,
    MediaPlaybackType,
    SystemMediaTransportControlsButton,
)
from winrt.windows.media.playback import MediaPlayer

from pyode.mediacontrols import Commands, NowPlayingInfo

log = logging.getLogger(__name__)


class SmtcSession:
    def __init__(self, commands: Commands) -> None:
        self._loop = asyncio.get_running_loop()
        buttons = SystemMediaTransportControlsButton
        self._actions: dict[int, Callable[[], None]] = {
            buttons.PLAY: commands.play,
            buttons.PAUSE: commands.pause,
            buttons.STOP: commands.stop,
            buttons.NEXT: commands.next,
            buttons.PREVIOUS: commands.previous,
        }
        self._player = MediaPlayer()
        # Otherwise the player answers the buttons itself, for media it has
        # never been given.
        self._player.command_manager.is_enabled = False
        controls = self._player.system_media_transport_controls
        controls.is_enabled = True
        controls.is_play_enabled = True
        controls.is_pause_enabled = True
        controls.is_stop_enabled = True
        controls.is_next_enabled = True
        controls.is_previous_enabled = True
        controls.playback_status = MediaPlaybackStatus.CLOSED
        self._controls = controls
        self._token = controls.add_button_pressed(self._pressed)
        self._last: NowPlayingInfo | None = None

    def _pressed(self, _sender, args) -> None:
        # On a thread-pool thread: touch nothing here but the loop.
        action = self._actions.get(int(args.button))
        if action is not None:
            self._loop.call_soon_threadsafe(self._run, action)

    @staticmethod
    def _run(action: Callable[[], None]) -> None:
        try:
            action()
        except Exception:
            log.exception("media key handler failed")

    def update(self, info: NowPlayingInfo | None, volume: int = 100) -> None:
        if info == self._last:
            return
        self._last = info
        controls, updater = self._controls, self._controls.display_updater
        if info is None:
            updater.clear_all()
            updater.update()
            controls.playback_status = MediaPlaybackStatus.STOPPED
            return
        updater.type = MediaPlaybackType.MUSIC
        music = updater.music_properties
        music.title = info.title
        music.artist = info.artist
        music.genres.clear()
        if info.genre:
            music.genres.append(info.genre)
        updater.update()
        controls.playback_status = (
            MediaPlaybackStatus.PAUSED if info.paused else MediaPlaybackStatus.PLAYING
        )

    def pump(self) -> None:
        pass

    def close(self) -> None:
        controls = self._controls
        controls.remove_button_pressed(self._token)
        controls.display_updater.clear_all()
        controls.playback_status = MediaPlaybackStatus.CLOSED
        controls.is_enabled = False
        self._player.close()
