"""Which hero the 4b home screen shows, its texts and the phone tiles.

Pure function of the inputs; the QML only lays out what :func:`home_view`
returns.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field

from apprestore_gui.ui4b.catalog import (
    ACTION_STORE,
    GROUP_OFFLOADED,
    GROUP_REGION,
    GROUP_REMOVED,
    RestoreItem,
    short_name_of,
)
from apprestore_gui.ui4b.region import IPA_LINK, IPA_MORE
from apprestore_gui.ui4b.component import COMPONENT_NOTE, DETAILS_LINK, HOWTO_LINK, QUIET_LINE
from apprestore_gui.ui4b.formatting import (
    format_size,
    join_names,
    missing_a11y,
    missing_caption,
    plural,
)
from apprestore_gui.ui4b.queue import CURRENT, DONE, FAILED, LIMIT_ERROR, STAGE_INSTALL, WAIT, RestoreQueue
from apprestore_gui.ui4b.space import SPACE_OVER, DeviceSpace, plan_space

STATE_DISCONNECTED = "disconnected"
STATE_LOADING = "loading"
STATE_SIGNIN = "signin"
STATE_MISSING = "missing"  # few apps: «Вернуть все N»
STATE_MANY = "many"  # many apps: «Выбрать и вернуть» opens the picker
STATE_REGION = "region"
STATE_INSTALLING = "installing"
STATE_DONE = "done"
STATE_EMPTY = "empty"
STATE_NEEDS_COMPONENT = "needs_component"  # ipatool without patch 0001 (variant of missing)
STATE_STORE_MISMATCH = "store_mismatch"  # -128, temporary look until Ника's part
#: error-apple-rejected (01 state table, 02 §6b п.4): ErrorCode.APPLE_REJECTED, not -128
STATE_APPLE_REJECTED = "apple_rejected"
APPLE_REJECTED_LEAD = "Apple не выдаёт это приложение вашему Apple ID. Остальные приложения можно вернуть как обычно."

#: Up to this many apps (and if they fit) the home screen offers «Вернуть все N»;
#: more → «Выбрать и вернуть». Mirrors ``Theme.restoreAllMax`` (test keeps them equal).
RESTORE_ALL_MAX = 8
DIRECT_LIMIT = RESTORE_ALL_MAX
PHONE_TILES = 16
#: Page dots on the phone: ceil(total / 24), at most 11.
PAGE_SIZE = 24
PAGES_MAX = 11
MINUTES_PER_APP = 1.25


@dataclass(frozen=True)
class PhoneApp:
    """An app that is on the phone (for the silhouette)."""

    name: str
    store_id: str = ""
    bundle_id: str = ""


@dataclass
class HomeInput:
    connected: bool = False
    loading: bool = False
    device_name: str = ""
    noun: str = "iPhone"
    signed_in: bool = False
    items: Sequence[RestoreItem] = ()
    space: DeviceSpace = field(default_factory=DeviceSpace)
    queue: RestoreQueue | None = None
    #: The user pressed «Готово» on the done screen.
    done_dismissed: bool = False
    phone_apps: Sequence[PhoneApp] = ()
    region_name: str = ""
    #: Apple wants the user again (expired session, session codes).
    relogin: bool = False
    #: -128: "" | "mismatch" | "unavailable" (again after signing in again)
    store_problem: str = ""
    store_problem_app: str = ""
    store_problem_name: str = ""
    #: «Лимит обновится завтра в 14:20» / «Общий лимит 15 исчерпан» / "".
    limit_note: str = ""
    #: The total limit (15) is used up (read_counts): no time, an explanation.
    limit_total: bool = False
    #: «Подробнее» pressed: the technical text instead of the quiet line.
    component_details: str = ""
    #: ipatool without AppRestore's patches: no new licenses (needs-component).
    component: bool = False


def _minutes(count: int) -> str:
    minutes = max(1, int(round(count * MINUTES_PER_APP)))
    return f"Около {minutes} {plural(minutes, 'минуты', 'минут', 'минут')}"


def _tile(name: str, store_id: str, bundle_id: str, kind: str, label: str = "", progress: float = -1) -> dict[str, object]:
    return {
        "name": label or name,
        "app": name,
        "storeId": store_id,
        "bundleId": bundle_id,
        # спека §2.4: app | slot | offloaded | waiting | downloading | installing | new | unavailable
        "kind": kind,
        "progress": progress,
    }


def phone_tiles(inp: HomeInput, state: str) -> list[dict[str, object]]:
    """16 icons: apps on the phone around, the missing ones as slots in row 3.

    Follows the queue: slot → waiting → downloading(p) → installing → new;
    a failed app goes back to slot. No icons of other apps are invented:
    without ``phone_apps`` only slots / offloaded are shown.
    """

    entries = {}
    if inp.queue is not None:
        entries = {entry.item.key: entry for entry in inp.queue.entries}
    targets: list[dict[str, object]] = []
    others: list[dict[str, object]] = []
    for item in inp.items:
        entry = entries.get(item.key)
        if item.group == GROUP_OFFLOADED and entry is None:
            others.append(_tile(item.label, item.store_id, item.bundle_id, "offloaded"))
            continue
        label = item.label
        progress = -1.0
        if item.group == GROUP_REGION:
            kind, label = "unavailable", "Недоступна"
        elif state == STATE_DONE and (entry is None or entry.state == DONE):
            kind = "new"
        elif entry is None or entry.state == FAILED:
            kind = "slot"
        elif entry.state == DONE:
            kind = "new"
        elif entry.state == CURRENT:
            if entry.stage == STAGE_INSTALL:
                kind = "installing"
            else:
                kind = "downloading"
                progress = entry.percent / 100 if entry.percent >= 0 else -1.0
        elif entry.state == WAIT and state == STATE_INSTALLING:
            kind, label = "waiting", "Ожидание"
        else:
            kind = "slot"
        targets.append(_tile(item.label, item.store_id, item.bundle_id, kind, label, progress))
    installed = [_tile(app.name, app.store_id, app.bundle_id, "app") for app in inp.phone_apps]
    around = installed + others
    head = around[:8]
    row = targets[:4]
    tail = around[8 : 8 + (PHONE_TILES - len(head) - len(row))]
    tiles = head + row + tail
    if len(tiles) < PHONE_TILES and len(targets) > 4:
        tiles += targets[4 : 4 + PHONE_TILES - len(tiles)]
    return tiles[:PHONE_TILES]


def phone_pages(total: int) -> int:
    return min(PAGES_MAX, max(1, math.ceil(total / PAGE_SIZE))) if total > 0 else 0


def link_action(name: str) -> str:
    """What a home-screen link does, or "" (then it is not shown)."""

    if name == "Остановить":
        return "stop"
    if name in ("Файлы IPA", IPA_LINK):
        return "ipa"  # a file already on this computer → install (installSaved)
    if name == "Apple ID":
        return "signin"
    if name == "Найти другое приложение":
        return "find"  # the «Найти» sheet (02-picker §6b)
    if name == "Настройки":
        return "settings"  # Windows only, last after «Apple ID» (01 §2.2)
    if name == HOWTO_LINK:
        return "howto"  # BUILD-ipatool.md / RUN-FROM-SOURCE.md
    if name == DETAILS_LINK:
        return "details"  # technical details of the missing component
    return ""


def home_view(inp: HomeInput) -> dict[str, object]:
    noun = inp.noun or "iPhone"
    over = inp.device_name or noun
    items = list(inp.items)
    removed = [item for item in items if item.group == GROUP_REMOVED]
    offloaded = [item for item in items if item.group == GROUP_OFFLOADED]
    region = [item for item in items if item.group == GROUP_REGION]
    selectable = [item for item in items if item.selectable]
    queue = inp.queue
    view: dict[str, object] = {
        "over": over,
        "number": 0,
        "word": "",
        "word2": "",
        "title": "",
        "lead": "",
        "cta": "",
        "cta2": "",
        "ctaSecondary": False,
        "hint": "",
        "fine": "",
        "links": [],
        "steps": [],
        "queue": [],
        "a11y": "",
        "pages": 0,
        "pill": f"{noun} подключён" if inp.connected else f"{noun} не подключён",
        "pillOn": inp.connected,
    }

    if not inp.connected:
        state = STATE_DISCONNECTED
        view.update(
            over=f"{noun} не найден",
            title=f"Подключите\n{noun}",
            steps=[
                "Соедините телефон с компьютером кабелем",
                f"Разблокируйте {noun}",
                "Нажмите «Доверять» и введите код телефона",
            ],
            links=["Не получается подключить"],
        )
    elif inp.store_problem == "rejected" and not (queue is not None and queue.active):
        state = STATE_APPLE_REJECTED
        short = inp.store_problem_app or "Приложение"
        view.update(
            over=inp.store_problem_name or short,
            title=f"{short}\nнедоступна",
            lead=APPLE_REJECTED_LEAD,
            cta="На главный",  # no sign-in, no retry
            links=[IPA_LINK],
        )
    elif inp.store_problem and not (queue is not None and queue.active):
        state = STATE_STORE_MISMATCH
        app = inp.store_problem_app
        over = f"{app} не скачалось" if app else "Приложение не скачалось"
        if inp.store_problem == "unavailable":
            view.update(
                over=over,
                title="Недоступно\nв магазине",
                lead="Приложение недоступно в магазине страны вашего Apple ID.",
                cta="На главный",
            )
        else:
            view.update(
                over=over,
                title="Магазин\nне совпал",
                lead="Магазин в текущем входе не совпадает со страной вашего Apple ID.",
                cta="На главный",
                cta2="Войти заново",
                hint="После входа ничего не начнётся само: нажмите «Вернуть» ещё раз.",
            )
    elif inp.relogin and not (queue is not None and queue.active):
        # No promise to continue: after signing in the user is back on the
        # home screen and presses «Вернуть» again.
        state = STATE_SIGNIN
        view.update(
            over="Сессия Apple ID истекла",
            title="Войдите\nзаново",
            lead="Apple попросил войти ещё раз. После входа вернётесь сюда "
            "и снова нажмёте «Вернуть».",
            cta="Войти заново",
            fine="Вход хранится только на этом компьютере, в связке ключей под вашим паролем. "
            "Код подтверждения придёт на ваши устройства Apple.",
            links=[IPA_LINK, "Почему это безопасно"],
        )
    elif queue is not None and queue.active:
        state = STATE_INSTALLING
        total = len(queue.entries)
        view.update(
            over="Возвращаю",
            number=queue.done_count,
            word=f"из {total}",
            word2=f"уже на {noun}",
            a11y=f"Уже на {noun}: {queue.done_count} из {total}",
            queue=queue.rows(noun),
            fine="Иконка на телефоне может появиться на минуту позже. "
            "Это нормально: ошибки нет, просто подождите.",
            links=["Остановить", "Журнал"],
        )
    elif queue is not None and queue.finished and not inp.done_dismissed:
        state = STATE_DONE
        ok = [entry.item.label for entry in queue.entries if entry.state == DONE]
        bad = queue.failed
        if not ok and not bad and queue.skipped:
            title = "Ничего\nне вернули"
        elif bad and not ok:
            title = "Не получилось"
        elif bad:
            title = "Почти всё\nна месте"
        else:
            title = "Всё\nна месте"
        lead = ""
        if ok:
            lead = f"<b>{join_names(ok)}</b> снова на телефоне. Вернувшиеся приложения отмечены синей точкой, как обычно в iOS."
        limit = [entry for entry in bad if entry.error == LIMIT_ERROR]
        other = [entry for entry in bad if entry.error != LIMIT_ERROR]
        if other:
            names = join_names([entry.item.label for entry in other])
            reason = other[0].error if len(other) == 1 and other[0].error else "подробности в журнале"
            lead = (lead + " " if lead else "") + f"Не вернулись: <b>{names}</b> ({reason})."
        if limit:
            names = join_names([entry.item.label for entry in limit], limit=8)
            if inp.limit_total:
                lead = (lead + " " if lead else "") + f"Для {names} лицензий больше нет."
                view["fine"] = (
                    "На этом компьютере AppRestore уже взял 15 бесплатных лицензий — это общий предел, "
                    "новых он больше не берёт. Приложения, которые уже есть на вашем Apple ID, "
                    "и сгруженные возвращаются как обычно."
                )
            else:
                lead = (lead + " " if lead else "") + f"На {names} не хватило лимита бесплатных лицензий."
                if inp.limit_note:
                    lead += f" {inp.limit_note}."
                lead += " Сами на завтра не ставим."
        for reason, label in (
            ("no_license", "Без новой лицензии не возвращали"),
            ("paid", "Платные не возвращаем"),
            ("component", COMPONENT_NOTE),
        ):
            names_skipped = queue.skipped.get(reason) or []
            if names_skipped:
                lead = (lead + " " if lead else "") + f"{label}: <b>{join_names(names_skipped)}</b>."
        view.update(
            title=title,
            lead=lead,
            cta="Готово",
            ctaSecondary=True,
            queue=queue.rows(noun),
            links=["Найти другое приложение", "Файлы IPA", "Apple ID"],
        )
    elif inp.loading:
        state = STATE_LOADING
        view.update(title="Смотрю,\nчего не хватает", hint="Телефоном можно пользоваться, только не отключайте кабель.")
    elif not items:
        state = STATE_EMPTY
        view.update(
            title="Всё\nна месте",
            lead="Сгруженных и удалённых из App Store приложений на этом телефоне нет.",
            links=["Найти другое приложение", "Файлы IPA", "Apple ID"],
        )
    elif removed and any(item.action == ACTION_STORE for item in removed) and not inp.signed_in and not offloaded:
        state = STATE_SIGNIN
        view.update(
            over="Остался один шаг",
            title="Войдите\nв Apple ID",
            lead=f"{join_names([item.label for item in removed])} есть только на серверах Apple. "
            "Скачать их можно на <b>ваш</b> Apple ID, поэтому нужен вход.",
            cta="Войти",
            fine="Вход хранится только на этом компьютере, в связке ключей под вашим паролем. "
            "Код подтверждения придёт на ваши устройства Apple.",
            links=[IPA_LINK, "Почему это безопасно"],
        )
    elif inp.component and any(item.note == COMPONENT_NOTE for item in items):
        # 01 §3.2 needs-component: ordinary main screen, «Вернуть M» = what really goes
        state = STATE_NEEDS_COMPONENT
        marked = [item for item in items if item.note == COMPONENT_NOTE]
        n_all = len(offloaded) + len(removed)
        m = len(selectable)
        parts = []
        off_ok = [item for item in selectable if item.group == GROUP_OFFLOADED]
        own_ok = [item for item in selectable if item.group != GROUP_OFFLOADED]
        if off_ok:
            parts.append(
                f"<b>{join_names([i.label for i in off_ok])}</b> "
                + ("сгружено, его можно вернуть сейчас." if len(off_ok) == 1 else "сгружены, их можно вернуть сейчас.")
            )
        if own_ok:
            parts.append(
                f"<b>{join_names([i.label for i in own_ok])}</b> "
                + ("уже на вашем Apple ID, его можно вернуть." if len(own_ok) == 1 else "уже на вашем Apple ID, их можно вернуть.")
            )
        parts.append(
            f"<b>{join_names([i.label for i in marked])}</b> "
            + ("удалено из App Store." if len(marked) == 1 else "удалены из App Store.")
        )
        line1, line2 = missing_caption(n_all, noun)
        view.update(
            number=n_all,
            word=line1,
            word2=line2,
            a11y=missing_a11y(n_all),
            lead=" ".join(parts),
            cta=(f"Вернуть {m}" if m > 1 else "Вернуть") if m else "",
            cta2="" if m else HOWTO_LINK,
            fine=inp.component_details or QUIET_LINE,
            links=["Найти другое приложение", DETAILS_LINK],
            pages=phone_pages(n_all),
        )
    elif region and len(selectable) <= RESTORE_ALL_MAX:
        state = STATE_REGION
        names_ok = join_names([item.label for item in selectable])
        names_na = join_names([item.label for item in region])
        # full names in the body and the link (Ника #7): «Альфа-Банк», not «Альфа»
        full_na = join_names([f"«{short_name_of(item.name)}»" for item in region])
        where = inp.region_name or "вашего региона"
        verb = "недоступна" if len(region) == 1 else "недоступны"
        view.update(
            over=f"{names_na} {verb}",
            number=len(selectable),
            word=f"из {len(items)}",
            word2="можно вернуть",
            lead=(f"<b>{names_ok}</b> вернутся как обычно. " if names_ok else "")
            + f"{full_na} нет в App Store региона вашего Apple ID ({where}), "
            f"и Apple не отдаёт {'его' if len(region) == 1 else 'их'} этой учётной записи.",
            cta=f"Вернуть {len(selectable)}" if selectable else "",
            # «Войти с другим Apple ID» — отложено (Лена)
            links=[IPA_LINK],
            fine=IPA_MORE,
        )
    else:
        plan = plan_space(selectable, inp.space)
        count = len(items)
        # спека §2.2: «Вернуть все N» при N ≤ restoreAllMax и если известные
        # размеры помещаются (неизвестный размер = 0); иначе «Выбрать и вернуть».
        many = len(selectable) > RESTORE_ALL_MAX or plan.verdict == SPACE_OVER
        links = ["Найти другое приложение", "Файлы IPA", "Apple ID"]
        if many:
            state = STATE_MANY
            parts = []
            if offloaded:
                parts.append(f"<b>{len(offloaded)} {plural(len(offloaded), 'сгруженное', 'сгруженных', 'сгруженных')}</b>")
            if removed:
                parts.append(f"{len(removed)} {plural(len(removed), 'удалённое', 'удалённых', 'удалённых')} из App Store")
            if region:
                parts.append(f"{len(region)} {plural(len(region), 'недоступно', 'недоступны', 'недоступны')} в регионе")
            lead = (", ".join(parts[:-1]) + " и " + parts[-1]) if len(parts) > 1 else (parts[0] if parts else "")
            lead += "."
            if plan.verdict == SPACE_OVER:
                lead += f" Все сразу не поместятся: на {noun} свободно {format_size(inp.space.free_bytes, floor=True)}."
            line1, line2 = missing_caption(count, noun)
            view.update(
                number=count,
                word=line1,
                word2=line2,
                a11y=missing_a11y(count),
                lead=lead,
                cta="Выбрать и вернуть",
                hint="Сначала покажем список, отметите нужные.",
                links=links,
                pages=phone_pages(count),
            )
        else:
            state = STATE_MISSING
            n = len(selectable)
            names = join_names([item.label for item in selectable])
            if removed and not offloaded:
                if n == 1:
                    lead = f"<b>{names}</b> пропало с телефона, его больше нет в App Store. Его можно вернуть."
                else:
                    lead = f"<b>{names}</b> пропали с телефона, их больше нет в App Store. Их можно вернуть."
            elif offloaded and not removed:
                if n == 1:
                    lead = f"<b>{names}</b> сгружено: иконка на месте, а самого приложения нет. Его можно вернуть."
                else:
                    lead = f"<b>{names}</b> сгружены: иконки на месте, а самих приложений нет. Их можно вернуть."
            else:
                lead = f"<b>{names}</b> пропали с телефона или сгружены. Их можно вернуть."
            if plan.unknown_count:
                hint = "Телефон не отключайте. Размер части приложений узнаем при скачивании."
            else:
                hint = f"{_minutes(n)}. Телефон не отключайте."
            line1, line2 = missing_caption(n, noun)
            view.update(
                number=n,
                word=line1,
                word2=line2,
                a11y=missing_a11y(n),
                lead=lead,
                cta=f"Вернуть все {n}" if n > 1 else "Вернуть",
                hint=hint,
                links=links,
            )
    view["state"] = state
    view["tiles"] = phone_tiles(inp, state) if inp.connected else []
    slots = sum(1 for tile in view["tiles"] if tile["kind"] == "slot")
    view["phoneA11y"] = (
        f"Экран {noun}: {slots} {plural(slots, 'пустое место', 'пустых места', 'пустых мест')}"
        if inp.connected
        else f"Экран {noun} выключен"
    )
    # Only links that lead to something that exists (Евгений); the rest are hidden.
    view["links"] = [name for name in view.get("links") or [] if link_action(str(name))]
    return view
