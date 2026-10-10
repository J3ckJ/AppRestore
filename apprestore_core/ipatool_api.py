"""Typed layer over the ipatool CLI for AppRestore (M-3).

Everything goes through ``ipatool ... --format json --non-interactive``.
ipatool prints one JSON object per line: results at info level on stdout and
``{"level":"error","error":"…","success":false}`` on stderr with exit code 1.
ipatool has no machine-readable error code in JSON, only the ``error``
string, so :func:`classify_error` maps the known messages (see
``maks-research/app-store-mechanisms.md``, the error-code catalogue) to a
stable :class:`ErrorCode` with Russian text.  Text output is never parsed.

What this module never does: sign in, buy (``purchase`` / ``--purchase``),
download packages, write to the keychain, or log the keychain passphrase,
the Apple ID email, tokens or the command line.  With ipatool patch 0003 the
passphrase is passed in the child's environment (IPATOOL_KEYCHAIN_PASSPHRASE),
not in argv, so it does not show up in ``ps``.

Public API (for Dima):

    client = IpatoolClient("/path/to/ipatool")         # passphrase from env; handed to ipatool via env (patch 0003) or flag (old)
    check = client.session_alive(timeout=8.0)          # -> SessionCheck
    check.state                                        # SessionState.ALIVE / EXPIRED / NO_NETWORK
    for page in client.iter_purchases(cancel_event):   # -> Iterator[PurchasesPage]
        page.total, page.items[0].cache_key            # --all first, pages of 100 as fallback
    client.all_purchases()                             # -> PurchasesPage (needs patch 0002)
    client.account_info()                              # -> AccountInfo (offline)

Python 3.10+, standard library only.
"""

from __future__ import annotations

import enum
import json
import math
import os
import re
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Iterator, Mapping, Optional, Protocol, Sequence

__all__ = [
    "AccountInfo",
    "CancelEvent",
    "ErrorCode",
    "IpatoolClient",
    "IpatoolError",
    "Purchase",
    "PurchasesPage",
    "RunResult",
    "SessionCheck",
    "SessionState",
    "classify_error",
    "apple_failure_type",
    "classify_failure",
    "purchase_refused",
    "scrub_line",
    "run_verbose_scrubbed",
    "PASSPHRASE_ENV",
    "PROBE_APP_ID",
    "PAGE_SIZE_MAX",
]

PASSPHRASE_ENV = "IPATOOL_KEYCHAIN_PASSPHRASE"

#: App used for the session probe: Apple's own "Apple Store" app (free,
#: published in every storefront).  ``list-versions`` sends one signed,
#: token-bearing request to Apple's download endpoint and only returns the
#: version identifiers; it never downloads a package and never buys.
PROBE_APP_ID = 375380948

#: ipatool caps ``list-purchases --max-results`` at 100.
PAGE_SIZE_MAX = 100


# --------------------------------------------------------------------------
# Errors
# --------------------------------------------------------------------------


class ErrorCode(str, enum.Enum):
    """Stable error codes.  Values are safe to store and to compare."""

    # Session / account (the user has to act: sign in again, 2FA, passphrase)
    TOKEN_EXPIRED = "token_expired"
    AUTH_CODE_REQUIRED = "auth_code_required"
    TWO_FACTOR_REJECTED = "two_factor_rejected"
    INVALID_CREDENTIALS = "invalid_credentials"
    PASSWORD_CHANGED = "password_changed"
    ACCOUNT_DISABLED = "account_disabled"
    DEVICE_VERIFICATION = "device_verification"
    NOT_SIGNED_IN = "not_signed_in"
    KEYCHAIN_PASSPHRASE_REQUIRED = "keychain_passphrase_required"
    KEYCHAIN_PASSPHRASE_WRONG = "keychain_passphrase_wrong"
    PASSPHRASE_NO_SECURE_METHOD = "passphrase_no_secure_method"
    # Store answers
    #: Apple failureType -128 "Account Not In This Store": the storefront in
    #: ipatool's saved session differs from the Apple ID's country (e.g. after
    #: the account changed country).  NOT a session code: the token is alive.
    STORE_MISMATCH = "store_mismatch"
    LICENSE_REQUIRED = "license_required"
    LICENSE_ALREADY_EXISTS = "license_already_exists"
    TEMPORARILY_UNAVAILABLE = "temporarily_unavailable"
    SUBSCRIPTION_REQUIRED = "subscription_required"
    NO_LONGER_AVAILABLE = "no_longer_available"
    TERMS_CHANGED = "terms_changed"
    APPLE_REJECTED = "apple_rejected"
    PAID_NOT_SUPPORTED = "paid_not_supported"
    APP_NOT_FOUND = "app_not_found"
    NO_CATALOG_VERSION = "no_catalog_version"
    RATE_LIMITED = "rate_limited"
    AUTH_SERVER_UNUSABLE = "auth_server_unusable"
    # Transport
    NETWORK = "network"
    TIMEOUT = "timeout"
    # Local / tool
    INVALID_ARGUMENT = "invalid_argument"
    FLAG_UNSUPPORTED = "flag_unsupported"
    BINARY_MISSING = "binary_missing"
    BAD_OUTPUT = "bad_output"
    UNKNOWN = "unknown"


