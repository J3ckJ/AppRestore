"""Fixed data for 4b screenshots and tests (no device, no network, no Apple ID).

Realistic public apps; the device is "iPhone Марины" — nobody's real data.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import Property, QObject, QUrl, Signal, Slot

from apprestore_gui.ui4b.catalog import (
    ACTION_NONE,
    ACTION_OFFLOADED,
    ACTION_STORE,
    GROUP_OFFLOADED,
    GROUP_REGION,
    GROUP_REMOVED,
    RestoreItem,
)
from apprestore_gui.ui4b.home import PhoneApp
from apprestore_gui.ui4b.qt_bridge import SourceBase
from apprestore_gui.ui4b.space import DeviceSpace

MB = 1000 * 1000
GB = 1000 * MB

#: (store id, name, short, developer, size MB or None)
REMOVED = (
    ("492224193", "Сбербанк Онлайн", "Сбер", "Сбербанк", 351),
    ("455652438", "Тинькофф (Т-Банк)", "Т-Банк", "Тинькофф Банк", 302),
    ("472951966", "ВТБ Онлайн", "ВТБ", "ВТБ", None),
    ("353127685", "Альфа-Банк", "Альфа", "Альфа-Банк", 373),
)
REGION = (
    ("1229016807", "Brawl Stars", "Brawl", "Supercell", 2000),
    ("529479190", "Clash of Clans", "Clash", "Supercell", 794),
    ("1094591345", "Pokémon GO", "Pokémon", "Niantic", 430),
)
OFFLOADED = (
    ("835599320", "TikTok", "TikTok", "TIKTOK", 1100),
    ("313877526", "Яндекс Карты и Навигатор", "Карты", "Яндекс", 916),
    ("1500855883", "CapCut", "CapCut", "BYTEDANCE", 825),
    ("422689480", "Gmail", "Gmail", "Google", 756),
    ("1232780281", "Notion", "Notion", "Notion Labs", 751),
    ("597880187", "WILDBERRIES", "WB", "Wildberries", 721),
    ("472650686", "Яндекс Go", "Яндекс Go", "Яндекс", 624),
    ("570060128", "Duolingo", "Duolingo", "Duolingo, Inc", 515),
    ("284882215", "Facebook", "Facebook", "Meta Platforms", 514),
    ("585027354", "Google Maps", "Google Maps", "Google", 494),
    ("460177396", "Twitch", "Twitch", "Twitch Interactive", 493),
    ("507874739", "Google Drive", "Drive", "Google", 467),
    ("535886823", "Google Chrome", "Chrome", "Google", 428),
    ("544007664", "YouTube", "YouTube", "Google", 389),
    ("686449807", "Telegram Messenger", "Telegram", "Telegram FZ-LLC", 312),
    ("407804998", "OZON", "OZON", "Ozon", 298),
    ("6448311069", "ChatGPT", "ChatGPT", "OpenAI", 272),
    ("546505307", "Zoom Workplace", "Zoom", "Zoom", 260),
    ("429047995", "Pinterest", "Pinterest", "Pinterest", 240),
)
#: Phone order of the concept's "main500" screen (offloaded tiles around the slots).
MANY_PHONE_ORDER = (
    "313877526", "686449807", "597880187", "407804998", "544007664", "6448311069",
    "422689480", "535886823", "570060128", "546505307", "1232780281", "429047995",
)
#: Installed apps around the slots on the small "missing" screen.
PHONE = (
    ("313877526", "Карты"), ("686449807", "Telegram"), ("597880187", "WB"), ("407804998", "OZON"),
    ("472650686", "Яндекс Go"), ("389801252", "Instagram"), ("363590051", "Netflix"), ("1477376905", "GitHub"),
    ("529479190", "Clash"), ("324684580", "Spotify"), ("564177498", "ВК"), ("310633997", "WhatsApp"),
)
_FILLER = (
    "Авито", "2ГИС", "Кинопоиск", "Okko", "Литрес", "Самокат", "Купер", "Госуслуги", "Дзен",
    "Яндекс Музыка", "Звук", "HeadHunter", "Учи.ру", "Lamoda", "Афиша", "Туту", "Аэрофлот",
    "Пятёрочка", "Перекрёсток", "ВкусВилл", "Delivery Club", "Мегамаркет", "М.Видео", "DNS",
)


def removed_items() -> list[RestoreItem]:
    return [
        RestoreItem(
            key=f"store:{sid}", name=name, short_name=short, developer=dev, group=GROUP_REMOVED,
            action=ACTION_STORE, store_id=sid, size_bytes=size * MB if size else None,
        )
        for sid, name, short, dev, size in REMOVED
    ]


def region_items() -> list[RestoreItem]:
    return [
        RestoreItem(
            key=f"store:{sid}", name=name, short_name=short, developer=dev, group=GROUP_REGION,
            action=ACTION_NONE, store_id=sid, size_bytes=size * MB, note="Нет в App Store России",
        )
        for sid, name, short, dev, size in REGION
    ]


def offloaded_items(count: int = len(OFFLOADED)) -> list[RestoreItem]:
    items = [
        RestoreItem(
            key=f"bundle.{sid}", name=name, short_name=short, developer=dev, group=GROUP_OFFLOADED,
            action=ACTION_OFFLOADED, store_id=sid, bundle_id=f"bundle.{sid}", size_bytes=size * MB,
        )
        for sid, name, short, dev, size in OFFLOADED
    ]
    n = 0
    while len(items) < count:
        name = _FILLER[n % len(_FILLER)] + ("" if n < len(_FILLER) else f" {n // len(_FILLER) + 1}")
        size = (230 - (n % 180)) * MB
        items.append(
            RestoreItem(
                key=f"bundle.filler.{n}", name=name, developer="", group=GROUP_OFFLOADED,
                action=ACTION_OFFLOADED, bundle_id=f"bundle.filler.{n}", size_bytes=size,
            )
        )
        n += 1
    return items[:count]


class FakeSource(SourceBase):
    """Scenario-driven source; actions only record what would be called."""

    def __init__(self, scenario: str = "missing") -> None:
        super().__init__()
        self.calls: list[tuple[str, object]] = []
        self.connected = True
        self.loading = False
        self.device_name = "iPhone Марины"
        self.noun = "iPhone"
        self.signed_in = True
        self.region_name = "Россия"
        self._items: list[RestoreItem] = []
        self._phone: list[PhoneApp] = []
        self._space = DeviceSpace(total_bytes=128 * GB, free_bytes=int(6.85 * GB))
        self._scan: tuple[int, int | None, bool] = (0, None, True)
        self.set_scenario(scenario)

    def set_scenario(self, scenario: str) -> None:
        self.scenario = scenario
        self.connected = scenario != "disconnected"
        self.signed_in = scenario != "signin"
        self.relogin = scenario == "relogin"
        phone = [PhoneApp(name, store_id=sid) for sid, name in PHONE]
        if scenario in ("missing", "installing", "done", "disconnected", "signin", "relogin"):
            self._items = removed_items()
            self._phone = phone
        elif scenario == "region":
            alfa = removed_items()[3]
            self._items = removed_items()[:3] + [
                replace(alfa, group=GROUP_REGION, action=ACTION_NONE, note="Нет в App Store России")
            ]
            self._phone = phone
        elif scenario in ("many", "picker", "picker-search", "picker-nospace"):
            self._items = removed_items() + region_items() + offloaded_items(512)
            order = {sid: i for i, sid in enumerate(MANY_PHONE_ORDER)}
            self._items.sort(key=lambda it: (it.group != GROUP_OFFLOADED, order.get(it.store_id, 999)))
            self._phone = []
        elif scenario == "empty":
            self._items = []
            self._phone = phone
        elif scenario.startswith("onboarding"):
            self._items = removed_items()
            self._phone = phone
            step = scenario[-1]
            self.connected = step in "34"
            self.signed_in = step == "4"
            self._scan = (218, 640, False) if step == "4" else (0, None, False)
        self.changed.emit()

    def items(self) -> list[RestoreItem]:
        return list(self._items)

    def phone_apps(self) -> list[PhoneApp]:
        return list(self._phone)

    def space(self) -> DeviceSpace:
        return self._space

    def scan(self) -> tuple[int, int | None, bool]:
        return self._scan

    def login(self, email: str, password: str) -> None:
        self.calls.append(("login", email))
        self.account_email = email
        self.auth_phase = "need_code"
        self.auth_status = "Код отправлен на ваши устройства Apple."
        self.changed.emit()

    def submit_code(self, code: str) -> None:
        self.calls.append(("submit_code", "******"))
        self.auth_phase = "in"
        self.auth_status = "Сессия открыта."
        self.signed_in = True
        self.relogin = False
        self.changed.emit()

    def restore_offloaded(self, keys: list[str]) -> None:
        self.calls.append(("restore_offloaded", list(keys)))

    def install_store(self, store_id: str) -> None:
        self.calls.append(("install_store", store_id))

    def install_ipa(self, path: str) -> None:
        self.calls.append(("install_ipa", path))


class FakeIconBook(QObject):
    """iconBook for screenshots: icons from a folder (``<store id>.png|jpg``), pre-rounded."""

    changed = Signal()

    def __init__(self, folder: Path | None = None, cache: Path | None = None) -> None:
        super().__init__()
        self.folder = folder
        self.cache = cache
        self._paths: dict[str, str] = {}

    @Property(int, notify=changed)
    def revision(self) -> int:
        return 0

    @Slot(str, result=str)
    def pathFor(self, key: str) -> str:
        if not key or self.folder is None:
            return ""
        if key in self._paths:
            return self._paths[key]
        url = ""
        for ext in (".png", ".jpg"):
            path = self.folder / f"{key}{ext}"
            if path.is_file():
                url = QUrl.fromLocalFile(str(self._rounded(path))).toString()
                break
        self._paths[key] = url
        return url

    def _rounded(self, path: Path) -> Path:
        if self.cache is None:
            return path
        from PySide6.QtCore import QRectF, Qt
        from PySide6.QtGui import QImage, QPainter, QPainterPath

        self.cache.mkdir(parents=True, exist_ok=True)
        out = self.cache / (path.stem + ".png")
        if out.is_file():
            return out
        src = QImage(str(path))
        size = 256
        img = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
        img.fill(Qt.GlobalColor.transparent)
        painter = QPainter(img)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        clip = QPainterPath()
        clip.addRoundedRect(QRectF(0, 0, size, size), size * 0.23, size * 0.23)
        painter.setClipPath(clip)
        painter.drawImage(QRectF(0, 0, size, size), src)
        painter.end()
        img.save(str(out), "PNG")
        return out
