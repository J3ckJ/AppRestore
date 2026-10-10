"""Apps Russian users most often need back after the App Store listing disappeared.

The shelf shows the last known release of each app. A later upload is a
different store id and comes back only if that release was on the Apple ID.

Last known, as named for this shelf:
СберБанк Онлайн 17.6.1 is the original card 492224193.
Т-Банк Spotluma на телефоне — карточка 6755181069, версия 2.8.2.
Альфа-Банк «Апгрейд — Умный помощник» 6473656113.
ВТБ «Сириус» 6749962031.
Домклик's later card is ДКлик 1660762523.
The remaining banks use the store ids from the working list, even where
a later reupload may exist. Банк Санкт-Петербург is 531096347.

VK and Mail.ru apps are the cards that were on the store until Apple
removed them in June 2026. They are not renamed reuploads:
VK 564177498, VK Музыка 1054372220, VK Видео 6447614666,
VK Мессенджер 1441659687, OK 398465290, Почта Mail.ru 511310430,
Облако Mail.ru 696551382, Дзен 1343242452, Юла 1016489154,
Маруся 1467719381, VK Знакомства 6449036810, VK Почта 1563419339.
MAX is the messenger card 6739530834, published by MAX LLC. Apple
removed it in the same June 2026 wave, a few weeks before the VK apps.
"""

from __future__ import annotations

from typing import NotRequired, TypedDict


class PopularApp(TypedDict):
    storeId: str
    name: str
    detail: str
    mark: str
    color: str
    ink: str
    site: NotRequired[str]


# Public sites used only when Apple no longer serves the store listing's artwork.
ICON_SITES: dict[str, str] = {
    "492224193": "sberbank.ru",
    "6755181069": "tbank.ru",
    "6749962031": "vtb.ru",
    "6473656113": "alfabank.ru",
    "1406492297": "gazprombank.ru",
    "1117817509": "rshb.ru",
    "909649844": "open.ru",
    "1660762523": "domclick.ru",
    "1208055056": "sovcombank.ru",
    "548000415": "psbank.ru",
    "1135404935": "mkb.ru",
    "1438090038": "rosbank.ru",
    "1622348767": "domrf.ru",
    "808733030": "uralsib.ru",
    "979116495": "pochtabank.ru",
    "1264479292": "akbars.ru",
    "531096347": "bspb.ru",
    "1184367593": "mtsbank.ru",
    "1061008282": "home.bank",
    "1083085558": "rencredit.ru",
    "564177498": "vk.com",
    "6739530834": "max.ru",
    "398465290": "ok.ru",
    "511310430": "mail.ru",
    "696551382": "cloud.mail.ru",
    "1054372220": "vk.com",
    "6447614666": "vkvideo.ru",
    "1441659687": "vk.me",
    "1563419339": "mail.ru",
    "1343242452": "dzen.ru",
    "1016489154": "youla.ru",
    "1467719381": "yandex.ru",
    "6449036810": "vk.com",
}


