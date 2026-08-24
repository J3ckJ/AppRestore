from __future__ import annotations

import argparse
from collections.abc import Iterable
from contextlib import nullcontext, redirect_stdout
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, NoReturn

from . import __version__
from .catalog import CatalogError
from .command import CommandError
from .ipa import IpaError
from .ipa import validate_bundle_id
from .known_apps import parse_app_store_id, remember_known_app
from .models import Device, MissingApp, OffloadedApp
from .service import AppRestoreError, AppRestoreService
from .tools import ToolUnavailable


_APPRESTORE_LOGO = r"""     _                ____           _
    / \   _ __  _ __ |  _ \ ___  ___| |_ ___  _ __ ___
   / _ \ | '_ \| '_ \| |_) / _ \/ __| __/ _ \| '__/ _ \
  / ___ \| |_) | |_) |  _ <  __/\__ \ || (_) | | |  __/
 /_/   \_\ .__/| .__/|_| \_\___||___/\__\___/|_|  \___|
         |_|   |_|"""
_APPRESTORE_TAGLINE = "Телефон → сгруженные / удалённые → скачать IPA → вернуть"
_MENU_RULE = "────────────────────────────────────────────"
_ANSI_BOLD_CYAN = "\033[1;36m"
_ANSI_DIM = "\033[2m"
_ANSI_RESET = "\033[0m"


class CliUsageError(ValueError):
    def __init__(self, parser: argparse.ArgumentParser, message: str) -> None:
        super().__init__(message)
        self.parser = parser


class AppRestoreArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        raise CliUsageError(self, message)


def _enable_windows_ansi() -> bool:
    if sys.platform != "win32":
        return True
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.GetStdHandle.argtypes = [wintypes.DWORD]
        kernel32.GetStdHandle.restype = wintypes.HANDLE
        kernel32.GetConsoleMode.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(wintypes.DWORD),
        ]
        kernel32.GetConsoleMode.restype = wintypes.BOOL
        kernel32.SetConsoleMode.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel32.SetConsoleMode.restype = wintypes.BOOL

        stdout_handle = kernel32.GetStdHandle(-11)
        invalid_handle = wintypes.HANDLE(-1).value
        if not stdout_handle or stdout_handle == invalid_handle:
            return False

        mode = wintypes.DWORD()
        if not kernel32.GetConsoleMode(stdout_handle, ctypes.byref(mode)):
            return False
        enable_virtual_terminal_processing = 0x0004
        return bool(
            kernel32.SetConsoleMode(
                stdout_handle,
                mode.value | enable_virtual_terminal_processing,
            )
        )
    except (AttributeError, OSError, ValueError):
        return False


def _supports_ansi(stream: Any) -> bool:
    if "NO_COLOR" in os.environ or os.environ.get("TERM", "").lower() == "dumb":
        return False
    isatty = getattr(stream, "isatty", None)
    if not callable(isatty) or not isatty():
        return False
    return _enable_windows_ansi()


def _print_header(*, color: bool | None = None) -> None:
    use_color = _supports_ansi(sys.stdout) if color is None else color
    if use_color:
        print(f"{_ANSI_BOLD_CYAN}{_APPRESTORE_LOGO}{_ANSI_RESET}")
        print(f"{_ANSI_DIM}{_APPRESTORE_TAGLINE}{_ANSI_RESET}")
    else:
        print(_APPRESTORE_LOGO)
        print(_APPRESTORE_TAGLINE)
    print(_MENU_RULE)


def _bytes_human(value: int) -> str:
    number = float(max(value, 0))
    units = ["B", "KiB", "MiB", "GiB", "TiB"]
    unit = units[0]
    for unit in units:
        if number < 1024 or unit == units[-1]:
            break
        number /= 1024
    return f"{number:.1f} {unit}"


def _pick_device(
    service: AppRestoreService,
    requested: str | None,
    *,
    noninteractive: bool = False,
) -> Device:
    if requested:
        devices = service.devices()
        for device in devices:
            if device.udid == requested:
                return device
        raise AppRestoreError(f"device is not connected: {requested}")

    devices = service.devices()
    if not devices:
        raise AppRestoreError(
            "no iPhone found; connect it by USB, unlock it and tap Trust"
        )
    if len(devices) == 1:
        return devices[0]

    if noninteractive:
        raise AppRestoreError(
            "multiple iPhones are connected; pass --udid in machine-readable mode"
        )

    print("Choose a device:")
    for index, device in enumerate(devices, 1):
        print(f"  {index}) {device.name}  iOS {device.ios_version}")
        print(f"     {device.udid}")
    raw = input("Number: ").strip()
    if not raw.isdigit() or not (1 <= int(raw) <= len(devices)):
        raise AppRestoreError("invalid device selection")
    return devices[int(raw) - 1]