#: Russian user-facing text, from the catalogue in app-store-mechanisms.md.
MESSAGES_RU: Mapping[ErrorCode, str] = {
    ErrorCode.TOKEN_EXPIRED: "Сессия Apple ID истекла. Войдите заново.",
    ErrorCode.AUTH_CODE_REQUIRED: "Нужен код подтверждения. Войдите заново и введите 6 цифр с доверенного устройства.",
    ErrorCode.TWO_FACTOR_REJECTED: "Код подтверждения не принят. Запросите новый и введите сразу.",
    ErrorCode.INVALID_CREDENTIALS: "Неверный пароль Apple ID. Проверьте и повторите.",
    ErrorCode.PASSWORD_CHANGED: "Пароль Apple ID изменился. Войдите заново.",
    ErrorCode.ACCOUNT_DISABLED: "Apple заблокировала этот Apple ID для магазина. Войдите на appleid.apple.com или обратитесь в поддержку.",
    ErrorCode.DEVICE_VERIFICATION: "Apple просит подтвердить устройство. Повторите вход.",
    ErrorCode.NOT_SIGNED_IN: "Вход в Apple ID не выполнен. Войдите в аккаунт.",
    ErrorCode.KEYCHAIN_PASSPHRASE_REQUIRED: "Нужен пароль связки ключей программы (это не пароль Apple ID).",
    ErrorCode.KEYCHAIN_PASSPHRASE_WRONG: "Пароль связки ключей программы не подошёл (это не пароль Apple ID).",
    ErrorCode.PASSPHRASE_NO_SECURE_METHOD: "Нет безопасного способа передать пароль связки ключей: нужен ipatool с патчем --keychain-passphrase-stdin, иначе введите пароль вручную.",
    ErrorCode.STORE_MISMATCH: "Магазин в текущем входе не совпадает со страной вашего Apple ID.",
    ErrorCode.LICENSE_REQUIRED: "На этом Apple ID нет лицензии. Бесплатное — кнопкой «Получить», платное без оплаты не ставится.",
    ErrorCode.LICENSE_ALREADY_EXISTS: "Лицензия на это приложение уже есть.",
    ErrorCode.TEMPORARILY_UNAVAILABLE: "Временно недоступно в этом магазине.",
    ErrorCode.SUBSCRIPTION_REQUIRED: "Нужна подписка Apple Arcade. Без неё скачать нельзя.",
    ErrorCode.NO_LONGER_AVAILABLE: "Apple не отдаёт пакет по обычному каналу. Если запасной путь не поможет — приложения нет для этого аккаунта.",
    ErrorCode.TERMS_CHANGED: "В App Store на телефоне примите обновлённые условия Apple, затем повторите.",
    ErrorCode.APPLE_REJECTED: "Apple отклонила запрос. Проверьте страну Apple ID и что приложение бесплатное и доступно.",
    ErrorCode.PAID_NOT_SUPPORTED: "Платные приложения без оплаты не загружаются.",
    ErrorCode.APP_NOT_FOUND: "В магазине этой страны карточки нет. Если приложение было у вас — ищем через покупки.",
    ErrorCode.NO_CATALOG_VERSION: "В каталоге Apple нет актуальной версии (часто снятое приложение). Нужна сохранённая версия или лицензия.",
    ErrorCode.RATE_LIMITED: "Слишком много попыток входа. Подождите несколько минут.",
    ErrorCode.AUTH_SERVER_UNUSABLE: "Сервер входа Apple не ответил нормально. Повторите позже или из другой сети.",
    ErrorCode.NETWORK: "Нет связи с Apple. Проверьте подключение к интернету и попробуйте ещё раз.",  # R5 (Лена): без VPN
    ErrorCode.TIMEOUT: "Apple не ответила вовремя. Проверьте интернет и повторите.",
    ErrorCode.INVALID_ARGUMENT: "Неверный параметр запроса к ipatool.",
    ErrorCode.FLAG_UNSUPPORTED: "Эта сборка ipatool не поддерживает нужную функцию. Обновите AppRestore.",
    ErrorCode.BINARY_MISSING: "Не найден ipatool. Переустановите AppRestore.",
    ErrorCode.BAD_OUTPUT: "ipatool ответил в неожиданном формате.",
    ErrorCode.UNKNOWN: "Неизвестная ошибка ipatool. Повторите позже.",
}

#: Codes meaning "the saved session cannot be used without the user".
#: STORE_MISMATCH is deliberately NOT here (the session is alive; see ErrorCode).
SESSION_CODES = frozenset(
    {
        ErrorCode.TOKEN_EXPIRED,
        ErrorCode.AUTH_CODE_REQUIRED,
        ErrorCode.TWO_FACTOR_REJECTED,
        ErrorCode.INVALID_CREDENTIALS,
        ErrorCode.PASSWORD_CHANGED,
        ErrorCode.ACCOUNT_DISABLED,
        ErrorCode.DEVICE_VERIFICATION,
        ErrorCode.NOT_SIGNED_IN,
        ErrorCode.KEYCHAIN_PASSPHRASE_REQUIRED,
        ErrorCode.KEYCHAIN_PASSPHRASE_WRONG,
    }
)

