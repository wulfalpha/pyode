# SPDX-License-Identifier: GPL-2.0-or-later
"""Run using an installed interpreter, outside the source checkout."""

import asyncio
import tempfile
from pathlib import Path

from pyode.app import PyodeApp
from pyode.player import PlayerStatus
from pyode.stations import Station, StationLibrary
from pyode.widgets import ControlBar


class SilentPlayer:
    status = PlayerStatus()

    def level(self):
        return None

    def close(self):
        pass


class OfflineDirectory:
    async def close(self):
        pass


async def main():
    with tempfile.TemporaryDirectory() as directory:
        library = StationLibrary(
            [Station("Installation check", "https://example.invalid/radio")],
            path=Path(directory) / "stations.csv",
        )
        app = PyodeApp(library=library, player=SilentPlayer(), api=OfflineDirectory(), keymap="vim")
        async with app.run_test() as pilot:
            await pilot.pause()
            assert app.query_one(ControlBar).display
            assert app.selected.name == "Installation check"
            await pilot.press("question_mark", "escape", "q")
    print("Installed TUI and stylesheet: OK")


if __name__ == "__main__":
    asyncio.run(main())
