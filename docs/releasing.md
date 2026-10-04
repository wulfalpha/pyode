# Releasing pyode

The next release is **0.2.0**. Tags publish the same tested wheel and source
archive to a GitHub release and PyPI. No hand-written launcher is needed:
`[project.scripts]` installs the `pyode` command with pip and uv.

## One-time PyPI setup

1. Register `pyode-radio` on PyPI (or create a pending publisher for it).
   The existing [PyODE](https://pypi.org/project/PyODE/) is an unrelated physics
   library. Package names are case-insensitive; this app keeps the command and
   import name `pyode`, but publishes as `pyode-radio`. The new name is not
   reserved until PyPI accepts the project/publisher setup.
2. Configure a [PyPI Trusted Publisher](https://docs.pypi.org/trusted-publishers/)
   for owner `wulfalpha`, repository `pyode`, workflow `release.yml`, and
   environment `pypi`. For a new project, create a pending publisher first.
3. Create the matching `pypi` GitHub environment. The publishing job requests
   an OIDC token; no long-lived PyPI API token is needed.

## Release checks

- Keep `pyproject.toml` and the `pyode-radio` record in `uv.lock` at the same version.
- Update `CHANGELOG.md`, run `uv sync --locked`, `uv run pytest -q`,
  `uv run ruff check .`, and `uv run ruff format --check .`.
- Build into a fresh output directory: `uv build --out-dir dist/0.2.0-radio`.
- Run `uv run python scripts/check_install.py dist/0.2.0-radio`. This uses temporary
  pip and uv tool environments, invokes the installed commands outside the
  checkout, and mounts the installed TUI to verify its stylesheet is packaged.
- Check CI, including installation on Linux, macOS, and Windows. The installation
  check uses a silent fake player; separately check actual playback with libmpv
  on target systems before release.

Commit the release changes and push the matching `v0.2.0` tag when ready to
publish. The workflow validates the tag, runs checks, builds distributions,
checks both installers, creates the GitHub release, and publishes to PyPI.
If PyPI setup is missing, the PyPI job fails explicitly; configure the publisher
and rerun that failed job. Never reuse a published PyPI version for changed files.

After publishing, verify `uv tool install pyode-radio==0.2.0` and
`pip install pyode-radio==0.2.0` in clean environments. Users still need libmpv;
this release does not bundle native libraries.