def _parse_selection(raw: str, count: int) -> list[int]:
    text = raw.strip().lower()
    if text in {"all", "*"}:
        return list(range(count))
    if not text:
        return []

    selected: list[int] = []
    for part in text.split(","):
        token = part.strip()
        if not token:
            raise AppRestoreError("empty item in selection")
        if "-" in token:
            pieces = token.split("-", 1)
            if len(pieces) != 2 or not all(
                re.fullmatch(r"[0-9]+", piece) for piece in pieces
            ):
                raise AppRestoreError(f"invalid range: {token}")
            start, end = (int(piece) for piece in pieces)
            if start > end:
                raise AppRestoreError(f"descending range is not allowed: {token}")
            values: Iterable[int] = range(start, end + 1)
        elif re.fullmatch(r"[0-9]+", token):
            values = [int(token)]
        else:
            raise AppRestoreError(f"invalid selection: {token}")
        for value in values:
            if value < 1 or value > count:
                raise AppRestoreError(f"selection is out of range: {value}")
            zero_based = value - 1
            if zero_based not in selected:
                selected.append(zero_based)
    return selected


def _print_apps(apps: list[OffloadedApp]) -> None:
    if not apps:
        print("No offloaded applications found.")
        return
    for index, app in enumerate(apps, 1):
        if app.local_ipa:
            source = "local IPA"
        elif app.store_id:
            source = f"store ID {app.store_id} ({app.store_match})"
        else:
            source = "bundle ID download"
        total = app.static_size + app.dynamic_size
        size = f", {_bytes_human(total)}" if total else ""
        print(f"{index:>3}) {app.name} ({app.bundle_id}, {app.version}{size})")
        print(f"     {source}")


def _print_missing_apps(apps: list[MissingApp]) -> None:
    if not apps:
        print("Список пуст — введите ID, URL или имя для поиска.")
        return
    for index, app in enumerate(apps, 1):
        bits = [app.source]
        if app.local_ipa:
            bits.append("local IPA")
        if app.store_id:
            bits.append(f"store ID {app.store_id}")
        identity = app.bundle_id or (f"id{app.store_id}" if app.store_id else "?")
        print(f"{index:>3}) {app.name} ({identity}, {app.version})")
        print(f"     {', '.join(bits)}")


def _json_dump(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2))


def _ensure_auth(
    service: AppRestoreService,
    email: str | None,
    *,
    noninteractive: bool = False,
) -> None:
    print("Проверка входа ipatool…")
    if service.tools.ipatool_authenticated():
        print("ipatool: вход уже выполнен.")
        return
    if not email and noninteractive:
        raise AppRestoreError(
            "ipatool is not authenticated; authenticate first or pass --email"
        )
    if not email:
        email = input("Apple ID email: ").strip()
    print("Вход в Apple ID через ipatool (пароль/2FA/passphrase — в его запросах)…")
    service.authenticate(email)
    print("ipatool: вход выполнен.")


def _command_doctor(service: AppRestoreService, json_output: bool) -> int:
    checks = service.tools.doctor()
    if json_output:
        _json_dump([check.to_dict() for check in checks])
    else:
        for check in checks:
            mark = "OK" if check.ok else ("FAIL" if check.required else "WARN")
            print(f"[{mark}] {check.name}: {check.detail}")
        if sys.platform == "win32" and any(
            (check.name.startswith("Apple") and not check.ok) for check in checks
        ):
            print("Run: apprestore setup")
            print("Or install Apple Devices / iTunes, then reconnect the iPhone.")
    return 0 if all(check.ok or not check.required for check in checks) else 1


