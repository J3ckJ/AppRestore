"""The REAL 4b window (Main.qml, Restore4b, SessionSource) on a real display, over a
fake QuickSession shaped like Eugene's phone (10.10, build 1f34c49): ~547 offloaded
apps, a few missing ones (banks), installed apps (some nameless, one «94…»), and an
install that completes. No network, no device, no Apple ID.

    DISPLAY=:21 python scripts/ui4b_eugene_session.py OUTDIR

Walks: main (many) → «Выбрать и вернуть» → install Сбербанк → main after install;
«Найти» before typing; «Файлы IPA»; phone tiles. Saves one PNG per step.
"""

from __future__ import annotations

import sys
import tempfile
import os
import time
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QObject, Property, QTimer, Signal, Slot  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402
from PySide6.QtQml import QQmlApplicationEngine  # noqa: E402
from PySide6.QtQuickControls2 import QQuickStyle  # noqa: E402

from apprestore_core.models import MissingApp, OffloadedApp  # noqa: E402
from apprestore_gui.ui4b.fake_data import FakeIconBook  # noqa: E402
from apprestore_gui.ui4b.qt_bridge import Restore4b, SessionSource  # noqa: E402
from apprestore_gui.ui4b.space import DeviceSpace  # noqa: E402
from apprestore_gui.ui4b.window import load  # noqa: E402

GB = 1024 ** 3
UDID = "00008110-000A1B2C3D4E5F60"

_NAMES = ["Telegram", "WhatsApp", "Яндекс Go", "Яндекс Карты", "2ГИС: карты и навигатор", "Кинопоиск",
          "Wildberries", "OZON: товары", "Avito", "Самокат", "Delivery Club", "Госуслуги", "Мой МТС",
          "Мегафон", "Spotify", "YouTube", "Instagram", "Pinterest", "Duolingo", "Notion", "Zoom",
          "Skype", "Discord", "Steam", "Clash of Clans", "Brawl Stars", "Subway Surfers", "PUBG MOBILE",
          "Shazam", "VSCO", "Snapseed", "Lightroom", "CapCut", "Яндекс Музыка", "Литрес", "Букмейт"]


def offloaded() -> list[OffloadedApp]:
    apps = []
    for i in range(547):
        base = _NAMES[i % len(_NAMES)]
        name = base if i < len(_NAMES) else f"{base} {i // len(_NAMES) + 1}"
        if i % 61 == 7:
            name = f"com.vendor{i}.app"  # placeholder without a display name on the device
        apps.append(OffloadedApp(bundle_id=f"com.vendor{i}.app", name=name, version="1.0",
                                 static_size=(40 + i % 300) * 1024 * 1024, store_id=str(900000000 + i)))
    return apps


MISSING = [
    MissingApp("ru.sberbankmobile", "Сбербанк Онлайн", "17.6.1", "492224193", "known", source="known"),
    MissingApp("com.idamob.tinkoff.android", "Т-Банк", "?", "6755181069", "known", source="known"),
    MissingApp("ru.vtb24.mobilebanking", "ru.vtb24.mobilebanking", "?", "6749962031", "known", source="known"),
    MissingApp("ru.alfabank.mobile", "", "?", "6473656113", "known", source="known"),
]

PHONE = [
    {"bundleId": "ru.yandex.maps", "storeId": "313877526", "name": "Яндекс Карты"},
    {"bundleId": "ph.telegra.Telegraph", "storeId": "686449807", "name": "Telegram"},
    {"bundleId": "com.wildberries.ru", "storeId": "597880187", "name": "WB"},
    {"bundleId": "ru.ozon.app", "storeId": "407804998", "name": "OZON"},
    {"bundleId": "ru.yandex.taxi", "storeId": "472650686", "name": "Яндекс Go"},
    {"bundleId": "com.burbn.instagram", "storeId": "389801252", "name": "Instagram"},
    {"bundleId": "com.netflix.Netflix", "storeId": "363590051", "name": "Netflix"},
    {"bundleId": "com.github.stormbreaker.prod", "storeId": "1477376905", "name": "GitHub"},
    {"bundleId": "com.example.noname", "storeId": "", "name": "com.example.noname"},
    {"bundleId": "com.example.quiz94", "storeId": "", "name": "94 секунды: викторина"},
    {"bundleId": "com.supercell.magic", "storeId": "529479190", "name": "Clash"},
    {"bundleId": "com.spotify.client", "storeId": "324684580", "name": "Spotify"},
]


