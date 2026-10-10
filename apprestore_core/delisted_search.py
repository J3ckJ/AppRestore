"""delisted_search — поиск удалённых из App Store приложений по НАЗВАНИЮ.

Задача Евгения (10.10.2026): во вкладке «Найти» человек вписывает «Сириус» или
«втб» и находит удалённое приложение, а не только по id/ссылке.

Две части:

(a) BUILTIN — встроенный список известных удалённых приложений банков и
    приложений, ссылку на которые публиковал сам банк. Без сети, мгновенно.
    LEGAL.md §1.13 (Лена, 10.10.2026): по умолчанию BUILTIN_STRICT=True —
    только уровень A: официальный источник банка (сайт или канал) с ПРЯМОЙ
    ссылкой на этот track_id, у записи обязательно есть post_url, archive_url
    (архивная копия этого поста/страницы в Wayback) и checked_date. Нет копии —
    записи нет (builtin_entries её не отдаёт). Уровень B (банк назвал приложение,
    но ссылка до id не прослеживается, link_verified=False) лежит в данных, но
    при strict выключен. Разработчик показывается как есть; developer_is_bank
    говорит GUI, нужна ли вторая строка «Ссылку на это приложение публиковал банк».
    Нечёткое совпадение: регистр, ё/е, дефисы, гомоглифы (латинская «C» в
    «Cириус»), неверная раскладка («dn,» → «втб», «cbhbec» → «сириус»),
    транслит («sirius» → «сириус»), опечатки (difflib).

(b) WAYBACK — запасной поиск по названию через Wayback CDX API Internet Archive
    для витрин ru/us: префикс apps.apple.com/<cc>/app/<slug>, затем сырой
    снимок страницы → название, разработчик, track_id, bundle (если на странице
    он однозначно принадлежит этому id), иконка mzstatic.

Рамка (как region_probe, LEGAL.md §1.10/§1.11):
1. АНОНИМНО. Собственный opener urllib без HTTPCookieProcessor/auth, заголовков
   ровно два (нейтральный User-Agent, Accept). Модуль не импортирует ipatool_api /
   license_guard, не знает про сессию, cookie, токен, DSID, витрину аккаунта.
2. ТОЛЬКО МЕТАДАННЫЕ. На выходе DelistedHit: track_id, название, разработчик,
   bundle, иконка (только https://isN-ssl.mzstatic.com/image/thumb/…). Никаких
   ссылок на файлы, никаких IPA-каталогов. Установка дальше — обычный путь через
   Apple ID пользователя и license_guard.
3. СЕТЬ — ТОЛЬКО ПО ПЕРЕКЛЮЧАТЕЛЮ. Запрос пользователя уходит в Internet Archive,
   поэтому в модуле сеть по умолчанию выключена (allow_network=False). GUI передаёт
   True, только когда включён переключатель «Искать в архиве» (LEGAL.md §1.12:
   раскрытие — переключатель + сноска, экрана согласия нет).
4. БЕРЕЖНО К АРХИВУ. Не чаще 1 запроса в MIN_INTERVAL_S, кэш (CDX и снимки),
   ограничение числа снимков на поиск, витрины только из ALLOWED_STOREFRONTS
   (не больше двух, перебора стран нет).

Логи: только счётчики (сколько найдено, сколько запросов/ошибок), без текста
запроса, без track_id и без тел ответов.
"""
from __future__ import annotations

import difflib
import enum
import gzip
import html
import json
import logging
import re
import threading
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Callable, Iterable, Mapping, Optional

log = logging.getLogger("apprestore.delisted_search")

CDX_URL = "https://web.archive.org/cdx/search/cdx"
SNAPSHOT_URL = "https://web.archive.org/web/{ts}id_/{original}"
ALLOWED_STOREFRONTS: tuple[str, ...] = ("ru", "us")
MAX_STOREFRONTS = 2
MIN_INTERVAL_S = 1.0            # не чаще 1 запроса в секунду (вся сеть модуля)
TIMEOUT_S = 20.0
RETRY_STATUSES = frozenset({429, 503})
RETRY_BACKOFF_S = 5.0           # одна повторная попытка на 429/503
CDX_LIMIT = 300                 # строк CDX на один префикс
MAX_SNAPSHOTS_PER_SEARCH = 5    # сколько страниц приложений читаем за поиск
MAX_QUERY_LEN = 100
MIN_QUERY_LEN = 2
CDX_TTL_S = 24 * 3600
CDX_NEGATIVE_TTL_S = 6 * 3600
SNAPSHOT_TTL_S = 7 * 24 * 3600  # снимок неизменен, кэшируем надолго
BUILTIN_ENOUGH_CONFIDENCE = 0.85  # есть такой встроенный результат → в сеть не идём
MIN_CONFIDENCE = 0.6
USER_AGENT = "AppRestore-delisted-search/1"
HEADERS: Mapping[str, str] = {"User-Agent": USER_AGENT, "Accept": "application/json, text/html"}
# True (LEGAL.md §1.13) — только уровень A: ссылка банка прослеживается до track_id
# (link_verified) и есть архивная копия поста (archive_url) с датой проверки.
BUILTIN_STRICT = True
# Не больше стольких вариантов slug на один Wayback-поиск (как ввели, гомоглиф, раскладка).
MAX_SLUGS = 3
_ARCHIVE_URL_RE = re.compile(r"^https://web\.archive\.org/web/\d{14}/https?://\S+$")
_DATE_RE = re.compile(r"^20\d{2}-\d{2}-\d{2}$")


class HitSource(str, enum.Enum):
    BUILTIN = "builtin"
    WAYBACK = "wayback"


