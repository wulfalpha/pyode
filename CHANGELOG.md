# Changelog

## 0.2.0 (unreleased)

- Standardize station controls: up/down select, right plays, left stops, and
  Space pauses/resumes; retain Enter, p, and s aliases.
- Add optional saved Vim controls: j/k select, l plays, h stops. Search text
  editing stays native, with j/k navigation in results.
- Add keymap-aware help (`?`) and footer hints from shared control definitions.
- Remember keymap, volume, and visualiser preferences in `config.toml`, with
  CLI overrides and `PYODE_CONFIG_FILE` support.
- Add favourites-only filtering (`F`) and undo of the last deletion (`u`).
- Prevent main-screen shortcuts from acting behind search and help dialogs.
- Fix timer updates during partial UI teardown and honor `PYODE_LIBMPV` before
  the system library loader.
- Use the distribution name `pyode-radio` to avoid the existing PyODE physics
  package on PyPI; retain `pyode` as the command and import name.
- Add PyPI Trusted Publishing and clean pip/uv tool installation checks,
  including the installed TUI and stylesheet on Linux, macOS, and Windows.
- Document installation and release setup; correct headless CLI examples.
