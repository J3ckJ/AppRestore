"""Which hero the 4b home screen shows, its texts and the phone tiles.

Pure function of the inputs; the QML only lays out what :func:`home_view`
returns.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from apprestore_gui.ui4b.catalog import (
    ACTION_STORE,
    GROUP_OFFLOADED,
    GROUP_REGION,
    GROUP_REMOVED,
    RestoreItem,
)
from apprestore_gui.ui4b.formatting import apps_word, format_size, join_names, plural
from apprestore_gui.ui4b.queue import CURRENT, DONE, FAILED, RestoreQueue
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

#: Up to this many apps the home screen offers «Вернуть все N» directly.
DIRECT_LIMIT = 8
PHONE_TILES = 16
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


def _minutes(count: int) -> str:
    minutes = max(1, int(round(count * MINUTES_PER_APP)))
    return f"Около {minutes} {plural(minutes, 'минуты', 'минут', 'минут')}"


def _tile(name: str, store_id: str, bundle_id: str, kind: str, label: str = "") -> dict[str, object]:
    return {
        "name": label or name,
        "storeId": store_id,
        "bundleId": bundle_id,
        # installed | cloud | slot | done | current | wait | na
        "kind": kind,
    }


def phone_tiles(inp: HomeInput, state: str) -> list[dict[str, object]]:
    """16 icons: other apps around, the missing ones as dashed slots in row 3."""

    queue_state: dict[str, str] = {}
    if inp.queue is not None:
        for entry in inp.queue.entries:
            queue_state[entry.item.key] = entry.state
    targets: list[dict[str, object]] = []
    others: list[dict[str, object]] = []
    for item in inp.items:
        if item.group == GROUP_OFFLOADED and item.key not in queue_state:
            others.append(_tile(item.label, item.store_id, item.bundle_id, "cloud"))
            continue
        if item.group == GROUP_REGION:
            kind = "na"
            label = "Недоступна"
        else:
            status = queue_state.get(item.key, "")
            if state == STATE_DONE or status == DONE:
                kind, label = "done", ""
            elif status == CURRENT:
                kind, label = "current", ""
            elif status in ("wait",) and state == STATE_INSTALLING:
                kind, label = "wait", "Ожидание"
            elif status == FAILED:
                kind, label = "slot", ""
            else:
                kind, label = "slot", ""
        targets.append(_tile(item.label, item.store_id, item.bundle_id, kind, label or item.label))
    installed = [_tile(app.name, app.store_id, app.bundle_id, "installed") for app in inp.phone_apps]
    around = installed + others
    head = around[:8]
    row = targets[:4]
    tail = around[8 : 8 + (PHONE_TILES - len(head) - len(row))]
    tiles = head + row + tail
    if len(tiles) < PHONE_TILES and len(targets) > 4:
        tiles += targets[4 : 4 + PHONE_TILES - len(tiles)]
    return tiles[:PHONE_TILES]


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
        "ctaSecondary": False,
        "hint": "",
        "fine": "",
        "links": [],
        "steps": [],
        "queue": [],
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
    elif queue is not None and queue.active:
        state = STATE_INSTALLING
        total = len(queue.entries)
        view.update(
            over="Возвращаю",
            number=queue.done_count,
            word=f"из {total}",
            word2=f"уже на {noun}",
            queue=queue.rows(noun),
            fine="Иконка на телефоне может появиться на минуту позже. "
            "Это нормально: ошибки нет, просто подождите.",
            links=["Остановить", "Журнал"],
        )
    elif queue is not None and queue.finished and not inp.done_dismissed:
        state = STATE_DONE
        ok = [entry.item.label for entry in queue.entries if entry.state == DONE]
        bad = queue.failed
        if bad and not ok:
            title = "Не получилось"
        elif bad:
            title = "Почти всё\nна месте"
        else:
            title = "Всё\nна месте"
        lead = ""
        if ok:
            lead = f"<b>{join_names(ok)}</b> снова на телефоне. Вернувшиеся приложения отмечены синей точкой, как обычно в iOS."
        if bad:
            names = join_names([entry.item.label for entry in bad])
            reason = bad[0].error if len(bad) == 1 and bad[0].error else "подробности в журнале"
            lead = (lead + " " if lead else "") + f"Не вернулись: <b>{names}</b> ({reason})."
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
            links=["Есть файл IPA", "Почему это безопасно"],
        )
    elif region and len(selectable) <= DIRECT_LIMIT:
        state = STATE_REGION
        names_ok = join_names([item.label for item in selectable])
        names_na = join_names([item.label for item in region])
        where = inp.region_name or "вашего региона"
        verb = "недоступна" if len(region) == 1 else "недоступны"
        view.update(
            over=f"{names_na} {verb}",
            number=len(selectable),
            word=f"из {len(items)}",
            word2="можно вернуть",
            lead=(f"<b>{names_ok}</b> вернутся как обычно. " if names_ok else "")
            + f"{names_na} нет в App Store региона вашего Apple ID ({where}), "
            f"и Apple не отдаёт {'его' if len(region) == 1 else 'их'} этой учётной записи.",
            cta=f"Вернуть {len(selectable)}" if selectable else "",
            links=["Есть файл IPA", "Войти с другим Apple ID", "Подробнее"],
        )
    else:
        plan = plan_space(selectable, inp.space)
        count = len(items)
        many = len(selectable) > DIRECT_LIMIT or plan.verdict == SPACE_OVER
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
            view.update(
                number=count,
                word=apps_word(count),
                word2="не хватает",
                lead=lead,
                cta="Выбрать и вернуть",
                hint="Сначала покажем список, отметите нужные.",
            )
        else:
            state = STATE_MISSING
            names = join_names([item.label for item in selectable])
            if removed and not offloaded:
                lead = f"<b>{names}</b> пропали с телефона, их больше нет в App Store. Их можно вернуть."
            elif offloaded and not removed:
                lead = f"<b>{names}</b> сгружены: иконки на месте, а самих приложений нет. Их можно вернуть."
            else:
                lead = f"<b>{names}</b> пропали с телефона или сгружены. Их можно вернуть."
            n = len(selectable)
            view.update(
                number=n,
                word=apps_word(n),
                word2="не хватает",
                lead=lead,
                cta=f"Вернуть все {n}" if n > 1 else "Вернуть",
                hint=f"{_minutes(n)}. Телефон не отключайте.",
                links=["Найти другое приложение", "Файлы IPA", "Apple ID"],
            )
    view["state"] = state
    view["tiles"] = phone_tiles(inp, state) if inp.connected else []
    return view