@dataclass(frozen=True)
class DelistedHit:
    track_id: int
    name: str
    developer: Optional[str]
    bundle_id: Optional[str]
    icon_url: Optional[str]
    source: HitSource
    confidence: float               # 0..1
    # Банк, чью ссылку нашли (только для поиска/сортировки). В UI НЕ ПОКАЗЫВАТЬ
    # (спека Ники 02-picker.md §6b): ни второй строкой, ни подсказкой.
    brand: Optional[str] = None
    storefront: Optional[str] = None  # для wayback: витрина снимка ('ru'/'us')
    snapshot: Optional[str] = None    # для wayback: дата снимка YYYYMMDD
    # Только builtin: True — разработчик и есть банк; False — разработчик не банк,
    # GUI пишет второй строкой «Ссылку на это приложение публиковал банк»
    # (LEGAL.md §1.13 п.2); None — wayback, неизвестно (вторую строку не писать).
    developer_is_bank: Optional[bool] = None

    def to_dict(self) -> dict:
        return {"track_id": self.track_id, "name": self.name, "developer": self.developer,
                "bundle_id": self.bundle_id, "icon_url": self.icon_url,
                "source": self.source.value, "confidence": round(self.confidence, 3),
                "brand": self.brand, "storefront": self.storefront, "snapshot": self.snapshot,
                "developer_is_bank": self.developer_is_bank}


@dataclass(frozen=True)
class BuiltinEntry:
    track_id: int
    name: str                   # как в App Store (снимок Wayback)
    developer: str              # как в App Store (снимок Wayback)
    brand: str                  # продукт банка (только для поиска, в UI не показывать)
    bank: str
    synonyms: tuple[str, ...]   # по чему искать, кроме name/brand
    link_verified: bool         # ссылка банка прослеживается до этого track_id (уровень A)
    evidence: str               # чем подтверждено (официальный источник + дата + Wayback)
    bundle_id: Optional[str] = None
    icon_url: Optional[str] = None
    developer_is_bank: bool = False   # разработчик в App Store — сам банк
    post_url: Optional[str] = None    # официальный пост/страница банка со ссылкой
    archive_url: Optional[str] = None  # снимок Wayback ЭТОГО поста/страницы (§1.13 п.1)
    link_archive_url: Optional[str] = None  # снимок Wayback редиректа ссылки поста → id
    checked_date: Optional[str] = None  # YYYY-MM-DD, когда архивная копия сверена

    @property
    def archive_of_post(self) -> bool:
        """archive_url — снимок Wayback именно post_url (t.me/<c>/<n> ≡ t.me/s/<c>/<n>)."""
        if not (self.post_url and self.archive_url):
            return False
        m = _ARCHIVE_URL_RE.match(self.archive_url)
        if not m:
            return False
        orig = self.archive_url.split("/", 5)[5]   # https://web.archive.org/web/<ts>/<orig>
        return orig.replace("://t.me/s/", "://t.me/") == self.post_url

    @property
    def tier_a_ok(self) -> bool:
        """Уровень A по §1.13: прямая ссылка + архивная копия поста + дата проверки."""
        return bool(self.link_verified and self.archive_of_post
                    and self.checked_date and _DATE_RE.match(self.checked_date))