def _command_setup(service: AppRestoreService, json_output: bool) -> int:
    notes: list[str] = []
    if sys.platform == "win32":
        notes.extend(service.tools.ensure_windows_bridge())
    else:
        notes.append(
            "On macOS reinstall from the signed release bootstrap or run "
            "./apprestore.sh setup from a source checkout."
        )

    checks = service.tools.doctor()
    if json_output:
        _json_dump(
            {
                "notes": notes,
                "doctor": [check.to_dict() for check in checks],
            }
        )
    else:
        for note in notes:
            print(note)
        print()
        for check in checks:
            mark = "OK" if check.ok else ("FAIL" if check.required else "WARN")
            print(f"[{mark}] {check.name}: {check.detail}")
        if sys.platform == "win32" and not service.tools.windows_bridge_ready():
            print(
                "\nIf the phone is connected: unlock it, tap Trust, then reconnect USB."
            )
    return 0 if all(check.ok or not check.required for check in checks) else 1


def _command_devices(service: AppRestoreService, json_output: bool) -> int:
    devices = service.devices()
    if json_output:
        _json_dump([device.to_dict() for device in devices])
    elif not devices:
        print("No USB devices found.")
    else:
        for device in devices:
            print(f"{device.name}  iOS {device.ios_version}")
            print(f"  {device.udid}")
    return 0 if devices else 1


def _command_scan(service: AppRestoreService, json_output: bool) -> int:
    entries, errors = service.scan_local(refresh=True)
    if json_output:
        _json_dump(
            {
                "ipas": [entry.to_dict() for entry in entries],
                "errors": [{"path": str(path), "error": error} for path, error in errors],
            }
        )
    else:
        for entry in entries:
            print(
                f"{entry.name} {entry.version}  {entry.bundle_id}  "
                f"{_bytes_human(entry.size)}"
            )
            print(f"  {entry.path}")
        for path, error in errors:
            print(f"SKIP {path}: {error}", file=sys.stderr)
        print(f"Found valid IPA files: {len(entries)}")
    return 0


def _command_offloaded(
    service: AppRestoreService,
    udid: str | None,
    json_output: bool,
) -> int:
    device = _pick_device(service, udid, noninteractive=json_output)
    apps = service.offloaded(device.udid)
    if json_output:
        _json_dump(
            {
                "device": device.to_dict(),
                "apps": [app.to_dict() for app in apps],
            }
        )
    else:
        print(f"Device: {device.name}, iOS {device.ios_version}")
        _print_apps(apps)
    return 0 if apps else 1


def _command_restore(
    service: AppRestoreService,
    *,
    udid: str | None,
    email: str | None,
    selection: str | None,
    acquire_license: bool = False,
    try_device_redownload: bool = True,
    noninteractive: bool = False,
) -> int:
    device = _pick_device(service, udid, noninteractive=noninteractive)
    print(f"Device: {device.name}, iOS {device.ios_version}")
    apps = service.offloaded(device.udid)
    if not apps:
        print("No offloaded applications found.")
        return 1
    _print_apps(apps)

    raw = (
        selection
        if selection is not None
        else input("Номер (1,3-5 / all): ")
    )
    selected = _parse_selection(raw, len(apps))
    if not selected:
        if noninteractive:
            raise AppRestoreError(
                "selection cannot be empty in machine-readable restore"
            )
        print("Cancelled.")
        return 0

    ok = 0
    failed = 0
    for index in selected:
        app = apps[index]
        print(f"\n→ {app.name} ({app.bundle_id})")
        try:
            restore_options = (
                {"acquire_license": True} if acquire_license else {}
            )
            if not try_device_redownload:
                restore_options["try_device_redownload"] = False
            status = service.restore_offloaded(
                device.udid,
                app,
                **restore_options,
            )
            print(f"  done: {status}")
            ok += 1
        except AppRestoreError as exc:
            if "not authenticated" in str(exc).lower():
                _ensure_auth(service, email, noninteractive=noninteractive)
                try:
                    retry_options: dict[str, bool] = {
                        "try_device_redownload": False,
                    }
                    if acquire_license:
                        retry_options["acquire_license"] = True
                    status = service.restore_offloaded(
                        device.udid, app, **retry_options
                    )
                    print(f"  done: {status}")
                    ok += 1
                    continue
                except (
                    AppRestoreError,
                    IpaError,
                    ToolUnavailable,
                    CommandError,
                ) as retry_exc:
                    print(f"  failed: {retry_exc}", file=sys.stderr)
                    failed += 1
                    continue
            if not noninteractive and "refusing a competing ipa install" in str(
                exc
            ).lower():
                print(f"  failed: {exc}", file=sys.stderr)
                confirmed = (
                    input(
                        "  iPhone точно не докачивает это приложение сейчас? "
                        "(y/N): "
                    )
                    .strip()
                    .lower()
                    in {"y", "yes", "д", "да"}
                )
                if confirmed:
                    try:
                        retry_options = {"try_device_redownload": False}
                        if acquire_license:
                            retry_options["acquire_license"] = True
                        status = service.restore_offloaded(
                            device.udid, app, **retry_options
                        )
                        print(f"  done: {status}")
                        ok += 1
                        continue
                    except (
                        AppRestoreError,
                        IpaError,
                        ToolUnavailable,
                        CommandError,
                    ) as retry_exc:
                        print(f"  failed: {retry_exc}", file=sys.stderr)
                        failed += 1
                        continue
                failed += 1
                continue
            print(f"  failed: {exc}", file=sys.stderr)
            failed += 1
        except (IpaError, ToolUnavailable, CommandError) as exc:
            print(f"  failed: {exc}", file=sys.stderr)
            failed += 1
    print(f"\nSuccessful: {ok}; failed: {failed}")
    return 0 if failed == 0 else 2