#: Codes meaning "we could not reach Apple"; retry later.
TRANSPORT_CODES = frozenset({ErrorCode.NETWORK, ErrorCode.TIMEOUT})

# Order matters: the first match wins.  Patterns are matched case-insensitively
# against ipatool's JSON "error" field (never against text output).
_RULES: Sequence[tuple[ErrorCode, "re.Pattern[str]"]] = tuple(
    (code, re.compile(pattern, re.IGNORECASE))
    for code, pattern in (
        # First: Apple's own text for -128 is definitive and must beat every
        # generic rule.  "-128" is matched as a whole number (not -1280, 2-128).
        (ErrorCode.STORE_MISMATCH, r"account not in this store|(?<![\w-])-128(?![\d.])"),
        (ErrorCode.KEYCHAIN_PASSPHRASE_REQUIRED, r"keychain passphrase is required"),
        (ErrorCode.KEYCHAIN_PASSPHRASE_WRONG, r"KeyUnwrap\(\)|integrity check failed"),
        (ErrorCode.NOT_SIGNED_IN, r"could not be found in the keyring|failed to get account"),
        (ErrorCode.TOKEN_EXPIRED, r"password token is expired|\b2034\b|\b2042\b"),
        (ErrorCode.AUTH_CODE_REQUIRED, r"auth code is required|BadLogin"),
        (ErrorCode.TWO_FACTOR_REJECTED, r"did not complete verification|\b5005\b"),
        (ErrorCode.PASSWORD_CHANGED, r"your password has changed"),
        (ErrorCode.ACCOUNT_DISABLED, r"account is disabled"),
        (ErrorCode.DEVICE_VERIFICATION, r"\b1008\b|device verification"),
        (ErrorCode.INVALID_CREDENTIALS, r"-5000\b|invalid credentials"),
        (ErrorCode.RATE_LIMITED, r"rate limited|\b429\b"),
        (ErrorCode.AUTH_SERVER_UNUSABLE, r"no usable authentication response|authentication redirect"),
        (ErrorCode.LICENSE_REQUIRED, r"license is required|\b9610\b"),
        (ErrorCode.LICENSE_ALREADY_EXISTS, r"license already exists|\b5002\b"),
        (ErrorCode.TEMPORARILY_UNAVAILABLE, r"temporarily unavailable|\b2059\b"),
        (ErrorCode.SUBSCRIPTION_REQUIRED, r"subscription required"),
        (ErrorCode.NO_LONGER_AVAILABLE, r"no longer available"),
        (ErrorCode.TERMS_CHANGED, r"\b3038\b|terms and conditions"),
        (ErrorCode.PAID_NOT_SUPPORTED, r"paid apps is not supported"),
        (ErrorCode.NO_CATALOG_VERSION, r"version lookup returned no app"),
        (ErrorCode.APP_NOT_FOUND, r"\bapp not found\b"),
        (ErrorCode.APPLE_REJECTED, r"unknown error has occurred|unable to process|MZCommerce"),
        (ErrorCode.FLAG_UNSUPPORTED, r"unknown flag|unknown shorthand flag"),
        (ErrorCode.INVALID_ARGUMENT, r"must (?:not exceed|be greater than)|invalid platform|either the app ID|cannot be combined"),
        (
            ErrorCode.NETWORK,
            r"no such host|dial tcp|connection refused|connection reset|network is unreachable|"
            r"i/o timeout|tls handshake|context deadline exceeded|proxyconnect|failed to get bag|"
            r"round trip|unexpected EOF|broken pipe|host is down|no route to host",
        ),
    )
)

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_GUID_RE = re.compile(r"(guid=)[0-9A-Fa-f]+")
# key:value / key=value, где ключ — чувствительное имя (cookie, токен, dsid,
# wosid-lite и т.п.). Значение (в кавычках или до разделителя ; , } пробел)
# заменяется на <redacted>. Регистр ключа игнорируется.
_SENSITIVE_KV_RE = re.compile(
    r'("?(?:set-cookie|cookie|wosid[\w-]*|mz_at[\w-]*|dsid|x-dsid|'
    r'x-apple-[\w-]*|passwordtoken|password|token|x-token|authorization|'
    r'x-apple-session-token|x-apple-jingle-correlation-key)"?\s*[:=]\s*)'
    r'("[^"]*"|[^\s,;}]+)',
    re.IGNORECASE,
)
# отдельные cookie-пары вида wosid-lite=VALUE внутри строки Set-Cookie.
_COOKIE_PAIR_RE = re.compile(
    r'\b(wosid[\w-]*|mz_at[\w-]*|itspod|mzf_[\w-]*)=([^\s;,"}]+)',
    re.IGNORECASE,
)
# длинный base64/hex токен, не привязанный к ключу.
_LONG_TOKEN_RE = re.compile(r"(?<![\w/+])[A-Za-z0-9+/_-]{32,}={0,2}(?![\w/+])")


