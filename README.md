# pyode

A simple terminal internet radio player: a Textual TUI over libmpv, with a
station library kept in a plain CSV file and discovery via
[radio-browser.info](https://www.radio-browser.info/).

![pyode playing SomaFM Groove Salad](docs/screenshot.png)

Stations on the left, what is on air on the right, and a signal meter that
reads the decoded stream — so it keeps moving even when you have it muted.

## Status

Complete and usable.

- [x] Phase 1 — player core (libmpv, ICY metadata, volume, error reporting)
- [x] Phase 2 — CSV station library (atomic writes, spreadsheet-safe)
- [x] Phase 3 — radio-browser.info API client
- [x] Phase 4 — Textual UI
- [x] Phase 5 — CLI polish

## Installation

Install libmpv for your platform (below), then install the Python application:

```bash
uv tool install pyode-radio
pyode
```

Or install into an activated Python virtual environment with `pip install pyode-radio`.
The distribution is named `pyode-radio` because [PyODE on PyPI](https://pypi.org/project/PyODE/)
is an unrelated physics library. Both installers create the `pyode` command automatically; no shell wrapper or
source checkout is needed. If uv's tool directory is not on PATH, run
`uv tool update-shell` and restart your shell.

For an unreleased checkout, use `uv tool install .` or `pip install .`.
The 0.2.0 changes described here become available by package name after the
release is published to PyPI. libmpv remains a separately installed native dependency.

## Requirements

Python 3.12+ and **libmpv** — the shared library, which is not always the same
thing as the mpv player.

| Platform | Install |
| --- | --- |
| Arch / CachyOS | `sudo pacman -S mpv` |
| Debian / Ubuntu | `sudo apt install libmpv2` |
| Fedora | `sudo dnf install mpv-libs` |
| openSUSE | `sudo zypper install libmpv2` |
| macOS | `brew install mpv` |
| Windows | see below — installing mpv is *not* enough |

pyode finds the library itself, including in the places the dynamic loader
does not look on its own: Homebrew's `/opt/homebrew/lib` and `/usr/local/lib`,
MacPorts, Scoop and Chocolatey install trees, and the `lib` directory beside an
`mpv` binary on your `PATH`. No environment variable should be necessary.

If one is, `PYODE_LIBMPV` takes the library file or the directory holding it,
and `pyode --libmpv` prints every place that was searched and what was found
there — start there when playback will not begin.

### Windows

Every Windows mpv package — Scoop, Chocolatey, winget, and the official builds
— ships `mpv.exe` as a single statically linked binary containing **no libmpv
DLL**, so `scoop install mpv` alone will not get pyode running. Get the library
from the separate development build:

1. Download `mpv-dev-x86_64-<date>-git-<hash>.7z` (or `mpv-dev-aarch64-…` on
   Arm) from [zhongfly/mpv-winbuild releases][winbuild].
2. Extract `libmpv-2.dll` into `%LOCALAPPDATA%\pyode\lib`.

pyode searches that directory, so nothing else needs configuring. Run
`pyode --libmpv` to confirm it was picked up. WSL is not required.

[winbuild]: https://github.com/zhongfly/mpv-winbuild/releases/latest

#### Known limitation: legacy SHOUTcast v1 stations

A handful of older stations run SHOUTcast v1 servers that reply with a bare
`ICY 200 OK` status line instead of a standards-conformant `HTTP/1.1 200 OK`.
The [zhongfly/mpv-winbuild][winbuild] libmpv links against libcurl for its
HTTP handling, and curl treats that line as invalid and refuses the
connection (`Received HTTP/0.9 when not allowed`) — so the stream fails to
load with "loading failed" even though the URL is fine. This affects Windows
only: builds elsewhere typically go through mpv's ffmpeg-based HTTP path,
which tolerates the `ICY` status line.

There is no in-app workaround; it is a property of the libmpv build in use.
If you hit it, report the affected station upstream at
[zhongfly/mpv-winbuild][winbuild] or try a different libmpv build.

## Station library

Stations live in a CSV file at `~/.config/pyode/stations.csv` (override with
`PYODE_STATIONS_FILE`). It is meant to be edited by hand or in a spreadsheet;
unknown columns are ignored, missing ones take defaults, and a malformed row is
skipped rather than taking the rest of the file down with it.

Favourites are a `favorite` column rather than a second file, so a station has
exactly one record and marking one cannot desynchronise from renaming or
deleting it. Reading accepts `true`/`yes`/`1`/`x`; writing is always
`true`/`false`.

## Command line

```bash
pyode                          # open the interface
pyode --station groove         # open playing the first match in your library
pyode --list                   # print the library and exit
pyode --libmpv                 # report where libmpv was looked for, and exit
pyode --no-visualiser          # start with the signal meter hidden
pyode --keymap vim              # save Vim controls (arrows remain available)
pyode --keymap standard         # return to the standard controls
pyode --visualiser              # show the signal meter again
pyode --volume 60
pyode --stations ./other.csv   # use a different library file
pyode --play <url> --seconds 10 --silent   # headless, for testing a stream
```

`--list` writes the station names to stdout and a one-line summary to stderr,
so it pipes cleanly.

On the very first run — when the library file does not yet exist — pyode seeds
six varied stations so there is something to play immediately. Every seeded URL
was checked against a live decoder. Seeding keys on the file never having
existed, not on the library being empty: if you delete every station, it stays
deleted.

## Using it

Stations sit on the left, what is on air sits on the right, and the signal
meter is bottom right. The rule between the two panes is a tuning scale: the
needle marks where the playing station falls in your library.

| Key | Does |
| --- | --- |
| `↑` / `↓` (Vim: `k` / `j`) | Previous / next station |
| `→` / `p` / `Enter` (Vim: `l`) | Play the selected station |
| `space` | Pause and resume |
| `←` / `s` (Vim: `h`) | Stop |
| `f` | Favourite the selected station |
| `F` (Shift+f) | Toggle favourites-only / all stations |
| `d` | Remove it from the library |
| `u` | Undo the last removal, restoring its position |
| `+` / `-` | Volume |
| `m` | Mute |
| `v` | Show or hide the signal meter |
| `/` | Search the directory |
| `?` | Show controls for the active keymap |
| `q` | Quit |

In directory search, Enter submits the query or adds the highlighted result,
Tab changes focus, and Escape closes the dialog. Up/down work in the results;
Vim mode adds j/k there. Text fields retain normal typing and cursor movement.
Playback and library shortcuts apply only to the main screen. Undo lasts for
this session and restores the station without restarting playback.

### Preferences

The keymap, volume, and signal meter visibility are saved in the platform's
pyode configuration directory (`~/.config/pyode/config.toml` on Linux).
Use `PYODE_CONFIG_FILE` to choose another path. For example:

```toml
keymap = "vim"
volume = 60
visualiser = true
```

CLI options override saved values and are remembered when the interface opens.
Volume and visualiser changes in the interface are saved immediately. Headless
playback and diagnostic commands do not change preferences. Invalid settings
produce an error identifying the file, so it can be corrected without losing it.

The meter plots signal level over time, not a frequency spectrum, because time
is what the player measures. It reads the decoded stream ahead of the volume
stage, so it keeps moving while muted — it tells you the stream is alive, not
that sound is coming out. A stalled stream flatlines.

Start with it hidden using `pyode --no-visualiser`.

## Station discovery

Stations come from [radio-browser.info](https://www.radio-browser.info/), a
free volunteer-run directory. pyode identifies itself by User-Agent, asks the
server to filter out broken streams, and reports plays back so the community's
popularity ranking stays useful. The mirror is found over DNS, falling back to
`de1.api.radio-browser.info`.

## Small terminals

Below 68 columns the metadata pane is dropped and the station list takes the
full width; below 20 rows the signal meter goes too. The list and the controls
are what the app cannot do without, so they are what survive.

## Development

```bash
uv sync
uv run pytest
uv run ruff check .
```

The entry point also provides a headless harness for testing streams:

```bash
uv run pyode --volume 65 --name "Smooth Jazz" --play <stream-url>
uv run pyode --silent --seconds 10 --play <stream-url>   # decode without an audio device
```

Build and check an installed release (requires network access for dependencies):

```bash
uv build --out-dir dist/0.2.0-radio
uv run python scripts/check_install.py dist/0.2.0-radio
```

See [the release procedure](docs/releasing.md) for PyPI setup and publishing.

## Licence

pyode is licensed under the GNU General Public License, version 2 or later.
See [LICENSE](LICENSE).

It plays audio through **libmpv** via `python-mpv`, which inherits libmpv's
licence — GPLv2-or-later on a stock build, LGPLv2.1-or-later if libmpv was
built that way. The remaining dependencies (Textual, httpx, platformdirs) are
MIT or BSD.
