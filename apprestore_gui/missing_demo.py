"""Stand-in for a phone's offloaded apps on the redesign home screen.

The real list will come from the device. This one is long enough that it
cannot fit on the phone silhouette, so the window has to offer a choice.
The first entries are the checked popular apps. Later ids are local only.
"""

from __future__ import annotations

from apprestore_gui.popular_apps import popular_apps

_EXTRA: tuple[tuple[str, str], ...] = (
    ("Телеграм", "мессенджер"),
    ("WhatsApp", "мессенджер"),
    ("VK", "соцсеть"),
    ("YouTube", "видео"),
    ("Кинопоиск", "кино"),
    ("Авито", "объявления"),
    ("2ГИС", "карты"),
    ("Ozon", "покупки"),
    ("Wildberries", "покупки"),
    ("Яндекс", "поиск"),
    ("Яндекс Музыка", "музыка"),
    ("Яндекс Карты", "карты"),
    ("Яндекс Go", "такси"),
    ("Яндекс Еда", "доставка"),
    ("Самокат", "продукты"),
    ("Купер", "продукты"),
    ("Госуслуги", "услуги"),
    ("Почта", "почта"),
    ("Дзен", "лента"),
    ("OK", "соцсеть"),
    ("Звук", "музыка"),
    ("IVI", "кино"),
    ("Okko", "кино"),
    ("Wink", "кино"),
    ("Литрес", "книги"),
    ("Дуолинго", "язык"),
    ("Учи.ру", "учёба"),
    ("HeadHunter", "работа"),
    ("Циан", "жильё"),
    ("РЖД", "билеты"),
    ("Туту", "билеты"),
    ("Аэрофлот", "билеты"),
    ("Победа", "билеты"),
    ("Whoosh", "самокаты"),
    ("Делимобиль", "каршеринг"),
    ("Магнит", "продукты"),
    ("Пятёрочка", "продукты"),
    ("ВкусВилл", "продукты"),
    ("МТС", "связь"),
    ("Билайн", "связь"),
    ("МегаФон", "связь"),
    ("Райффайзен", "банк"),
    ("Совкомбанк", "банк"),
    ("ПСБ", "банк"),
    ("ЮMoney", "кошелёк"),
    ("БКС", "инвестиции"),
    ("Zoom", "звонки"),
    ("Discord", "чаты"),
    ("Signal", "мессенджер"),
    ("Viber", "мессенджер"),
    ("Shazam", "музыка"),
    ("Chrome", "браузер"),
    ("Gmail", "почта"),
    ("Notion", "заметки"),
    ("Todoist", "дела"),
    ("Speedtest", "сеть"),
    ("MyBook", "книги"),
    ("Skyeng", "язык"),
    ("Stepik", "учёба"),
    ("Steam", "игры"),
    ("Roblox", "игры"),
    ("Minecraft", "игры"),
    ("Chess", "игры"),
    ("Судоку", "игры"),
)

_PALETTE: tuple[tuple[str, str], ...] = (
    ("#2AABEE", "#FFFFFF"),
    ("#25D366", "#1C1C1E"),
    ("#0077FF", "#FFFFFF"),
    ("#FF0033", "#FFFFFF"),
    ("#FF6600", "#FFFFFF"),
    ("#04E061", "#1C1C1E"),
    ("#2E9E4E", "#FFFFFF"),
    ("#005BFF", "#FFFFFF"),
    ("#CB11AB", "#FFFFFF"),
    ("#FC3F1D", "#FFFFFF"),
    ("#1C1C1E", "#FFFFFF"),
    ("#7B61FF", "#FFFFFF"),
    ("#0A84FF", "#FFFFFF"),
    ("#FF9F0A", "#1C1C1E"),
    ("#34C759", "#1C1C1E"),
    ("#5E5CE6", "#FFFFFF"),
)


def missing_demo_apps() -> list[dict[str, str]]:
    apps = [dict(app) for app in popular_apps()]
    seen = {str(app["name"]) for app in apps}
    extra_index = 0
    for name, detail in _EXTRA:
        if name in seen:
            continue
        color, ink = _PALETTE[extra_index % len(_PALETTE)]
        apps.append(
            {
                "storeId": f"off-{len(apps)}",
                "name": name,
                "detail": detail,
                "mark": name[:1],
                "color": color,
                "ink": ink,
            }
        )
        seen.add(name)
        extra_index += 1
    return apps
