"""Progress of «Возвращаю 2 из 4» (installing state of the home screen).

Driven by QuickSession signals: ``installProgress(percent, text)`` for the
current app, ``appRestored(key)`` / ``installSettled(storeId, ok, text)`` when
one finishes. Stages per app: «Скачано» → «Установка» → «Готово».
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

from apprestore_gui.ui4b.catalog import RestoreItem
from apprestore_gui.ui4b.formatting import format_size

WAIT = "wait"
CURRENT = "current"
DONE = "done"
FAILED = "failed"

STAGE_DOWNLOAD = 0
STAGE_INSTALL = 1
STAGE_DONE = 2

#: Status words that mean the IPA is already being installed on the device.
_INSTALL_WORDS = ("установ", "ставим", "ставится", "install")


from apprestore_core.license_gate import LICENSE_NOTICE, REGION_NOTICE  # noqa: E402

#: QueueEntry.error for a gate refusal because the license limit is used up.
LIMIT_ERROR = "не хватило лимита"


@dataclass
class QueueEntry:
    item: RestoreItem
    state: str = WAIT
    stage: int = STAGE_DOWNLOAD
    percent: int = -1
    status: str = ""
    error: str = ""


@dataclass
class RestoreQueue:
    entries: list[QueueEntry] = field(default_factory=list)
    stopped: bool = False
    skipped: dict[str, list[str]] = field(default_factory=dict)

    def start(self, items: Iterable[RestoreItem], skipped: dict[str, list[str]] | None = None) -> None:
        self.entries = [QueueEntry(item) for item in items]
        #: Left out before the start, by reason ("paid", "no_license"): labels.
        self.skipped = {k: list(v) for k, v in (skipped or {}).items() if v}
        self.stopped = False
        self._advance()

    @property
    def active(self) -> bool:
        return any(entry.state in (WAIT, CURRENT) for entry in self.entries) and not self.stopped

    @property
    def finished(self) -> bool:
        return bool(self.entries) and not self.active

    @property
    def done_count(self) -> int:
        return sum(1 for entry in self.entries if entry.state == DONE)

    @property
    def failed(self) -> list[QueueEntry]:
        return [entry for entry in self.entries if entry.state == FAILED]

    def current(self) -> QueueEntry | None:
        for entry in self.entries:
            if entry.state == CURRENT:
                return entry
        return None

    def _advance(self) -> None:
        if self.stopped or self.current() is not None:
            return
        for entry in self.entries:
            if entry.state == WAIT:
                entry.state = CURRENT
                entry.stage = STAGE_DOWNLOAD
                return

    def _find(self, key: str) -> QueueEntry | None:
        for entry in self.entries:
            if key and key in (entry.item.key, entry.item.store_id, entry.item.bundle_id, entry.item.ipa_path):
                return entry
        return None

    def progress(self, percent: int, text: str = "") -> None:
        entry = self.current()
        if entry is None:
            return
        if text:
            entry.status = text
        installing = any(word in text.casefold() for word in _INSTALL_WORDS)
        if percent >= 0:
            entry.percent = max(0, min(100, int(percent)))
            # ipatool prints «downloading N %»; installation follows at 100.
            entry.stage = STAGE_INSTALL if installing else STAGE_DOWNLOAD
        elif installing:
            entry.stage = STAGE_INSTALL

    def settle(self, key: str, ok: bool, error: str = "") -> None:
        entry = self._find(key) or self.current()
        if entry is None:
            return
        entry.state = DONE if ok else FAILED
        entry.stage = STAGE_DONE if ok else entry.stage
        entry.error = "" if ok else error
        entry.percent = 100 if ok else entry.percent
        self._advance()

    def stop(self) -> None:
        """Nothing new starts; the app being installed now finishes on its own."""

        self.stopped = True

    def rows(self, noun: str = "iPhone") -> list[dict[str, object]]:
        """Rows of the installing queue (спека §3).

        Current app, three stages: «Скачивание N %» (percent from ipatool) →
        «Установка» (no number: indeterminate bar, ring on the phone) →
        «Готово» only by ``installSettled(ok)``.
        """

        rows: list[dict[str, object]] = []
        for entry in self.entries:
            item = entry.item
            size = format_size(item.size_bytes) if item.size_bytes else ""
            stages = ["Скачано", "Установка", "Готово"]
            if entry.state == DONE:
                detail, right = size, f"На {noun}"
            elif entry.state == FAILED:
                detail, right = entry.error or "Не получилось", "Ошибка"
            elif entry.state == CURRENT:
                if entry.stage == STAGE_INSTALL:
                    detail = f"Ставится на {noun}"
                else:
                    pct = f" {entry.percent} %" if entry.percent >= 0 else ""
                    detail = f"Скачиваем{pct}"
                    if entry.status in (LICENSE_NOTICE, REGION_NOTICE) and entry.percent <= 0:
                        # the gate's notice before a free license (smoke 12): shown, not swallowed
                        detail = entry.status
                    stages[0] = f"Скачивание{pct}"
                right = ""
            else:
                detail, right = size, "Остановлено" if self.stopped else "В очереди"
            rows.append(
                {
                    "key": item.key,
                    "name": item.label,
                    "storeId": item.store_id,
                    "bundleId": item.bundle_id,
                    "state": entry.state,
                    "stage": entry.stage,
                    # download only; the install stage has no number
                    "percent": entry.percent if entry.stage == STAGE_DOWNLOAD else -1,
                    "stages": stages,
                    "detail": detail,
                    "right": right,
                }
            )
        return rows