class FakeQuickSession(QObject):
    changed = Signal()
    appRestored = Signal(str)
    restoreSettled = Signal(str)
    installSettled = Signal(str, bool, str)
    installProgress = Signal(int, str)
    filesChanged = Signal()
    copySettled = Signal(str, bool, str)
    sessionChanged = Signal()
    purchasesChanged = Signal()
    filesNoteChanged = Signal(str, bool)

    def __init__(self) -> None:
        super().__init__()
        self.connected, self.loading = True, True  # first read in progress, like the real start
        self.deviceName, self.deviceNoun = "iPhone Евгения", "iPhone"
        self.signedIn, self.authPhase, self.sessionState = True, "in", "alive"
        self.sessionRelogin, self.sessionNote, self.authStatus = False, "", ""
        self.accountEmail = self.boundEmail = "person@example.com"
        self.purchasesBusy, self.purchasesProgress = False, ""
        self.purchases = [{"trackId": "492224193", "name": "СберБанк Онлайн", "bundleId": "ru.sberbankmobile"}]
        self._off = offloaded()
        self._missing = list(MISSING)
        self.phoneApps = list(PHONE)
        self.refreshes = 0
        self.filesNote, self.filesBusy = "", False
        lib = Path(tempfile.mkdtemp(prefix="ipa-lib-"))
        self.libraryFiles = []
        for name, bid, ver, mb in (("Сбербанк Онлайн", "ru.sberbankmobile", "17.6.1", 412),
                                   ("Telegram", "ph.telegra.Telegraph", "11.2", 168),
                                   ("Яндекс Карты", "ru.yandex.maps", "19.4.0", 301)):
            f = lib / f"{name}_{ver}.ipa"
            with f.open("wb") as fh:
                fh.truncate(mb * 1024 * 1024)
            self.libraryFiles.append({"path": str(f), "bundleId": bid, "storeId": "", "name": name, "detail": ver})
        tools = SimpleNamespace(account_country=lambda: "RU", license_preflight=lambda: None,
                                ipatool_capabilities=lambda: None)
        self.service = SimpleNamespace(core=SimpleNamespace(tools=tools), missing=lambda udid: list(self._missing))

    def current_udid(self) -> str:
        return UDID

    def offloaded_snapshot(self):
        return list(self._off)

    def loadPhone(self) -> None:
        self.filesChanged.emit()

    def loadPurchases(self) -> None:
        pass

    def loadLibrary(self) -> None:
        self.filesChanged.emit()

    def saveCopies(self, ids) -> None:
        self.filesNote = f"Сохраняем копию: {len(ids)}…"
        self.filesNoteChanged.emit(self.filesNote, True)

    def refresh(self) -> None:
        self.refreshes += 1
        self.changed.emit()

    def installStore(self, store_id: str) -> None:
        self.installProgress.emit(40, "Скачивание")

        def done() -> None:
            app = next((m for m in self._missing if m.store_id == store_id), None)
            if app is not None:  # the phone now has it
                self._missing.remove(app)
                self.phoneApps.append({"bundleId": app.bundle_id, "storeId": store_id, "name": "СберБанк"})
            self.installSettled.emit(store_id, True, "")

        QTimer.singleShot(1500, done)

    def restore(self, keys) -> None:
        def done() -> None:
            for key in keys:
                self._off = [a for a in self._off if f"off:{a.bundle_id}" != key]
                self.appRestored.emit(key)
            self.restoreSettled.emit("")

        QTimer.singleShot(1500, done)

    def installSaved(self, path: str) -> None:
        self.installSettled.emit("", True, "")

    def login(self, *_a) -> None: ...
    def submitCode(self, *_a) -> None: ...
    def cancelLogin(self) -> None: ...
    def signOut(self) -> None: ...


