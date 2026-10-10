"""Free licenses before a run: who needs one, and the consent sheet.

Before «Вернуть» starts, the apps that will need a new free license on the
Apple ID are counted once:

* «нет на аккаунте» — the store id is not in the purchase history
  (``list-purchases`` through ``ipatool_api.all_purchases`` /
  ``iter_purchases``, the same list ``purchases.py`` keeps);
* «бесплатное» — price 0 in iTunes lookup in the account's country.

K = apps that are both. Paid apps that are not on the account never enter the
run (AppRestore does not take paid ones). K = 0 → no sheet.

The remaining limit on the sheet comes from ``license_guard.read_counts`` —
the journal the gate itself reads — and is read again every time the sheet is
shown; the GUI does not count anything. Each app still goes through
``license_gate`` one by one and ``acquire_and_record`` has the last word: a
limit refusal puts the app into «не хватило лимита» in the summary. Nothing is
retried later by itself.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from apprestore_core.license_guard import DEFAULT_DAILY_LIMIT, DEFAULT_TOTAL_LIMIT

from .catalog import ACTION_STORE, RestoreItem
from .formatting import join_names, plural

CONTINUE = "continue"
OWNED_ONLY = "owned_only"
CANCEL = "cancel"


@dataclass(frozen=True)
class LicensePlan:
    #: Free and not on the account: a license will be taken (K).
    need: tuple[RestoreItem, ...] = ()
    #: Not on the account and paid: left out of the run.
    paid: tuple[RestoreItem, ...] = ()
    #: Everything else (on the account, offloaded, IPA files, price unknown —
    #: the gate refuses those itself, nothing is bought blindly).
    rest: tuple[RestoreItem, ...] = ()

    @property
    def k(self) -> int:
        return len(self.need)

    def items_for(self, choice: str) -> list[RestoreItem]:
        if choice == CONTINUE:
            return list(self.rest) + list(self.need)
        if choice == OWNED_ONLY:
            return list(self.rest)
        return []


def _price(value: object) -> float | None:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def plan_licenses(
    items: Iterable[RestoreItem],
    owned: Iterable[str] | None,
    prices: Mapping[str, object],
) -> LicensePlan:
    """Split the chosen items. ``owned`` None = history unknown: treat as not on it.

    The order of ``items`` is kept inside each group.
    """

    owned_ids = {str(sid) for sid in (owned or ()) if str(sid)}
    need: list[RestoreItem] = []
    paid: list[RestoreItem] = []
    rest: list[RestoreItem] = []
    for item in items:
        if item.action != ACTION_STORE or not item.store_id or item.store_id in owned_ids:
            rest.append(item)
            continue
        price = _price(prices.get(item.store_id))
        if price is None:
            rest.append(item)
        elif price == 0:
            need.append(item)
        else:
            paid.append(item)
    return LicensePlan(tuple(need), tuple(paid), tuple(rest))


def candidates(items: Iterable[RestoreItem], owned: Iterable[str] | None) -> list[str]:
    """Store ids whose price must be looked up (not on the account)."""

    owned_ids = {str(sid) for sid in (owned or ())}
    return [i.store_id for i in items if i.action == ACTION_STORE and i.store_id and i.store_id not in owned_ids]


def consent_view(
    plan: LicensePlan,
    counts: tuple[int, int],
    *,
    daily_limit: int = DEFAULT_DAILY_LIMIT,
    total_limit: int = DEFAULT_TOTAL_LIMIT,
) -> dict[str, object]:
    """Texts of the consent sheet. ``counts`` = ``license_guard.read_counts()``."""

    used_today, used_total = (max(0, int(counts[0])), max(0, int(counts[1])))
    left_today = max(0, daily_limit - used_today)
    left_total = max(0, total_limit - used_total)
    k = plan.k
    fits = min(left_today, left_total)
    lead = (
        f"Для {k} {plural(k, 'приложения', 'приложений', 'приложений')} программа возьмёт "
        "бесплатную лицензию на ваш Apple ID."
    )
    warn = ""
    if fits == 0:
        warn = "Лимит бесплатных лицензий исчерпан: эти приложения не вернём, они будут в итоге списком «не хватило лимита»."
    elif k > fits:
        rest = k - fits
        warn = (
            f"Лимита хватит на {fits}: ещё {rest} {plural(rest, 'приложение', 'приложения', 'приложений')} "
            "не вернём, они будут в итоге списком «не хватило лимита»."
        )
    paid = ""
    if plan.paid:
        paid = f"Платные не возвращаем: {join_names([i.label for i in plan.paid])}."
    return {
        "open": True,
        "title": "Бесплатные лицензии",
        "k": k,
        "lead": lead,
        "names": join_names([i.label for i in plan.need], limit=6),
        "limit": f"Осталось на сегодня: {left_today} из {daily_limit}, всего: {left_total} из {total_limit}.",
        "leftToday": left_today,
        "leftTotal": left_total,
        "usedToday": used_today,
        "usedTotal": used_total,
        "warn": warn,
        "paid": paid,
        "go": "Продолжить",
        "owned": "Только уже купленные",
        "ownedEnabled": bool(plan.rest),
        "cancel": "Отмена",
    }
