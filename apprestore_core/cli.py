from __future__ import annotations

import argparse
from contextlib import nullcontext, redirect_stdout
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

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


def _pick_device(service: AppRestoreService, requested: str | None) -> Device:
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
            values = range(start, end + 1)
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
        print(
            "Список пуст (iMazing / локальные IPA / known-apps). "
            "Можно ввести App Store ID, URL или search <имя>."
        )
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


def _ensure_auth(service: AppRestoreService, email: str | None) -> None:
    print("Проверка входа ipatool…")
    if service.tools.ipatool_authenticated():
        print("ipatool: вход уже выполнен.")
        return
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
            mark = "OK" if check.ok else "FAIL"
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
            mark = "OK" if check.ok else "FAIL"
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
    entries, errors = service.scan_local()
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
    device = _pick_device(service, udid)
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
) -> int:
    device = _pick_device(service, udid)
    print(f"Device: {device.name}, iOS {device.ios_version}")
    apps = service.offloaded(device.udid)
    if not apps:
        print("No offloaded applications found.")
        return 1
    _print_apps(apps)

    raw = selection if selection is not None else input("Restore (1,3-5 or all): ")
    selected = _parse_selection(raw, len(apps))
    if not selected:
        print("Cancelled.")
        return 0

    ok = 0
    failed = 0
    for index in selected:
        app = apps[index]
        print(f"\n→ {app.name} ({app.bundle_id})")
        try:
            status = service.restore_offloaded(device.udid, app)
            print(f"  done: {status}")
            ok += 1
        except AppRestoreError as exc:
            if "not authenticated" in str(exc).lower():
                _ensure_auth(service, email)
                try:
                    status = service.restore_offloaded(
                        device.udid,
                        app,
                        try_device_redownload=False,
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


def _command_missing(
    service: AppRestoreService,
    udid: str | None,
    json_output: bool,
) -> int:
    device = _pick_device(service, udid)
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
        print(
            "Удалённые / отсутствующие: нет ярлыка и нет placeholder на iPhone."
        )
        print("Источники: iMazing Apps.plist и локальная библиотека IPA.")
        _print_missing_apps(apps)
    return 0 if apps else 1


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
    print("Поиск: iTunes + архив IPA Filezone…")
    results = service.search_apps(query)
    if not results:
        print(
            "Ничего не найдено. Для снятых с витрины приложений нужен "
            "App Store ID / URL из покупок или истории."
        )
        return []
    print("\nРезультаты поиска:")
    for index, row in enumerate(results, 1):
        bundle = row.get("bundleId") or "—"
        source = row.get("source") or "?"
        print(
            f"{index:>3}) {row.get('name') or '?'} "
            f"(id{row.get('storeId')}, {bundle}) [{source}]"
        )
    try:
        pick = input("Номер результата (Enter — отмена): ").strip()
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
) -> list[MissingApp]:
    text = raw.strip()
    if not text:
        return []

    lowered = text.casefold()
    for prefix in ("search:", "search ", "найти:", "найти ", "find:", "find "):
        if lowered.startswith(prefix):
            if service is None:
                raise AppRestoreError("поиск недоступен в этом контексте")
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
    except IpaError as exc:
        raise AppRestoreError(
            "укажите номер из списка, App Store ID/URL, bundle ID "
            "или search <имя>"
        ) from exc
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
) -> int:
    device = _pick_device(service, udid)
    print(f"Device: {device.name}, iOS {device.ios_version}")
    print(
        "Сценарий: приложение было, но ярлык/placeholder уже удалён. "
        "Штатный redownload с телефона недоступен."
    )

    if store_id and not bundle_id:
        parsed = parse_app_store_id(store_id) or (
            store_id.strip() if store_id.strip().isdigit() else None
        )
        if not parsed:
            raise AppRestoreError("некорректный --store-id")
        targets = [_missing_from_store_id(parsed)]
    elif bundle_id:
        try:
            normalized = validate_bundle_id(bundle_id)
        except IpaError as exc:
            raise AppRestoreError(str(exc)) from exc
        targets = [
            MissingApp(
                bundle_id=normalized,
                name=normalized,
                store_id=store_id,
                store_match="manual" if store_id else "none",
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
            else input(
                "Restore: номер / App Store ID или URL / bundle ID / search <имя>: "
            )
        )
        targets = _resolve_missing_targets(
            raw,
            apps,
            service=service,
            email=email,
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
        print("Cancelled.")
        return 0

    ok = 0
    failed = 0
    for app in targets:
        identity = app.bundle_id or (f"id{app.store_id}" if app.store_id else "?")
        print(f"\n→ {app.name} ({identity})")
        try:
            status = service.restore_missing(device.udid, app)
            print(f"  done: {status}")
            ok += 1
        except AppRestoreError as exc:
            if "not authenticated" in str(exc).lower():
                _ensure_auth(service, email)
                try:
                    status = service.restore_missing(device.udid, app)
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
    query = (term or "").strip() or input("Поиск (iTunes + IPA Filezone): ").strip()
    if not query:
        print("Cancelled.")
        return 0
    if not json_output:
        print("Поиск: iTunes + IPA Filezone + веб (если нужно)…")
    results = service.search_apps(query, limit=limit)
    for row in results:
        remember_known_app(
            store_id=row["storeId"],
            bundle_id=row.get("bundleId") or None,
            name=row.get("name"),
        )
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
        print(
            "ID сохранены в known-apps.json. "
            "Дальше: меню 2 → вставить id… / URL или выбрать из списка."
        )
    return 0 if results else 1


def _pause_menu() -> None:
    try:
        input("\nEnter, чтобы продолжить…")
    except EOFError:
        pass


def _clear_screen() -> None:
    if sys.platform == "win32":
        os.system("cls")
    else:
        os.system("clear")


def _run_menu(service: AppRestoreService) -> int:
    while True:
        _clear_screen()
        _print_header()
        if sys.platform == "win32" and not service.tools.windows_bridge_ready():
            print("Apple USB-мост не готов — для настройки выберите пункт B.")
            print(_MENU_RULE)
        print(
            """
  1) Восстановить сгруженные приложения
  2) Восстановить удалённые (нет ярлыка) — ID / URL / поиск
  3) Проверить зависимости
  4) Показать подключённые устройства
  5) Показать сгруженные приложения
  6) Показать удалённые / отсутствующие
  7) Найти локальные IPA
  8) Скачать IPA (bundle ID или App Store ID)
  9) Проверить и установить локальный IPA
  S) Поиск приложения в App Store
  A) Войти в Apple ID через ipatool
  B) Установить/обновить зависимости
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
                _command_doctor(service, json_output=False)
            elif choice == "4":
                _command_devices(service, json_output=False)
            elif choice == "5":
                _command_offloaded(service, None, json_output=False)
            elif choice == "6":
                _command_missing(service, None, json_output=False)
            elif choice == "7":
                _command_scan(service, json_output=False)
            elif choice == "8":
                value = input("Bundle ID или App Store ID/URL: ").strip()
                if value:
                    _ensure_auth(service, None)
                    store_id = parse_app_store_id(value)
                    if store_id and (
                        value.strip().isdigit()
                        or value.casefold().startswith("id")
                        or "apps.apple.com" in value.casefold()
                    ):
                        path = service.download_by_store_id(store_id)
                    else:
                        path = service.download(value, None)
                    print(path)
            elif choice == "9":
                ipa_path = input("Путь к IPA: ").strip()
                if ipa_path:
                    device = _pick_device(service, None)
                    metadata = service.install(device.udid, Path(ipa_path))
                    print(f"Installed {metadata.name} {metadata.version}")
            elif choice.lower() == "s":
                _command_search(service, None, email=None, json_output=False)
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
    parser = argparse.ArgumentParser(
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

    search = subparsers.add_parser(
        "search",
        help="search App Store via ipatool and remember found IDs",
    )
    search.add_argument("term", nargs="?")
    search.add_argument("--email")
    search.add_argument("--limit", type=int, default=10)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    service = AppRestoreService(library=args.ipa_dir, cache=args.cache_dir)

    try:
        if not args.command:
            if args.json:
                parser.error("interactive menu does not support --json")
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
                return 0
            email = args.email or input("Apple ID email: ").strip()
            service.authenticate(email)
            return 0
        if args.command == "download":
            output_context = redirect_stdout(sys.stderr) if args.json else nullcontext()
            with output_context:
                _ensure_auth(service, args.email)
                path = service.download(args.bundle_id, args.store_id)
            if args.json:
                _json_dump({"path": str(path)})
            else:
                print(path)
            return 0
        if args.command == "install":
            device = _pick_device(service, args.udid)
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
            return _command_restore(
                service,
                udid=args.udid,
                email=args.email,
                selection=args.selection,
            )
        if args.command == "restore-missing":
            return _command_restore_missing(
                service,
                udid=args.udid,
                email=args.email,
                selection=args.selection,
                bundle_id=args.bundle_id,
                store_id=args.store_id,
            )
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
