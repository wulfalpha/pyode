# SPDX-License-Identifier: GPL-2.0-or-later
"""Small, atomic preferences file, separate from the station library."""

import contextlib
import os
import tempfile
import tomllib
from dataclasses import dataclass
from pathlib import Path

from platformdirs import user_config_path


def default_path() -> Path:
    override = os.environ.get("PYODE_CONFIG_FILE")
    return Path(override).expanduser() if override else user_config_path("pyode") / "config.toml"


@dataclass
class Preferences:
    keymap: str = "standard"
    volume: int = 80
    visualiser: bool = True

    @classmethod
    def load(cls, path: Path) -> "Preferences":
        try:
            with path.open("rb") as stream:
                data = tomllib.load(stream)
        except FileNotFoundError:
            return cls()
        prefs = cls(**{key: data[key] for key in ("keymap", "volume", "visualiser") if key in data})
        if prefs.keymap not in ("standard", "vim"):
            raise ValueError("keymap must be 'standard' or 'vim'")
        if type(prefs.volume) is not int or not 0 <= prefs.volume <= 100:
            raise ValueError("volume must be an integer between 0 and 100")
        if type(prefs.visualiser) is not bool:
            raise ValueError("visualiser must be true or false")
        return prefs

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".config-", suffix=".toml")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                stream.write(
                    f'keymap = "{self.keymap}"\n'
                    f"volume = {self.volume}\n"
                    f"visualiser = {str(self.visualiser).lower()}\n"
                )
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(temporary)
