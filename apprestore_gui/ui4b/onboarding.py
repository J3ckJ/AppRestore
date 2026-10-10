"""Four onboarding steps of 4b: Привет → Подключение → Apple ID → Проверка.

Step 2 and 3 move on by themselves (phone connected, signed in); «Позже» on
step 3 skips the Apple ID (offloaded apps and IPA files do not need it).
"""

from __future__ import annotations

from dataclasses import dataclass

STEP_TITLES: tuple[str, ...] = ("Привет", "Подключение", "Apple ID", "Проверка")


@dataclass
class Onboarding:
    started: bool = False
    apple_id_skipped: bool = False
    finished: bool = False

    def step(self, *, connected: bool, signed_in: bool, scan_done: bool) -> int:
        """1..4, or 0 when onboarding is over."""

        if self.finished:
            return 0
        if not self.started:
            return 1
        if not connected:
            return 2
        if not signed_in and not self.apple_id_skipped:
            return 3
        if not scan_done:
            return 4
        self.finished = True
        return 0

    def stepper(self, current: int) -> list[dict[str, object]]:
        """Rows for the stepper: number, title, state ``ok`` | ``on`` | ``todo``."""

        out: list[dict[str, object]] = []
        for index, title in enumerate(STEP_TITLES, start=1):
            state = "ok" if index < current else ("on" if index == current else "todo")
            out.append({"n": index, "title": title, "state": state})
        return out


#: Step 1 illustration: a home screen with empty places (static, not data).
ILLUSTRATION_TILES = 16


def illustration_tiles(count: int = ILLUSTRATION_TILES) -> list[dict[str, object]]:
    return [
        {"name": "", "app": "", "storeId": "", "bundleId": "", "kind": "slot", "progress": -1}
        for _ in range(count)
    ]


def scanning_tiles(tiles: list[dict[str, object]], fraction: float) -> list[dict[str, object]]:
    """Step 4 (03-onboarding §1/§5): tiles show up as the check goes; the ones not
    checked yet are ``pending`` (icon at opacity .6, empty places not drawn yet)."""

    shown = round(max(0.0, min(1.0, fraction)) * len(tiles))
    return [dict(tile, pending=index >= shown) for index, tile in enumerate(tiles)]


def scanning_count(count: int, fraction: float, done: bool) -> int:
    """Counters grow with the check instead of jumping to the final number."""

    if done:
        return count
    return min(count, int(max(0.0, min(1.0, fraction)) * count))
