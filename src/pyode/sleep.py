# SPDX-License-Identifier: GPL-2.0-or-later
"""The sleep timer: play for a while, fade out, stop.

Pure arithmetic on a monotonic clock, so it can be tested without waiting.  The
app checks it on every tick and does the actual fading and stopping.
"""

from __future__ import annotations

from dataclasses import dataclass

# What pressing the sleep key steps through, in minutes, before turning off.
STEPS = (15, 30, 45, 60, 90)
# The volume falls to nothing over the last stretch rather than cutting out,
# which is the difference between drifting off and being woken by the silence.
FADE_SECONDS = 30.0


@dataclass(frozen=True, slots=True)
class SleepTimer:
    minutes: float  # as chosen, for stepping to the next setting
    deadline: float  # on the same clock as `now` below

    @classmethod
    def start(cls, minutes: float, now: float) -> SleepTimer:
        return cls(minutes, now + minutes * 60)

    def remaining(self, now: float) -> float:
        return max(0.0, self.deadline - now)

    def expired(self, now: float) -> bool:
        return self.remaining(now) <= 0

    def fading(self, now: float) -> bool:
        return 0 < self.remaining(now) <= FADE_SECONDS

    def volume(self, base: int, now: float) -> int:
        """The volume to play at: `base` until the fade, then falling to 0."""
        left = self.remaining(now)
        if left >= FADE_SECONDS:
            return base
        return round(base * left / FADE_SECONDS)

    def label(self, now: float) -> str:
        """Time left as m:ss, rounded up so it reaches 0:00 only at the end."""
        seconds = int(-(-self.remaining(now) // 1))
        return f"{seconds // 60}:{seconds % 60:02}"


def next_step(current: float | None) -> int | None:
    """The setting after `current`, in minutes: the first step longer than it,
    or off once past the longest.  From off, the shortest step.

    Steps from what was chosen, not from what is left, so pressing the key
    again a few minutes later still moves on to the next step.
    """
    if current is None:
        return STEPS[0]
    return next((step for step in STEPS if step > current), None)
