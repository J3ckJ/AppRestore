"""4b window logic without Qt: formatting, catalog, selection, space, queue, flow, home."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from apprestore_core.models import MissingApp, OffloadedApp
from apprestore_gui.ui4b import catalog, formatting, home, onboarding, scan, selection, space
from apprestore_gui.ui4b.catalog import (
    ACTION_IPA,
    ACTION_NONE,
    ACTION_OFFLOADED,
    ACTION_STORE,
    GROUP_OFFLOADED,
    GROUP_REGION,
    GROUP_REMOVED,
    RestoreItem,
)
from apprestore_gui.ui4b.flow import RestoreFlow
from apprestore_gui.ui4b.queue import CURRENT, DONE, FAILED, WAIT, RestoreQueue

MB = 1000 * 1000
GB = 1000 * MB


def item(key: str, group: str = GROUP_OFFLOADED, size: int | None = 100, **kw) -> RestoreItem:
    action = {GROUP_OFFLOADED: ACTION_OFFLOADED, GROUP_REMOVED: ACTION_STORE, GROUP_REGION: ACTION_NONE}[group]
    return RestoreItem(
        key=key,
        name=kw.pop("name", key),
        group=group,
        action=kw.pop("action", action),
        size_bytes=size * MB if size is not None else None,
        store_id=kw.pop("store_id", key if group != GROUP_OFFLOADED else ""),
        **kw,
    )


# -- formatting ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("n", "word"),
    [(1, "приложение"), (2, "приложения"), (4, "приложения"), (5, "приложений"), (11, "приложений"),
     (21, "приложение"), (22, "приложения"), (112, "приложений"), (519, "приложений")],
)
def test_plural(n: int, word: str) -> None:
    assert formatting.apps_word(n) == word


def test_format_size() -> None:
    assert formatting.format_size(None) == "—"
    assert formatting.format_size(0) == "—"
    assert formatting.format_size(351 * MB) == "351 МБ"
    assert formatting.format_size(2 * GB) == "2,0 ГБ"
    assert formatting.format_size(1_100 * MB) == "1,1 ГБ"
    assert formatting.format_size(3_867 * MB) == "3,9 ГБ"
    assert formatting.format_size(3_867 * MB, floor=True) == "3,8 ГБ"
    assert formatting.format_size(128 * GB) == "128 ГБ"


def test_join_names() -> None:
    assert formatting.join_names(["Сбер"]) == "Сбер"
    assert formatting.join_names(["Сбер", "Т-Банк", "ВТБ", "Альфа"]) == "Сбер, Т-Банк, ВТБ и Альфа"
    assert formatting.join_names(list("abcdef")) == "a, b, c и ещё 3"


# -- catalog --------------------------------------------------------------------


def test_build_items_groups_actions_and_dedupe(tmp_path: Path) -> None:
    ipa = tmp_path / "a.ipa"
    ipa.write_bytes(b"x" * 5000)
    offloaded = [
        OffloadedApp("com.a", "A", "1.0", store_id="11"),
        OffloadedApp("com.b", "B", "1.0", local_ipa=ipa),
    ]
    missing = [
        MissingApp("com.a", "A again", store_id="11"),  # already offloaded
        MissingApp("com.sber", "Сбербанк Онлайн", store_id="492224193"),
        MissingApp("com.file", "Только файл", local_ipa=ipa),
        MissingApp("com.none", "Ни номера, ни файла"),
        MissingApp("com.region", "Brawl", store_id="1229016807"),
    ]
    items = catalog.build_items(
        offloaded,
        missing,
        region_blocked={"1229016807"},
        region_name="России",
        short_names={"492224193": "Сбер"},
    )
    by = {it.key: it for it in items}
    assert set(by) == {"com.a", "com.b", "com.sber", "com.file", "com.region"}
    assert by["com.a"].action == ACTION_OFFLOADED and by["com.a"].size_bytes is None
    assert by["com.b"].size_bytes == 5000  # IPA size as a lower bound
    assert by["com.sber"].group == GROUP_REMOVED and by["com.sber"].action == ACTION_STORE
    assert by["com.sber"].label == "Сбер"
    assert by["com.file"].action == ACTION_IPA and by["com.file"].ipa_path == str(ipa)
    assert by["com.region"].group == GROUP_REGION and not by["com.region"].selectable
    assert by["com.region"].note == "Нет в App Store России"


# -- selection ------------------------------------------------------------------


def make_selection() -> selection.Selection:
    items = [
        item("sber", GROUP_REMOVED, 351, name="Сбербанк Онлайн", developer="Сбербанк"),
        item("vtb", GROUP_REMOVED, None, name="ВТБ Онлайн"),
        item("brawl", GROUP_REGION, 2000, name="Brawl Stars"),
        item("gmail", GROUP_OFFLOADED, 756, name="Gmail", developer="Google LLC", store_id="422689480"),
        item("maps", GROUP_OFFLOADED, 494, name="Google Maps", developer="Google LLC"),
        item("tiktok", GROUP_OFFLOADED, 1100, name="TikTok"),
    ]
    return selection.Selection(items, space=space.DeviceSpace(128 * GB, 2 * GB))


def test_default_selection_is_removed_apps_only() -> None:
    sel = make_selection()
    assert {it.key for it in sel.selected_items()} == {"sber", "vtb"}


def test_region_items_cannot_be_selected() -> None:
    sel = make_selection()
    assert sel.toggle("brawl") is False
    sel.select_visible(True)
    assert not sel.is_selected("brawl")
    assert sel.group_state(GROUP_REGION) == selection.CHECK_DISABLED


def test_group_toggle_tristate() -> None:
    sel = make_selection()
    assert sel.group_state(GROUP_OFFLOADED) == selection.CHECK_OFF
    sel.toggle("gmail")
    assert sel.group_state(GROUP_OFFLOADED) == selection.CHECK_MIXED
    sel.toggle_group(GROUP_OFFLOADED)
    assert sel.group_state(GROUP_OFFLOADED) == selection.CHECK_ON
    sel.toggle_group(GROUP_OFFLOADED)
    assert sel.group_state(GROUP_OFFLOADED) == selection.CHECK_OFF


def test_sort_by_size_puts_unknown_last_and_by_name() -> None:
    sel = make_selection()
    names = [r["name"] for r in sel.rows() if r["kind"] == "app" and r["group"] == GROUP_REMOVED]
    assert names == ["Сбербанк Онлайн", "ВТБ Онлайн"]
    offl = [r["key"] for r in sel.rows() if r["kind"] == "app" and r["group"] == GROUP_OFFLOADED]
    assert offl == ["tiktok", "gmail", "maps"]
    sel.set_sort(selection.SORT_NAME)
    offl = [r["key"] for r in sel.rows() if r["kind"] == "app" and r["group"] == GROUP_OFFLOADED]
    assert offl == ["gmail", "maps", "tiktok"]


def test_search_by_name_developer_store_id_and_link() -> None:
    sel = make_selection()
    sel.set_query("google")
    keys = [r["key"] for r in sel.rows() if r["kind"] == "app"]
    assert keys == ["gmail", "maps"]
    header = next(r for r in sel.rows() if r["kind"] == "header")
    assert header["countText"] == "2 из 3"
    assert header["action"] == "Выбрать найденные"
    maps = next(r for r in sel.rows() if r["key"] == "maps")
    assert '<span style="background-color:#f6ebe4">Google</span> Maps' == maps["nameHtml"]
    assert sel.search_note() == "Совпадения по названию и разработчику (Google LLC)."
    sel.set_query("422689480")
    assert [r["key"] for r in sel.rows() if r["kind"] == "app"] == ["gmail"]
    sel.set_query("https://apps.apple.com/ru/app/gmail/id422689480")
    assert [r["key"] for r in sel.rows() if r["kind"] == "app"] == ["gmail"]


def test_select_all_applies_to_what_is_visible() -> None:
    sel = make_selection()
    sel.set_query("google")
    sel.select_visible(True)
    assert {it.key for it in sel.selected_items()} == {"sber", "vtb", "gmail", "maps"}
    sel.select_visible(False)
    assert {it.key for it in sel.selected_items()} == {"sber", "vtb"}


def test_rail_counts_and_filter() -> None:
    sel = make_selection()
    rail = {r["key"]: r for r in sel.rail_rows()}
    assert [r["key"] for r in sel.rail_rows()] == ["all", GROUP_REMOVED, GROUP_OFFLOADED, GROUP_REGION]
    assert rail["all"]["count"] == 6 and rail["all"]["sub"] == "выбрано 2"
    assert rail[GROUP_REGION]["sub"] == "нельзя выбрать"
    sel.set_rail(GROUP_OFFLOADED)
    assert {r["group"] for r in sel.rows()} == {GROUP_OFFLOADED}


def test_marks_survive_new_list_and_drop_vanished_keys() -> None:
    sel = make_selection()
    sel.toggle("gmail")
    sel.set_items([it for it in sel.items if it.key != "sber"])
    assert {it.key for it in sel.selected_items()} == {"vtb", "gmail"}


def test_html_in_names_is_escaped() -> None:
    sel = selection.Selection([item("x", name="<b>evil</b>")])
    row = next(r for r in sel.rows() if r["kind"] == "app")
    assert "<b>" not in str(row["nameHtml"])


# -- space ----------------------------------------------------------------------


def test_parse_disk_usage_lockdown_and_afc() -> None:
    got = space.parse_disk_usage({"TotalDiskCapacity": 128 * GB, "AmountDataAvailable": 6_850_000_000})
    assert got == space.DeviceSpace(128 * GB, 6_850_000_000)
    afc = space.parse_disk_usage({"FSTotalBytes": "1000", "FSFreeBytes": "400"})
    assert afc == space.DeviceSpace(1000, 400)
    assert space.parse_disk_usage(None) == space.UNKNOWN_SPACE
    assert space.parse_disk_usage({"AmountDataAvailable": "x"}).free_bytes is None
    # nonsense: free above total
    assert space.parse_disk_usage({"TotalDiskCapacity": 10, "AmountDataAvailable": 20}).free_bytes is None


def test_query_device_space_uses_lockdown_get(monkeypatch: pytest.MonkeyPatch) -> None:
    import apprestore_core.frozen as frozen

    monkeypatch.setattr(frozen, "is_frozen", lambda: False)
    calls: list[list[str]] = []

    class Runner:
        def run(self, command, check=True, timeout=None):
            calls.append(list(command))
            return SimpleNamespace(stdout=json.dumps({"TotalDiskCapacity": 64 * GB, "AmountDataAvailable": GB}))

    tools = SimpleNamespace(
        runner=Runner(),
        _pymobiledevice3_only_in_user_site=lambda: False,
        _pymobiledevice3_cmd=lambda *parts: ["pmd3", *parts],
    )
    assert space.query_device_space(tools, "UDID") == space.DeviceSpace(64 * GB, GB)
    assert calls == [["pmd3", "lockdown", "get", "--udid", "UDID", "--domain", "com.apple.disk_usage"]]


def test_query_device_space_failure_is_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    import apprestore_core.frozen as frozen

    monkeypatch.setattr(frozen, "is_frozen", lambda: False)

    class Runner:
        def run(self, *a, **k):
            raise RuntimeError("device locked")

    tools = SimpleNamespace(
        runner=Runner(), _pymobiledevice3_only_in_user_site=lambda: False, _pymobiledevice3_cmd=lambda *p: list(p)
    )
    assert space.query_device_space(tools, "UDID") == space.UNKNOWN_SPACE
    assert space.query_device_space(tools, "") == space.UNKNOWN_SPACE


def test_plan_space_verdicts() -> None:
    free = space.DeviceSpace(128 * GB, 1 * GB)
    assert space.plan_space([], free).verdict == space.SPACE_EMPTY
    fits = space.plan_space([item("a", size=300), item("b", size=400)], free)
    assert fits.verdict == space.SPACE_FITS and not fits.blocked
    assert fits.total_text() == "Выбрано 2 · от 700 МБ"
    partly = space.plan_space([item("a", size=300), item("b", size=None)], free)
    assert partly.verdict == space.SPACE_FITS_PARTLY_KNOWN and not partly.blocked
    over = space.plan_space([item("a", size=800), item("b", size=500)], free)
    assert over.verdict == space.SPACE_OVER and over.blocked
    assert over.shortfall_bytes == 300 * MB
    assert over.warning()[0] == "Не поместится: не хватает 300 МБ."
    unknown = space.plan_space([item("a", size=300)], space.UNKNOWN_SPACE)
    assert unknown.verdict == space.SPACE_UNKNOWN and not unknown.blocked
    assert unknown.free_text() == "свободное место не удалось проверить"


# -- scan counter ----------------------------------------------------------------


def test_scan_counter_caption_and_eta() -> None:
    now = [100.0]
    counter = scan.ScanCounter(clock=lambda: now[0])
    counter.start(640)
    now[0] += 30
    counter.update(218, 640)
    assert counter.caption() == "Проверено 218 из 640"
    assert counter.eta() == "ещё около минуты"
    assert counter.fraction == pytest.approx(218 / 640)
    counter.finish()
    assert counter.fraction == 1.0 and counter.caption() == "Проверено 640 из 640"
    unknown = scan.ScanCounter(clock=lambda: 0.0)
    unknown.update(12, None)
    assert unknown.caption() == "Проверено 12" and unknown.eta() == ""


# -- queue & flow ---------------------------------------------------------------


class Backend:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []

    def restore_offloaded(self, keys):
        self.calls.append(("restore_offloaded", list(keys)))

    def install_store(self, store_id):
        self.calls.append(("install_store", store_id))

    def install_ipa(self, path):
        self.calls.append(("install_ipa", path))


def test_flow_blocks_when_space_is_over() -> None:
    backend = Backend()
    flow = RestoreFlow(backend)
    plan = flow.begin([item("big", size=5000)], space.DeviceSpace(128 * GB, 1 * GB))
    assert plan.blocked and backend.calls == [] and not flow.running


def test_flow_goes_through_gated_store_install_then_offloaded_batch() -> None:
    backend = Backend()
    flow = RestoreFlow(backend)
    items = [
        item("sber", GROUP_REMOVED, 351, store_id="492224193"),
        item("file", GROUP_REMOVED, 10, action=ACTION_IPA, ipa_path="/x.ipa"),
        item("com.a", GROUP_OFFLOADED, 100),
        item("com.b", GROUP_OFFLOADED, 100),
        item("brawl", GROUP_REGION, 100),  # never sent
    ]
    flow.begin(items, space.DeviceSpace(128 * GB, 10 * GB))
    assert backend.calls == [("install_store", "492224193")]
    flow.on_progress(40, "downloading 40 %")
    assert flow.queue.current().percent == 40
    flow.on_install_settled("492224193", True, "")
    assert backend.calls[-1] == ("install_ipa", "/x.ipa")
    flow.on_install_settled("/x.ipa", False, "Файл копии не найден.")
    assert backend.calls[-1] == ("restore_offloaded", ["com.a", "com.b"])
    flow.on_app_restored("com.a")
    assert len(backend.calls) == 3  # the batch is not sent twice
    flow.on_restore_settled("B: Нет связи")
    states = [e.state for e in flow.queue.entries]
    assert states == [DONE, FAILED, DONE, FAILED]
    assert flow.queue.entries[1].error == "Файл копии не найден."
    assert flow.queue.finished and not flow.running


def test_queue_rows_and_stop() -> None:
    queue = RestoreQueue()
    queue.start([item("a", GROUP_REMOVED, 351, name="Сбер"), item("b", GROUP_REMOVED, 302, name="Т-Банк")])
    queue.progress(40, "Скачиваем")
    rows = queue.rows()
    assert rows[0]["detail"] == "Скачиваем 40 %"
    assert rows[0]["stages"] == ["Скачивание 40 %", "Установка", "Готово"]
    assert rows[0]["percent"] == 40
    queue.progress(100, "Ставим на iPhone")
    rows = queue.rows()
    # спека: у установки нет числа
    assert rows[0]["state"] == CURRENT and rows[0]["detail"] == "Ставится на iPhone"
    assert rows[0]["stage"] == 1 and rows[0]["percent"] == -1
    assert rows[0]["stages"] == ["Скачано", "Установка", "Готово"]
    assert rows[1]["state"] == WAIT and rows[1]["right"] == "В очереди"
    queue.stop()
    assert not queue.active and queue.finished
    queue.settle("a", True)
    assert queue.current() is None  # stopped: nothing new starts


# -- home -------------------------------------------------------------------------


def removed4() -> list[RestoreItem]:
    return [
        item(k, GROUP_REMOVED, s, short_name=n)
        for k, s, n in (("1", 351, "Сбер"), ("2", 302, "Т-Банк"), ("3", None, "ВТБ"), ("4", 373, "Альфа"))
    ]


def test_home_missing_state_texts_and_tiles() -> None:
    view = home.home_view(
        home.HomeInput(
            connected=True, device_name="iPhone Марины", signed_in=True, items=removed4(),
            space=space.DeviceSpace(128 * GB, 6 * GB),
            phone_apps=[home.PhoneApp(f"app{i}") for i in range(12)],
        )
    )
    assert view["state"] == home.STATE_MISSING
    assert view["number"] == 4 and view["word"] == "приложения" and view["word2"] == "не хватает"
    assert view["cta"] == "Вернуть все 4"
    assert view["lead"].startswith("<b>Сбер, Т-Банк, ВТБ и Альфа</b> пропали с телефона")
    # ВТБ size unknown → no minutes promise, honest note
    assert view["hint"] == "Телефон не отключайте. Размер части приложений узнаем при скачивании."
    assert view["a11y"] == "Не хватает 4 приложений"
    kinds = [t["kind"] for t in view["tiles"]]
    assert len(kinds) == 16 and kinds[8:12] == ["slot"] * 4


def test_home_many_when_too_many_or_not_fitting() -> None:
    items = removed4() + [item(f"o{i}", GROUP_OFFLOADED, 100) for i in range(512)] + [item("r", GROUP_REGION, 1)]
    view = home.home_view(
        home.HomeInput(connected=True, signed_in=True, items=items, space=space.DeviceSpace(128 * GB, int(6.85 * GB)))
    )
    assert view["state"] == home.STATE_MANY and view["number"] == 517
    assert view["cta"] == "Выбрать и вернуть"
    assert "Все сразу не поместятся: на iPhone свободно 6,8 ГБ." in view["lead"]


def test_home_states() -> None:
    assert home.home_view(home.HomeInput(connected=False))["state"] == home.STATE_DISCONNECTED
    assert home.home_view(home.HomeInput(connected=True, loading=True))["state"] == home.STATE_LOADING
    assert home.home_view(home.HomeInput(connected=True))["state"] == home.STATE_EMPTY
    signin = home.home_view(home.HomeInput(connected=True, signed_in=False, items=removed4()))
    assert signin["state"] == home.STATE_SIGNIN and signin["cta"] == "Войти"
    region_items = removed4()[:3] + [item("4", GROUP_REGION, 373, short_name="Альфа")]
    region = home.home_view(home.HomeInput(connected=True, signed_in=True, items=region_items, region_name="Россия"))
    assert region["state"] == home.STATE_REGION
    assert region["number"] == 3 and region["word"] == "из 4" and region["cta"] == "Вернуть 3"
    assert region["over"] == "Альфа недоступна"
    queue = RestoreQueue()
    queue.start(removed4())
    queue.settle("1", True)
    installing = home.home_view(home.HomeInput(connected=True, signed_in=True, items=removed4(), queue=queue))
    assert installing["state"] == home.STATE_INSTALLING
    assert installing["number"] == 1 and installing["word"] == "из 4"
    for key in "234":
        queue.settle(key, True)
    done = home.home_view(home.HomeInput(connected=True, signed_in=True, items=removed4(), queue=queue))
    assert done["state"] == home.STATE_DONE and done["title"] == "Всё\nна месте"
    assert all(t["kind"] == "new" for t in done["tiles"] if t["name"] in ("Сбер", "Альфа"))


# -- onboarding --------------------------------------------------------------------


def test_onboarding_steps() -> None:
    ob = onboarding.Onboarding()
    assert ob.step(connected=False, signed_in=False, scan_done=False) == 1
    ob.started = True
    assert ob.step(connected=False, signed_in=False, scan_done=False) == 2
    assert ob.step(connected=True, signed_in=False, scan_done=False) == 3
    ob.apple_id_skipped = True
    assert ob.step(connected=True, signed_in=False, scan_done=False) == 4
    assert [s["state"] for s in ob.stepper(4)] == ["ok", "ok", "ok", "on"]
    assert ob.step(connected=True, signed_in=False, scan_done=True) == 0
    assert ob.finished


# -- sign in again: no auto-continue (Ника/Лена) -----------------------------------

class RecordingBackend:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []

    def restore_offloaded(self, keys):
        self.calls.append(("restore", list(keys)))

    def install_store(self, store_id):
        self.calls.append(("store", store_id))

    def install_ipa(self, path):
        self.calls.append(("ipa", path))


def _store_items(count: int = 3):
    from apprestore_gui.ui4b.fake_data import removed_items

    return removed_items()[:count]


def test_needs_signin_recognises_session_failures() -> None:
    from apprestore_gui.ui4b.flow import needs_signin

    # -128 is STORE_MISMATCH now: its own screen, not «Войдите заново»
    assert not needs_signin("Отказ -128")
    assert not needs_signin("failed to purchase item: Account Not In This Store")
    assert needs_signin("failed to get account: could not be found in the keyring")
    assert needs_signin("Сессия Apple ID истекла. Войдите заново.")
    assert needs_signin("password token is expired")
    assert not needs_signin("")
    assert not needs_signin("Недостаточно места на iPhone")
    assert not needs_signin("Код 1128 не найден")
    assert not needs_signin("license already exists")


def test_session_failure_drops_the_run_and_signin_does_not_resume() -> None:
    from apprestore_gui.ui4b.flow import RestoreFlow
    from apprestore_gui.ui4b.home import STATE_MISSING, STATE_SIGNIN, HomeInput, home_view
    from apprestore_gui.ui4b.space import DeviceSpace

    backend = RecordingBackend()
    flow = RestoreFlow(backend)
    items = _store_items()
    space = DeviceSpace(total_bytes=128 * 10**9, free_bytes=50 * 10**9)
    flow.observe_account(True, "in", False)
    flow.begin(items, space)
    assert backend.calls == [("store", items[0].store_id)]

    flow.on_install_settled(items[0].store_id, False, "Сессия Apple ID истекла. Войдите заново.")
    assert flow.needs_signin and not flow.running
    assert flow.queue.entries == []  # nothing kept to resume
    view = home_view(HomeInput(connected=True, signed_in=True, items=items, space=space,
                               relogin=flow.needs_signin))
    assert view["state"] == STATE_SIGNIN
    assert view["cta"] == "Войти заново"
    text = " ".join(str(view.get(k, "")) for k in ("over", "title", "lead", "fine", "hint"))
    assert "автомат" not in text.casefold() and "продолж" not in text.casefold()

    # Late signals from the dropped run change nothing.
    flow.on_install_settled(items[1].store_id, True, "")
    flow.on_progress(50, "Скачиваю")
    # «Войти заново» → 2FA → signed in again.
    flow.observe_account(True, "running", False)
    flow.observe_account(True, "need_code", False)
    flow.observe_account(True, "in", False)
    assert not flow.needs_signin
    assert backend.calls == [("store", items[0].store_id)]  # no new install by itself
    view = home_view(HomeInput(connected=True, signed_in=True, items=items, space=space,
                               queue=flow.queue if flow.queue.entries else None,
                               relogin=flow.needs_signin))
    assert view["state"] == STATE_MISSING
    assert view["cta"].startswith("Вернуть")

    # The user presses «Вернуть» again: a new run from the first app.
    flow.begin(items, space)
    assert backend.calls[-1] == ("store", items[0].store_id)


def test_expired_session_during_run_drops_it_and_first_signin_does_not_start() -> None:
    from apprestore_gui.ui4b.flow import RestoreFlow
    from apprestore_gui.ui4b.space import DeviceSpace

    backend = RecordingBackend()
    flow = RestoreFlow(backend)
    items = _store_items(2)
    space = DeviceSpace(total_bytes=128 * 10**9, free_bytes=50 * 10**9)
    flow.observe_account(True, "in", False)
    flow.begin(items, space)
    flow.observe_account(True, "in", True)  # QuickSession.sessionRelogin
    assert not flow.running and flow.needs_signin
    flow.observe_account(False, "out", True)
    flow.observe_account(True, "in", False)
    assert not flow.needs_signin and not flow.running
    assert len(backend.calls) == 1

    # Plain first sign-in from the «Войдите в Apple ID» state starts nothing either.
    backend2 = RecordingBackend()
    flow2 = RestoreFlow(backend2)
    flow2.observe_account(False, "out", False)
    flow2.observe_account(True, "in", False)
    assert backend2.calls == [] and not flow2.running


def test_restore_settled_with_session_error_drops_offloaded_batch() -> None:
    from apprestore_gui.ui4b.fake_data import offloaded_items
    from apprestore_gui.ui4b.flow import RestoreFlow
    from apprestore_gui.ui4b.space import DeviceSpace

    backend = RecordingBackend()
    flow = RestoreFlow(backend)
    items = offloaded_items()[:2]
    flow.begin(items, DeviceSpace(total_bytes=10**11, free_bytes=5 * 10**10))
    flow.on_restore_settled(f"{items[0].name}: Сессия Apple ID истекла. Войдите заново.")
    assert flow.needs_signin and flow.queue.entries == []


# -- спека Ники, часть 1 -------------------------------------------------------------

import math as _math
import re as _re
from pathlib import Path as _Path

_QML = _Path(__file__).resolve().parents[1] / "apprestore_gui" / "qml4b"


def _sized(n: int, size_mb: int = 100) -> list[RestoreItem]:
    return [item(str(i), GROUP_REMOVED, size_mb, short_name=f"App{i}") for i in range(n)]


def test_restore_all_max_matches_theme() -> None:
    text = (_QML / "theme" / "Theme.qml").read_text(encoding="utf-8")
    value = int(_re.search(r"restoreAllMax:\s*(\d+)", text).group(1))
    assert value == home.RESTORE_ALL_MAX == 12


def test_return_all_up_to_12_if_it_fits() -> None:
    roomy = space.DeviceSpace(128 * GB, 50 * GB)
    v12 = home.home_view(home.HomeInput(connected=True, signed_in=True, items=_sized(12), space=roomy))
    assert v12["state"] == home.STATE_MISSING and v12["cta"] == "Вернуть все 12"
    v13 = home.home_view(home.HomeInput(connected=True, signed_in=True, items=_sized(13), space=roomy))
    assert v13["state"] == home.STATE_MANY and v13["cta"] == "Выбрать и вернуть"
    assert v13["hint"] == "Сначала покажем список, отметите нужные."
    tight = space.DeviceSpace(128 * GB, 500 * 1000 * 1000)
    over = home.home_view(home.HomeInput(connected=True, signed_in=True, items=_sized(6), space=tight))
    assert over["cta"] == "Выбрать и вернуть"
    unknown = [item(str(i), GROUP_REMOVED, None, short_name=f"A{i}") for i in range(3)]
    v = home.home_view(home.HomeInput(connected=True, signed_in=True, items=unknown, space=tight))
    assert v["cta"] == "Вернуть все 3" and "узнаем при скачивании" in v["hint"]


def test_lead_one_and_many_names() -> None:
    roomy = space.DeviceSpace(128 * GB, 50 * GB)
    one = home.home_view(home.HomeInput(connected=True, signed_in=True, items=_sized(1), space=roomy))
    assert one["lead"] == "<b>App0</b> пропало с телефона, его больше нет в App Store. Его можно вернуть."
    six = home.home_view(home.HomeInput(connected=True, signed_in=True, items=_sized(6), space=roomy))
    assert six["lead"].startswith("<b>App0, App1, App2 и ещё 3</b> пропали с телефона")


def test_caption_goes_through_one_function(monkeypatch) -> None:
    from apprestore_gui.ui4b import formatting

    assert formatting.missing_caption(1) == ("приложение", "не хватает")
    assert formatting.missing_caption(21) == ("приложение", "не хватает")
    assert formatting.missing_caption(5) == ("приложений", "не хватает")
    monkeypatch.setattr(home, "missing_caption", lambda n, noun="iPhone": ("приложение", f"пропало с {noun}"))
    v = home.home_view(home.HomeInput(connected=True, signed_in=True, items=_sized(1), space=space.DeviceSpace(128 * GB, 50 * GB)))
    assert v["word2"] == "пропало с iPhone"


def test_short_names() -> None:
    from apprestore_gui.ui4b.catalog import short_name_of

    assert short_name_of("Сбер: банк и кошелёк") == "Сбер"
    assert short_name_of("Альфа — банк") == "Альфа"
    assert short_name_of("Т-Банк - деньги") == "Т-Банк"
    assert short_name_of("Т-Банк") == "Т-Банк"


def test_phone_tiles_follow_queue_kinds() -> None:
    items = removed4()
    queue = RestoreQueue()
    queue.start(items)
    queue.settle("1", True)
    queue.progress(64, "Скачиваем")
    v = home.home_view(home.HomeInput(connected=True, signed_in=True, items=items, queue=queue,
                                      phone_apps=[home.PhoneApp(f"a{i}") for i in range(12)]))
    by = {t["app"]: t for t in v["tiles"]}
    assert by["Сбер"]["kind"] == "new"
    assert by["Т-Банк"]["kind"] == "downloading" and by["Т-Банк"]["progress"] == 0.64
    assert by["ВТБ"]["kind"] == "waiting" and by["ВТБ"]["name"] == "Ожидание"
    queue.progress(100, "Ставим на iPhone")
    v = home.home_view(home.HomeInput(connected=True, signed_in=True, items=items, queue=queue))
    by = {t["app"]: t for t in v["tiles"]}
    assert by["Т-Банк"]["kind"] == "installing" and by["Т-Банк"]["progress"] == -1
    queue.settle("2", False, "ошибка")
    v = home.home_view(home.HomeInput(connected=True, signed_in=True, items=items, queue=queue))
    by = {t["app"]: t for t in v["tiles"]}
    assert by["Т-Банк"]["kind"] == "slot"


def test_tiles_have_no_letters_or_colours() -> None:
    items = removed4() + [item(f"o{i}", GROUP_OFFLOADED, 10) for i in range(20)]
    v = home.home_view(home.HomeInput(connected=True, signed_in=True, items=items,
                                      space=space.DeviceSpace(128 * GB, 50 * GB)))
    for tile in v["tiles"]:
        assert "mark" not in tile and "color" not in tile and "ink" not in tile
    assert v["pages"] == _math.ceil(24 / 24) == 1
    assert home.phone_pages(519) == 11 and home.phone_pages(30) == 2


def test_qml4b_uses_no_letter_placeholders() -> None:
    for path in _QML.rglob("*.qml"):
        text = path.read_text(encoding="utf-8")
        assert "placeholder_pixmap" not in text and "modelData.mark" not in text, path
        assert "5E5CE6" not in text.upper(), path
    theme = (_QML / "theme" / "Theme.qml").read_text(encoding="utf-8")
    assert _re.search(r"iconPlaceholder:\s*track", theme)
    assert '"#D3D1CA"' in theme



# -- -128 STORE_MISMATCH -------------------------------------------------------------

MISMATCH = "failed to purchase item with param 'STDQ': Account Not In This Store"


def _mismatch_flow():
    from apprestore_gui.ui4b.flow import RestoreFlow
    from apprestore_gui.ui4b.space import DeviceSpace

    backend = RecordingBackend()
    flow = RestoreFlow(backend)
    flow.observe_account(True, "in", False, "marina@example.com")
    return flow, backend, DeviceSpace(total_bytes=128 * 10**9, free_bytes=50 * 10**9)


def _view(flow, items):
    from apprestore_gui.ui4b.home import HomeInput, home_view

    return home_view(HomeInput(connected=True, signed_in=True, items=items,
                               queue=flow.queue if flow.queue.entries else None,
                               relogin=flow.needs_signin, store_problem=flow.store_problem,
                               store_problem_app=flow.store_problem_app))


def test_store_mismatch_first_time_offers_home_and_relogin() -> None:
    flow, backend, room = _mismatch_flow()
    items = _store_items()
    flow.begin(items, room)
    flow.on_install_settled(items[0].store_id, False, MISMATCH)
    assert not flow.running and flow.queue.entries == [] and not flow.needs_signin
    view = _view(flow, items)
    assert view["state"] == "store_mismatch"
    assert view["lead"] == "Магазин в текущем входе не совпадает со страной вашего Apple ID."
    assert view["cta"] == "На главный" and view["cta2"] == "Войти заново"
    text = " ".join(str(view[k]) for k in ("over", "title", "lead", "hint")).casefold()
    assert "vpn" not in text and "регион" not in text and "автомат" not in text
    # «На главный»: back to «Вернуть все», nothing started
    flow.dismiss_store_problem()
    assert _view(flow, items)["state"] == "missing"
    assert len(backend.calls) == 1


def test_store_mismatch_again_after_relogin_same_account_says_unavailable() -> None:
    flow, backend, room = _mismatch_flow()
    items = _store_items()
    flow.begin(items, room)
    flow.on_install_settled(items[0].store_id, False, MISMATCH)
    flow.store_relogin_requested()  # «Войти заново»
    flow.observe_account(True, "running", False, "marina@example.com")
    flow.observe_account(True, "in", False, "marina@example.com")
    assert flow.store_problem == "" and len(backend.calls) == 1  # nothing retried
    assert _view(flow, items)["state"] == "missing"
    flow.begin(items, room)  # the user presses «Вернуть» again
    flow.on_install_settled(items[0].store_id, False, '{"failureType":"-128"}')
    view = _view(flow, items)
    assert flow.store_problem == "unavailable"
    assert view["lead"] == "Приложение недоступно в магазине страны вашего Apple ID."
    assert view["cta"] == "На главный" and view["cta2"] == ""


def test_store_mismatch_after_switching_account_is_first_time_again() -> None:
    flow, backend, room = _mismatch_flow()
    items = _store_items()
    flow.begin(items, room)
    flow.on_install_settled(items[0].store_id, False, MISMATCH)
    flow.store_relogin_requested()
    flow.observe_account(False, "out", False, "")
    flow.observe_account(True, "in", False, "other@example.com")
    flow.begin(items, room)
    flow.on_install_settled(items[0].store_id, False, MISMATCH)
    assert flow.store_problem == "mismatch"