# ---------------------------------------------------------------------------
# ВСТРОЕННЫЙ СПИСОК. Проверено 10.10.2026, см. delisted-search.md (таблица).
# track_id клонов и Сбера: публичный lookup ru и us → resultCount 0 (удалены).
# Архивные копии постов/страниц уровня A сверены 10.10.2026 анонимным GET к
# web.archive.org: на снимке есть прямая ссылка на track_id (или на трекер, чей
# архивный 302 ведёт на этот id — link_archive_url).
# ---------------------------------------------------------------------------
_MZ = "https://is1-ssl.mzstatic.com/image/thumb/"
BUILTIN: tuple[BuiltinEntry, ...] = (
    BuiltinEntry(
        472951966, "ВТБ Онлайн", "VTB Bank (PJSC)", "ВТБ Онлайн", "ВТБ",
        ("втб", "vtb", "втб онлайн", "vtb online", "втб банк", "внешторгбанк"),
        True,
        "Официальный сайт: vtb.ru/personal/online-servisy/vtb-online/ (снимок Wayback "
        "01.01.2020) ссылается на itunes.apple.com/ru/app/id472951966. Страница приложения "
        "(Wayback 28.11.2021): «ВТБ Онлайн», VTB Bank (PJSC), bundle ru.vtb24.mobilebanking.iphone.",
        "ru.vtb24.mobilebanking.iphone",
        icon_url="https://is5-ssl.mzstatic.com/image/thumb/Purple126/v4/fe/bb/39/febb391c-4b58-f181-7e73-"
        "a5dce1770e71/AppIcon-0-0-1x_U007emarketing-0-0-0-10-0-0-sRGB-0-0-0-GLES2_U002c0-512MB-"
        "85-220-0-0.png/512x512bb.png",
        developer_is_bank=True,
        post_url="https://www.vtb.ru/personal/online-servisy/vtb-online/",
        archive_url="https://web.archive.org/web/20200101102720/"
                    "https://www.vtb.ru/personal/online-servisy/vtb-online/",
        checked_date="2026-10-10"),
    BuiltinEntry(
        6749962031, "Cириус", "Sergei Smirnov", "ВТБ Онлайн", "ВТБ",
        ("сириус", "sirius", "втб", "vtb", "втб онлайн"),
        False,
        "Официальный канал ВТБ t.me/bankvtb/3591 (05.06.2026 08:00 МСК): «ВТБ Онлайн "
        "вернулся на iOS… в App Store приложение называется «Сириус»» (ссылка ведёт на "
        "vtb.ru/promo/ustanovite-vtbonline-ios/, июньская версия страницы не сохранена). "
        "Wayback 05.06.2026 10:46 МСК: apps.apple.com/us/app/cириус/id6749962031, "
        "«Cириус» (первая буква латинская), Sergei Smirnov; единственное приложение с таким "
        "slug. Повторно: t.me/bankvtb/3785 (11.09.2026) «Если у вас установлен «Сириус»».",
        "com.sm.FlowTime",
        icon_url=_MZ + "Purple211/v4/7a/4a/e1/7a4ae123-f112-8c8b-aab7-0be6399aa982/"
        "AppIcon-0-0-1x_U007emarketing-0-8-0-85-220.png/512x512bb.png",
        # Уровень B: в посте нет прямой ссылки на id6749962031 (ссылка на промо-страницу
        # vtb.ru; её июньская версия не сохранена, сентябрьская — без ссылки на App Store).
        post_url="https://t.me/bankvtb/3591",
        archive_url="https://web.archive.org/web/20260605085420/https://t.me/bankvtb/3591",
        checked_date="2026-10-10"),
    BuiltinEntry(
        353127685, "Альфа-Банк", "AO ALFA-BANK", "Альфа-Банк", "Альфа-Банк",
        ("альфа", "альфа банк", "альфабанк", "alfa", "alfa bank", "alfabank", "альфа онлайн"),
        True,
        "Официальный сайт: alfabank.ru/everyday/online/ (снимок Wayback 06.01.2020) ссылается "
        "на itunes.apple.com/ru/app/id353127685. Страница приложения (Wayback 08.03.2021): "
        "«Альфа-Банк», AO ALFA-BANK.",
        "com.alfabank.app",
        icon_url="https://is5-ssl.mzstatic.com/image/thumb/Purple124/v4/94/0d/51/940d51e2-4c5e-2bac-c757-"
        "1f5b9e2bfffb/AppIcon-0-0-1x_U007emarketing-0-0-0-7-0-0-sRGB-0-0-0-GLES2_U002c0-512MB-"
        "85-220-0-0.png/512x512bb.png",
        developer_is_bank=True,
        post_url="https://alfabank.ru/everyday/online/",
        archive_url="https://web.archive.org/web/20200106203459/"
                    "https://alfabank.ru/everyday/online/",
        checked_date="2026-10-10"),
    BuiltinEntry(
        6739035108, "Делим Вместе", "Eyup KECIYOKUSU", "Альфа-Банк", "Альфа-Банк",
        ("делим вместе", "split activities", "альфа", "альфа банк", "alfa", "alfabank"),
        True,
        "Официальный канал Альфа-Банка t.me/AlfaBank/2896 (11.07.2025 09:00 МСК): «Установите "
        "на айфон приложение Делим вместе»; ссылка поста trk.mail.ru/c/g5rzb1 → "
        "apps.apple.com/ru/app/id6739035108 (архив редиректа 11.07.2025 → "
        "apps.apple.com/ru/app/делим-вместе/id6739035108?mt_click_id=mt-g5rzb1…). Страница "
        "приложения (Wayback 11.07.2025 06:58 UTC): «Делим Вместе», Eyup KECIYOKUSU.",
        "com.splitactivities.app",
        icon_url=_MZ + "Purple221/v4/67/a4/7e/67a47e74-9e24-4cf5-b7e5-fa357143397b/"
        "AppIcon-0-0-1x_U007epad-0-1-85-220.png/512x512bb.png",
        developer_is_bank=False,
        post_url="https://t.me/AlfaBank/2896",
        archive_url="https://web.archive.org/web/20250711101351/https://t.me/s/AlfaBank/2896",
        link_archive_url="https://web.archive.org/web/20250711070157/https://trk.mail.ru/c/g5rzb1",
        checked_date="2026-10-10"),
    BuiltinEntry(
        6747703692, "My Products", "Paul Carney", "Альфа-Инвестиции", "Альфа-Банк",
        ("my products", "май продактс", "альфа инвестиции", "alfa investments", "альфа"),
        False,
        "Официальный канал Альфа-Банка t.me/AlfaBank/2998 (20.08.2025): «Наше приложение "
        "My Products до сих пор не удалили» (Альфа-Инвестиции; ссылка alfa.me/SIF801 сейчас "
        "403, архив — страница-заглушка). Wayback 28.08.2025: apps.apple.com/us/app/"
        "my-products/id6747703692, «My Products», Paul Carney; других приложений с точным "
        "названием «My Products» в 2025 в архиве нет.",
        "com.myproducts.myproductsapp",
        icon_url=_MZ + "Purple221/v4/1d/2c/b4/1d2cb41f-bf59-916a-8fee-e87370ac1a41/"
        "AppIcon-0-0-1x_U007epad-0-1-85-220.png/512x512bb.png",
        post_url="https://t.me/AlfaBank/2998", checked_date="2026-10-10"),
    BuiltinEntry(
        492224193, "Сбербанк Онлайн", "Сбербанк России", "СберБанк Онлайн", "Сбер",
        ("сбер", "сбербанк", "sber", "sberbank", "сбербанк онлайн", "sberbank online", "сбол",
         "сбер онлайн"),
        True,
        "Официальный сайт: sberbank.ru/ru/person/dist_services/inner_apps («Сбербанк — "
        "Мобильное приложение», снимок Wayback 24.07.2019): кнопка «Скачайте приложение» → "
        "itunes.apple.com/ru/app/sberbank-onlajn/id492224193 (то же на снимке 02.08.2018). "
        "Страница приложения (Wayback 18.06.2019): «Сбербанк Онлайн», Сбербанк России.",
        None,
        icon_url="https://is3-ssl.mzstatic.com/image/thumb/Purple113/v4/6e/e4/b7/6ee4b781-de52-"
        "77d5-3801-abed0b19c53a/AppIcon-0-1x_U007emarketing-0-0-GLES2_U002c0-512MB-sRGB-0-0-0-"
        "85-220-0-0-0-7.png/512x512bb.png",
        developer_is_bank=True,
        post_url="https://www.sberbank.ru/ru/person/dist_services/inner_apps",
        archive_url="https://web.archive.org/web/20190724082459/"
                    "https://www.sberbank.ru/ru/person/dist_services/inner_apps",
        checked_date="2026-10-10"),
    BuiltinEntry(
        6742457200, "Активы Онлайн", "Aidai Zamirbekova", "СберБанк Онлайн", "Сбер",
        ("активы онлайн", "aktivy online", "сбер", "сбербанк", "sber", "sberbank",
         "сбербанк онлайн", "сбол"),
        False,
        "Официальный канал Сбера t.me/sberbank/4469 (25.08.2025): «Скачайте новое приложение "
        "АКТИВЫ ОНЛАЙН в App Store»; t.me/sberbank/4475 (25.08.2025): «новое приложение Сбера — "
        "АКТИВЫ ОНЛАЙН» (ссылка — смартлинк sberbank.com/sms/active, до id не прослеживается). "
        "Wayback 25.08.2025: apps.apple.com/ru/app/активы-онлайн/id6742457200, «Активы Онлайн», "
        "Aidai Zamirbekova; единственное приложение с таким slug.",
        "com.assetsonline.ios",
        icon_url=_MZ + "Purple221/v4/2d/e0/fd/2de0fd84-a982-766b-95f4-d78ef24cf557/"
        "AppIcon-0-0-1x_U007ephone-0-1-0-85-220.png/512x512bb.png",
        post_url="https://t.me/sberbank/4469", checked_date="2026-10-10"),
    BuiltinEntry(
        6748446597, "DriveInvest", "Patrick Warsito", "СберИнвестиции", "Сбер",
        ("driveinvest", "drive invest", "драйв инвест", "сбер инвестиции", "сберинвестор",
         "sber invest", "сбер"),
        False,
        "Официальный канал Сбера t.me/sberbank/4490 (28.08.2025 09:45 МСК): «DRIVEINVEST "
        "недоступно в App Store. Наше новое приложение теперь там не скачать» (ссылка на "
        "sberbank.ru/…/investments/invest_app_ios_del). Wayback 28.08.2025 12:22 МСК: "
        "apps.apple.com/ru/app/driveinvest/id6748446597, «DriveInvest», Patrick Warsito; "
        "единственное приложение с таким slug.",
        "id.drive.invest",
        icon_url=_MZ + "Purple221/v4/d6/ea/a7/d6eaa7ae-6fd0-a042-c6ed-d2168892d128/"
        "AppIcon_appstore-0-0-1x_U007ephone-0-1-85-220.png/512x512bb.png",
        post_url="https://t.me/sberbank/4490", checked_date="2026-10-10"),
    BuiltinEntry(
        455652438, "Тинькофф", "Tinkoff Bank", "Т-Банк", "Т-Банк",
        ("т банк", "тбанк", "t bank", "tbank", "тинькофф", "tinkoff", "тинькофф банк",
         "tinkoff bank"),
        True,
        "Официальный сайт: tinkoff.ru/apps/ (снимок Wayback 06.01.2020): иконка App Store → "
        "app.appsflyer.com/id455652438?pid=tinkoff.ru&c=apps_page; архив этого трекера "
        "(Wayback 09.03.2018) — 302 на itunes.apple.com/RU/app/id455652438. Страница "
        "приложения (Wayback 08.12.2020): «Тинькофф», Tinkoff Bank.",
        None,
        icon_url="https://is2-ssl.mzstatic.com/image/thumb/Purple124/v4/66/6a/77/666a7775-d874-"
        "a271-6cf4-9ea64cd1ce0a/AppIcon-0-0-1x_U007emarketing-0-0-0-7-0-0-sRGB-0-0-0-GLES2_"
        "U002c0-512MB-85-220-0-0.png/512x512bb.png",
        developer_is_bank=True,
        post_url="https://www.tinkoff.ru/apps/",
        archive_url="https://web.archive.org/web/20200106202720/https://www.tinkoff.ru/apps/",
        link_archive_url="https://web.archive.org/web/20180309213604/https://app.appsflyer.com/"
                         "id455652438?pid=tinkoff.ru&c=apps_page&af_cost_model=prm.unp",
        checked_date="2026-10-10"),
    BuiltinEntry(
        6760469916, "Drive Transit", "Amitabh Kulkarni", "Т-Банк", "Т-Банк",
        ("drive transit", "драйв транзит", "т банк", "тбанк", "t bank", "tbank",
         "тинькофф", "tinkoff"),
        True,
        "Официальный канал Т-Банка t.me/tbank/10591 (20.04.2026): «ссылку на наше новое "
        "приложение для iOS, которое называется Drive Transit: https://l.tbank.ru/tg_ios»; "
        "архив Wayback этой ссылки (21.04.2026) — 302 на apps.apple.com/app/id6760469916. "
        "Wayback 14.04.2026: «Drive Transit», Amitabh Kulkarni.",
        "com.delivery.drive.almagul.daurbayeva",
        icon_url=_MZ + "Purple211/v4/ec/f7/11/ecf71140-d6bd-fb5a-d8ef-1543e08840ee/"
        "DeliveryDriveSignPrimary-0-0-1x_U007epad-0-1-85-220.png/512x512bb.png",
        developer_is_bank=False,
        post_url="https://t.me/tbank/10591",
        archive_url="https://web.archive.org/web/20260421032214/https://t.me/tbank/10591",
        link_archive_url="https://web.archive.org/web/20260421024133/https://l.tbank.ru/tg_ios",
        checked_date="2026-10-10"),
    BuiltinEntry(
        6744684419, "Office-Capital", "Yauheni Pazniak", "Т-Инвестиции", "Т-Банк",
        ("office capital", "офис капитал", "т инвестиции", "тинькофф инвестиции",
         "t investments", "tinkoff investments"),
        False,
        "Официальный канал Т-Банка t.me/tbank/9439 (24.06.2025): «Т-Инвестиции выпустили новое "
        "официальное приложение в App Store. Обновленная версия называется Office-Capital» "
        "(ссылка tbinv.onelink.me, до id не прослеживается). Wayback 21.06.2025: "
        "apps.apple.com/ru/app/office-capital/id6744684419, «Office-Capital», Yauheni Pazniak; "
        "единственное приложение с таким slug.",
        "com.dreamgoods.officecapital",
        icon_url=_MZ + "Purple211/v4/e7/b7/b7/e7b7b7bc-2bb2-ff6f-7451-dcb45516e8e9/"
        "AppIcon-0-0-1x_U007epad-0-1-0-85-220.jpeg/512x512bb.png",
        post_url="https://t.me/tbank/9439", checked_date="2026-10-10"),
)


