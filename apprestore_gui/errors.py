"""Turn tool and exception text into one Russian sentence for the window.

The download failure from the core lists every typical cause in one
paragraph, including a TLS timeout, a missing license, and the region.
Matching that boilerplate would blame the wrong thing. The real cause is
the part before ``Typical causes``.
"""

from __future__ import annotations

import re

_NETWORK = (
    "tls handshake",
    "handshake timeout",
    "dial tcp",
    "failed to get bag",
    "connection refused",
    "i/o timeout",
    "net/http",
    "no such host",
    "context deadline exceeded",
    "urlopen",
    "timed out",
    "init.itunes.apple.com",
)
_LICENSE = (
    "license not found",
    "license is required",
    "license required",
    "9610",
    "no purchase",
)
_REGION = (
    "temporarily unavailable",
    "2059",
    "another store",
    "другого магазина",
    "unavailable in this region",
    "unavailable for this apple id",
    "not available in your country",
)
_AUTH = (
    "not authenticated",
    "passphrase is required",
    "keychain passphrase",
    "password token",
    "password has changed",
    "sign in to",
    "handle is invalid",
)
_DEVICE = (
    "device not found",
    "usbmux",
    "connectionterminated",
    "connection terminated",
    "device is not connected",
    "not connected",
)


NOT_OWNED_TEXT = (
    "Этого приложения нет на вашем Apple ID (нет лицензии). "
    "Бесплатное AppRestore добавит само, платное без оплаты не ставится."
)


def is_license_missing(message: str) -> bool:
    """True when the failure means "no license on this Apple ID".

    Only for attempts made without ``--purchase``: only then may AppRestore
    go through the license gate (price==0, limits, journal) and retry.
    """

    raw = (message or "").strip()
    if not raw or "with --purchase:" in raw.casefold():
        return False
    low = _signal(raw).casefold()
    if any(hint in low for hint in _AUTH + _NETWORK + _DEVICE + _REGION):
        return False
    return any(hint in low for hint in _LICENSE)


def _body(message: str) -> str:
    text = " ".join(message.split())
    marker = text.casefold().find("typical causes")
    if marker != -1:
        text = text[:marker].rstrip(" .")
    return text


def _rank(fragment: str) -> int:
    low = fragment.casefold()
    if any(hint in low for hint in _AUTH):
        return 100
    if any(hint in low for hint in _REGION):
        return 90
    if "failed to purchase" in low or "purchasing paid apps" in low:
        return 80
    if any(hint in low for hint in _LICENSE):
        return 70
    if any(hint in low for hint in _NETWORK):
        return 60
    if "refusing to install" in low:
        return 50
    if "app not found" in low or "failed to find" in low:
        return 40
    if fragment.strip():
        return 10
    return 0


def _signal(message: str) -> str:
    """The attempt that actually explains the failure.

    A later bundle-id retry often says only ``app not found``. That must not
    hide an earlier purchase refusal from the store id.
    """

    text = _body(message)
    quoted = re.findall(r'error="([^"]*)"', text, flags=re.IGNORECASE)
    fragments = [item.strip() for item in quoted if item.strip()]
    if not fragments:
        parts = re.split(r"(?:with|without) --purchase:", text, flags=re.IGNORECASE)
        fragments = [part.strip(" ;") for part in parts if part.strip(" ;")]
    if not fragments:
        return text
    best = max(range(len(fragments)), key=lambda index: (_rank(fragments[index]), index))
    return fragments[best]


def _has_cyrillic(text: str) -> bool:
    folded = text.casefold()
    return any("а" <= char <= "я" or char == "ё" for char in folded)


def explain_user_error(message: str) -> str:
    """One sentence a person can read. The operations log keeps the original."""

    raw = (message or "").strip()
    if not raw:
        return "Не получилось. Повторите ещё раз."
    asked_purchase = "with --purchase:" in raw.casefold()
    signal = _signal(raw)
    low = signal.casefold()

    if any(hint in low for hint in _AUTH):
        return (
            "Сессия Apple ID закрыта. Откройте её в разделе Apple ID "
            "и повторите."
        )
    if any(hint in low for hint in _NETWORK):
        return (
            "Сервер Apple не ответил. Проверьте интернет или VPN и повторите."
        )
    if any(hint in low for hint in _DEVICE):
        return "iPhone не ответил. Разблокируйте его и подключите кабелем ещё раз."
    if "purchasing paid apps is not supported" in low:
        return "Платное приложение без оплаты не ставится."
    if "valid apple id email" in low:
        return "Нужна почта Apple ID."
    if any(hint in low for hint in _LICENSE):
        if asked_purchase:
            return (
                "Apple не выдала лицензию на эту версию. "
                "С этой карточки получить её нельзя."
            )
        return NOT_OWNED_TEXT
    if any(hint in low for hint in _REGION):
        return (
            "Apple не отдаёт это приложение для страны этого Apple ID. "
            "Программа страну магазина не меняет."
        )
    if "failed to purchase" in low:
        return (
            "Apple не выдала лицензию на эту версию. "
            "Если на iPhone App Store просит принять условия, примите их и нажмите «Получить» ещё раз. "
            "Иначе эта карточка уже не выдаётся."
        )
    if "could not download" in low or "ipatool failed" in low or "ipa output missing" in low:
        return (
            "Файл не скачался. Если связь с Apple оборвалась, повторите. "
            "Если Apple ответила отказом, у этого Apple ID нет доступа к этой версии."
        )
    if "refusing to install" in low:
        return "Скачанный файл оказался другим приложением, поэтому копия не сохранена."
    if "app not found" in low or "failed to find" in low:
        return (
            "Apple не нашла это приложение. "
            "Номер на телефоне не совпадает с карточкой, которую ещё можно скачать."
        )
    if _has_cyrillic(signal):
        return signal
    quote = signal.strip()
    if quote:
        if len(quote) > 160:
            quote = quote[:160].rstrip() + "…"
        return f"Не получилось: {quote}"
    return "Не получилось. Повторите ещё раз."


def explain_update_error(message: str) -> str:
    """Update failures are not Apple Store errors."""

    text = " ".join((message or "").split())
    if _has_cyrillic(text) and len(text) <= 240:
        return text
    return "Обновление не установилось."