def _redact(text: str, secrets: Sequence[str] = ()) -> str:
    text = _ANSI_RE.sub("", text)
    for secret in secrets:
        if secret:
            text = text.replace(secret, "<redacted>")
    text = _EMAIL_RE.sub("<email>", text)
    text = _SENSITIVE_KV_RE.sub(r"\1<redacted>", text)
    text = _COOKIE_PAIR_RE.sub(r"\1=<redacted>", text)
    text = _LONG_TOKEN_RE.sub("<token>", text)
    return _GUID_RE.sub(r"\1<guid>", text)


def scrub_line(line: str, secrets: Sequence[str] = ()) -> str:
    """Один проход скраббера над строкой вывода ipatool (для --verbose).

    Снимает ANSI, маскирует cookie (в т.ч. wosid-lite, Set-Cookie), X-Apple-*
    токены, dsid, passwordToken, пароль связки (через ``secrets``), почту, guid и
    длинные токены. Применяйте к КАЖДОЙ строке до print/записи в лог.
    """

    return _redact(line, secrets)


def classify_error(message: str) -> ErrorCode:
    """Map ipatool's JSON ``error`` string to an :class:`ErrorCode`."""

    for code, pattern in _RULES:
        if pattern.search(message or ""):
            return code
    return ErrorCode.UNKNOWN


def _ci_get(mapping: object, key: str) -> object:
    """dict.get без учёта регистра ключа."""

    if not isinstance(mapping, Mapping):
        return None
    target = key.casefold()
    for name, value in mapping.items():
        if str(name).casefold() == target:
            return value
    return None


def apple_failure_type(stdout: str, stderr: str = "") -> str:
    """Непустой Apple ``FailureType`` из verbose-JSON ipatool, РЕГИСТРОНЕЗАВИСИМО.

    ipatool печатает ответ Apple как ``metadata.Data.FailureType`` (именно так, с
    заглавной F), а также иногда как верхнеуровневый ``failureType``. Возвращает
    строку failureType, если она непустая и не "0" (= Apple ЯВНО отказала), иначе
    "". Правило вызывающего кода: непустой failureType → отказ → в журнал НЕ писать.
    """

    for row in _json_lines(stdout) + _json_lines(stderr):
        candidates = []
        meta = _ci_get(row, "metadata")
        data = _ci_get(meta, "data") if meta is not None else None
        candidates.append(_ci_get(data, "failureType") if data is not None else None)
        candidates.append(_ci_get(row, "failureType"))
        for value in candidates:
            if value is None:
                continue
            text = str(value).strip()
            if text and text != "0":
                return text
    return ""


#: Apple failureType for "Account Not In This Store".
STORE_MISMATCH_FAILURE_TYPE = "-128"


def _apple_customer_messages(stdout: str, stderr: str = "") -> list[str]:
    """Все непустые ``customerMessage`` и ``error`` из JSON-строк, регистронезависимо по ключу."""

    texts: list[str] = []
    for row in _json_lines(stdout) + _json_lines(stderr):
        meta = _ci_get(row, "metadata")
        data = _ci_get(meta, "data") if meta is not None else None
        for value in (_ci_get(data, "customerMessage"), _ci_get(row, "customerMessage"),
                      _ci_get(row, "error")):
            if value is not None and str(value).strip():
                texts.append(str(value))
    return texts


def classify_failure(stdout: str, stderr: str = "") -> Optional[ErrorCode]:
    """Код отказа Apple по verbose-JSON ipatool (purchase/download), или None.

    * failureType ``-128`` (строка или число) → :attr:`ErrorCode.STORE_MISMATCH`;
    * customerMessage / error содержит "Account Not In This Store" (любой регистр,
      в т.ч. форма ipatool "failed to purchase item with param 'STDQ': Account
      Not In This Store") → STORE_MISMATCH, даже без failureType;
    * иной непустой failureType → :func:`classify_error` по тексту Apple, а если
      текст неизвестен (например 2040 "Purchase of this item is not currently
      available") → :attr:`ErrorCode.APPLE_REJECTED` (генерик-отказ);
    * отказа нет → None.

    Любой не-None результат — это отказ: в журнал лицензий НЕ писать.
    """

    failure = apple_failure_type(stdout, stderr)
    if failure == STORE_MISMATCH_FAILURE_TYPE:
        return ErrorCode.STORE_MISMATCH
    texts = _apple_customer_messages(stdout, stderr)
    if any("account not in this store" in t.casefold() for t in texts):
        return ErrorCode.STORE_MISMATCH
    if not failure:
        return None
    for text in texts:
        code = classify_error(text)
        if code not in (ErrorCode.UNKNOWN, ErrorCode.STORE_MISMATCH):
            return code
    return ErrorCode.APPLE_REJECTED


def purchase_refused(stdout: str, stderr: str = "") -> bool:
    """True, если Apple явно отказала в purchase (непустой FailureType)."""

    return bool(apple_failure_type(stdout, stderr))