def builtin_entries(strict: Optional[bool] = None) -> tuple[BuiltinEntry, ...]:
    """strict=True (по умолчанию, §1.13): только уровень A — tier_a_ok (прямая ссылка,
    архивная копия поста, дата проверки). Запись без архивной копии не отдаётся."""
    strict = BUILTIN_STRICT if strict is None else strict
    return tuple(e for e in BUILTIN if e.tier_a_ok or not strict)


# ---------------------------------------------------------------------------
# Нормализация: регистр, ё, гомоглифы, раскладка, транслит
# ---------------------------------------------------------------------------
_EN = "qwertyuiop[]asdfghjkl;'zxcvbnm,.`"
_RU = "йцукенгшщзхъфывапролджэячсмитьбюё"
_EN2RU = str.maketrans(_EN + _EN.upper(), _RU + _RU.upper())
_RU2EN = str.maketrans(_RU + _RU.upper(), _EN + _EN.upper())
# Латинские буквы, которые выглядят как кириллические (после lower()).
_HOMO_LAT2CYR = str.maketrans("aceopxykmtbh", "асеорхукмтвн")
_CYR2LAT = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ж": "zh", "з": "z",
    "и": "i", "й": "y", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o", "п": "p",
    "р": "r", "с": "s", "т": "t", "у": "u", "ф": "f", "х": "kh", "ц": "ts", "ч": "ch",
    "ш": "sh", "щ": "sch", "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
}
_LAT2CYR_MULTI = (("shch", "щ"), ("sch", "щ"), ("zh", "ж"), ("kh", "х"), ("ts", "ц"),
                  ("ch", "ч"), ("sh", "ш"), ("yu", "ю"), ("ya", "я"), ("yo", "ё"),
                  ("ph", "ф"), ("th", "т"))
