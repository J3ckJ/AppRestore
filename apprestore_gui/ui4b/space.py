"""Free space on the device, checked before anything is installed.

Source: lockdown domain ``com.apple.disk_usage`` (``AmountDataAvailable`` /
``TotalDataAvailable``, ``TotalDiskCapacity``) through pymobiledevice3 — the
same numbers iOS Settings → Storage uses. Fallback: AFC ``GET_DEVINFO``
(``FSFreeBytes`` / ``FSTotalBytes``). Any failure gives an unknown space and
the window says «не удалось проверить» instead of guessing.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from apprestore_gui.ui4b.catalog import RestoreItem
from apprestore_gui.ui4b.formatting import format_size

DISK_USAGE_DOMAIN = "com.apple.disk_usage"

#: Verdicts of :func:`plan_space`.
SPACE_EMPTY = "empty"  # nothing selected
SPACE_FITS = "fits"  # every size known and it fits
SPACE_FITS_PARTLY_KNOWN = "fits_known"  # known part fits; some sizes unknown
SPACE_OVER = "over"  # known sizes alone exceed free space → blocked
SPACE_UNKNOWN = "unknown"  # free space could not be read


@dataclass(frozen=True)
class DeviceSpace:
    total_bytes: int | None = None
    free_bytes: int | None = None

    @property
    def known(self) -> bool:
        return self.free_bytes is not None


UNKNOWN_SPACE = DeviceSpace()


def _int(value: Any) -> int | None:
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return number if number >= 0 else None


def parse_disk_usage(payload: Any) -> DeviceSpace:
    """lockdown disk_usage or AFC devinfo dict → :class:`DeviceSpace`."""

    if not isinstance(payload, Mapping):
        return UNKNOWN_SPACE
    free = None
    for key in ("AmountDataAvailable", "TotalDataAvailable", "FSFreeBytes"):
        free = _int(payload.get(key))
        if free is not None:
            break
    total = None
    for key in ("TotalDiskCapacity", "TotalDataCapacity", "FSTotalBytes"):
        total = _int(payload.get(key))
        if total is not None:
            break
    if free is not None and total is not None and free > total:
        free = None
    return DeviceSpace(total_bytes=total, free_bytes=free)


def _in_process(udid: str, timeout: float) -> DeviceSpace:
    async def _run() -> DeviceSpace:
        from pymobiledevice3.lockdown import create_using_usbmux

        async with await create_using_usbmux(serial=udid, connection_type="USB") as lockdown:
            space = parse_disk_usage(await lockdown.get_value(domain=DISK_USAGE_DOMAIN))
            if space.known:
                return space
            from pymobiledevice3.services.afc import AfcService

            afc = AfcService(lockdown)
            try:
                return parse_disk_usage(await afc.get_device_info())
            finally:
                close = getattr(afc, "close", None)
                if close is not None:
                    result = close()
                    if asyncio.iscoroutine(result):
                        await result

    return asyncio.run(asyncio.wait_for(_run(), timeout=timeout))


def query_device_space(tools: Any, udid: str, *, timeout: float = 30) -> DeviceSpace:
    """Ask the device. Never raises: unknown space on any problem."""

    if not udid:
        return UNKNOWN_SPACE
    try:
        from apprestore_core.frozen import is_frozen

        if is_frozen() or tools._pymobiledevice3_only_in_user_site():
            return _in_process(udid, timeout)
        command = tools._pymobiledevice3_cmd(
            "lockdown", "get", "--udid", udid, "--domain", DISK_USAGE_DOMAIN
        )
        result = tools.runner.run(command, check=True, timeout=timeout)
        return parse_disk_usage(json.loads(result.stdout or "null"))
    except Exception:  # noqa: BLE001 - "could not check" is a valid answer
        return UNKNOWN_SPACE


@dataclass(frozen=True)
class SpacePlan:
    verdict: str
    count: int = 0
    needed_bytes: int = 0
    unknown_count: int = 0
    free_bytes: int | None = None
    total_bytes: int | None = None

    @property
    def shortfall_bytes(self) -> int:
        if self.free_bytes is None:
            return 0
        return max(0, self.needed_bytes - self.free_bytes)

    @property
    def blocked(self) -> bool:
        return self.verdict in (SPACE_OVER, SPACE_EMPTY)

    @property
    def ratio(self) -> float:
        """Fill of the capacity bar (0..1), 1 when over."""

        if self.verdict == SPACE_OVER:
            return 1.0
        if not self.free_bytes:
            return 0.0
        return max(0.0, min(1.0, self.needed_bytes / self.free_bytes))

    # -- texts for the footer of «Что вернуть» ---------------------------------

    def total_text(self) -> str:
        """«Выбрано 7 · от 3,8 ГБ»."""

        if self.count == 0:
            return "Ничего не выбрано"
        if self.needed_bytes <= 0:
            return f"Выбрано {self.count} · размер узнаем при скачивании"
        # Always a lower bound: an IPA is compressed, unknown sizes add more.
        return f"Выбрано {self.count} · от {format_size(self.needed_bytes, floor=True)}"

    def free_text(self) -> str:
        if self.free_bytes is None:
            return "свободное место не удалось проверить"
        return f"из {format_size(self.free_bytes, floor=True)} свободных"

    def warning(self, noun: str = "iPhone") -> tuple[str, str]:
        """(bold part, rest) or ("", "")."""

        if self.verdict == SPACE_OVER:
            return (
                f"Не поместится: не хватает {format_size(self.shortfall_bytes)}.",
                f"Снимите часть отметок или освободите место на {noun}: "
                f"Настройки → Основные → Хранилище {noun}.",
            )
        if self.verdict == SPACE_UNKNOWN and self.count:
            return (
                "Свободное место проверить не удалось.",
                f"Если места не хватит, {noun} сам остановит установку.",
            )
        if self.verdict == SPACE_FITS_PARTLY_KNOWN:
            return (
                "",
                f"Размер ещё {self.unknown_count} узнаем при скачивании.",
            )
        return ("", "")


def plan_space(
    selected: Iterable[RestoreItem],
    space: DeviceSpace,
    *,
    reserve_bytes: int = 0,
) -> SpacePlan:
    """Will the selection fit? Unknown sizes never make a «fits» certain."""

    items = list(selected)
    needed = sum(item.size_bytes or 0 for item in items)
    unknown = sum(1 for item in items if not item.size_bytes)
    free = space.free_bytes
    if free is not None:
        free = max(0, free - max(0, reserve_bytes))
    if not items:
        verdict = SPACE_EMPTY
    elif free is None:
        verdict = SPACE_UNKNOWN
    elif needed > free:
        verdict = SPACE_OVER
    elif unknown:
        verdict = SPACE_FITS_PARTLY_KNOWN
    else:
        verdict = SPACE_FITS
    return SpacePlan(
        verdict=verdict,
        count=len(items),
        needed_bytes=needed,
        unknown_count=unknown,
        free_bytes=free,
        total_bytes=space.total_bytes,
    )
