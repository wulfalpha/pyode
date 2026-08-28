# SPDX-License-Identifier: GPL-2.0-or-later
"""Command line entry point.

Running pyode with no arguments opens the interface.  The other modes exist
because a radio player is often wanted without a screen attached: `--play` is a
headless decoder useful for testing a stream, and `--list` prints the library
for scripts and shell pipelines.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from pyode import __version__, libmpv
from pyode.player import LibmpvNotFoundError, MpvPlayer, PlayerState, PlayerStatus
from pyode.stations import SEED_STATIONS, Station, StationLibrary, default_path


def load_library(path: Path | None = None) -> StationLibrary:
    """Load the library, seeding it on the very first run.

    Seeding keys on the file never having existed rather than on the library
    being empty: someone who deleted every station meant it, and should not
    find them all back the next morning.
    """
    path = path or default_path()
    first_run = not path.exists()
    library = StationLibrary.load(path)
    if first_run:
        for station in SEED_STATIONS:
            library.add(station)
        library.save()
    return library


def match_station(library: StationLibrary, term: str) -> Station | None:
    """Find one station by name, preferring an exact match."""
    wanted = term.strip().lower()
    for station in library:
        if station.name.lower() == wanted:
            return station
    results = library.search(term)
    return results[0] if results else None


def _format_status(status: PlayerStatus) -> str:
    now = status.now_playing
    parts = [f"[{status.state.value:>9}]", now.display]
    if now.codec:
        parts.append(f"({now.codec}{f' {now.bitrate}k' if now.bitrate else ''})")
    parts.append(f"vol {status.volume}{'  MUTED' if status.muted else ''}")
    if status.cache_seconds:
        parts.append(f"buf {status.cache_seconds:.1f}s")
    if status.error:
        parts.append(f"-- {status.error}")
    return "  ".join(parts)


def _print_library(library: StationLibrary) -> int:
    if not len(library):
        print("No stations yet. Run pyode and press / to search the directory.")
        return 0
    width = max(len(station.name) for station in library)
    for station in library:
        quality = " ".join(
            part
            for part in (station.codec.upper(), f"{station.bitrate}k" if station.bitrate else "")
            if part
        )
        mark = "*" if station.favorite else " "
        print(f"{mark} {station.name:<{width}}  {quality:<10}  {station.country}")
    # The summary goes to stderr so `pyode --list` stays pipe-clean, but stdout
    # has to be flushed first or the two streams interleave out of order.
    sys.stdout.flush()
    print(f"\n{len(library)} stations in {library.path}", file=sys.stderr)
    return 0


def _play_headless(args: argparse.Namespace) -> int:
    last = ""

    def on_change(status: PlayerStatus) -> None:
        # Runs on mpv's event thread. Fine for a CLI; the interface polls instead.
        nonlocal last
        line = _format_status(status)
        if line != last:
            last = line
            print(line, flush=True)

    options = {"ao": "null"} if args.silent else {}
    try:
        player = MpvPlayer(on_change=on_change, volume=args.volume, **options)
    except LibmpvNotFoundError as exc:
        print(exc, file=sys.stderr)
        return 1

    deadline = time.monotonic() + args.seconds if args.seconds else None
    try:
        with player:
            player.play(args.play, args.name)
            while deadline is None or time.monotonic() < deadline:
                time.sleep(0.25)
                if player.status.state is PlayerState.ERROR:
                    return 1
    except KeyboardInterrupt:
        print("\nstopped", flush=True)
    return 0


def _run_tui(
    library: StationLibrary,
    autoplay: Station | None,
    *,
    volume: int,
    visualiser: bool,
) -> int:
    from pyode.app import PyodeApp

    try:
        player = MpvPlayer(volume=volume)
    except LibmpvNotFoundError as exc:
        print(exc, file=sys.stderr)
        return 1
    PyodeApp(library=library, player=player, visualiser=visualiser, autoplay=autoplay).run()
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pyode",
        description="Terminal internet radio player.",
        epilog="Run with no arguments to open the interface.",
    )
    parser.add_argument("--version", action="version", version=f"pyode {__version__}")
    parser.add_argument(
        "--stations",
        metavar="PATH",
        type=Path,
        help=f"library file to use (default: {default_path()})",
    )
    parser.add_argument(
        "--station",
        metavar="NAME",
        help="open playing the first library station matching NAME",
    )
    parser.add_argument("--volume", type=int, default=80, metavar="0-100", help="starting volume")
    parser.add_argument(
        "--no-visualiser", action="store_true", help="start with the signal meter hidden"
    )
    parser.add_argument(
        "--list", action="store_true", dest="list_stations", help="print the library and exit"
    )
    parser.add_argument(
        "--libmpv",
        action="store_true",
        dest="show_libmpv",
        help="report where pyode looked for libmpv, and exit",
    )

    headless = parser.add_argument_group("headless playback")
    headless.add_argument("--play", metavar="URL", help="play a stream without the interface")
    headless.add_argument("--name", default="", help="station name, used as the display fallback")
    headless.add_argument(
        "--seconds", type=float, default=0.0, help="stop after N seconds (0 = until Ctrl-C)"
    )
    headless.add_argument(
        "--silent", action="store_true", help="decode without opening an audio device"
    )
    return parser


def _loader_finds_libmpv() -> bool:
    """Whether python-mpv would load on its own, without our help."""
    try:
        libmpv.load()
    except libmpv.LibmpvNotFoundError:
        return False
    return True


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if not 0 <= args.volume <= 100:
        parser.error("--volume must be between 0 and 100")

    if args.show_libmpv:
        # Exits non-zero when libmpv is missing, so it doubles as a check that
        # a script or an installer can branch on.
        print(libmpv.report())
        return 0 if libmpv.find() or _loader_finds_libmpv() else 1

    if args.play:
        return _play_headless(args)

    library = load_library(args.stations)

    if args.list_stations:
        return _print_library(library)

    autoplay = None
    if args.station:
        autoplay = match_station(library, args.station)
        if autoplay is None:
            print(f"No station in your library matches {args.station!r}.", file=sys.stderr)
            print("Run pyode --list to see what is there.", file=sys.stderr)
            return 1

    return _run_tui(library, autoplay, volume=args.volume, visualiser=not args.no_visualiser)


if __name__ == "__main__":
    raise SystemExit(main())