_LAT2CYR_ONE = str.maketrans({"a": "а", "b": "б", "v": "в", "g": "г", "d": "д", "e": "е",
                              "z": "з", "i": "и", "j": "й", "k": "к", "l": "л", "m": "м",
                              "n": "н", "o": "о", "p": "п", "r": "р", "s": "с", "t": "т",
                              "u": "у", "f": "ф", "h": "х", "c": "к", "y": "и", "w": "в",
                              "x": "кс", "q": "к"})
_CYR = re.compile(r"[а-яё]")
_LAT = re.compile(r"[a-z]")


def norm(s: str) -> str:
    """Регистр, ё→е, NFKC, пунктуация/дефисы → пробел, схлопнуть пробелы."""
    s = unicodedata.normalize("NFKC", s or "").lower().replace("ё", "е")
    s = re.sub(r"[^\w]+", " ", s, flags=re.UNICODE).replace("_", " ")
    return " ".join(s.split())


def _homoglyph_fold(s: str) -> str:
    """Смешанная строка (кириллица + латиница-двойники) → кириллица: «cириус» → «сириус»."""
    out = []
    for word in s.split():
        if _CYR.search(word) and _LAT.search(word):
            word = word.translate(_HOMO_LAT2CYR)
        out.append(word)
    return " ".join(out)


# Строчные кириллические буквы, у которых есть латинский двойник (для slug App Store:
# «Cириус» с латинской C даёт slug «cириус», кириллический запрос его не находит).
_HOMO_CYR2LAT_FIRST = {"а": "a", "с": "c", "е": "e", "о": "o", "р": "p", "х": "x", "у": "y"}


def homoglyph_first_latin(s: str) -> Optional[str]:
    """«сириус» → «cириус» (первая буква латинская), если у неё есть двойник; иначе None."""
    if s and _CYR.match(s[0]) and s[0] in _HOMO_CYR2LAT_FIRST:
        return _HOMO_CYR2LAT_FIRST[s[0]] + s[1:]
    return None


def layout_en2ru(s: str) -> str:
    """Набрано в EN-раскладке, имелась в виду RU: «dn,» → «втб»."""
    return s.translate(_EN2RU)


def layout_ru2en(s: str) -> str:
    """Набрано в RU-раскладке, имелась в виду EN: «ыиук» → «sber»."""
    return s.translate(_RU2EN)


def translit_cyr2lat(s: str) -> str:
    return "".join(_CYR2LAT.get(ch, ch) for ch in s)


def translit_lat2cyr(s: str) -> str:
    for a, b in _LAT2CYR_MULTI:
        s = s.replace(a, b)
    return s.translate(_LAT2CYR_ONE)


def query_variants(query: str) -> list[tuple[str, float]]:
    """Варианты запроса с весом: (нормализованная строка, множитель уверенности)."""
    raw = unicodedata.normalize("NFKC", query or "").strip()
    out: list[tuple[str, float]] = []

    def add(v: str, w: float) -> None:
        v = norm(v)
        if v and all(v != x for x, _ in out):
            out.append((v, w))

    base = norm(raw)
    add(_homoglyph_fold(base), 1.0)
    add(base, 1.0)
    if not _CYR.search(base):                  # только латиница/знаки
        add(layout_en2ru(raw.lower()), 0.95)   # «dn,» → «втб» (знаки важны — до norm)
        add(translit_lat2cyr(base), 0.9)       # «sirius» → «сириус»
    if not _LAT.search(base):                  # только кириллица
        add(layout_ru2en(raw.lower()), 0.95)   # «ыиук» → «sber»
        add(translit_cyr2lat(base), 0.9)       # «драйв» → «drayv»
    return out


def _entry_keys(e: BuiltinEntry) -> list[str]:
    keys: list[str] = []
    for k in (e.name, e.brand, *e.synonyms):
        for v in (norm(k), _homoglyph_fold(norm(k))):
            if v and v not in keys:
                keys.append(v)
            t = translit_cyr2lat(v)
            if t and t not in keys:
                keys.append(t)
    return keys


def _score(q: str, key: str) -> float:
    if q == key:
        return 1.0
    if len(q) >= 3 and key.startswith(q):
        return 0.9
    qt, kt = q.split(), key.split()
    if len(q) >= 3 and (q in kt or (len(qt) > 1 and all(t in kt for t in qt))):
        return 0.88
    if len(q) < 4 or q[0] != key[0]:
        return 0.0                             # короткое — только точно/префикс;
                                               # опечатку в первой букве не угадываем
    r = difflib.SequenceMatcher(None, q, key).ratio()
    # опечатка в начале длинного названия: сравнить с префиксом той же длины
    if len(key) > len(q):
        r = max(r, difflib.SequenceMatcher(None, q, key[:len(q)]).ratio() * 0.95)
    return r * 0.9 if r >= 0.78 else 0.0


