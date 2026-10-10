#!/usr/bin/env python3
"""Замер пути AppRestore «сессия → lookup → лицензия → скачивание → установка» (M-1).

Пишет JSON: время, результат и код ошибки каждого шага в каждом прогоне,
плюс агрегат по шагам (медиана, p90, доля сбоев, главные причины).

Режимы (ровно один):
  --dry-run   по умолчанию: печатает план и команды, ничего не запускает;
  --mock FILE прогоняет весь путь на записанных расшифровках (без аккаунта и сети);
  --real      настоящий ipatool и iTunes Lookup. Только после того, как Евгений
              выбрал Apple ID для замеров. Вход в Apple ID скрипт НЕ делает:
              сессия должна быть открыта заранее (`apprestore auth`).

Безопасность:
  * `ipatool download` НИКОГДА не получает `--purchase` (см. download_command,
    там стоит assert).
  * Лицензия берётся только отдельным шагом `ipatool purchase`, только для
    бесплатных приложений (price == 0 по lookup ДО вызова), только с флагом
    --allow-free-license, и не больше 5 за сутки / 15 всего по журналу
    licenses_acquired.jsonl (лимиты настраиваются флагами).
  * В отчёт не попадают Apple ID, телефоны, пароли, токены, UDID и пути
    с именем пользователя: всё проходит через mask_obj().

Шаг лицензии и все механизмы цепочки --fallback помечены LEGAL_REVIEW:
«требует правовой проверки Лены». Только официальные механизмы Apple,
никакого обхода DRM и шифрования.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import statistics
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable

SCHEMA = "apprestore-bench/1"
LEGAL_REVIEW = "требует правовой проверки Лены"
STEPS = ("session", "lookup", "search", "license", "download", "install", "total")
DEFAULT_STOREFRONTS = ("us", "ru", "gb", "de", "kz")
DEFAULT_FALLBACK = ("retry", "refresh-session", "storefronts", "old-versions")
FALLBACKS = {
    "retry": "повтор с экспоненциальной паузой (только сеть/тайм-аут)",
    "refresh-session": "повторная проверка сессии `auth info` (без входа и пароля)",
    "storefronts": "iTunes Lookup в других storefront и для iPad (entity=iPadSoftware)",
    "old-versions": "`list-versions` + `download --external-version-id` (старые версии)",
}
LICENSE_DAILY_LIMIT = 5
LICENSE_TOTAL_LIMIT = 15
MB = 1024 * 1024

# ---------------------------------------------------------------- маскирование

_EMAIL = re.compile(r"[\w.+\-]+@[\w\-]+(?:\.[\w\-]+)+")
_PHONE = re.compile(r"(?<![\w.])\+\d[\d \-()]{8,}\d")
_UDID = re.compile(
    r"\b(?:[0-9A-Fa-f]{8}-[0-9A-Fa-f]{16}"
    r"|[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}"
    r"|[0-9A-Fa-f]{40})\b"
)
_TOKEN = re.compile(r"(?<![\w/+])[A-Za-z0-9+/_\-]{32,}={0,2}(?![\w/+])")
_POSIX_HOME = re.compile(r"(/Users/|/home/)[^/\s\"':]+")
_WIN_HOME = re.compile(
    r"([A-Za-z]:(?:\\\\|\\|/)Users(?:\\\\|\\|/))[^\\/\s\"':]+", re.IGNORECASE
)
_SECRET_KEYS = {
    "email", "appleid", "apple_id", "account", "accountname",
    "password", "passphrase", "keychainpassphrase", "token", "passwordtoken",
    "dsid", "cookie", "cookies", "udid", "serial", "serialnumber",
    "authcode", "auth_code", "phone",
}


def mask_text(text: str, secrets: Iterable[str] = ()) -> str:
    """Убрать из строки всё, что может указать на человека или дать доступ."""

    if not text:
        return text
    out = str(text)
    for secret in secrets:
        if secret and len(secret) >= 3:
            out = out.replace(secret, "[secret]")
    home = str(Path.home())
    if len(home) > 1:
        out = out.replace(home, "~")
    out = _EMAIL.sub("[email]", out)
    out = _UDID.sub("[udid]", out)
    out = _TOKEN.sub("[token]", out)
    out = _PHONE.sub("[phone]", out)
    out = _POSIX_HOME.sub(r"\1[user]", out)
    out = _WIN_HOME.sub(r"\1[user]", out)
    return out


def mask_obj(value: Any, secrets: Iterable[str] = ()) -> Any:
    """Рекурсивно: ключи из _SECRET_KEYS → «[redacted]», строки → mask_text."""

    secrets = tuple(secrets)
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in value.items():
            flat = str(key).replace("-", "").replace("_", "").casefold()
            if flat in _SECRET_KEYS and item not in (None, "", False):
                out[key] = "[redacted]"
            else:
                out[key] = mask_obj(item, secrets)
        return out
    if isinstance(value, (list, tuple)):
        return [mask_obj(item, secrets) for item in value]
    if isinstance(value, str):
        return mask_text(value, secrets)
    return value


# ---------------------------------------------------------------- коды ошибок

_ERROR_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("keychain_locked", ("keychain passphrase", "passphrase is required",
                         "failed to decrypt", "handle is invalid")),
    ("token_expired", ("password token is expired", "token is expired")),
    ("not_authenticated", ("not authenticated", "failed to get account",
                           "item not found", "could not be found")),
    ("auth_redirect", ("authentication redirect",)),
    ("bad_credentials", ("invalid password", "incorrect password", "bad credentials")),
    ("license_required", ("license is required", "license not found", "license required")),
    ("subscription_required", ("subscription required",)),
    ("purchase_refused", ("failed to purchase", "purchasing paid apps",
                          "paid apps are not supported")),
    ("region", ("unavailable in this region", "another store",
                "not available in your country")),
    ("temporarily_unavailable", ("temporarily unavailable",)),
    ("app_not_found", ("app not found", "failed to find app",
                       "returned no app", "no results")),
    ("incompatible", ("incompatible", "minimumosversion",
                      "unsupported platform", "does not support")),
    ("disk_full", ("no space left", "not enough space", "disk full")),
    ("timeout", ("timeout", "timed out", "deadline exceeded")),
    ("network", ("dial tcp", "connection reset", "connection refused",
                 "no such host", "failed to get bag", "unexpected eof",
                 "broken pipe", "network is unreachable", "request failed")),
)
RETRYABLE = {"network", "timeout", "temporarily_unavailable"}
SESSION_CODES = {"token_expired", "not_authenticated"}
VERSION_CODES = {"incompatible", "app_not_found"}
STOREFRONT_CODES = {"app_not_found", "region", "incompatible"}


def classify_error(text: str) -> str:
    low = (text or "").casefold()
    for code, needles in _ERROR_RULES:
        if any(needle in low for needle in needles):
            return code
    return "unknown"


def ipatool_events(stdout: str, stderr: str = "") -> list[dict[str, Any]]:
    """События zerolog из `--format json` (по одному JSON-объекту в строке)."""

    events = []
    for line in f"{stdout}\n{stderr}".splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            events.append(item)
    return events


def ipatool_error(stdout: str, stderr: str) -> str:
    """Текст ошибки: поле `error` из JSON, иначе последняя строка вывода."""

    for event in reversed(ipatool_events(stdout, stderr)):
        if event.get("error"):
            return str(event["error"])
    quoted = re.findall(r'error="([^"]*)"', f"{stdout}\n{stderr}")
    if quoted:
        return quoted[-1]
    lines = [ln.strip() for ln in f"{stdout}\n{stderr}".splitlines() if ln.strip()]
    return lines[-1][:300] if lines else ""

# ---------------------------------------------------------------- данные


@dataclass
class AppRef:
    bundle_id: str
    store_id: str = ""

    @classmethod
    def parse(cls, text: str) -> "AppRef":
        text = text.strip()
        if "@" in text:
            bundle, store = text.split("@", 1)
            return cls(bundle.strip(), store.strip())
        if text.isdigit():
            return cls("", text)
        return cls(text, "")

    @property
    def label(self) -> str:
        return self.bundle_id or f"id{self.store_id}"


@dataclass
class Result:
    ok: bool
    seconds: float
    code: str = ""
    error: str = ""
    data: dict[str, Any] = field(default_factory=dict)


def _skipped(reason: str) -> dict[str, Any]:
    return {"status": "skipped", "reason": reason, "seconds": 0.0}


# ---------------------------------------------------------------- журнал лицензий


class LicenseJournal:
    """licenses_acquired.jsonl: одна строка на взятую лицензию, без Apple ID."""

    def __init__(self, path: Path, *, daily_limit: int, total_limit: int,
                 now: Callable[[], dt.datetime]):
        self.path = path
        self.daily_limit = daily_limit
        self.total_limit = total_limit
        self.now = now

    def entries(self) -> list[dict[str, Any]]:
        if not self.path.is_file():
            return []
        items = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(item, dict):
                items.append(item)
        return items

    def counts(self) -> tuple[int, int]:
        entries = self.entries()
        since = self.now() - dt.timedelta(hours=24)
        daily = 0
        for item in entries:
            raw = str(item.get("time", ""))
            try:
                when = dt.datetime.fromisoformat(raw)
            except ValueError:
                daily += 1  # битая дата считается свежей: лимит строже, не мягче
                continue
            if when.tzinfo is None:
                when = when.replace(tzinfo=dt.timezone.utc)
            if when >= since:
                daily += 1
        return daily, len(entries)

    def refusal(self) -> str:
        daily, total = self.counts()
        if total >= self.total_limit:
            return f"лимит лицензий исчерпан: всего {total}/{self.total_limit}"
        if daily >= self.daily_limit:
            return f"лимит лицензий за сутки исчерпан: {daily}/{self.daily_limit}"
        return ""

    def append(self, *, bundle_id: str, app_id: str, storefront: str,
               price: float, mode: str) -> dict[str, Any]:
        entry = {
            "time": self.now().isoformat(timespec="seconds"),
            "bundle_id": bundle_id,
            "app_id": app_id,
            "storefront": storefront,
            "price": price,
            "mode": mode,
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
        return entry


# ---------------------------------------------------------------- лицензия


def _price_of(meta: dict[str, Any]) -> float | None:
    price = meta.get("price")
    try:
        return float(price) if price is not None else None
    except (TypeError, ValueError):
        return None


def acquire_free_license(backend: "Backend", app: AppRef, meta: dict[str, Any],
                         opts: "Options", journal: LicenseJournal,
                         steps: dict[str, Any], original: AppRef) -> bool:
    """Получить бесплатную лицензию. Возвращает True при успехе.

    LEGAL_REVIEW: получение лицензии меняет историю аккаунта — только для
    бесплатных приложений, только с согласия и в пределах лимита.
    """

    def deny(code: str, msg: str) -> bool:
        steps["license"] = {"status": "denied", "seconds": 0.0, "code": code,
                            "error": msg, "legal_review": LEGAL_REVIEW}
        return False

    if not opts.allow_free_license:
        return deny("license_disabled",
                    "нужна лицензия, но шаг выключен (нет --allow-free-license)")
    price = _price_of(meta)
    if price is None:
        return deny("price_unknown", "цена не подтверждена через lookup — лицензию не берём")
    if price > 0:
        return deny("paid_app", f"приложение платное (price={price}) — лицензию не берём")
    refusal = journal.refusal()
    if refusal:
        return deny("license_limit", refusal)

    app_id = app.store_id or meta.get("store_id", "")
    if not app_id:
        return deny("no_app_id", "нет числового App Store ID для purchase")
    res = backend.purchase(app_id)
    rec = {"status": "ok" if res.ok else "fail", "seconds": round(res.seconds, 3),
           "legal_review": LEGAL_REVIEW, "price": price}
    if res.ok:
        entry = journal.append(
            bundle_id=app.bundle_id or meta.get("bundle_id", ""),
            app_id=str(app_id),
            storefront=meta.get("storefront", ""),
            price=price, mode=backend.mode,
        )
        daily, total = journal.counts()
        rec["recorded"] = {k: entry[k] for k in ("bundle_id", "app_id", "storefront")}
        rec["journal_counts"] = {"daily": daily, "total": total}
    else:
        rec["code"] = res.code or "unknown"
        rec["error"] = res.error[:300]
    steps["license"] = rec
    return res.ok

# ---------------------------------------------------------------- бэкенды


class Backend:
    mode = "abstract"
    secrets: tuple[str, ...] = ()

    def begin_run(self, run: int, app: AppRef) -> None: ...
    def sleep(self, seconds: float) -> None: ...
    def auth_info(self) -> Result: raise NotImplementedError
    def lookup(self, app: AppRef, country: str = "", entity: str = "") -> Result: raise NotImplementedError
    def search(self, term: str) -> Result: raise NotImplementedError
    def purchase(self, app_id: str) -> Result: raise NotImplementedError
    def download(self, app: AppRef, output: Path, version: str = "") -> Result: raise NotImplementedError
    def list_versions(self, app: AppRef) -> Result: raise NotImplementedError
    def install(self, ipa: Path) -> Result: raise NotImplementedError
    def describe(self) -> dict[str, Any]: return {}


def download_command(ipatool: str, app: AppRef, output: Path, version: str = "") -> list[str]:
    """Команда скачивания. `--purchase` сюда не попадает НИКОГДА."""

    cmd = [ipatool, "--non-interactive", "--format", "json", "download"]
    if app.store_id:
        cmd += ["--app-id", app.store_id]
    else:
        cmd += ["--bundle-identifier", app.bundle_id]
    if version:
        cmd += ["--external-version-id", version]
    cmd += ["--output", str(output)]
    assert "--purchase" not in cmd, "download must never carry --purchase"
    return cmd


def lookup_result(body: Any, seconds: float, label: str) -> Result:
    results = body.get("results") if isinstance(body, dict) else None
    if not results:
        return Result(False, seconds, "app_not_found", "lookup returned no results",
                      {"storefront": label or "default"})
    row = results[0]
    size = row.get("fileSizeBytes")
    data = {
        "storefront": label or "default",
        "store_id": str(row.get("trackId", "")),
        "bundle_id": row.get("bundleId", ""),
        "price": row.get("price"),
        "currency": row.get("currency", ""),
        "size_bytes": int(size) if str(size).isdigit() else None,
        "kind": row.get("kind", ""),
        "min_os": row.get("minimumOsVersion", ""),
    }
    return Result(True, seconds, data=data)


def classify_install_error(text: str) -> str:
    low = text.casefold()
    if "passwordprotected" in low or "locked" in low:
        return "device_locked"
    if "pair" in low or "trust" in low:
        return "device_not_trusted"
    if "no device" in low or "not connected" in low or "connectionterminated" in low:
        return "device_disconnected"
    if "space" in low:
        return "disk_full"
    return classify_error(text)


def run_install_hook(hook: str, ipa: Path, udid: str, timeout: float) -> Result:
    """Установка на телефон — хук для Димы (D-2).

    ``none``             шаг пропускается;
    ``pymobiledevice3``  `python -m pymobiledevice3 apps install <ipa> [--udid]`;
    ``cmd:<шаблон>``     своя команда, `{ipa}` и `{udid}` подставляются.
    """

    if hook == "pymobiledevice3":
        args = [sys.executable, "-m", "pymobiledevice3", "apps", "install", str(ipa)]
        if udid:
            args += ["--udid", udid]
    elif hook.startswith("cmd:"):
        args = [p.replace("{ipa}", str(ipa)).replace("{udid}", udid) for p in hook[4:].split()]
    else:
        return Result(True, 0.0, data={"status": "skipped"})
    start = time.perf_counter()
    try:
        proc = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return Result(False, time.perf_counter() - start, "timeout", f"install timeout {timeout:.0f}s")
    except OSError as exc:
        return Result(False, 0.0, "tool_missing", str(exc))
    seconds = time.perf_counter() - start
    if proc.returncode == 0:
        return Result(True, seconds)
    text = (proc.stderr or proc.stdout or "").strip()[-300:]
    return Result(False, seconds, classify_install_error(text), text)


class RealBackend(Backend):
    """Настоящий ipatool. Только после выбора Apple ID; вход НЕ делает."""

    mode = "real"

    def __init__(self, ipatool: str, *, install_hook: str, timeouts: dict[str, float]):
        self.ipatool = ipatool
        self.install_hook = install_hook
        self.timeouts = timeouts
        self.passphrase = os.environ.get("APPRESTORE_BENCH_KEYCHAIN_PASSPHRASE", "")
        self.udid = os.environ.get("APPRESTORE_BENCH_UDID", "")
        self.secrets = tuple(s for s in (self.passphrase, self.udid) if s)

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)

    def _base(self) -> list[str]:
        cmd = [self.ipatool, "--non-interactive", "--format", "json"]
        if self.passphrase:
            cmd += ["--keychain-passphrase", self.passphrase]
        return cmd

    def _run(self, args: list[str], timeout: float, watch: Path | None = None) -> Result:
        start = time.perf_counter()
        first_byte = None
        try:
            proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    stdin=subprocess.DEVNULL, text=True,
                                    encoding="utf-8", errors="replace")
        except OSError as exc:
            return Result(False, 0.0, "tool_missing", str(exc))
        deadline = start + timeout
        while proc.poll() is None:
            if watch is not None and first_byte is None:
                tmp = Path(f"{watch}.tmp")
                if tmp.exists() and tmp.stat().st_size > 0:
                    first_byte = time.perf_counter() - start
            if time.perf_counter() > deadline:
                proc.kill()
                proc.communicate()
                return Result(False, time.perf_counter() - start, "timeout",
                              f"step timeout {timeout:.0f}s")
            time.sleep(0.2)
        stdout, stderr = proc.communicate()
        seconds = time.perf_counter() - start
        data: dict[str, Any] = {"events": ipatool_events(stdout, stderr)}
        if first_byte is not None:
            data["first_byte_s"] = round(first_byte, 3)
        if proc.returncode == 0:
            return Result(True, seconds, data=data)
        error = ipatool_error(stdout, stderr)
        return Result(False, seconds, classify_error(error), error, data)

    def auth_info(self) -> Result:
        res = self._run(self._base() + ["auth", "info"], self.timeouts["session"])
        res.data = {"authenticated": res.ok}  # ни почты, ни имени
        return res

    def lookup(self, app: AppRef, country: str = "", entity: str = "") -> Result:
        query = {"id": app.store_id} if app.store_id else {"bundleId": app.bundle_id}
        if country:
            query["country"] = country
        if entity:
            query["entity"] = entity
        url = "https://itunes.apple.com/lookup?" + urllib.parse.urlencode(query)
        label = country + ("/" + entity if entity else "")
        start = time.perf_counter()
        try:
            with urllib.request.urlopen(url, timeout=self.timeouts["lookup"]) as resp:  # noqa: S310
                body = json.loads(resp.read().decode("utf-8"))
        except Exception as exc:  # noqa: BLE001 - любой сбой сети = результат шага
            return Result(False, time.perf_counter() - start,
                          classify_error(str(exc)), str(exc)[:300])
        return lookup_result(body, time.perf_counter() - start, label)

    def search(self, term: str) -> Result:
        return self._run(self._base() + ["search", term, "--limit", "5"], self.timeouts["lookup"])

    def purchase(self, app_id: str) -> Result:
        return self._run(self._base() + ["purchase", "--app-id", app_id], self.timeouts["license"])

    def download(self, app: AppRef, output: Path, version: str = "") -> Result:
        args = self._base() + download_command(self.ipatool, app, output, version)[4:]
        assert "--purchase" not in args
        res = self._run(args, self.timeouts["download"], watch=output)
        if res.ok and output.is_file():
            res.data["bytes"] = output.stat().st_size
        return res

    def list_versions(self, app: AppRef) -> Result:
        args = self._base() + ["list-versions"]
        args += ["--app-id", app.store_id] if app.store_id else ["--bundle-identifier", app.bundle_id]
        res = self._run(args, self.timeouts["lookup"])
        for event in res.data.get("events", []):
            if isinstance(event.get("externalVersionIdentifiers"), list):
                res.data["versions"] = [str(v) for v in event["externalVersionIdentifiers"]]
        return res

    def install(self, ipa: Path) -> Result:
        return run_install_hook(self.install_hook, ipa, self.udid, self.timeouts["install"])

    def describe(self) -> dict[str, Any]:
        try:
            out = subprocess.run([self.ipatool, "--version"], capture_output=True,
                                 text=True, timeout=20)
            version = (out.stdout or out.stderr).strip()
        except (OSError, subprocess.TimeoutExpired):
            version = "unknown"
        return {"ipatool": version}

class MockBackend(Backend):
    """Записанные расшифровки вместо сети и аккаунта. Время виртуальное."""

    mode = "mock"

    def __init__(self, transcripts: dict[str, Any]):
        self.transcripts = transcripts
        self.app: AppRef | None = None
        self.run = 0
        self.calls: Counter[str] = Counter()
        self.slept: list[float] = []
        self.purchases: list[str] = []
        self.download_cmds: list[list[str]] = []

    def begin_run(self, run: int, app: AppRef) -> None:
        self.app, self.run = app, run
        self.calls = Counter()

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)

    def _script(self) -> dict[str, Any]:
        assert self.app is not None
        apps = self.transcripts.get("apps", {})
        script = dict(apps.get(self.app.label) or apps.get(self.app.bundle_id) or {})
        override = (script.get("runs", {}) or {}).get(str(self.run), {})
        script.update(override)
        return script

    def _next(self, op: str) -> dict[str, Any]:
        script = self._script()
        seq = script.get(op, self.transcripts.get("defaults", {}).get(op))
        if seq is None:
            raise KeyError(f"no mock transcript for {self.app.label if self.app else '?'}:{op}")
        items = seq if isinstance(seq, list) else [seq]
        index = min(self.calls[op], len(items) - 1)
        self.calls[op] += 1
        return items[index]

    def _ipatool(self, op: str) -> Result:
        rec = self._next(op)
        stdout, stderr = rec.get("stdout", ""), rec.get("stderr", "")
        seconds = float(rec.get("seconds", 0.0))
        data: dict[str, Any] = {"events": ipatool_events(stdout, stderr)}
        for key in ("bytes", "first_byte_s"):
            if key in rec:
                data[key] = rec[key]
        if int(rec.get("returncode", 0)) == 0:
            return Result(True, seconds, data=data)
        error = ipatool_error(stdout, stderr)
        return Result(False, seconds, classify_error(error), error, data)

    def auth_info(self) -> Result:
        res = self._ipatool("auth_info")
        res.data = {"authenticated": res.ok}
        return res

    def lookup(self, app: AppRef, country: str = "", entity: str = "") -> Result:
        label = country + ("/" + entity if entity else "")
        key = "lookup" + (f":{label}" if label else "")
        script = self._script()
        if key not in script and key != "lookup":
            return lookup_result({"results": []}, 0.2, label)
        rec = self._next(key)
        if rec.get("status", 200) != 200:
            err = rec.get("error", "request failed")
            return Result(False, float(rec.get("seconds", 0.0)), classify_error(err), err)
        return lookup_result(rec.get("body", {}), float(rec.get("seconds", 0.0)), label)

    def search(self, term: str) -> Result:
        return self._ipatool("search")

    def purchase(self, app_id: str) -> Result:
        self.purchases.append(app_id)
        return self._ipatool("purchase")

    def download(self, app: AppRef, output: Path, version: str = "") -> Result:
        self.download_cmds.append(download_command("ipatool", app, output, version))
        return self._ipatool("download_version" if version else "download")

    def list_versions(self, app: AppRef) -> Result:
        res = self._ipatool("list_versions")
        for event in res.data.get("events", []):
            if isinstance(event.get("externalVersionIdentifiers"), list):
                res.data["versions"] = [str(v) for v in event["externalVersionIdentifiers"]]
        return res

    def install(self, ipa: Path) -> Result:
        script = self._script()
        if "install" not in script and "install" not in self.transcripts.get("defaults", {}):
            return Result(True, 0.0, data={"status": "skipped"})
        rec = self._next("install")
        seconds = float(rec.get("seconds", 0.0))
        if int(rec.get("returncode", 0)) == 0:
            return Result(True, seconds)
        return Result(False, seconds, classify_install_error(rec.get("stderr", "")), rec.get("stderr", ""))

    def describe(self) -> dict[str, Any]:
        return {"ipatool": self.transcripts.get("ipatool_version", "mock")}

# ---------------------------------------------------------------- прогон


@dataclass
class Options:
    search: bool = False
    allow_free_license: bool = False
    fallback: tuple[str, ...] = DEFAULT_FALLBACK
    retries: int = 2
    backoff_base: float = 1.0
    backoff_max: float = 30.0
    storefronts: tuple[str, ...] = DEFAULT_STOREFRONTS
    max_old_versions: int = 2
    install: bool = False


def _step(res: Result, **extra: Any) -> dict[str, Any]:
    out: dict[str, Any] = {"status": "ok" if res.ok else "fail", "seconds": round(res.seconds, 3)}
    if not res.ok:
        out["code"] = res.code or "unknown"
        out["error"] = res.error[:300]
    out.update(extra)
    return out


def _attempt(mechanism: str, res: Result, **extra: Any) -> dict[str, Any]:
    rec = {"mechanism": mechanism, "result": "ok" if res.ok else "fail",
           "seconds": round(res.seconds, 3)}
    if not res.ok:
        rec["code"] = res.code or "unknown"
    if mechanism not in ("initial",):
        rec["legal_review"] = LEGAL_REVIEW
    rec.update(extra)
    return rec


def run_one(backend: Backend, app: AppRef, run: int, opts: Options,
            journal: LicenseJournal, workdir: Path) -> dict[str, Any]:
    backend.begin_run(run, app)
    steps: dict[str, Any] = {}
    record: dict[str, Any] = {"run": run, "app": app.label, "steps": steps}

    # 1. Сессия: только `auth info`, никакого входа.
    res = backend.auth_info()
    steps["session"] = _step(res)
    if not res.ok:
        for name in ("lookup", "search", "license", "download", "install"):
            steps[name] = _skipped("нет открытой сессии")
        steps["total"] = {"status": "fail", "seconds": steps["session"]["seconds"],
                          "code": res.code or "not_authenticated"}
        return record

    # 2. Lookup (публичный iTunes Lookup, без аккаунта).
    lookup = backend.lookup(app)
    steps["lookup"] = _step(lookup, **{k: v for k, v in lookup.data.items()
                                       if k in ("storefront", "price", "size_bytes")})
    meta = dict(lookup.data) if lookup.ok else {}
    target = AppRef(app.bundle_id or meta.get("bundle_id", ""),
                    app.store_id or meta.get("store_id", ""))

    # 3. Поиск через ipatool (по желанию).
    if opts.search:
        term = app.bundle_id.rsplit(".", 1)[-1] if app.bundle_id else app.store_id
        steps["search"] = _step(backend.search(term))
    else:
        steps["search"] = _skipped("--search не задан")

    # 4–5. Скачивание с цепочкой попыток; лицензия по требованию.
    steps["license"] = _skipped("лицензия не понадобилась")
    attempts: list[dict[str, Any]] = []
    output = workdir / f"{(target.store_id or target.bundle_id or 'app')}-r{run}.ipa"
    total_dl = 0.0

    def download(ref: AppRef, mechanism: str, version: str = "", **extra: Any) -> Result:
        nonlocal total_dl
        r = backend.download(ref, output, version)
        total_dl += r.seconds
        attempts.append(_attempt(mechanism, r, **extra))
        return r

    dl = download(target, "initial")
    session_refreshed = False
    tried_storefronts = False
    tried_versions = False
    retries_left = opts.retries
    retry_no = 0
    guard = 0
    while not dl.ok and guard < 24:
        guard += 1
        if dl.code == "license_required":
            if acquire_free_license(backend, target, meta, opts, journal, steps, app):
                dl = download(target, "after-license")
                continue
            break
        progressed = False
        for mech in opts.fallback:
            if mech == "retry" and dl.code in RETRYABLE and retries_left > 0:
                retries_left -= 1
                retry_no += 1
                pause = min(opts.backoff_max, opts.backoff_base * (2 ** (retry_no - 1)))
                backend.sleep(pause)
                dl = download(target, "retry", backoff_s=pause, attempt=retry_no)
                progressed = True
                break
            if mech == "refresh-session" and dl.code in SESSION_CODES and not session_refreshed:
                session_refreshed = True
                check = backend.auth_info()
                attempts.append(_attempt("refresh-session", check))
                if check.ok:
                    dl = download(target, "after-refresh-session")
                    progressed = True
                break
            if mech == "storefronts" and dl.code in STOREFRONT_CODES and not tried_storefronts:
                tried_storefronts = True
                progressed = True  # механизм использован, идём дальше по цепочке
                found = False
                for country in opts.storefronts:
                    for entity in ("", "iPadSoftware"):
                        alt = backend.lookup(target, country=country, entity=entity)
                        attempts.append(_attempt("storefronts", alt, storefront=country,
                                                 entity=entity or "iPhone"))
                        if alt.ok and _price_of(alt.data) == 0:
                            meta = dict(alt.data)
                            target = AppRef(target.bundle_id or meta.get("bundle_id", ""),
                                            meta.get("store_id", "") or target.store_id)
                            dl = download(target, "after-storefront", storefront=country)
                            found = True
                            break
                    if found:
                        break
                break
            if mech == "old-versions" and dl.code in VERSION_CODES and not tried_versions:
                tried_versions = True
                progressed = True  # механизм использован, идём дальше по цепочке
                listed = backend.list_versions(target)
                attempts.append(_attempt("old-versions", listed))
                versions = listed.data.get("versions", []) if listed.ok else []
                for version in list(reversed(versions))[: opts.max_old_versions]:
                    dl = download(target, "old-version", version=version, external_version_id=version)
                    if dl.ok:
                        break
                break
        if not progressed:
            break

    steps["download"] = _step(dl)
    steps["download"]["attempts"] = attempts
    steps["download"]["seconds"] = round(total_dl, 3)
    size = dl.data.get("bytes") or meta.get("size_bytes")
    if dl.ok and size and total_dl > 0:
        steps["download"]["mb"] = round(size / MB, 2)
        steps["download"]["mb_per_s"] = round((size / MB) / total_dl, 2)
    if "first_byte_s" in dl.data:
        steps["download"]["first_byte_s"] = dl.data["first_byte_s"]

    # 6. Установка на телефон.
    if not dl.ok:
        steps["install"] = _skipped("нет IPA для установки")
    elif not opts.install:
        steps["install"] = _skipped("--install не задан (хук pymobiledevice3 для Димы)")
    else:
        steps["install"] = _step(backend.install(output))

    try:
        if output.exists():
            output.unlink()
    except OSError:
        pass

    done = [steps[s] for s in ("session", "lookup", "search", "license", "download", "install")]
    total_s = sum(s.get("seconds", 0.0) for s in done)
    failed = next((s for s in (steps["download"], steps["license"], steps["lookup"], steps["session"])
                   if s.get("status") in ("fail", "denied")), None)
    total = {"status": "ok" if dl.ok else "fail", "seconds": round(total_s, 3)}
    if not dl.ok and failed:
        total["code"] = failed.get("code", "unknown")
    steps["total"] = total
    return record

# ---------------------------------------------------------------- агрегация


def _percentile(values: list[float], pct: float) -> float:
    """Линейная интерполяция (как numpy по умолчанию). values непустой."""

    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (pct / 100) * (len(ordered) - 1)
    low = int(rank)
    high = min(low + 1, len(ordered) - 1)
    frac = rank - low
    return ordered[low] + (ordered[high] - ordered[low]) * frac


def aggregate(records: list[dict[str, Any]]) -> dict[str, Any]:
    """По каждому шагу: медиана и p90 времени, доля сбоев, частые коды."""

    per_step: dict[str, Any] = {}
    for step in STEPS:
        entries = [r["steps"].get(step) for r in records if step in r["steps"]]
        entries = [e for e in entries if e]
        attempted = [e for e in entries if e.get("status") != "skipped"]
        times = [float(e.get("seconds", 0.0)) for e in attempted]
        fails = [e for e in attempted if e.get("status") in ("fail", "denied")]
        codes = Counter(e.get("code", "unknown") for e in fails)
        summary: dict[str, Any] = {
            "runs": len(entries),
            "attempted": len(attempted),
            "skipped": len(entries) - len(attempted),
            "failures": len(fails),
            "fail_share": round(len(fails) / len(attempted), 3) if attempted else None,
        }
        if times:
            summary["median_s"] = round(statistics.median(times), 3)
            summary["p90_s"] = round(_percentile(times, 90), 3)
        if codes:
            summary["top_codes"] = [{"code": c, "count": n} for c, n in codes.most_common(5)]
        per_step[step] = summary

    speeds = [r["steps"]["download"].get("mb_per_s") for r in records
              if r["steps"].get("download", {}).get("mb_per_s")]
    if speeds:
        per_step["download"]["median_mb_per_s"] = round(statistics.median(speeds), 2)
        per_step["download"]["p90_mb_per_s"] = round(_percentile(speeds, 90), 2)
    return per_step


# ---------------------------------------------------------------- CLI


def build_backend(args: argparse.Namespace, timeouts: dict[str, float]) -> Backend:
    if args.mock:
        transcripts = json.loads(Path(args.mock).read_text(encoding="utf-8"))
        return MockBackend(transcripts)
    ipatool = args.ipatool or _find_ipatool()
    if not ipatool:
        raise SystemExit("ipatool не найден: укажите --ipatool PATH")
    return RealBackend(ipatool, install_hook=args.install_hook, timeouts=timeouts)


def _find_ipatool() -> str | None:
    import shutil
    found = shutil.which("ipatool")
    if found:
        return found
    for candidate in (Path(sys.executable).parent / "ipatool",
                      Path(sys.executable).parent / "ipatool.exe"):
        if candidate.exists():
            return str(candidate)
    return None


def dry_run_plan(apps: list[AppRef], runs: int, opts: Options, journal: LicenseJournal,
                 timeouts: dict[str, float]) -> dict[str, Any]:
    daily, total = journal.counts()
    sample = apps[0] if apps else AppRef("com.example.app", "123")
    return {
        "schema": SCHEMA,
        "mode": "dry-run",
        "note": "Ничего не запущено. Это план: шаги, команды и ограничения.",
        "apps": [a.label for a in apps],
        "runs_per_app": runs,
        "steps": list(STEPS),
        "timeouts_s": timeouts,
        "fallback_chain": [{"mechanism": m, "what": FALLBACKS[m], "legal_review": LEGAL_REVIEW}
                           for m in opts.fallback],
        "license": {
            "enabled": opts.allow_free_license,
            "rule": "только price==0 по lookup, только шаг `ipatool purchase`, "
                    "download без --purchase",
            "limits": {"daily": journal.daily_limit, "total": journal.total_limit},
            "journal": str(journal.path),
            "current_counts": {"daily": daily, "total": total},
            "legal_review": LEGAL_REVIEW,
        },
        "example_commands": {
            "session": "ipatool --non-interactive --format json auth info",
            "lookup": "GET https://itunes.apple.com/lookup?bundleId=<bundle>",
            "download": " ".join(download_command("ipatool", sample, Path("<out>.ipa"))),
            "purchase_free_only": "ipatool --non-interactive --format json purchase --app-id <id>",
            "install_hook": "python -m pymobiledevice3 apps install <out>.ipa",
        },
        "guarantees": [
            "download никогда не содержит --purchase",
            "вход в Apple ID не выполняется (только auth info)",
            "в выводе нет Apple ID, паролей, токенов, UDID, путей с именем пользователя",
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="AppRestore ipatool bench (M-1).")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="по умолчанию: только план")
    mode.add_argument("--mock", metavar="FILE", help="расшифровки JSON вместо сети/аккаунта")
    mode.add_argument("--real", action="store_true", help="настоящий ipatool (после выбора Apple ID)")
    parser.add_argument("--app", action="append", default=[],
                        help="bundle id, число (store id) или bundle@storeid; можно несколько")
    parser.add_argument("--apps-file", help="файл со списком приложений (по одному в строке)")
    parser.add_argument("--runs", type=int, default=1, help="прогонов на приложение")
    parser.add_argument("--search", action="store_true", help="добавить шаг `ipatool search`")
    parser.add_argument("--install", action="store_true", help="добавить шаг установки (хук)")
    parser.add_argument("--install-hook", default="none",
                        help="none | pymobiledevice3 | cmd:<шаблon {ipa} {udid}>")
    parser.add_argument("--allow-free-license", action="store_true",
                        help="разрешить шаг лицензии ТОЛЬКО для бесплатных приложений")
    parser.add_argument("--fallback", default=",".join(DEFAULT_FALLBACK),
                        help="цепочка попыток через запятую: " + ",".join(FALLBACKS))
    parser.add_argument("--retries", type=int, default=2)
    parser.add_argument("--backoff-base", type=float, default=1.0)
    parser.add_argument("--backoff-max", type=float, default=30.0)
    parser.add_argument("--storefronts", default=",".join(DEFAULT_STOREFRONTS))
    parser.add_argument("--license-journal", default="licenses_acquired.jsonl")
    parser.add_argument("--license-daily-limit", type=int, default=LICENSE_DAILY_LIMIT)
    parser.add_argument("--license-total-limit", type=int, default=LICENSE_TOTAL_LIMIT)
    parser.add_argument("--ipatool", help="путь к ipatool (для --real)")
    parser.add_argument("--timeout-session", type=float, default=60)
    parser.add_argument("--timeout-lookup", type=float, default=30)
    parser.add_argument("--timeout-license", type=float, default=120)
    parser.add_argument("--timeout-download", type=float, default=1800)
    parser.add_argument("--timeout-install", type=float, default=600)
    parser.add_argument("--out", help="файл для JSON-отчёта (иначе stdout)")
    args = parser.parse_args(argv)

    apps = [AppRef.parse(a) for a in args.app]
    if args.apps_file:
        for line in Path(args.apps_file).read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                apps.append(AppRef.parse(line))

    opts = Options(
        search=args.search,
        allow_free_license=args.allow_free_license,
        fallback=tuple(m.strip() for m in args.fallback.split(",") if m.strip()),
        retries=args.retries,
        backoff_base=args.backoff_base,
        backoff_max=args.backoff_max,
        storefronts=tuple(s.strip() for s in args.storefronts.split(",") if s.strip()),
        install=args.install,
    )
    timeouts = {
        "session": args.timeout_session, "lookup": args.timeout_lookup,
        "license": args.timeout_license, "download": args.timeout_download,
        "install": args.timeout_install,
    }
    journal = LicenseJournal(
        Path(args.license_journal), daily_limit=args.license_daily_limit,
        total_limit=args.license_total_limit,
        now=lambda: dt.datetime.now(dt.timezone.utc),
    )

    if not (args.mock or args.real):  # по умолчанию dry-run
        report = dry_run_plan(apps, args.runs, opts, journal, timeouts)
        _emit(report, args.out, secrets=())
        return 0

    backend = build_backend(args, timeouts)
    records: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="apprestore-bench-") as tmp:
        workdir = Path(tmp)
        for app in apps:
            for run in range(1, args.runs + 1):
                records.append(run_one(backend, app, run, opts, journal, workdir))

    report = {
        "schema": SCHEMA,
        "mode": backend.mode,
        "generated": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "tool": backend.describe(),
        "apps": [a.label for a in apps],
        "runs_per_app": args.runs,
        "options": {"fallback": list(opts.fallback), "allow_free_license": opts.allow_free_license,
                    "license_limits": {"daily": journal.daily_limit, "total": journal.total_limit}},
        "runs": records,
        "aggregate": aggregate(records),
    }
    _emit(report, args.out, secrets=getattr(backend, "secrets", ()))
    return 0


def _emit(report: dict[str, Any], out: str | None, secrets: Iterable[str]) -> None:
    safe = mask_obj(report, secrets)
    text = json.dumps(safe, ensure_ascii=False, indent=2)
    if out:
        Path(out).write_text(text + "\n", encoding="utf-8")
        print(f"отчёт записан: {out}")
    else:
        print(text)


if __name__ == "__main__":
    raise SystemExit(main())