class OfflineSafeSource(SessionSource):
    """SessionSource as is; only the network/device reads are answered locally."""

    def _load_space(self, udid: str) -> None:
        self._spaceReady.emit(udid, DeviceSpace(free_bytes=40 * GB, total_bytes=128 * GB))

    def fresh_space(self) -> DeviceSpace:
        return DeviceSpace(free_bytes=40 * GB, total_bytes=128 * GB)

    def free_prices(self, store_ids):
        return {s: 0.0 for s in store_ids}

    def store_offers(self, store_ids):
        return {s: {"price": 0.0, "developer": ""} for s in store_ids}

    def store_statuses(self, store_ids):
        return {}

    def missing_patches(self):
        return ()

    def search_store(self, term, purchases):
        return []

    def _classify(self, apps):
        return {}

    def license_counts(self):
        return (0, 0)


def main() -> int:
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "screenshots/eugene-bugs")
    out.mkdir(parents=True, exist_ok=True)
    QQuickStyle.setStyle("Basic")
    app = QApplication([sys.argv[0]])
    session = FakeQuickSession()
    source = OfflineSafeSource(session)
    # app_names.display_names stand-in (no network): answers after 1 s, like a lookup
    from apprestore_gui.ui4b.names_lookup import NameLookup

    def fake_display_names(ids, *, bundle_ids=(), account_country=None, device_locale=None):
        time.sleep(1.0)
        book = {"6749962031": "Cириус", "6473656113": "Апгрейд — Умный помощник", "6755181069": "Spotluma"}
        return {k: book[k] for k in [*ids, *bundle_ids] if k in book}

    source._names = NameLookup(fake_display_names, notify=source._namesReady.emit)
    source.online = True
    controller = Restore4b(source, onboarded=True)
    # design concepts next to the repo checkout (only for the fake icon book); override with APPRESTORE_CONCEPTS
    concepts = Path(os.environ.get("APPRESTORE_CONCEPTS") or Path(__file__).resolve().parents[2] / "design" / "concepts")
    icons = FakeIconBook(concepts / "assets" / "icons", Path(tempfile.gettempdir()) / "ui4b-icons")
    engine = QQmlApplicationEngine()
    warnings: list[str] = []
    engine.warnings.connect(lambda ws: warnings.extend(w.toString() for w in ws))
    if not load(engine, controller, icons):
        print("QML failed", file=sys.stderr)
        return 1
    window = engine.rootObjects()[0]
    window.resize(1440, 900)
    window.show()
    log: list[str] = []

    def shot(name: str) -> None:
        window.grabWindow().save(str(out / name))
        h = controller.home
        log.append(f"{name}: state={h.get('state')} number={h.get('number')} title={h.get('title')!r} cta={h.get('cta')!r}")

    steps: list[tuple[int, object]] = []

    def at(ms: int, fn) -> None:
        steps.append((ms, fn))

    session.changed.emit()  # loading: «Смотрю, чего не хватает» (no button)

    def loaded() -> None:
        session.loading = False
        session.changed.emit()

    at(900, loaded)
    at(1500, lambda: shot("main-select-restore.png"))
    at(1600, lambda: shot("phone-tiles.png"))
    # install Сбербанк Онлайн the usual way (picker path → flow → installStore)
    at(1800, lambda: controller.installFound("492224193", "Сбербанк Онлайн"))
    at(2300, lambda: shot("main-installing.png"))
    at(5000, lambda: shot("main-after-install.png"))
    at(5100, lambda: log.append(f"cta row visible: {window.findChild(QObject, 'heroCtaRow').property('visible')}"))
    at(5200, lambda: controller.link("Найти другое приложение"))
    at(6000, lambda: shot("find-empty-popular.png"))
    at(6200, lambda: controller.finder.close())
    at(6400, lambda: controller.link("Файлы IPA"))
    at(7400, lambda: shot("ipa-files.png"))
    at(7600, lambda: controller.closeFiles())
    at(7800, lambda: shot("phone-tiles-names.png"))
    at(8000, lambda: controller.link("Найти другое приложение"))
    at(8800, lambda: shot("find-empty-popular-33.png"))
    at(9000, app.quit)
    for ms, fn in steps:
        QTimer.singleShot(ms, fn)
    app.exec()
    print("\n".join(log))
    print("refreshes after run:", session.refreshes)
    print("warnings:", len(warnings))
    for w in warnings[:20]:
        print("  ", w)
    del engine
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