def _command_missing(
    service: AppRestoreService,
    udid: str | None,
    json_output: bool,
) -> int:
    device = _pick_device(service, udid, noninteractive=json_output)
    apps = service.missing(device.udid)
    if json_output:
        _json_dump(
            {
                "device": device.to_dict(),
                "apps": [app.to_dict() for app in apps],
            }
        )
    else:
        print(f"Device: {device.name}, iOS {device.ios_version}")
        _print_missing_apps(apps)
    return 0 if apps else 1


def _looks_like_store_id_input(value: str) -> bool:
    text = value.strip()
    if not text:
        return False
    lowered = text.casefold()
    return bool(
        text.isdigit()
        or re.fullmatch(r"id[0-9]*", lowered)
        or re.search(r"(?:^|://)apps\.apple\.com/", lowered)
    )


def _missing_from_store_id(store_id: str, *, name: str | None = None) -> MissingApp:
    return MissingApp(
        bundle_id="",
        name=name or f"App Store {store_id}",
        store_id=store_id,
        store_match="manual",
        source="manual",
    )


def _search_missing_targets(
    service: AppRestoreService,
    term: str,
    *,
    email: str | None = None,
) -> list[MissingApp]:
    del email  # public catalogs; Apple ID needed only later for download
    query = term.strip()
    if not query:
        raise AppRestoreError("пустой поисковый запрос")
    print("Поиск…")
    results = service.search_apps(query)
    if not results:
        print("Ничего не найдено. Нужен App Store ID или URL.")
        return []
    print("\nНайдено:")
    for index, row in enumerate(results, 1):
        bundle = row.get("bundleId") or "—"
        source = row.get("source") or "?"
        print(
            f"{index:>3}) {row.get('name') or '?'} "
            f"(id{row.get('storeId')}, {bundle}) [{source}]"
        )
    try:
        pick = input("Номер (Enter — отмена): ").strip()
    except EOFError:
        return []
    if not pick:
        return []
    try:
        index = int(pick)
    except ValueError as exc:
        raise AppRestoreError("нужен номер из списка поиска") from exc
    if index < 1 or index > len(results):
        raise AppRestoreError("номер вне диапазона поиска")
    row = results[index - 1]
    store_id = row["storeId"]
    bundle_id = (row.get("bundleId") or "").strip()
    name = row.get("name") or store_id
    remember_known_app(
        store_id=store_id,
        bundle_id=bundle_id or None,
        name=name,
        provenance="search-selection",
        status="confirmed",
    )
    if bundle_id:
        return [
            MissingApp(
                bundle_id=bundle_id,
                name=name,
                store_id=store_id,
                store_match="search",
                local_ipa=service.find_local(bundle_id),
                source="search",
            )
        ]
    return [_missing_from_store_id(store_id, name=name)]