def search_builtin(query: str, *, strict: Optional[bool] = None,
                   limit: int = 10) -> list[DelistedHit]:
    variants = query_variants(query)
    if not variants or len(variants[0][0]) < MIN_QUERY_LEN:
        return []
    hits: list[DelistedHit] = []
    order: dict[int, int] = {}
    for idx, e in enumerate(builtin_entries(strict)):
        order[e.track_id] = idx
        keys = _entry_keys(e)
        best = 0.0
        for q, w in variants:
            for k in keys:
                best = max(best, _score(q, k) * w)
        if best >= MIN_CONFIDENCE:
            # Совпадение по названию в App Store весомее, чем по имени банка.
            hits.append(DelistedHit(e.track_id, e.name, e.developer, e.bundle_id,
                                    e.icon_url, HitSource.BUILTIN, round(best, 3),
                                    brand=e.brand, developer_is_bank=e.developer_is_bank))
    hits.sort(key=lambda h: (-h.confidence, order[h.track_id]))  # при равенстве — порядок BUILTIN
    return hits[:limit]


# ---------------------------------------------------------------------------
# Wayback
# ---------------------------------------------------------------------------
class WaybackError(Exception):
    def __init__(self, msg: str, status: Optional[int] = None) -> None:
        super().__init__(msg)
        self.status = status


# fetcher(url, headers, timeout) -> bytes; подменяется в тестах. Ошибки — WaybackError.
Fetcher = Callable[[str, Mapping[str, str], float], bytes]


def _default_fetcher(url: str, headers: Mapping[str, str], timeout: float) -> bytes:
    opener = urllib.request.build_opener()     # без HTTPCookieProcessor и auth
    opener.addheaders = []
    req = urllib.request.Request(url, headers=dict(headers), method="GET")
    try:
        with opener.open(req, timeout=timeout) as resp:
            return resp.read()
    except urllib.error.HTTPError as exc:
        raise WaybackError(f"HTTP {exc.code}", exc.code) from None
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise WaybackError(type(exc).__name__) from None


def _decode(raw: bytes) -> str:
    if raw[:2] == b"\x1f\x8b":                 # Wayback id_ отдаёт исходный gzip
        try:
            raw = gzip.decompress(raw)
        except OSError:
            pass
    return raw.decode("utf-8", "replace")


_APP_URL = re.compile(
    r"^https?://apps\.apple\.com(?::80|:443)?/([a-z]{2})/app/([^/?#{}\"'<>\s]+)/id(\d{5,12})"
    r"(?:[?#][^{}\"'<>\s]*)?$", re.I)
_ICON_OK = re.compile(r"^https://is\d-ssl\.mzstatic\.com/image/thumb/[^\s\"'<>]+$")


def slugify(query: str) -> str:
    """Slug App Store: нижний регистр, пробелы → «-», буквы/цифры (кириллица остаётся)."""
    s = unicodedata.normalize("NFKC", query or "").lower()
    s = re.sub(r"[^\w\s-]+", "", s, flags=re.UNICODE).replace("_", " ")
    return "-".join(s.split())


def _norm_icon(url: Optional[str]) -> Optional[str]:
    if not isinstance(url, str):
        return None
    url = url.strip()
    if not _ICON_OK.match(url) or ".ipa" in url.lower():
        return None
    return re.sub(r"/[^/]+$", "/512x512bb.png", url)


def _clean_text(s: object, maxlen: int = 200) -> Optional[str]:
    if not isinstance(s, str):
        return None
    s = " ".join(html.unescape(s).split())
    if not s or re.search(r"https?://|\.ipa\b", s, re.I):
        return None                            # в полях текста ссылок быть не должно
    return s[:maxlen]


_BUNDLE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{1,150}$")


def _walk(node, depth: int = 0):
    if depth > 40:
        return
    if isinstance(node, dict):
        yield node
        for v in node.values():
            yield from _walk(v, depth + 1)
    elif isinstance(node, list):
        for v in node:
            yield from _walk(v, depth + 1)


def _own_bundle_id(t: str, track_id: int) -> Optional[str]:
    """bundleId только из объекта ЭТОГО приложения (id/adamId == track_id).

    На странице много чужих bundleId (полки «Вам может понравиться»), поэтому
    ищем структурно:
    * формат 2019–2024 (Ember): <script id="shoebox-media-api-cache-apps"> —
      JSON, значения которого — JSON-строки с data[] = {id, type:"apps",
      attributes.platformAttributes.<ios|…>.bundleId};
    * формат 2019–2021 (Ember data store): <script id="shoebox-ember-data-store"> —
      объект с id == track_id и attributes.bundleId / platformAttributes.*.bundleId /
      relationships.platforms.data[*].attributes.bundleId;
    * формат 2025+ (Svelte): <script id="serialized-server-data"> — объект с
      adamId == track_id и bundleId рядом (titleOfferDisplayProperties).
    Если найдено не ровно одно значение — None (не гадаем).
    """
    tid = str(track_id)
    found: set[str] = set()
    m = re.search(r'<script[^>]*id="shoebox-media-api-cache-apps"[^>]*>(.*?)</script>', t, re.S)
    if m:
        try:
            outer = json.loads(m.group(1))
        except ValueError:
            outer = {}
        for v in (outer.values() if isinstance(outer, dict) else ()):
            try:
                inner = json.loads(v) if isinstance(v, str) else v
            except ValueError:
                continue
            for node in _walk(inner):
                if str(node.get("id")) == tid and node.get("type") == "apps":
                    pa = (node.get("attributes") or {}).get("platformAttributes") or {}
                    for plat in pa.values() if isinstance(pa, dict) else ():
                        b = plat.get("bundleId") if isinstance(plat, dict) else None
                        if isinstance(b, str) and _BUNDLE_RE.match(b):
                            found.add(b)
    m = re.search(r'<script[^>]*id="shoebox-ember-data-store"[^>]*>(.*?)</script>', t, re.S)
    if m:
        try:
            store = json.loads(m.group(1))
        except ValueError:
            store = None
        for node in _walk(store):
            if str(node.get("id")) != tid:
                continue
            attrs = node.get("attributes") if isinstance(node.get("attributes"), dict) else {}
            cands = [node.get("bundleId"), attrs.get("bundleId")]
            rel = ((node.get("relationships") or {}).get("platforms") or {}).get("data")
            if isinstance(rel, list):   # relationships.platforms.data[*].attributes.bundleId
                cands += [(p.get("attributes") or {}).get("bundleId") for p in rel
                          if isinstance(p, dict)]
            pa = attrs.get("platformAttributes")
            if isinstance(pa, dict):
                cands += [v.get("bundleId") for v in pa.values() if isinstance(v, dict)]
            for b in cands:
                if isinstance(b, str) and _BUNDLE_RE.match(b):
                    found.add(b)
    m = re.search(r'<script[^>]*id="serialized-server-data"[^>]*>(.*?)</script>', t, re.S)
    if m:
        try:
            data = json.loads(m.group(1))
        except ValueError:
            data = None
        for node in _walk(data):
            if str(node.get("adamId")) == tid:
                b = node.get("bundleId")
                if isinstance(b, str) and _BUNDLE_RE.match(b):
                    found.add(b)
    return found.pop() if len(found) == 1 else None


