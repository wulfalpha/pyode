# SPDX-License-Identifier: GPL-2.0-or-later
"""Recording: what to call the files, and the cue sheet of tracks inside.

libmpv does the recording itself (``stream-record``), copying the stream's
packets into a file without re-encoding, so a recording sounds exactly like
the broadcast.  This module decides where that file goes and keeps a cue sheet
beside it: one entry per track title the station announced, timed from the
start of the recording, so players that read cue sheets can jump between
tracks while the audio stays one unbroken file.

The cue sheet is rewritten whole on every new title.  If pyode is killed
mid-recording, the audio file and the cue sheet both stay usable up to that
point -- which is also why AAC is saved as raw ADTS ``.aac`` rather than
``.m4a``: an MP4 container is unreadable until it is properly closed.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from platformdirs import user_music_path

ENV_VAR = "PYODE_RECORDINGS_DIR"

# Containers that survive being cut off, by the codec mpv reports.  Anything
# else goes into Matroska, which takes any codec and also survives a cut.
EXTENSIONS = {
    "mp3": ".mp3",
    "aac": ".aac",
    "vorbis": ".ogg",
    "opus": ".ogg",
    "flac": ".flac",
}
FALLBACK_EXTENSION = ".mka"

# Characters that are illegal or troublesome in a file name on some platform.
_UNSAFE = re.compile(r'[\x00-\x1f<>:"/\\|?*]+')


def default_dir() -> Path:
    override = os.environ.get(ENV_VAR)
    return Path(override).expanduser() if override else user_music_path() / "pyode"


def extension_for(codec: str) -> str:
    return EXTENSIONS.get(codec.lower(), FALLBACK_EXTENSION)


def safe_name(text: str) -> str:
    cleaned = _UNSAFE.sub(" ", text).strip(" .")
    return re.sub(r"\s+", " ", cleaned)[:120] or "pyode"


def _cue_time(seconds: float) -> str:
    """mm:ss:ff, where a frame is 1/75 s -- the cue sheet's own unit."""
    frames = round(max(0.0, seconds) * 75)
    minutes, frames = divmod(frames, 60 * 75)
    secs, frames = divmod(frames, 75)
    return f"{minutes:02}:{secs:02}:{frames:02}"


def _quote(text: str) -> str:
    return '"' + text.replace('"', "'") + '"'


@dataclass(frozen=True, slots=True)
class Track:
    offset: float  # seconds into the recording
    title: str


@dataclass
class Recording:
    station: str
    path: Path  # the audio file
    started: float  # on the app's monotonic clock
    tracks: list[Track] = field(default_factory=list)

    @classmethod
    def begin(
        cls, station: str, extension: str, started: float, *, directory: Path, when: datetime
    ) -> Recording:
        """A new recording, with a file name nothing else is using yet."""
        stem = safe_name(f"{station} {when:%Y-%m-%d %H.%M}")
        path = directory / f"{stem}{extension}"
        number = 2
        while path.exists() or path.with_suffix(".cue").exists():
            path = directory / f"{stem} ({number}){extension}"
            number += 1
        return cls(station, path, started)

    @property
    def cue_path(self) -> Path:
        return self.path.with_suffix(".cue")

    def elapsed(self, now: float) -> float:
        return max(0.0, now - self.started)

    def label(self, now: float) -> str:
        seconds = int(self.elapsed(now))
        hours, rest = divmod(seconds, 3600)
        if hours:
            return f"{hours}:{rest // 60:02}:{rest % 60:02}"
        return f"{rest // 60}:{rest % 60:02}"

    def mark(self, title: str, now: float) -> bool:
        """Note a track starting now.  Returns whether it was new."""
        if not title or (self.tracks and self.tracks[-1].title == title):
            return False
        self.tracks.append(Track(self.elapsed(now), title))
        return True

    def cue_sheet(self) -> str:
        kind = "MP3" if self.path.suffix == ".mp3" else "WAVE"
        lines = [
            f"PERFORMER {_quote(self.station)}",
            f"TITLE {_quote(self.path.stem)}",
            f"FILE {_quote(self.path.name)} {kind}",
        ]
        tracks = self.tracks or [Track(0.0, self.station)]
        for number, track in enumerate(tracks, start=1):
            # "Artist - Title" is the near-universal ICY convention.
            artist, sep, title = track.title.partition(" - ")
            if not sep:
                artist, title = self.station, track.title
            # Track 1 must start at the very beginning of the file.
            offset = 0.0 if number == 1 else track.offset
            lines += [
                f"  TRACK {number:02} AUDIO",
                f"    TITLE {_quote(title.strip())}",
                f"    PERFORMER {_quote(artist.strip())}",
                f"    INDEX 01 {_cue_time(offset)}",
            ]
        return "\n".join(lines) + "\n"

    def write_cue(self) -> None:
        self.cue_path.write_text(self.cue_sheet(), encoding="utf-8")