def _resolve_missing_targets(
    raw: str,
    apps: list[MissingApp],
    *,
    service: AppRestoreService | None = None,
    email: str | None = None,
    noninteractive: bool = False,
) -> list[MissingApp]:
    text = raw.strip()
    if not text:
        return []

    lowered = text.casefold()
    for prefix in ("search:", "search ", "найти:", "найти ", "find:", "find "):
        if lowered.startswith(prefix):
            if service is None:
                raise AppRestoreError("поиск недоступен в этом контексте")
            if noninteractive:
                raise AppRestoreError(
                    "machine-readable restore cannot choose a search result; "
                    "pass --store-id or --bundle-id"
                )
            return _search_missing_targets(
                service,
                text[len(prefix) :],
                email=email,
            )

    if apps:
        try:
            selected = _parse_selection(text, len(apps))
            return [apps[index] for index in selected]
        except AppRestoreError:
            pass

    store_id = parse_app_store_id(text)
    if store_id:
        return [_missing_from_store_id(store_id)]

    try:
        bundle_id = validate_bundle_id(text)
    except IpaError:
        bundle_id = None
    # Reverse-DNS bundle IDs contain a dot; a bare name is a search query.
    if bundle_id is None or "." not in bundle_id:
        if service is None:
            raise AppRestoreError(
                "укажите номер, App Store ID/URL, bundle ID или имя"
            )
        if noninteractive:
            raise AppRestoreError(
                "machine-readable restore cannot choose a search result; "
                "pass --store-id or --bundle-id"
            )
        return _search_missing_targets(service, text, email=email)
    return [
        MissingApp(
            bundle_id=bundle_id,
            name=bundle_id,
            source="manual",
        )
    ]


def _command_restore_missing(
    service: AppRestoreService,
    *,
    udid: str | None,
    email: str | None,
    selection: str | None,
    bundle_id: str | None = None,
    store_id: str | None = None,
    acquire_license: bool = False,
    noninteractive: bool = False,
) -> int:
    device = _pick_device(service, udid, noninteractive=noninteractive)
    print(f"Device: {device.name}, iOS {device.ios_version}")

    parsed_store_id: str | None = None
    if store_id is not None:
        parsed_store_id = parse_app_store_id(store_id)
        if not parsed_store_id:
            raise AppRestoreError("некорректный --store-id")
    if parsed_store_id and not bundle_id:
        targets = [_missing_from_store_id(parsed_store_id)]
    elif bundle_id:
        try:
            normalized = validate_bundle_id(bundle_id)
        except IpaError as exc:
            raise AppRestoreError(str(exc)) from exc
        targets = [
            MissingApp(
                bundle_id=normalized,
                name=normalized,
                store_id=parsed_store_id,
                store_match="manual" if parsed_store_id else "none",
                local_ipa=service.find_local(normalized),
                source="manual",
            )
        ]
    else:
        apps = service.missing(device.udid)
        _print_missing_apps(apps)
        raw = (
            selection
            if selection is not None
            else input("Номер / ID / URL / имя: ")
        )
        targets = _resolve_missing_targets(
            raw,
            apps,
            service=service,
            email=email,
            noninteractive=noninteractive,
        )
        if store_id and len(targets) == 1 and targets[0].store_id is None:
            targets = [
                MissingApp(
                    bundle_id=targets[0].bundle_id,
                    name=targets[0].name,
                    version=targets[0].version,
                    store_id=store_id,
                    store_match="manual",
                    local_ipa=targets[0].local_ipa,
                    source=targets[0].source,
                )
            ]

    if not targets:
        if noninteractive:
            raise AppRestoreError(
                "selection cannot be empty in machine-readable restore-missing"
            )
        print("Cancelled.")
        return 0

    ok = 0
    failed = 0
    for app in targets:
        identity = app.bundle_id or (f"id{app.store_id}" if app.store_id else "?")
        print(f"\n→ {app.name} ({identity})")
        try:
            restore_options = (
                {"acquire_license": True} if acquire_license else {}
            )
            status = service.restore_missing(
                device.udid,
                app,
                **restore_options,
            )
            print(f"  done: {status}")
            ok += 1
        except AppRestoreError as exc:
            if "not authenticated" in str(exc).lower():
                _ensure_auth(service, email, noninteractive=noninteractive)
                try:
                    status = service.restore_missing(
                        device.udid,
                        app,
                        **restore_options,
                    )
                    print(f"  done: {status}")
                    ok += 1
                    continue
                except (
                    AppRestoreError,
                    IpaError,
                    ToolUnavailable,
                    CommandError,
                ) as retry_exc:
                    print(f"  failed: {retry_exc}", file=sys.stderr)
                    failed += 1
                    continue
            print(f"  failed: {exc}", file=sys.stderr)
            failed += 1
        except (IpaError, ToolUnavailable, CommandError) as exc:
            print(f"  failed: {exc}", file=sys.stderr)
            failed += 1
    print(f"\nSuccessful: {ok}; failed: {failed}")
    return 0 if failed == 0 else 2