def run_verbose_scrubbed(
    argv: Sequence[str],
    *,
    timeout: float = 120.0,
    sink: Optional[Callable[[str, str], None]] = None,
    secrets: Sequence[str] = (),
    popen: Optional[Callable[..., object]] = None,
) -> RunResult:
    """Запустить ipatool и читать вывод ПОТОКОВО, скрабя КАЖДУЮ строку до вывода.

    stdout и stderr читаются построчно; каждая строка проходит :func:`scrub_line`
    ДО того как попадёт в ``sink`` (по умолчанию ничего не печатает — передайте,
    например, ``lambda tag, line: print(line)``). Так сырые cookie (wosid-lite,
    Set-Cookie), X-Apple-* токены, dsid и пароль не мелькают в терминале/логе.
    Возвращает :class:`RunResult` с уже заскрабленными stdout/stderr.
    """

    import threading

    spawn = popen or subprocess.Popen
    proc = spawn(  # noqa: S603 - fixed argv, no shell
        list(argv),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    out_lines: list[str] = []
    err_lines: list[str] = []

    def pump(stream, bucket: list[str], tag: str) -> None:
        if stream is None:
            return
        for raw in stream:
            safe = scrub_line(raw.rstrip("\n"), secrets)
            bucket.append(safe)
            if sink is not None:
                sink(tag, safe)

    err_thread = threading.Thread(target=pump, args=(proc.stderr, err_lines, "stderr"))
    err_thread.start()
    pump(proc.stdout, out_lines, "stdout")
    err_thread.join()
    returncode = proc.wait(timeout=timeout) if hasattr(proc, "wait") else 0
    return RunResult(int(returncode or 0), "\n".join(out_lines), "\n".join(err_lines))


class IpatoolError(Exception):
    """A typed ipatool failure.  ``str()`` is the Russian user text."""

    def __init__(self, code: ErrorCode, detail: str = "") -> None:
        self.code = code
        self.message_ru = MESSAGES_RU[code]
        #: Raw ipatool error with email/guid/passphrase removed.  For logs.
        self.detail = detail
        super().__init__(self.message_ru)

    @property
    def is_session_problem(self) -> bool:
        return self.code in SESSION_CODES

    @property
    def is_transport_problem(self) -> bool:
        return self.code in TRANSPORT_CODES

    def __repr__(self) -> str:
        return f"IpatoolError(code={self.code.value!r}, detail={self.detail!r})"


# --------------------------------------------------------------------------
# Result types
# --------------------------------------------------------------------------


class SessionState(str, enum.Enum):
    ALIVE = "alive"
    EXPIRED = "expired"
    NO_NETWORK = "no_network"


@dataclass(frozen=True)
class SessionCheck:
    state: SessionState
    elapsed_s: float
    #: Why the session is not ALIVE (None when ALIVE).
    error: Optional[IpatoolError] = None
    #: Which network call was used for the probe.
    probe: str = "list-versions"

    @property
    def message_ru(self) -> str:
        if self.state is SessionState.ALIVE:
            return "Сессия Apple ID активна."
        assert self.error is not None
        return self.error.message_ru


@dataclass(frozen=True)
class Purchase:
    track_id: int
    bundle_id: str
    name: str
    purchase_date: Optional[datetime]
    version: str = ""
    price: float = 0.0
    platforms: tuple[str, ...] = ()

    @property
    def cache_key(self) -> tuple[int, str, str, str]:
        """(track_id, bundle_id, name, purchase_date ISO or "") for GUI caches.

        ``track_id`` alone identifies the app; name and date are included so a
        cache entry is refreshed when Apple renames the app or the purchase
        date changes.
        """

        date = self.purchase_date.isoformat() if self.purchase_date else ""
        return (self.track_id, self.bundle_id, self.name, date)

    @classmethod
    def from_json(cls, data: Mapping[str, object]) -> "Purchase":
        raw_id = data.get("id", data.get("trackId"))
        if raw_id is None:
            raise IpatoolError(ErrorCode.BAD_OUTPUT, "purchase without id")
        return cls(
            track_id=int(raw_id),  # type: ignore[arg-type]
            bundle_id=str(data.get("bundleID", data.get("bundleId", "")) or ""),
            name=str(data.get("name", data.get("trackName", "")) or ""),
            purchase_date=_parse_time(data.get("purchaseDate")),
            version=str(data.get("version", "") or ""),
            price=float(data.get("price", 0) or 0),  # type: ignore[arg-type]
            platforms=tuple(str(p) for p in (data.get("platforms") or ())),  # type: ignore[union-attr]
        )


@dataclass(frozen=True)
class PurchasesPage:
    page: int
    page_size: int
    items: tuple[Purchase, ...]
    #: Total purchases reported by ipatool (``totalCount``), or None if absent.
    total: Optional[int]
    #: Number of pages, derived from ``total`` (None if total is unknown).
    pages: Optional[int]
    is_last: bool
    elapsed_s: float = 0.0


@dataclass(frozen=True)
class AccountInfo:
    name: str
    #: Raw X-Set-Apple-Store-Front value ("143441-1,34") when the patched
    #: ipatool reports it, else None.
    store_front: Optional[str] = None
    #: ISO country ("US") when the patched ipatool reports it, else None.
    country_code: Optional[str] = None
    email: str = field(default="", repr=False)  # never printed by repr()


def _parse_time(value: object) -> Optional[datetime]:
    if not value:
        return None
    text = str(value)
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


# --------------------------------------------------------------------------
# Process runner (injectable for tests)
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class RunResult:
    returncode: int
    stdout: str
    stderr: str


class CancelEvent(Protocol):
    def is_set(self) -> bool: ...


#: ``runner(argv, timeout, env, stdin=None) -> RunResult``; ``env`` is the
#: complete child environment and ``stdin`` is text fed to the child's stdin
#: (used only for ``--keychain-passphrase-stdin``; ``None`` means no stdin).
#: Raises subprocess.TimeoutExpired on timeout and FileNotFoundError if the
#: binary is missing.
Runner = Callable[..., RunResult]

#: How the keychain passphrase reaches ipatool:
#:   "auto" — stdin on a patched binary (``--keychain-passphrase-stdin``);
#:            on an old binary NO secure method -> PASSPHRASE_NO_SECURE_METHOD
#:            (never the ``--keychain-passphrase`` flag, never env, both leak:
#:            flag shows in ``ps``, env in ``/proc/<pid>/environ``). The caller
#:            (GUI) then collects the passphrase interactively.
#:   "flag" — ``--keychain-passphrase <pass>`` (bench only; visible in ps).
#:   "env"  — IPATOOL_KEYCHAIN_PASSPHRASE env var (bench only; visible in
#:            /proc/<pid>/environ). Only ever chosen when asked explicitly.
PassphraseVia = str  # "auto" | "env" | "flag"

#: The stdin passphrase flag; present in ``ipatool --help`` only with AppRestore's
#: patch 0003.  Its presence is how "auto" detects a patched binary.
PASSPHRASE_STDIN_FLAG = "--keychain-passphrase-stdin"
#: Back-compat alias (patch 0003 named it this).
PASSPHRASE_ENV_MARKER = PASSPHRASE_STDIN_FLAG


def _subprocess_runner(argv: Sequence[str], timeout: float, env: Mapping[str, str],
                       stdin: Optional[str] = None) -> RunResult:
    # When a passphrase is fed via stdin we pass it as the first line; otherwise
    # stdin is /dev/null so ipatool never blocks waiting for input.
    completed = subprocess.run(  # noqa: S603 - fixed argv, no shell
        list(argv),
        env=dict(env),
        input=(stdin + "\n") if stdin is not None else None,
        stdin=None if stdin is not None else subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )
    return RunResult(completed.returncode, completed.stdout, completed.stderr)


def _json_lines(text: str) -> list[dict]:
    rows: list[dict] = []
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            rows.append(value)
    return rows


# --------------------------------------------------------------------------
# Client
# --------------------------------------------------------------------------


class IpatoolClient:
    """Thin, typed, read-only wrapper over one ipatool binary."""

    def __init__(
        self,
        binary: str,
        *,
        keychain_passphrase: Optional[str] = None,
        runner: Optional[Runner] = None,
        probe_app_id: int = PROBE_APP_ID,
        clock: Callable[[], float] = time.monotonic,
        passphrase_via: PassphraseVia = "auto",
        base_env: Optional[Mapping[str, str]] = None,
    ) -> None:
        if passphrase_via not in ("auto", "env", "flag"):
            raise ValueError("passphrase_via must be 'auto', 'env' or 'flag'")
        self.binary = binary
        self._passphrase = (
            keychain_passphrase if keychain_passphrase is not None else os.environ.get(PASSPHRASE_ENV, "")
        )
        self._run = runner or _subprocess_runner
        self.probe_app_id = probe_app_id
        self._clock = clock
        self._passphrase_via = passphrase_via
        self._base_env = dict(os.environ if base_env is None else base_env)
        self._stdin_supported: Optional[bool] = None

    # -- plumbing ---------------------------------------------------------

    def _stdin_flag_supported(self) -> bool:
        """True when this ipatool understands ``--keychain-passphrase-stdin``.

        Probed once via ``ipatool --help`` (no keychain, network or account
        access).  Result is cached.
        """

        if self._stdin_supported is None:
            try:
                result = self._run([self.binary, "--help"], 20.0,
                                   self._env_without_passphrase(), None)
                self._stdin_supported = PASSPHRASE_STDIN_FLAG in (result.stdout + result.stderr)
            except (OSError, subprocess.SubprocessError):
                self._stdin_supported = False
        return self._stdin_supported

    def passphrase_method(self) -> str:
        """How the passphrase will be delivered: 'stdin' | 'flag' | 'env' | 'none'.

        'none' means "auto" found no secure channel (old binary): the caller must
        collect the passphrase interactively (GUI hidden terminal) instead. The
        flag and env channels are chosen ONLY when asked for explicitly, because
        both leak the secret (flag -> ps, env -> /proc/<pid>/environ).
        """

        if self._passphrase_via == "flag":
            return "flag"
        if self._passphrase_via == "env":
            return "env"
        # auto: stdin on a patched binary, otherwise no secure method at all.
        return "stdin" if self._stdin_flag_supported() else "none"

    def passphrase_uses_env(self) -> bool:
        """Back-compat: True only when the passphrase is delivered via env."""

        return self.passphrase_method() == "env"

    def _env_without_passphrase(self) -> dict[str, str]:
        # Never inherit a stray IPATOOL_KEYCHAIN_PASSPHRASE from the parent: a
        # patched ipatool ignores env when fed via stdin, but we also refuse to
        # pass it along so it can never leak via the child's /proc environ.
        env = dict(self._base_env)
        env.pop(PASSPHRASE_ENV, None)
        return env

    def _child_env(self) -> dict[str, str]:
        env = self._env_without_passphrase()
        if self._passphrase and self.passphrase_method() == "env":
            env[PASSPHRASE_ENV] = self._passphrase
        return env

    def _passphrase_stdin(self) -> Optional[str]:
        """Text to feed on stdin, or None when the passphrase goes another way."""

        if self._passphrase and self.passphrase_method() == "stdin":
            return self._passphrase
        return None

    def _argv(self, *args: str) -> list[str]:
        argv = [self.binary, *args, "--format", "json", "--non-interactive"]
        method = self.passphrase_method()
        if self._passphrase and method == "stdin":
            argv.append(PASSPHRASE_STDIN_FLAG)  # secret goes on stdin, not here
        elif self._passphrase and method == "flag":
            # Explicit opt-in only (bench). Visible in ps; argv is never logged
            # or put into errors by this module.
            argv += ["--keychain-passphrase", self._passphrase]
        return argv

    def _call(self, args: Sequence[str], timeout: float) -> dict:
        """Run ipatool, return the success object or raise IpatoolError."""

        if self._passphrase and self.passphrase_method() == "none":
            raise IpatoolError(
                ErrorCode.PASSPHRASE_NO_SECURE_METHOD,
                "нет безопасного способа передать passphrase: этот ipatool без "
                "патча --keychain-passphrase-stdin; введите пароль интерактивно "
                "(скрытый терминал), флаг и env не используем — они светят пароль",
            )
        try:
            result = self._run(self._argv(*args), timeout, self._child_env(),
                               self._passphrase_stdin())
        except subprocess.TimeoutExpired:
            raise IpatoolError(ErrorCode.TIMEOUT, f"ipatool {args[0]} timed out after {timeout:g}s") from None
        except FileNotFoundError:
            raise IpatoolError(ErrorCode.BINARY_MISSING, "ipatool binary not found") from None
        except PermissionError:
            raise IpatoolError(ErrorCode.BINARY_MISSING, "ipatool binary is not executable") from None

        rows = _json_lines(result.stdout) + _json_lines(result.stderr)
        failure = next((row for row in rows if row.get("success") is False), None)
        if failure is not None or result.returncode != 0:
            raw = str((failure or {}).get("error") or "")
            if not raw:
                tail = (result.stderr or result.stdout).strip().splitlines()[-1:] or [""]
                raw = tail[0][:500]
            detail = _redact(raw, (self._passphrase,))
            code = classify_error(raw)
            if classify_failure(result.stdout, result.stderr) is ErrorCode.STORE_MISMATCH:
                code = ErrorCode.STORE_MISMATCH  # -128 in metadata, even if "error" is vague
            raise IpatoolError(code, detail)

        success = next((row for row in reversed(rows) if row.get("level") == "info"), None)
        if success is None:
            raise IpatoolError(ErrorCode.BAD_OUTPUT, f"no JSON result from ipatool {args[0]}")
        return success

    # -- public API -------------------------------------------------------

    def account_info(self, timeout: float = 10.0) -> AccountInfo:
        """Saved account from the keychain.  Offline: proves nothing about the token."""

        data = self._call(["auth", "info"], timeout)
        return AccountInfo(
            name=str(data.get("name", "") or ""),
            store_front=(str(data["storeFront"]) if data.get("storeFront") else None),
            country_code=(str(data["countryCode"]) if data.get("countryCode") else None),
            email=str(data.get("email", "") or ""),
        )

    def session_alive(self, timeout: float = 8.0) -> SessionCheck:
        """One authenticated network round trip; three outcomes.

        Probe: ``ipatool list-versions -i PROBE_APP_ID`` — one signed request
        with the saved token to Apple's download endpoint, returns only version
        identifiers (no package download, no purchase).

        * ALIVE      — Apple answered with versions, or with "license is
                       required" (9610), which also needs a valid token.
        * EXPIRED    — a session/keychain code (see SESSION_CODES).  Note that
                       ipatool itself retries once with a silent re-login using
                       the saved password when Apple says 2034/2042; EXPIRED
                       therefore means "even the re-login failed" (2FA needed,
                       password changed, …) or there is no saved account.
        * NO_NETWORK — timeout, DNS, refused/reset connection, or an unknown
                       failure (we only declare EXPIRED on a definite signal,
                       so the GUI never forces a re-login on a flaky line).
        """

        timeout = max(1.0, float(timeout))
        started = self._clock()
        try:
            self._call(["list-versions", "-i", str(self.probe_app_id)], timeout)
        except IpatoolError as error:
            elapsed = self._clock() - started
            if error.code is ErrorCode.BINARY_MISSING:
                raise
            if error.code is ErrorCode.LICENSE_REQUIRED:
                return SessionCheck(SessionState.ALIVE, elapsed)
            if error.code in SESSION_CODES:
                return SessionCheck(SessionState.EXPIRED, elapsed, error)
            # Transport failures, plus store-side oddities (5002, "unknown
            # error", …) or unknown output: the token was not rejected, but we
            # cannot claim the session is fine either.  Report "try again".
            return SessionCheck(SessionState.NO_NETWORK, elapsed, error)
        return SessionCheck(SessionState.ALIVE, self._clock() - started)

    def all_purchases(self, *, timeout: float = 120.0, platform: Optional[str] = None) -> PurchasesPage:
        """Whole history in one call: ``ipatool list-purchases --all``.

        Needs AppRestore's ipatool patch 0002; an unpatched ipatool raises
        IpatoolError(FLAG_UNSUPPORTED).
        """

        args = ["list-purchases", "--all"]
        if platform:
            args += ["--platform", platform]
        started = self._clock()
        data = self._call(args, timeout)
        page = self._page_from(data, page=1, page_size=0, elapsed=self._clock() - started)
        size = len(page.items)
        return PurchasesPage(
            page=1,
            page_size=size,
            items=page.items,
            total=page.total if page.total is not None else size,
            pages=1,
            is_last=True,
            elapsed_s=page.elapsed_s,
        )

    @staticmethod
    def _page_from(data: Mapping[str, object], *, page: int, page_size: int, elapsed: float) -> PurchasesPage:
        raw_apps = data.get("apps") or []
        if not isinstance(raw_apps, list):
            raise IpatoolError(ErrorCode.BAD_OUTPUT, "list-purchases: 'apps' is not a list")
        items = tuple(Purchase.from_json(app) for app in raw_apps if isinstance(app, dict))
        total_raw = data.get("totalCount")
        total = int(total_raw) if isinstance(total_raw, (int, float)) and not isinstance(total_raw, bool) else None
        if page_size > 0:
            pages = math.ceil(total / page_size) if total is not None else None
            is_last = page * page_size >= total if total is not None else len(items) < page_size
        else:
            pages, is_last = 1, True
        return PurchasesPage(page, page_size, items, total, pages, is_last, elapsed)

    def iter_purchases(
        self,
        cancel_event: Optional[CancelEvent] = None,
        *,
        page_size: int = PAGE_SIZE_MAX,
        timeout: float = 120.0,
        platform: Optional[str] = None,
        start_page: int = 1,
        prefer_all: bool = True,
    ) -> Iterator[PurchasesPage]:
        """Yield purchase-history pages (newest purchase first).

        With ``prefer_all`` (default) and ``start_page == 1`` the first call is
        ``list-purchases --all`` (AppRestore's ipatool patch 0002): the whole
        history arrives as ONE page (~10 s for 2.8k purchases instead of
        ~4.5 min).  An ipatool without the patch answers "unknown flag", and
        the generator silently falls back to pages of ``page_size``.

        ``cancel_event`` (anything with ``is_set()``, e.g. threading.Event) is
        checked before every page request; a set event ends the generator
        without an error.  A running ipatool call is never killed, so the
        keychain/cookie files it may write stay consistent.

        Note: ipatool fetches the *whole* history from Apple on every call and
        slices the page locally (~9 s per page for ~2.8k purchases).  The list
        is sorted by purchase date, so a purchase made during iteration can
        shift items between pages; dedupe on ``track_id``.

        Raises IpatoolError on failure (EXPIRED/NO_NETWORK etc. by ``code``).
        """

        if not 1 <= page_size <= PAGE_SIZE_MAX:
            raise IpatoolError(ErrorCode.INVALID_ARGUMENT, f"page_size must be 1..{PAGE_SIZE_MAX}")
        if start_page < 1:
            raise IpatoolError(ErrorCode.INVALID_ARGUMENT, "start_page must be >= 1")

        if prefer_all and start_page == 1:
            if cancel_event is not None and cancel_event.is_set():
                return
            try:
                yield self.all_purchases(timeout=timeout, platform=platform)
                return
            except IpatoolError as error:
                if error.code is not ErrorCode.FLAG_UNSUPPORTED:
                    raise

        page = start_page
        while True:
            if cancel_event is not None and cancel_event.is_set():
                return
            args = ["list-purchases", "--page", str(page), "--max-results", str(page_size)]
            if platform:
                args += ["--platform", platform]
            started = self._clock()
            data = self._call(args, timeout)
            result = self._page_from(data, page=page, page_size=page_size, elapsed=self._clock() - started)
            yield result
            if result.is_last or not result.items:
                return
            page += 1
