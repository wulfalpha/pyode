# Changelog

## Unreleased

### Fixed

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
