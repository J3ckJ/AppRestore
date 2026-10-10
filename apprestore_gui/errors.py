"""Turn tool and exception text into one Russian sentence for the window.

The download failure from the core lists every typical cause in one
paragraph, including a TLS timeout, a missing license, and the region.
Matching that boilerplate would blame the wrong thing. The real cause is
the part before ``Typical causes``.
"""

from __future__ import annotations

from apprestore_core.error_signal import (  # noqa: F401 - re-exported
    _AUTH,
    _DEVICE,
    _LICENSE,
    _NETWORK,
    _REGION,
    _body,
    _rank,
    _signal,
    is_license_missing,
)
from apprestore_core.license_gate import STORE_MISMATCH_TEXT, is_store_mismatch, is_store_refusal

IPATOOL_FALLBACK_TEXT = "ipatool не смог выполнить запрос. Повторите позже."

STORE_REFUSED_TEXT = "Apple сейчас не выдаёт это приложение для вашего аккаунта."

NOT_OWNED_TEXT = (
    "Этого приложения нет на вашем Apple ID (нет лицензии). "
    "Бесплатное AppRestore добавит само, платное без оплаты не ставится."
)


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

    if is_store_mismatch(raw):
        # -128: before every other rule; the 4b screen recognises this text.
        return STORE_MISMATCH_TEXT
    if any(hint in low for hint in _AUTH):
        return (
            "Сессия Apple ID закрыта. Откройте её в разделе Apple ID "
            "и повторите."
        )
    if any(hint in low for hint in _NETWORK):
        return (
            "Сервер Apple не ответил. Проверьте подключение к интернету и попробуйте ещё раз."
        )
    if any(hint in low for hint in _DEVICE):
        return "iPhone не ответил. Разблокируйте его и подключите кабелем ещё раз."
    if "purchasing paid apps is not supported" in low:
        return "Платное приложение без оплаты не ставится."
    if is_store_refusal(low):
        return STORE_REFUSED_TEXT
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


def explain_ipatool_error(error: BaseException) -> str:
    """Text for a typed ``ipatool_api.IpatoolError`` (or anything else).

    Only the fixed Russian sentence for the error code is shown. ``detail``
    (the raw ipatool text, already without email/guid) stays out of the
    window, so nothing from the account leaks into a screenshot.
    """

    message = getattr(error, "message_ru", "")
    if isinstance(message, str) and message.strip():
        return message.strip()
    if type(error).__name__ == "IpatoolError" or hasattr(error, "detail"):
        # A typed ipatool error without a Russian text: never quote the raw
        # ipatool output (it may carry account data) — fixed sentence only.
        return IPATOOL_FALLBACK_TEXT
    return explain_user_error(str(error))