def _command_search(
    service: AppRestoreService,
    term: str | None,
    *,
    email: str | None = None,
    limit: int = 10,
    json_output: bool = False,
) -> int:
    del email  # public catalogs; login needed only later for download
    query = (term or "").strip()
    if not query and json_output:
        raise AppRestoreError("search term is required with --json")
    query = query or input("Поиск: ").strip()
    if not query:
        print("Cancelled.")
        return 0
    if not json_output:
        print("Поиск: iTunes + IPA Filezone + веб (если нужно)…")
    results = service.search_apps(query, limit=limit)
    if json_output:
        _json_dump({"term": query, "apps": results})
    else:
        if not results:
            print(
                "Ничего не найдено ни в iTunes, ни в архиве. "
                "Остаётся ID/URL из покупок Apple / истории."
            )
            return 1
        print(f"Найдено: {len(results)}")
        for index, row in enumerate(results, 1):
            bundle = row.get("bundleId") or "—"
            source = row.get("source") or "?"
            print(
                f"{index:>3}) {row.get('name') or '?'} "
                f"(id{row.get('storeId')}, {bundle}) [{source}]"
            )
        print("Дальше: меню 2 → вставить id… / URL или выбрать из списка.")
    return 0 if results else 1


def _pause_menu() -> None:
    try:
        input("\nEnter, чтобы продолжить…")
    except EOFError:
        pass


def _clear_screen() -> None:
    # Clearing is cosmetic.  Avoid launching a PATH-resolved shell command;
    # when output is redirected, leaving earlier output intact is preferable.
    if _supports_ansi(sys.stdout):
        print("\033[2J\033[H", end="")


def _menu_local_ipa(service: AppRestoreService) -> None:
    print(
        """
  1) Сканировать локальные IPA
  2) Скачать IPA
  3) Установить IPA
  0) Назад
""".rstrip()
    )
    try:
        choice = input("Выбор: ").strip()
    except EOFError:
        return
    if choice == "1":
        _command_scan(service, json_output=False)
    elif choice == "2":
        value = input("Bundle ID или App Store ID/URL: ").strip()
        if not value:
            return
        _ensure_auth(service, None)
        if _looks_like_store_id_input(value):
            parsed = parse_app_store_id(value)
            if not parsed:
                raise AppRestoreError("некорректный App Store ID")
            path = service.download_by_store_id(parsed)
        else:
            path = service.download(value, None)
        print(path)
    elif choice == "3":
        ipa_path = input("Путь к IPA: ").strip()
        if not ipa_path:
            return
        device = _pick_device(service, None)
        metadata = service.install(device.udid, Path(ipa_path))
        print(f"Installed {metadata.name} {metadata.version}")
    elif choice != "0":
        print("Неверный пункт.")


def _menu_devices_and_doctor(service: AppRestoreService) -> None:
    _command_devices(service, json_output=False)
    checks = service.tools.doctor()
    ok = sum(1 for check in checks if check.ok)
    fail = [check.name for check in checks if not check.ok]
    if fail:
        print(f"Диагностика: OK {ok}, FAIL {len(fail)} ({', '.join(fail)})")
    else:
        print(f"Диагностика: OK {ok}")


