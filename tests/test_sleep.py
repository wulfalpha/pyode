# SPDX-License-Identifier: GPL-2.0-or-later
from dataclasses import replace

import pytest

from pyode.app import PyodeApp
from pyode.cli import main
from pyode.player import PlayerState
from pyode.sleep import FADE_SECONDS, STEPS, SleepTimer, next_step
from pyode.widgets import TopBar

# -- the timer ----------------------------------------------------------------


def test_counts_down_and_expires():
    timer = SleepTimer.start(15, now=100.0)
    assert timer.remaining(100.0) == 15 * 60
    assert not timer.expired(100.0 + 15 * 60 - 1)
    assert timer.expired(100.0 + 15 * 60)
    assert timer.remaining(10_000.0) == 0


def test_label_rounds_up_to_the_second():
    timer = SleepTimer.start(15, now=0.0)
    assert timer.label(0.0) == "15:00"
    assert timer.label(0.5) == "15:00"
    assert timer.label(61.0) == "13:59"
    assert timer.label(15 * 60 - 0.2) == "0:01"


def test_volume_holds_then_fades_to_nothing():
    timer = SleepTimer.start(1, now=0.0)
    fade_start = 60 - FADE_SECONDS
    assert timer.volume(80, fade_start - 1) == 80
    assert not timer.fading(fade_start - 1)
    assert timer.fading(fade_start + 1)
    assert timer.volume(80, fade_start + FADE_SECONDS / 2) == 40
    assert timer.volume(80, 60) == 0


def test_steps_cycle_then_turn_off():
    seen, current = [], None
    for _ in range(len(STEPS) + 1):
        current = next_step(current)
        seen.append(current)
    assert seen == [*STEPS, None]
    assert next_step(20) == 30  # an odd --sleep value joins the cycle


# -- in the app ----------------------------------------------------------------


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def app(library, player, api, clock):
    app = PyodeApp(library=library, player=player, api=api)
    app._clock = clock
    return app


def _shown(app) -> str:
    return str(app.query_one("#sleep").render())


async def test_key_steps_through_settings_and_shows_the_time(app, clock):
    async with app.run_test() as pilot:
        assert not app.query_one("#sleep").display
        await pilot.press("z")
        await pilot.pause()
        assert _shown(app) == "sleep 15:00"
        clock.now += 5 * 60  # pressing again later still moves on
        await pilot.press("z")
        await pilot.pause()
        assert _shown(app) == "sleep 30:00"
        for _ in STEPS[2:]:
            await pilot.press("z")
        await pilot.press("z")
        await pilot.pause()
        assert app._sleep is None
        assert not app.query_one("#sleep").display


async def test_fades_out_stops_and_restores_the_volume(app, player, clock):
    async with app.run_test() as pilot:
        player.set_volume(80)
        await pilot.press("p", "z")
        clock.now += 15 * 60 - FADE_SECONDS / 2
        await pilot.pause(0.2)
        assert player.status.volume == 40
        assert app._resting_volume == 80  # what preferences would save
        clock.now += FADE_SECONDS
        await pilot.pause(0.2)
        assert player.status.state is PlayerState.IDLE
        assert player.status.volume == 80
        assert app._sleep is None
        assert not app.query_one(TopBar).query_one("#sleep").display


async def test_volume_key_during_the_fade_cancels_it(app, player, clock):
    async with app.run_test() as pilot:
        player.set_volume(80)
        await pilot.press("p", "z")
        clock.now += 15 * 60 - FADE_SECONDS / 2
        await pilot.pause(0.2)
        await pilot.press("plus")
        await pilot.pause(0.2)
        assert app._sleep is None
        assert player.status.volume == 85  # back to 80, then one step up
        assert player.status.state is PlayerState.PLAYING


async def test_volume_key_before_the_fade_leaves_the_timer(app, player, clock):
    async with app.run_test() as pilot:
        await pilot.press("p", "z", "minus")
        await pilot.pause()
        assert app._sleep is not None


async def test_turning_off_mid_fade_restores_the_volume(app, player, clock):
    async with app.run_test() as pilot:
        player.set_volume(60)
        await pilot.press("p", "z")
        clock.now += 15 * 60 - FADE_SECONDS / 2
        await pilot.pause(0.2)
        for _ in STEPS[1:]:
            await pilot.press("z")
        await pilot.press("z")
        await pilot.pause(0.2)
        assert app._sleep is None
        assert player.status.volume == 60


async def test_expiring_with_nothing_playing_just_clears(app, player, clock):
    async with app.run_test() as pilot:
        await pilot.press("z")
        clock.now += 15 * 60
        await pilot.pause(0.2)
        assert app._sleep is None
        assert ("stop",) not in player.calls


async def test_paused_stream_still_fades_and_stops(app, player, clock):
    async with app.run_test() as pilot:
        await pilot.press("p", "z", "space")
        player.status = replace(player.status, state=PlayerState.PAUSED)
        clock.now += 15 * 60
        await pilot.pause(0.2)
        assert player.status.state is PlayerState.IDLE


async def test_starts_from_the_command_line(library, player, api, clock):
    app = PyodeApp(library=library, player=player, api=api, sleep_minutes=20)
    app._clock = clock
    async with app.run_test() as pilot:
        await pilot.pause()
        assert _shown(app) == "sleep 20:00"
        await pilot.press("z")
        assert app._sleep.minutes == 30


@pytest.mark.parametrize("value", ["0", "-5", "2000"])
def test_cli_rejects_silly_durations(value, capsys):
    with pytest.raises(SystemExit):
        main(["--sleep", value])
    assert "--sleep" in capsys.readouterr().err
