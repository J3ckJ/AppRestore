"""Russian text helpers for the 4b screens: sizes, plurals, name lists."""

from __future__ import annotations

import math
from collections.abc import Sequence

_MB = 1000 * 1000
_GB = 1000 * _MB


def plural(count: int, one: str, few: str, many: str) -> str:
    """Russian plural form: 1 приложение, 2 приложения, 5 приложений."""

    n = abs(int(count)) % 100
    if 11 <= n <= 14:
        return many
    n %= 10
    if n == 1:
        return one
    if 2 <= n <= 4:
        return few
    return many


def apps_word(count: int) -> str:
    return plural(count, "приложение", "приложения", "приложений")


def missing_caption(count: int, noun: str = "iPhone") -> tuple[str, str]:
    """Two lines next to the big number: «приложения / не хватает».

    The one place for this wording. «1 приложение не хватает» is awkward
    (genitive is needed); Ника suggests «пропало с iPhone» for 1, 21, 31…
    The decision is Евгений's; until then the spec default stays.
    """

    return apps_word(count), "не хватает"


def missing_a11y(count: int) -> str:
    """«Не хватает 4 приложений» (one phrase for screen readers)."""

    return f"Не хватает {count} {plural(count, 'приложения', 'приложений', 'приложений')}"


def _decimal(value: float) -> str:
    return f"{value:.1f}".replace(".", ",")


def format_size(size: int | None, *, floor: bool = False, ceil: bool = False) -> str:
    """Decimal units, the way iOS Settings shows storage: «351 МБ», «2,0 ГБ».

    ``floor`` rounds down (used for free space, so we never promise more);
    ``ceil`` rounds up to the shown step, 1 МБ / 0,1 ГБ (a shortfall: never less).
    Unknown or non-positive size is «—».
    """

    if size is None or size <= 0:
        return "—"
    if size < _GB:
        mb = size / _MB
        value = math.floor(mb) if floor else math.ceil(mb - 1e-9) if ceil else round(mb)
        return f"{max(1, int(value))} МБ"
    gb = size / _GB
    if gb >= 100:
        # Capacities: «128 ГБ», not «128,0 ГБ».
        return f"{math.floor(gb) if floor else math.ceil(gb - 1e-9) if ceil else round(gb)} ГБ"
    if floor:
        gb = math.floor(gb * 10 + 1e-9) / 10
    elif ceil:
        gb = math.ceil(gb * 10 - 1e-9) / 10
    return f"{_decimal(gb)} ГБ"


def join_names(names: Sequence[str], limit: int = 4) -> str:
    """«Сбер, Т-Банк, ВТБ и Альфа»; longer lists end with «и ещё N»."""

    clean = [name.strip() for name in names if name and name.strip()]
    if not clean:
        return ""
    if len(clean) == 1:
        return clean[0]
    if len(clean) <= limit:
        return ", ".join(clean[:-1]) + " и " + clean[-1]
    rest = len(clean) - (limit - 1)
    return ", ".join(clean[: limit - 1]) + f" и ещё {rest}"
