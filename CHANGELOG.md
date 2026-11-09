# Changelog

## Unreleased

### Added

- Record just this track: `R` saves the song playing now from where it began,
  even partway through, and stops by itself at the next track. pyode keeps the
  last stretch of the stream in memory to make this possible. The file is
  named after the track.

## 0.6.0

### Added

- Sleep timer: `z` steps through 15, 30, 45, 60 and 90 minutes, then off,
  with the time left in the top bar. The volume fades out over the last 30
  seconds, then playback stops and the volume is restored. Touching the
  volume during the fade cancels it. `--sleep MINUTES` starts with a timer
  of any length for that run.
- Recording: `r` records the playing station without re-encoding, to
  `~/Music/pyode` (or `PYODE_RECORDINGS_DIR`), with a `.cue` track list of
  every title announced. Stopping or changing station ends it; a reconnect
  after a drop continues in a new file.
- Windows media controls: pyode appears in the media flyout beside the
  volume control, and the media keys play, pause, stop, and step through
  stations, as on macOS and Linux. On Windows this adds a dependency on the
  pywinrt `winrt-Windows.Media` bindings.

### Fixed

- SHOUTcast v1 stations such as SLAY Radio play again with libmpv built on
  current ffmpeg, which since August 2026 refuses their `ICY 200 OK` reply.
  A plain `http://` station that fails before starting is retried once
  through a relay on `127.0.0.1` that rewrites that one line; pyode then
  remembers to use it for that station.
- Track titles that a station sends encoded as UTF-8 twice are repaired, so
  SLAY Radio shows "Bachliñski" rather than "BachliÃ±ski". This also applies
  to the track history and recording track lists.
- The libmpv search no longer calls `os.uname()`, which Windows lacks.

## 0.5.0

### Added

- macOS Now Playing and media keys: pyode appears in Control Center with the
  track and station, play/pause pauses and resumes, and next/previous step
  through the station list. `--no-media-keys` leaves them to other apps for
  that run. On macOS this adds a dependency on PyObjC's MediaPlayer bindings.
- MPRIS on Linux: the same controls through GNOME, KDE Plasma, waybar and
  `playerctl`, plus the panel's volume slider. A second pyode registers under
  its own instance name. On Linux this adds a dependency on dbus-fast.

## 0.4.0

### Added

- Browse the directory: the search dialog opens on a menu of popular
  stations, top voted, by genre, and by country. Enter opens a list or adds a
  station, Escape goes back a level, and Down moves from the search box into
  the list. Genres match their tag exactly.
- Listen before adding: Space in a directory list plays the highlighted
  station. On closing, a station you added keeps playing; otherwise
  whatever was playing before comes back.

### Fixed

- Stations the directory marks with codec "UNKNOWN" no longer show it:
  HLS streams (`.m3u8`) show "HLS", and others show no codec. The
  placeholder is dropped from the library on the next save.
- A station removed from `stations.csv` by an outside edit while it plays
  keeps playing as before: its titles are still logged and a dropped stream
  is still reconnected.
- The "try again" hint after a stream fails names the play key for the
  active keymap (`l` in Vim mode, `→` otherwise), as the footer does.
- Holding the volume keys no longer writes `config.toml` on every press; the
  volume is saved once the keys settle, and on quit if a save is pending.

## 0.3.0

The first release published to PyPI, as `pyode-radio`. Version 0.2.0 was
prepared but never released; its changes are included here.

### Added

- Track history: announced titles are logged to `history.csv` with the
  station and time. `t` shows the history, `c` copies the current or
  highlighted title, and `pyode --history` prints the log. Set
  `history = false` to keep it to the session.
- Automatic reconnection, with backoff, when a playing stream drops. pyode
  gives up after five attempts and shows progress in the now-playing pane.
- Repair of moved stations: when a directory station fails to start, pyode
  looks it up again by UUID and, if its address has changed, saves the new
  one and retries.
- Edits made to `stations.csv` while pyode is running are picked up and never
  overwritten. Changes are applied on top of the file as it is on disk, and an
  unreadable file is left alone.
- Reorder stations with Shift+↑/↓ (Vim: K/J).
- Optional saved Vim controls: j/k select, l plays, h stops. Search text
  editing stays native, with j/k navigation in results.
- Keymap-aware help (`?`) and footer hints from shared control definitions.
- Preferences for keymap, volume, and visualiser in `config.toml`, with CLI
  overrides and `PYODE_CONFIG_FILE` support.
- Favourites-only filtering (`F`) and undo of the last deletion (`u`).

### Changed

- Standard station controls: up/down select, right plays, left stops, and
  Space pauses/resumes; Enter, p, and s remain as aliases. Space also
  restarts a stream that has failed.
- The distribution is named `pyode-radio`, to avoid the unrelated PyODE
  physics package on PyPI; the command and import name remain `pyode`.

### Fixed

- SHOUTcast v1 (`ICY 200 OK`) stations play again on libmpv builds with
  mpv 0.42's libcurl stream layer, such as Homebrew's: http streams are opened
  through ffmpeg, which also makes the reconnect options apply again.
- Main-screen shortcuts no longer act behind the search and help dialogs.
- No crash when a timer tick or resize lands during shutdown.
- `PYODE_LIBMPV` is honoured before the system library loader.

### Packaging

- PyPI Trusted Publishing, with clean pip and uv tool installation checks on
  Linux, macOS, and Windows, including the installed TUI and stylesheet.
- Documented installation and release setup; corrected headless CLI examples.