def _run_menu(service: AppRestoreService) -> int:
    while True:
        _clear_screen()
        _print_header()
        if sys.platform == "win32" and not service.tools.windows_bridge_ready():
            print("Apple USB-мост не готов — для настройки выберите пункт B.")
            print(_MENU_RULE)
        print(
            """
  1) Сгруженные — список и восстановление
  2) Удалённые — список, ID/URL/поиск и восстановление
  3) Локальные IPA — найти / скачать / установить
  4) Устройства и диагностика
  A) Войти в Apple ID
  B) Настроить зависимости
  0) Выход
""".rstrip()
        )
        print()
        try:
            choice = input("Выбор: ").strip()
        except EOFError:
            return 0

        try:
            if choice == "1":
                _command_restore(service, udid=None, email=None, selection=None)
            elif choice == "2":
                _command_restore_missing(
                    service,
                    udid=None,
                    email=None,
                    selection=None,
                )
            elif choice == "3":
                _menu_local_ipa(service)
            elif choice == "4":
                _menu_devices_and_doctor(service)
            elif choice.lower() == "a":
                email = input("Apple ID email: ").strip()
                if email:
                    service.authenticate(email)
            elif choice.lower() == "b":
                _command_setup(service, json_output=False)
            elif choice == "0":
                return 0
            else:
                print("Неверный пункт.")
        except (
            AppRestoreError,
            CatalogError,
            CommandError,
            IpaError,
            ToolUnavailable,
            ValueError,
            OSError,
        ) as exc:
            print(f"Error: {exc}", file=sys.stderr)

        if choice != "0":
            _pause_menu()
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = AppRestoreArgumentParser(
        prog="apprestore",
        description="Restore offloaded iOS applications from macOS or Windows.",
    )
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("--ipa-dir", type=Path, help="local IPA library")
    parser.add_argument("--cache-dir", type=Path, help="cache directory")
    parser.add_argument("--json", action="store_true", help="machine-readable output")

    subparsers = parser.add_subparsers(dest="command", required=False)
    subparsers.add_parser("doctor", help="check dependencies")
    subparsers.add_parser(
        "setup",
        help="install missing Windows Apple USB bridge / re-check dependencies",
    )
    subparsers.add_parser("devices", help="list connected USB devices")
    subparsers.add_parser("scan", help="scan local IPA files")

    offloaded = subparsers.add_parser("offloaded", help="list offloaded applications")
    offloaded.add_argument("--udid")

    missing = subparsers.add_parser(
        "missing",
        help="list apps absent from the device (no icon/placeholder)",
    )
    missing.add_argument("--udid")

    auth = subparsers.add_parser("auth", help="authenticate ipatool securely")
    auth.add_argument("--email")
    auth.add_argument("--revoke", action="store_true")

    download = subparsers.add_parser("download", help="download and verify an IPA")
    download.add_argument("bundle_id")
    download.add_argument("--store-id")
    download.add_argument("--email")
    download.add_argument(
        "--acquire-license",
        action="store_true",
        help="explicitly allow ipatool --purchase after read-only attempts fail",
    )

    install = subparsers.add_parser("install", help="verify and install a local IPA")
    install.add_argument("ipa", type=Path)
    install.add_argument("--udid")
    install.add_argument("--expect-bundle-id")

    restore = subparsers.add_parser(
        "restore",
        help="restore offloaded apps that still have a placeholder",
    )
    restore.add_argument("--udid")
    restore.add_argument("--email")
    restore.add_argument("--select", dest="selection")
    restore.add_argument("--acquire-license", action="store_true")
    restore.add_argument(
        "--skip-device-redownload",
        action="store_true",
        help="skip native iOS restore after confirming no download is active",
    )

    restore_missing = subparsers.add_parser(
        "restore-missing",
        help="restore apps with no icon/placeholder left on the phone",
    )
    restore_missing.add_argument("--udid")
    restore_missing.add_argument("--email")
    restore_missing.add_argument("--select", dest="selection")
    restore_missing.add_argument("--bundle-id")
    restore_missing.add_argument(
        "--store-id",
        help="App Store numeric ID or apps.apple.com URL (bundle ID optional)",
    )
    restore_missing.add_argument("--acquire-license", action="store_true")

    search = subparsers.add_parser(
        "search",
        help="search public catalogs without changing restore history",
    )
    search.add_argument("term", nargs="?")
    search.add_argument("--email")
    search.add_argument("--limit", type=int, default=10)
    return parser


