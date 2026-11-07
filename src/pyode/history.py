# SPDX-License-Identifier: GPL-2.0-or-later
"""A log of the tracks heard, kept as a plain CSV file.

ICY titles go by and are gone, and "what was that?" is the commonest question
anyone asks of a radio.  So every new title is appended to a log alongside the
station and the time, in the same spreadsheet-friendly format as the library.

The log is append-only and written a line at a time, not atomically: a torn
final line costs one entry, which reading skips, whereas rewriting the whole
file for every track would be the riskier operation.  Only the most recent
`KEEP` entries are held in memory; the file itself is never trimmed, since it
is the user's to keep or delete.
"""

from __future__ import annotations

import csv
import logging
import os
from collections import deque
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from platformdirs import user_data_path

log = logging.getLogger(__name__)

ENV_VAR = "PYODE_HISTORY_FILE"
FIELDNAMES = ("time", "station", "title")
KEEP = 500


def default_path() -> Path:
    override = os.environ.get(ENV_VAR)
    if override:
        return Path(override).expanduser()
    return user_data_path("pyode") / "history.csv"


@dataclass(frozen=True, slots=True)
class Track:
    time: datetime  # timezone-aware, local
    station: str
    title: str

    def to_row(self) -> dict[str, str]:
        return {
            "time": self.time.isoformat(timespec="seconds"),
            "station": self.station,
            "title": self.title,
        }

    @classmethod
    def from_row(cls, row: dict[str, str | None]) -> Track:
        """Raises ``ValueError`` for a row without a valid time or a title."""
        title = (row.get("title") or "").strip()
        if not title:
            raise ValueError("row has no title")
        when = datetime.fromisoformat((row.get("time") or "").strip())
        if when.tzinfo is None:
            when = when.astimezone()  # a hand-edited time is taken as local
        return cls(when, (row.get("station") or "").strip(), title)

    def when(self, now: datetime | None = None) -> str:
        """A short time: just the clock for today, the date as well otherwise."""
        now = now or datetime.now().astimezone()
        local = self.time.astimezone(now.tzinfo)
        if local.date() == now.date():
            return local.strftime("%H:%M")
        if local.year == now.year:
            return f"{local:%b} {local.day} {local:%H:%M}"
        return local.strftime("%Y-%m-%d %H:%M")


class TrackHistory:
    """Recent tracks, newest last, optionally mirrored to a CSV file."""

    def __init__(self, path: Path | None = None, *, persist: bool = True) -> None:
        self.path = path or default_path()
        self.persist = persist
        self._tracks: deque[Track] = deque(maxlen=KEEP)

    @classmethod
    def load(cls, path: Path | None = None, *, persist: bool = True) -> TrackHistory:
        """Read the tail of the log.  A missing or unreadable file is empty."""
        history = cls(path, persist=persist)
        if not persist:
            return history
        try:
            with history.path.open("r", encoding="utf-8-sig", newline="") as fh:
                for number, row in enumerate(csv.DictReader(fh), start=2):
                    try:
                        history._tracks.append(Track.from_row(row))
                    except ValueError as exc:
                        log.debug("%s line %d skipped: %s", history.path, number, exc)
        except FileNotFoundError:
            pass
        except (OSError, ValueError, csv.Error):
            # History is a convenience; never refuse to start over it.
            log.warning("could not read %s", history.path, exc_info=True)
        return history

    def record(self, station: str, title: str, when: datetime | None = None) -> Track | None:
        """Note a title, returning the new entry or None if it is not new.

        Stations repeat the current title whenever they resend metadata, and a
        reconnect replays it too, so a title matching the last entry for the
        same station is not logged again.

        Raises `OSError` if the entry could not be written.  It is still kept
        in memory, so the session's history stays complete.
        """
        title = title.strip()
        if not title:
            return None
        last = self._tracks[-1] if self._tracks else None
        if last is not None and (last.station, last.title) == (station, title):
            return None
        track = Track(when or datetime.now().astimezone(), station, title)
        self._tracks.append(track)
        if self.persist:
            self._append(track)
        return track

    def _append(self, track: Track) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        new = not self.path.exists() or self.path.stat().st_size == 0
        with self.path.open("a", encoding="utf-8", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=FIELDNAMES)
            if new:
                writer.writeheader()
            writer.writerow(track.to_row())

    def recent(self) -> list[Track]:
        """Newest first, which is the order anyone looking wants."""
        return list(reversed(self._tracks))

    def __iter__(self) -> Iterator[Track]:
        return iter(self._tracks)

    def __len__(self) -> int:
        return len(self._tracks)
