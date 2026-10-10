"""«Проверено 218 из 640» on onboarding step 4 (the check counter).

Reading of the concept: while AppRestore compares the Apple ID purchase
history with what is on the phone, it counts the purchases already checked
out of the total and what it found so far (offloaded, removed from the App
Store). ``total`` may be unknown (old ipatool without ``--all`` pages): then
only the number checked is shown.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field

from apprestore_gui.ui4b.formatting import plural


def eta_text(seconds: float | None) -> str:
    if seconds is None:
        return ""
    if seconds < 15:
        return "почти готово"
    if seconds < 90:
        return "ещё около минуты"
    minutes = int(round(seconds / 60))
    return f"ещё около {minutes} {plural(minutes, 'минуты', 'минут', 'минут')}"


@dataclass
class ScanCounter:
    clock: Callable[[], float] = time.monotonic
    checked: int = 0
    total: int | None = None
    found: dict[str, int] = field(default_factory=dict)
    started_at: float | None = None
    done: bool = False

    def start(self, total: int | None = None) -> None:
        self.checked = 0
        self.total = total if total and total > 0 else None
        self.found = {}
        self.started_at = self.clock()
        self.done = False

    def update(self, checked: int, total: int | None = None, found: dict[str, int] | None = None) -> None:
        if self.started_at is None:
            self.start(total)
        if total and total > 0:
            self.total = total
        self.checked = max(0, checked if self.total is None else min(checked, self.total))
        if found is not None:
            self.found = dict(found)

    def finish(self) -> None:
        self.done = True
        if self.total is not None:
            self.checked = self.total

    @property
    def fraction(self) -> float:
        if self.done:
            return 1.0
        if not self.total:
            return 0.0
        return max(0.0, min(1.0, self.checked / self.total))

    def caption(self) -> str:
        if self.total:
            return f"Проверено {self.checked} из {self.total}"
        return f"Проверено {self.checked}"

    def eta(self) -> str:
        if self.done:
            return "готово"
        if not self.total or not self.checked or self.started_at is None:
            return ""
        spent = self.clock() - self.started_at
        if spent <= 0:
            return ""
        rate = self.checked / spent
        return eta_text((self.total - self.checked) / rate) if rate > 0 else ""