def main(argv: list[str] | None = None) -> int:
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    try:
        args = parser.parse_args(raw_argv)
    except CliUsageError as exc:
        if "--json" in raw_argv:
            _json_dump({"error": str(exc)})
        else:
            exc.parser.print_usage(sys.stderr)
            print(f"{exc.parser.prog}: error: {exc}", file=sys.stderr)
        return 2

    try:
        service = AppRestoreService(
            library=args.ipa_dir,
            cache=args.cache_dir,
            json_output=args.json,
        )
        if not args.command:
            if args.json:
                raise AppRestoreError("interactive menu does not support --json")
            return _run_menu(service)
        if args.command == "doctor":
            return _command_doctor(service, args.json)
        if args.command == "setup":
            return _command_setup(service, args.json)
        if args.command == "devices":
            return _command_devices(service, args.json)
        if args.command == "scan":
            return _command_scan(service, args.json)
        if args.command == "offloaded":
            return _command_offloaded(service, args.udid, args.json)
        if args.command == "missing":
            return _command_missing(service, args.udid, args.json)
        if args.command == "auth":
            if args.revoke:
                service.tools.ipatool_revoke()
                if args.json:
                    _json_dump({"revoked": True})
                return 0
            if args.json and not args.email:
                raise AppRestoreError("--email is required with --json auth")
            email = args.email or input("Apple ID email: ").strip()
            service.authenticate(email)
            if args.json:
                _json_dump({"authenticated": True, "email": email})
            return 0
        if args.command == "download":
            output_context = redirect_stdout(sys.stderr) if args.json else nullcontext()
            with output_context:
                _ensure_auth(service, args.email, noninteractive=args.json)
                if args.store_id is not None:
                    parsed_store_id = parse_app_store_id(args.store_id)
                    if not parsed_store_id:
                        raise AppRestoreError("некорректный --store-id")
                    if args.acquire_license:
                        path = service.download(
                            args.bundle_id,
                            parsed_store_id,
                            acquire_license=True,
                        )
                    else:
                        path = service.download(args.bundle_id, parsed_store_id)
                elif _looks_like_store_id_input(args.bundle_id):
                    parsed = parse_app_store_id(args.bundle_id)
                    if not parsed:
                        raise AppRestoreError("некорректный App Store ID")
                    if args.acquire_license:
                        path = service.download_by_store_id(
                            parsed,
                            acquire_license=True,
                        )
                    else:
                        path = service.download_by_store_id(parsed)
                else:
                    if args.acquire_license:
                        path = service.download(
                            args.bundle_id,
                            None,
                            acquire_license=True,
                        )
                    else:
                        path = service.download(args.bundle_id, None)
            if args.json:
                _json_dump({"path": str(path)})
            else:
                print(path)
            return 0
        if args.command == "install":
            device = _pick_device(
                service,
                args.udid,
                noninteractive=args.json,
            )
            metadata = service.install(
                device.udid,
                args.ipa,
                expected_bundle_id=args.expect_bundle_id,
            )
            if args.json:
                _json_dump(metadata.to_dict())
            else:
                print(f"Installed {metadata.name} {metadata.version}")
            return 0
        if args.command == "restore":
            if args.json and args.selection is None:
                raise AppRestoreError("--select is required with --json restore")
            restore_context = (
                redirect_stdout(sys.stderr) if args.json else nullcontext()
            )
            with restore_context:
                result = _command_restore(
                    service,
                    udid=args.udid,
                    email=args.email,
                    selection=args.selection,
                    acquire_license=args.acquire_license,
                    try_device_redownload=not args.skip_device_redownload,
                    noninteractive=args.json,
                )
            if args.json:
                _json_dump({"success": result == 0, "exitCode": result})
            return result
        if args.command == "restore-missing":
            if (
                args.json
                and args.selection is None
                and args.bundle_id is None
                and args.store_id is None
            ):
                raise AppRestoreError(
                    "--select, --bundle-id, or --store-id is required with "
                    "--json restore-missing"
                )
            restore_context = (
                redirect_stdout(sys.stderr) if args.json else nullcontext()
            )
            with restore_context:
                result = _command_restore_missing(
                    service,
                    udid=args.udid,
                    email=args.email,
                    selection=args.selection,
                    bundle_id=args.bundle_id,
                    store_id=args.store_id,
                    acquire_license=args.acquire_license,
                    noninteractive=args.json,
                )
            if args.json:
                _json_dump({"success": result == 0, "exitCode": result})
            return result
        if args.command == "search":
            return _command_search(
                service,
                args.term,
                email=args.email,
                limit=args.limit,
                json_output=args.json,
            )
    except (
        AppRestoreError,
        CatalogError,
        CommandError,
        IpaError,
        ToolUnavailable,
        ValueError,
        OSError,
    ) as exc:
        if args.json:
            _json_dump({"error": str(exc)})
        else:
            print(f"Error: {exc}", file=sys.stderr)
        return 1

    parser.error("unknown command")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
