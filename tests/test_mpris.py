# SPDX-License-Identifier: GPL-2.0-or-later
"""MPRIS over a real, private D-Bus daemon -- the same calls playerctl makes."""

import asyncio
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

if sys.platform == "win32":
    # dbus-fast needs Unix socket features Windows lacks, even to import.
    pytest.skip("D-Bus is not used on Windows", allow_module_level=True)
pytest.importorskip("dbus_fast")

from dbus_fast import Variant  # noqa: E402
from dbus_fast.aio import MessageBus  # noqa: E402

from pyode.mediacontrols import Commands, NowPlayingInfo  # noqa: E402
from pyode.mpris import BUS_NAME, NO_TRACK, OBJECT_PATH, MprisSession  # noqa: E402

PLAYER = "org.mpris.MediaPlayer2.Player"

CONFIG = """<!DOCTYPE busconfig PUBLIC "-//freedesktop//DTD D-Bus Bus Configuration 1.0//EN"
 "http://www.freedesktop.org/standards/dbus/1.0/busconfig.dtd">
<busconfig>
  <type>session</type>
  <listen>unix:tmpdir={tmp}</listen>
  <auth>EXTERNAL</auth>
  <policy context="default">
    <allow send_destination="*" eavesdrop="true"/>
    <allow eavesdrop="true"/>
    <allow own="*"/>
  </policy>
</busconfig>
"""


@pytest.fixture
def bus_address():
    daemon = shutil.which("dbus-daemon")
    if daemon is None:
        pytest.skip("dbus-daemon is not installed")
    # The system temp dir, not pytest's: socket paths have a length limit.
    tmp = Path(tempfile.mkdtemp(prefix="pyode-dbus-"))
    config = tmp / "bus.conf"
    config.write_text(CONFIG.format(tmp=tmp))
    process = subprocess.Popen(
        [daemon, f"--config-file={config}", "--nofork", "--print-address"],
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        address = process.stdout.readline().strip()
        if not address:
            pytest.skip("dbus-daemon did not start")
        yield address
    finally:
        process.terminate()
        process.wait()
        shutil.rmtree(tmp, ignore_errors=True)


class Recorder:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def commands(self) -> Commands:
        def record(name):
            return lambda *args: self.calls.append((name, *args))

        return Commands(
            *(record(n) for n in ("play", "pause", "toggle", "stop", "next", "previous")),
            set_volume=record("set_volume"),
        )


@pytest.fixture
async def session(bus_address):
    recorder = Recorder()
    session = MprisSession(recorder.commands(), bus_address=bus_address)
    await session.ready()
    session.recorder = recorder
    yield session
    session.close()


@pytest.fixture
async def client(bus_address):
    bus = await MessageBus(bus_address=bus_address).connect()
    yield bus
    bus.disconnect()


async def _interface(client, name=BUS_NAME, interface=PLAYER):
    introspection = await client.introspect(name, OBJECT_PATH)
    return client.get_proxy_object(name, OBJECT_PATH, introspection).get_interface(interface)


async def test_takes_the_mpris_name_and_says_who_it_is(session, client):
    assert session.name == BUS_NAME
    root = await _interface(client, interface="org.mpris.MediaPlayer2")
    assert await root.get_identity() == "pyode"
    assert await root.get_can_raise() is False


async def test_starts_stopped_with_no_track(session, client):
    player = await _interface(client)
    assert await player.get_playback_status() == "Stopped"
    assert (await player.get_metadata())["mpris:trackid"].value == NO_TRACK
    assert await player.get_can_seek() is False
    assert await player.get_can_go_next() is True


async def test_reports_what_is_playing(session, client):
    session.update(NowPlayingInfo("So What", "Jazz Radio", "jazz"), 60)
    player = await _interface(client)
    assert await player.get_playback_status() == "Playing"
    metadata = await player.get_metadata()
    assert metadata["xesam:title"].value == "So What"
    assert metadata["xesam:artist"].value == ["Jazz Radio"]
    assert metadata["xesam:genre"].value == ["jazz"]
    assert await player.get_volume() == pytest.approx(0.6)
    session.update(NowPlayingInfo("So What", "Jazz Radio", "jazz", paused=True), 60)
    assert await player.get_playback_status() == "Paused"


async def test_announces_changes(session, client):
    properties = await _interface(client, interface="org.freedesktop.DBus.Properties")
    changes: asyncio.Queue = asyncio.Queue()
    properties.on_properties_changed(lambda iface, changed, _: changes.put_nowait(changed))
    session.update(NowPlayingInfo("Blue in Green", "Jazz Radio"), 100)
    changed = await asyncio.wait_for(changes.get(), 2)
    assert changed["PlaybackStatus"] == Variant("s", "Playing")
    assert changed["Metadata"].value["xesam:title"].value == "Blue in Green"
    first_track = changed["Metadata"].value["mpris:trackid"].value
    session.update(NowPlayingInfo("Flamenco Sketches", "Jazz Radio"), 100)
    changed = await asyncio.wait_for(changes.get(), 2)
    assert "PlaybackStatus" not in changed  # still playing
    assert changed["Metadata"].value["mpris:trackid"].value != first_track


async def test_transport_reaches_the_app(session, client):
    player = await _interface(client)
    await player.call_play_pause()
    await player.call_play()
    await player.call_pause()
    await player.call_stop()
    await player.call_next()
    await player.call_previous()
    await player.call_seek(5_000_000)  # ignored: live radio
    await player.set_volume(0.25)
    assert session.recorder.calls == [
        ("toggle",),
        ("play",),
        ("pause",),
        ("stop",),
        ("next",),
        ("previous",),
        ("set_volume", 25),
    ]


async def test_a_second_pyode_takes_an_instance_name(session, bus_address):
    second = MprisSession(Recorder().commands(), bus_address=bus_address)
    await second.ready()
    try:
        assert second.name.startswith(f"{BUS_NAME}.instance")
    finally:
        second.close()


async def test_without_a_bus_it_stays_quiet():
    session = MprisSession(Recorder().commands(), bus_address="unix:path=/nonexistent/bus")
    await session.ready()
    session.update(NowPlayingInfo("Anything"), 50)  # must not raise
    assert session.name is None
    session.close()