POPULAR_APPS: tuple[PopularApp, ...] = (
    {
        "storeId": "492224193",
        "name": "Сбер",
        "detail": "Онлайн 17.6.1",
        "mark": "С",
        "color": "#21A038",
        "ink": "#FFFFFF",
    },
    {
        "storeId": "6755181069",
        "name": "Т-Банк",
        "detail": "Spotluma 2.8.2",
        "mark": "Т",
        "color": "#FFDD2D",
        "ink": "#1C1C1E",
    },
    {
        "storeId": "6749962031",
        "name": "ВТБ",
        "detail": "Сириус",
        "mark": "В",
        "color": "#0A2896",
        "ink": "#FFFFFF",
    },
    {
        "storeId": "6473656113",
        "name": "Альфа",
        "detail": "Апгрейд",
        "mark": "А",
        "color": "#EF3124",
        "ink": "#FFFFFF",
    },
    {
        "storeId": "1406492297",
        "name": "Газпромбанк",
        "detail": "банк",
        "mark": "Г",
        "color": "#0A4DB3",
        "ink": "#FFFFFF",
    },
    {
        "storeId": "1117817509",
        "name": "Россельхоз",
        "detail": "Россельхозбанк",
        "mark": "Р",
        "color": "#1F8F4E",
        "ink": "#FFFFFF",
    },
    {
        "storeId": "909649844",
        "name": "Открытие",
        "detail": "банк",
        "mark": "О",
        "color": "#0097D8",
        "ink": "#FFFFFF",
    },
    {
        "storeId": "1660762523",
        "name": "Домклик",
        "detail": "ДКлик",
        "mark": "Д",
        "color": "#14804A",
        "ink": "#FFFFFF",
    },
    {
        "storeId": "1208055056",
        "name": "Совкомбанк",
        "detail": "Халва",
        "mark": "С",
        "color": "#E30613",
        "ink": "#FFFFFF",
    },
    {
        "storeId": "548000415",
        "name": "ПСБ",
        "detail": "банк",
        "mark": "П",
        "color": "#EE2D24",
        "ink": "#FFFFFF",
    },
    {
        "storeId": "1135404935",
        "name": "МКБ",
        "detail": "банк",
        "mark": "М",
        "color": "#1A1A1A",
        "ink": "#FFFFFF",
    },
    {
        "storeId": "1438090038",
        "name": "Росбанк",
        "detail": "банк",
        "mark": "Р",
        "color": "#E4002B",
        "ink": "#FFFFFF",
    },
    {
        "storeId": "1622348767",
        "name": "ДОМ.РФ",
        "detail": "банк",
        "mark": "Д",
        "color": "#00A651",
        "ink": "#FFFFFF",
    },
    {
        "storeId": "808733030",
        "name": "Уралсиб",
        "detail": "банк",
        "mark": "У",
        "color": "#003DA5",
        "ink": "#FFFFFF",
    },
    {
        "storeId": "979116495",
        "name": "Почта Банк",
        "detail": "банк",
        "mark": "П",
        "color": "#0055A5",
        "ink": "#FFFFFF",
    },
    {
        "storeId": "1264479292",
        "name": "Ак Барс",
        "detail": "банк",
        "mark": "А",
        "color": "#007A33",
        "ink": "#FFFFFF",
    },
    {
        "storeId": "531096347",
        "name": "Банк СПБ",
        "detail": "Санкт-Петербург",
        "mark": "С",
        "color": "#003399",
        "ink": "#FFFFFF",
    },
    {
        "storeId": "1184367593",
        "name": "МТС Банк",
        "detail": "банк",
        "mark": "М",
        "color": "#E30611",
        "ink": "#FFFFFF",
    },
    {
        "storeId": "1061008282",
        "name": "Хоум Банк",
        "detail": "банк",
        "mark": "Х",
        "color": "#D61F26",
        "ink": "#FFFFFF",
    },
    {
        "storeId": "1083085558",
        "name": "Ренессанс",
        "detail": "банк",
        "mark": "Р",
        "color": "#6B2D5B",
        "ink": "#FFFFFF",
    },
    {
        "storeId": "564177498",
        "name": "VK",
        "detail": "соцсеть",
        "mark": "В",
        "color": "#0077FF",
        "ink": "#FFFFFF",
    },
    {
        "storeId": "6739530834",
        "name": "MAX",
        "detail": "мессенджер",
        "mark": "М",
        "color": "#3B4BFF",
        "ink": "#FFFFFF",
    },
    {
        "storeId": "398465290",
        "name": "OK",
        "detail": "соцсеть",
        "mark": "О",
        "color": "#EE8208",
        "ink": "#FFFFFF",
    },
    {
        "storeId": "511310430",
        "name": "Почта",
        "detail": "Mail.ru",
        "mark": "П",
        "color": "#005FF9",
        "ink": "#FFFFFF",
    },
    {
        "storeId": "696551382",
        "name": "Облако",
        "detail": "Mail.ru",
        "mark": "О",
        "color": "#0090C8",
        "ink": "#FFFFFF",
    },
    {
        "storeId": "1054372220",
        "name": "VK Музыка",
        "detail": "музыка",
        "mark": "М",
        "color": "#FF2D55",
        "ink": "#FFFFFF",
    },
    {
        "storeId": "6447614666",
        "name": "VK Видео",
        "detail": "видео",
        "mark": "В",
        "color": "#FF5A1F",
        "ink": "#FFFFFF",
    },
    {
        "storeId": "1441659687",
        "name": "VK Мессенджер",
        "detail": "чаты",
        "mark": "М",
        "color": "#2787F5",
        "ink": "#FFFFFF",
    },
    {
        "storeId": "1563419339",
        "name": "VK Почта",
        "detail": "почта",
        "mark": "В",
        "color": "#4C6FFF",
        "ink": "#FFFFFF",
    },
    {
        "storeId": "1343242452",
        "name": "Дзен",
        "detail": "лента",
        "mark": "Д",
        "color": "#1C1C1E",
        "ink": "#FFFFFF",
    },
    {
        "storeId": "1016489154",
        "name": "Юла",
        "detail": "объявления",
        "mark": "Ю",
        "color": "#7B3FF2",
        "ink": "#FFFFFF",
    },
    {
        "storeId": "1467719381",
        "name": "Маруся",
        "detail": "помощник",
        "mark": "М",
        "color": "#6C2BD9",
        "ink": "#FFFFFF",
    },
    {
        "storeId": "6449036810",
        "name": "Знакомства",
        "detail": "VK",
        "mark": "З",
        "color": "#FF2D87",
        "ink": "#FFFFFF",
    },
)


def popular_apps() -> list[PopularApp]:
    rows: list[PopularApp] = []
    for app in POPULAR_APPS:
        row = dict(app)
        site = ICON_SITES.get(app["storeId"])
        if site:
            row["site"] = site
        rows.append(row)
    return rows