def parse_snapshot(raw: bytes, track_id: int) -> Optional[dict]:
    """Сырой снимок apps.apple.com → {name, developer, bundle_id, icon_url} или None.

    Источник названия/разработчика/иконки — JSON-LD SoftwareApplication (есть в
    форматах 2019–2026). Проверяем, что страница про этот track_id.
    """
    t = _decode(raw)
    if f"id{track_id}" not in t:
        return None
    app = None
    for m in re.finditer(r'<script[^>]*type=["\']?application/ld\+json["\']?[^>]*>(.*?)</script>',
                         t, re.S | re.I):
        try:
            d = json.loads(m.group(1))
        except ValueError:
            continue
        for item in (d if isinstance(d, list) else [d]):
            if isinstance(item, dict) and item.get("@type") == "SoftwareApplication":
                app = item
                break
        if app:
            break
    if not app:
        return None
    name = _clean_text(app.get("name"))
    if not name:
        return None
    author = app.get("author") if isinstance(app.get("author"), dict) else {}
    return {"name": name, "developer": _clean_text(author.get("name")),
            "bundle_id": _own_bundle_id(t, track_id),
            "icon_url": _norm_icon(app.get("image"))}


def _unslug(slug: str) -> str:
    return urllib.parse.unquote(slug).replace("-", " ").strip()


@dataclass
class _Cached:
    value: object
    expires: float


