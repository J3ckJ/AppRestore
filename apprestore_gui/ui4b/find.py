"""«Найти» (02-picker §6b): App Store + purchases, then the built-in list of
removed apps, then the Internet Archive as a fallback only.

Pure logic, no Qt. Data for removed apps comes from Макс's ``delisted_search``
(``search_delisted(query, *, storefronts=('ru','us'), allow_network=False,
limit=10) -> list[DelistedHit]``). Rules (Ника §6b, LEGAL §1.12):

* never show ``hit.brand`` (no «X — это приложение банка Y» anywhere, a11y too);
* hide weak hits (``confidence < MIN_CONFIDENCE``) and archive hits without a
  snapshot or a developer — not greyed, not shown;
* «Удалено из App Store» only after ``region_probe`` (account storefront); if
  the app is in the account's App Store now it is a normal «В App Store» row;
* the developer is shown as it is on the App Store page;
* built-in results appear only for a query: no suggestions for an empty one;
* installs go the usual way (consent, license gate); this module buys nothing.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from apprestore_core.region_probe import RegionStatus

from .component import HOWTO_LINK
from .selection import parse_query
from .settings import ARCHIVE_NOTE
from .store_labels import caption

GROUP_STORE = "store"
GROUP_PURCHASES = "purchases"
GROUP_DELISTED = "delisted"

TITLE = "Найти"
STORE_TITLE = "В App Store"
PURCHASES_TITLE = "Ваши покупки"
DELISTED_NOTE = "Вернуть получится, только если Apple ещё хранит приложение для вашего Apple ID"
SEARCHING_ARCHIVE = "Ищем среди удалённых…"
EMPTY_TEXT = "Ничего не нашли. Попробуйте другое название или вставьте ссылку на App Store"
ARCHIVE_LINK = "Искать и в архиве"
ARCHIVE_DOWN = "Архив сейчас не отвечает, показываем только найденное в App Store"
OFFLINE_BANNER = "Нет интернета — поставить найденное можно, когда он появится."
BANK_LINK_NOTE = "Ссылку на это приложение публиковал банк"
INSTALL = "Поставить"
PAID_NOTE = "Платное, нет в ваших покупках"
UNKNOWN_PRICE = "Не удалось проверить"
COMPONENT_NOTE = "Нужен дополнительный компонент"
APPLE_REJECTED_NOTE = "Apple отказала в выдаче"  # = flow.APPLE_REJECTED_NOTE
PLACEHOLDER = "Название или ссылка на App Store"

#: Spinner gives up after this (Макс: worst case 9 requests ≈ 10 s).
ARCHIVE_TIMEOUT_S = 12.0
#: Fallback when the module is absent; the module's own value wins.
DEFAULT_MIN_CONFIDENCE = 0.6
LIMIT = 10

_MONTHS = (
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
)


def snapshot_date(raw: object) -> str:
    """«20240312» → «12 марта 2024»; anything else → ""."""

    text = str(raw or "")
    if not re.fullmatch(r"\d{8}", text):
        return ""
    year, month, day = int(text[:4]), int(text[4:6]), int(text[6:])
    if not (1 <= month <= 12 and 1 <= day <= 31):
        return ""
    return f"{day} {_MONTHS[month - 1]} {year}"


def _norm(text: str) -> str:
    return re.sub(r"[\W_]+", "", str(text or "").casefold().replace("ё", "е"))


def found_by_alias(query: str, name: str) -> bool:
    """True when the name does not contain what was typed (found by a synonym,
    a fixed keyboard layout, a typo…): then «Найдено по запросу „…“»."""

    q = _norm(query)
    return bool(q) and q not in _norm(name)


@dataclass(frozen=True)
class Hit:
    """The fields of ``DelistedHit`` the UI uses. ``brand`` is not taken at all."""

    store_id: str
    name: str
    developer: str
    icon_url: str
    origin: str  # "builtin" | "wayback"
    confidence: float
    snapshot: str = ""
    developer_is_bank: bool | None = None


def hit_from(obj: Any) -> Hit | None:
    sid = str(getattr(obj, "track_id", "") or "")
    if not sid.isdigit():
        return None
    source = getattr(obj, "source", "")
    origin = str(getattr(source, "value", source) or "").lower()
    dib = getattr(obj, "developer_is_bank", None)
    return Hit(
        store_id=sid,
        name=str(getattr(obj, "name", "") or sid),
        developer=str(getattr(obj, "developer", "") or ""),
        icon_url=str(getattr(obj, "icon_url", "") or ""),
        origin=origin,
        confidence=float(getattr(obj, "confidence", 0.0) or 0.0),
        snapshot=str(getattr(obj, "snapshot", "") or ""),
        developer_is_bank=dib if isinstance(dib, bool) else None,
    )


def visible_hits(hits: Iterable[Hit], min_confidence: float = DEFAULT_MIN_CONFIDENCE) -> list[Hit]:
    """Weak and snapshot-less archive hits are dropped (not shown grey)."""

    out = [
        h for h in hits
        if h.confidence >= min_confidence
        and not (h.origin == "wayback" and (not h.snapshot or not h.developer))
    ]
    return sorted(out, key=lambda h: -h.confidence)


# -- Макс's module (loaded lazily; absent → no built-in list, no archive) -----------


def load_module() -> Any | None:
    try:
        from apprestore_core import delisted_search  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001 - not vendored yet
        return None
    return delisted_search


def run_delisted(query: str, allow_network: bool, module: Any | None = None) -> tuple[list[Hit], bool]:
    """(hits, archive_down). Worker thread when ``allow_network``; instant otherwise."""

    mod = module if module is not None else load_module()
    if mod is None:
        return [], False
    search = getattr(mod, "default_search", None)
    engine = search() if callable(search) else None
    before = getattr(engine, "errors", 0)
    try:
        raw = mod.search_delisted(query, storefronts=("ru", "us"), allow_network=allow_network, limit=LIMIT)
    except Exception:  # noqa: BLE001
        return [], allow_network
    hits = [h for h in (hit_from(r) for r in raw or []) if h is not None]
    errors = getattr(engine, "errors", 0) - before
    down = allow_network and errors > 0 and not any(h.origin == "wayback" for h in hits)
    return hits, down


def min_confidence(module: Any | None) -> float:
    try:
        return float(getattr(module, "MIN_CONFIDENCE", DEFAULT_MIN_CONFIDENCE))
    except (TypeError, ValueError):
        return DEFAULT_MIN_CONFIDENCE


# -- state -----------------------------------------------------------------------------


@dataclass
class FindState:
    query: str = ""
    offline: bool = False
    archive_enabled: bool = True
    archive_available: bool = True
    component_missing: bool = False
    owned: set[str] | None = None
    min_confidence: float = DEFAULT_MIN_CONFIDENCE
    #: search_store rows {storeId, name, source: appstore|purchases}
    store: list[dict[str, str]] = field(default_factory=list)
    store_done: bool = False
    builtin: list[Hit] = field(default_factory=list)
    archive: list[Hit] = field(default_factory=list)
    archive_state: str = ""  # "" | busy | done | down
    archive_asked: bool = False
    #: {store id: {"price": float|None, "developer": str}} — lookup in the account's country
    offers: dict[str, dict[str, object]] = field(default_factory=dict)
    offers_done: bool = False
    statuses: dict[str, RegionStatus] = field(default_factory=dict)
    #: Apple refused these in this program run (memory only) — no button
    rejected: set[str] = field(default_factory=set)

    def reset(self, query: str) -> None:
        self.query = query
        self.store, self.builtin, self.archive = [], [], []
        self.store_done = self.offers_done = False
        self.archive_state, self.archive_asked = "", False
        self.offers, self.statuses = {}, {}

    @property
    def words(self) -> str:
        return parse_query(self.query)[0]

    def wants_archive(self) -> bool:
        """Fallback only: App Store, purchases and the built-in list gave nothing."""

        return (
            self.archive_enabled and self.archive_available and not self.offline
            and bool(self.words) and not parse_query(self.query)[1]
            and self.store_done and not self.store
            and not visible_hits(self.builtin, self.min_confidence)
            and self.archive_state == ""
        )

    def delisted_ids(self) -> list[str]:
        return [h.store_id for h in self.builtin + self.archive]

    # -- rows ---------------------------------------------------------------------------

    def _action(self, sid: str) -> dict[str, object]:
        none = {"action": "", "actionNote": "", "actionLink": ""}
        if sid in self.rejected:
            return {**none, "actionNote": APPLE_REJECTED_NOTE}
        if self.owned is not None and sid in self.owned:
            return {**none, "action": INSTALL}
        if self.component_missing:
            return {**none, "actionNote": COMPONENT_NOTE, "actionLink": HOWTO_LINK}
        if not self.offers_done and sid not in self.offers:
            return none  # price not known yet: nothing for a moment
        price = (self.offers.get(sid) or {}).get("price")
        if price is None:
            return {**none, "actionNote": UNKNOWN_PRICE}
        if float(price) == 0:  # type: ignore[arg-type]
            return {**none, "action": INSTALL}
        return {**none, "actionNote": PAID_NOTE}

    def _row(self, sid: str, name: str, developer: str, *, group: str, tag: str = "",
             line3: str = "", icon_url: str = "") -> dict[str, object]:
        dev = developer or str((self.offers.get(sid) or {}).get("developer") or "")
        return {
            "kind": "app", "group": group, "storeId": sid, "name": name, "developer": dev,
            "tag": tag, "line3": line3, "iconUrl": icon_url, "enabled": not self.offline,
            **self._action(sid),
        }

    def _line3(self, hit: Hit) -> str:
        parts: list[str] = []
        if hit.origin == "wayback":
            date = snapshot_date(hit.snapshot)
            parts.append(f"Из архива · снимок {date}" if date else "Из архива")
        if hit.origin == "builtin" and hit.developer_is_bank is False:  # level A, never archive
            parts.append(BANK_LINK_NOTE)
        if found_by_alias(self.query, hit.name):
            parts.append(f"Найдено по запросу „{self.query.strip()}“")
        return " · ".join(parts)

    def rows(self) -> list[dict[str, object]]:
        if not self.query.strip():
            return []  # no suggestions or collections for an empty query
        seen: set[str] = set()
        groups: dict[str, list[dict[str, object]]] = {GROUP_STORE: [], GROUP_PURCHASES: [], GROUP_DELISTED: []}
        for source, group in (("appstore", GROUP_STORE), ("purchases", GROUP_PURCHASES)):
            for r in self.store:
                sid = str(r.get("storeId") or "")
                if r.get("source") != source or not sid or sid in seen:
                    continue
                seen.add(sid)
                groups[group].append(self._row(sid, str(r.get("name") or sid), str(r.get("developer") or ""), group=group))
        for hit in visible_hits(self.builtin + self.archive, self.min_confidence):
            if hit.store_id in seen:
                continue
            seen.add(hit.store_id)
            status = self.statuses.get(hit.store_id)
            if status is RegionStatus.AVAILABLE:
                # in the account's App Store now: an ordinary result
                groups[GROUP_STORE].append(self._row(hit.store_id, hit.name, hit.developer, group=GROUP_STORE,
                                                     icon_url=hit.icon_url))
                continue
            groups[GROUP_DELISTED].append(self._row(
                hit.store_id, hit.name, hit.developer, group=GROUP_DELISTED,
                # the tag only once region_probe confirmed it is not in the account's store
                tag=caption(status) if status in (RegionStatus.DELISTED, RegionStatus.NOT_IN_REGION) else "",
                line3=self._line3(hit), icon_url=hit.icon_url,
            ))
        titles = {GROUP_STORE: STORE_TITLE, GROUP_PURCHASES: PURCHASES_TITLE,
                  GROUP_DELISTED: caption(RegionStatus.DELISTED)}
        out: list[dict[str, object]] = []
        for group in (GROUP_STORE, GROUP_PURCHASES, GROUP_DELISTED):
            rows = groups[group]
            if not rows:
                continue  # empty groups are not shown
            out.append({"kind": "header", "group": group, "title": titles[group], "count": len(rows),
                        "note": DELISTED_NOTE if group == GROUP_DELISTED else ""})
            out.extend(rows)
        return out

    def view(self) -> dict[str, object]:
        rows = self.rows()
        typed = bool(self.query.strip())
        busy = self.archive_state == "busy"
        searching = typed and not self.store_done
        empty = typed and self.store_done and not busy and not rows
        banner = OFFLINE_BANNER if self.offline else (ARCHIVE_DOWN if self.archive_state == "down" else "")
        return {
            "rows": rows,
            "typed": typed,
            "searching": searching,
            "spinner": SEARCHING_ARCHIVE if busy else "",
            "empty": EMPTY_TEXT if empty else "",
            "emptyLink": ARCHIVE_LINK if empty and not self.archive_enabled else "",
            "banner": banner,
            "footnote": ARCHIVE_NOTE if self.archive_enabled and self.archive_asked else "",
        }


def offers_from(lookups: Mapping[str, Mapping[str, object] | None]) -> dict[str, dict[str, object]]:
    out: dict[str, dict[str, object]] = {}
    for sid, offer in lookups.items():
        price = offer.get("price") if offer else None
        out[str(sid)] = {
            "price": float(price) if isinstance(price, (int, float)) and not isinstance(price, bool) else None,
            "developer": str((offer or {}).get("artist") or ""),
        }
    return out