class DelistedSearch:
    """Поиск удалённых по названию: встроенный список + Wayback. Потокобезопасен."""

    def __init__(self, *, fetcher: Optional[Fetcher] = None,
                 clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep,
                 min_interval_s: float = MIN_INTERVAL_S,
                 timeout_s: float = TIMEOUT_S,
                 max_snapshots: int = MAX_SNAPSHOTS_PER_SEARCH,
                 strict: Optional[bool] = None) -> None:
        self._fetch = fetcher or _default_fetcher
        self._clock = clock
        self._sleep = sleep
        self._min_interval = max(0.0, float(min_interval_s))
        self._timeout = timeout_s
        self._max_snapshots = max(0, int(max_snapshots))
        self._strict = strict
        self._cache: dict[tuple, _Cached] = {}
        self._lock = threading.RLock()
        self._last_request: Optional[float] = None
        self.requests_made = 0
        self.errors = 0

    # --- кэш -------------------------------------------------------------------
    def _get_cached(self, key: tuple):
        e = self._cache.get(key)
        if e is None:
            return None
        if self._clock() >= e.expires:
            del self._cache[key]
            return None
        return e.value

    def _put(self, key: tuple, value, ttl: float) -> None:
        self._cache[key] = _Cached(value, self._clock() + ttl)

    def clear_cache(self) -> None:
        with self._lock:
            self._cache.clear()

    # --- сеть ------------------------------------------------------------------
    def _throttle(self) -> None:
        if self._last_request is not None and self._min_interval > 0:
            wait = self._last_request + self._min_interval - self._clock()
            if wait > 0:
                self._sleep(wait)
        self._last_request = self._clock()

    def _get(self, url: str) -> bytes:
        for attempt in (0, 1):
            self._throttle()
            self.requests_made += 1
            try:
                return self._fetch(url, HEADERS, self._timeout)
            except WaybackError as exc:
                if attempt == 0 and exc.status in RETRY_STATUSES:
                    self._sleep(RETRY_BACKOFF_S)
                    continue
                raise
        raise WaybackError("unreachable")

    def _cdx(self, cc: str, slug: str) -> list[tuple[int, str, str, str]]:
        """→ [(track_id, slug, timestamp, original)] — по одному на track_id, свежий 200."""
        key = ("cdx", cc, slug)
        cached = self._get_cached(key)
        if cached is not None:
            return cached
        prefix = f"apps.apple.com/{cc}/app/{urllib.parse.quote(slug, safe='-')}"
        q = urllib.parse.urlencode({"url": prefix, "matchType": "prefix", "collapse": "urlkey",
                                    "fl": "original,timestamp,statuscode",
                                    "filter": "statuscode:200", "output": "json",
                                    "limit": str(CDX_LIMIT)})
        raw = self._get(f"{CDX_URL}?{q}")
        try:
            text = _decode(raw).strip()
            rows = json.loads(text) if text else []
            if not isinstance(rows, list):
                raise TypeError
        except (ValueError, TypeError):
            raise WaybackError("bad CDX JSON") from None
        best: dict[int, tuple[int, str, str, str]] = {}
        for row in rows[1:] if rows and rows[0] and rows[0][0] == "original" else rows:
            if not isinstance(row, list) or len(row) < 2:
                continue
            original, ts = str(row[0]), str(row[1])
            if len(row) > 2 and str(row[2]) != "200":
                continue
            m = _APP_URL.match(original)
            if not m or m.group(1).lower() != cc or not re.fullmatch(r"\d{14}", ts):
                continue
            tid = int(m.group(3))
            cur = best.get(tid)
            if cur is None or ts > cur[2]:
                best[tid] = (tid, m.group(2), ts, original)
        out = list(best.values())
        self._put(key, out, CDX_TTL_S if out else CDX_NEGATIVE_TTL_S)
        return out

    def _snapshot(self, ts: str, original: str, tid: int) -> Optional[dict]:
        key = ("snap", ts, original)
        cached = self._get_cached(key)
        if cached is not None:
            return cached or None
        raw = self._get(SNAPSHOT_URL.format(ts=ts, original=original))
        parsed = parse_snapshot(raw, tid)
        self._put(key, parsed or {}, SNAPSHOT_TTL_S)
        return parsed

    def search_wayback(self, query: str, *, storefronts: Iterable[str] = ALLOWED_STOREFRONTS,
                       limit: int = 10) -> list[DelistedHit]:
        ccs = _norm_storefronts(storefronts)
        variants = query_variants(query)
        if not variants or len(variants[0][0]) < MIN_QUERY_LEN:
            return []
        # Slug: как ввёл человек (после фикса гомоглифов), тот же slug с латинской
        # первой буквой-двойником («сириус» → «cириус»: так в App Store записан
        # «Cириус») и исправленная раскладка, если ввод латиницей похож на русский
        # в EN-раскладке. Не больше MAX_SLUGS.
        slugs: list[str] = []
        for v, w in variants:
            s = slugify(v)
            if s and s not in slugs and (w == 1.0 or (w == 0.95 and _CYR.search(v))):
                slugs.append(s)
                h = homoglyph_first_latin(s)
                if h and h not in slugs:
                    slugs.append(h)
        slugs = slugs[:MAX_SLUGS]
        with self._lock:
            before_req, before_err = self.requests_made, self.errors
            cands: dict[int, tuple] = {}  # tid → (score, cc, slug, ts, orig, closeness)
            for cc in ccs:
                for s in slugs:
                    try:
                        rows = self._cdx(cc, s)
                    except WaybackError:
                        self.errors += 1
                        continue
                    qn = norm(s.replace("-", " "))
                    for tid, slug, ts, orig in rows:
                        un = norm(_unslug(slug))
                        sc = _score(qn, un) or 0.7  # префикс slug уже совпал
                        close = difflib.SequenceMatcher(None, qn, un).ratio()
                        cur = cands.get(tid)
                        if cur is None or (sc, close, ts) > (cur[0], cur[5], cur[3]):
                            cands[tid] = (sc, cc, slug, ts, orig, close)
            # выше: точнее совпадение → ближе slug к запросу → свежее снимок
            ranked = sorted(cands.items(), key=lambda kv: (kv[1][0], kv[1][5], kv[1][3]),
                            reverse=True)
            hits: list[DelistedHit] = []
            for n, (tid, (sc, cc, slug, ts, orig, _close)) in enumerate(ranked[:max(limit, 0)]):
                meta = None
                if n < self._max_snapshots:
                    try:
                        meta = self._snapshot(ts, orig, tid)
                    except WaybackError:
                        self.errors += 1
                if meta:
                    conf = min(0.8, 0.55 + 0.25 * sc)
                    hits.append(DelistedHit(tid, meta["name"], meta["developer"],
                                            meta["bundle_id"], meta["icon_url"],
                                            HitSource.WAYBACK, round(conf, 3),
                                            storefront=cc, snapshot=ts[:8]))
                else:
                    name = _clean_text(_unslug(slug)) or str(tid)
                    hits.append(DelistedHit(tid, name, None, None, None, HitSource.WAYBACK,
                                            round(min(0.5, 0.3 + 0.2 * sc), 3),
                                            storefront=cc, snapshot=ts[:8]))
            hits.sort(key=lambda h: (-h.confidence, h.name))
            log.info("delisted_search: wayback %d hits, %d requests, %d errors", len(hits),
                     self.requests_made - before_req, self.errors - before_err)
            return hits

    # --- API -------------------------------------------------------------------
    def search(self, query: str, *, storefronts: Iterable[str] = ALLOWED_STOREFRONTS,
               allow_network: bool = False, limit: int = 10) -> list[DelistedHit]:
        """Сначала встроенный список (без сети), потом Wayback, если нужно и разрешено.

        allow_network=False по умолчанию: запрос уходит в Internet Archive только
        когда в настройках включён переключатель «Искать в архиве» (раскрытие —
        сноска под выдачей, LEGAL.md §1.12; отдельного экрана согласия нет).
        """
        if not isinstance(query, str):
            return []
        query = query.strip()[:MAX_QUERY_LEN]
        if re.fullmatch(r"(id)?\d+", query, re.I) or "://" in query or "apps.apple.com" in query:
            return []  # номер/ссылка — это обычный путь «Найти», не этот модуль
        ccs = _norm_storefronts(storefronts)
        hits = search_builtin(query, strict=self._strict, limit=limit)
        if allow_network and not any(h.confidence >= BUILTIN_ENOUGH_CONFIDENCE for h in hits):
            seen = {h.track_id for h in hits}
            for h in self.search_wayback(query, storefronts=ccs, limit=limit):
                if h.track_id not in seen:
                    seen.add(h.track_id)
                    hits.append(h)
        log.info("delisted_search: %d hits (network=%s)", len(hits), bool(allow_network))
        return hits[:limit]


def _norm_storefronts(storefronts: Iterable[str]) -> tuple[str, ...]:
    out: list[str] = []
    for c in storefronts or ():
        c = str(c).strip().lower()
        if c not in ALLOWED_STOREFRONTS:
            raise ValueError(f"витрина вне ALLOWED_STOREFRONTS {ALLOWED_STOREFRONTS}")
        if c not in out:
            out.append(c)
    if not 1 <= len(out) <= MAX_STOREFRONTS:
        raise ValueError("нужна одна-две витрины из ALLOWED_STOREFRONTS")
    return tuple(out)


_default: Optional[DelistedSearch] = None
_default_lock = threading.Lock()


def default_search() -> DelistedSearch:
    global _default
    with _default_lock:
        if _default is None:
            _default = DelistedSearch()
        return _default


def search_delisted(query: str, *, storefronts: Iterable[str] = ("ru", "us"),
                    allow_network: bool = False, limit: int = 10) -> list[DelistedHit]:
    """Обёртка над общим DelistedSearch (общий кэш и rate-limit на процесс)."""
    return default_search().search(query, storefronts=storefronts,
                                   allow_network=allow_network, limit=limit)
