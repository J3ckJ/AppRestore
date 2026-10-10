from __future__ import annotations

from unittest.mock import patch

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import Qt

from apprestore_core.models import CommandResult
from apprestore_gui.auth_pty import (
    KeychainRunner,
    _explain_login_failure,
    classify_keychain_unlock,
    extract_json_document,
    unlock_keychain,
)
from apprestore_gui.main_window import file_install_prompt, friendly_restore_error
from apprestore_gui.service_adapter import GuiService
from apprestore_gui.theme import INK, PANEL, STYLESHEET, pin_light_native_chrome


def test_offloaded_redownload_prompt_matches_cli_fallback() -> None:
    message = (
        "could not safely determine whether native redownload "
        "started (last state: offloaded); refusing a competing IPA install. "
        "After confirming that iPhone is not downloading, retry with "
        "--skip-device-redownload"
    )
    prompt = file_install_prompt(message, "Карты")
    assert prompt is not None
    assert "Карты" in prompt
    assert "всё ещё сгружено" in prompt
    assert "--skip" not in prompt

    downloading = (
        "iPhone did not finish downloading com.example "
        "(last state: downloading); refusing a competing IPA install."
    )
    still = file_install_prompt(downloading, "Карты")
    assert still is not None
    assert "ещё качает" in still
    assert file_install_prompt("device not found") is None
    assert "Apple ID" in friendly_restore_error(
        "ipatool is not authenticated; run `apprestore auth`"
    )


def test_pin_light_native_chrome_requests_a_light_menu() -> None:
    class _Hints:
        def __init__(self) -> None:
            self.scheme = None

        def setColorScheme(self, scheme: Qt.ColorScheme) -> None:
            self.scheme = scheme

    class _App:
        def __init__(self) -> None:
            self.hints = _Hints()

        def styleHints(self) -> _Hints:
            return self.hints

    app = _App()
    pin_light_native_chrome(app)  # type: ignore[arg-type]
    assert app.hints.scheme == Qt.ColorScheme.Light


def test_context_menu_stylesheet_pairs_background_with_text() -> None:
    menu = STYLESHEET.split("QMenu {", 1)[1].split("}", 1)[0]
    assert PANEL in menu
    assert INK in menu
    item = STYLESHEET.split("QMenu::item {", 1)[1].split("}", 1)[0]
    assert INK in item


def test_pty_transcript_keeps_the_json_document() -> None:
    raw = (
        'enter passphrase to unlock "C:\\Users\\me\\.ipatool": '
        '{"success": true, "email": "owner@example.com"}'
    )
    document = extract_json_document(raw)
    assert document is not None
    assert document.startswith("{")
    assert "owner@example.com" in document
    assert extract_json_document("no json here") is None


def test_keychain_runner_replays_passphrase_without_putting_it_on_the_command() -> None:
    with patch("apprestore_gui.auth_pty.run_pty_command") as run:
        run.return_value = CommandResult(("ipatool.exe", "download"), 0, "", "")
        runner = KeychainRunner(lambda: "once-only")
        result = runner.run(
            [r"C:\Tools\ipatool.exe", "download", "--app-id", "1"],
            capture=False,
            timeout=30,
        )
    assert result.returncode == 0
    assert run.call_args.kwargs["passphrase"] == "once-only"
    assert "once-only" not in run.call_args.args[0]


def test_keychain_runner_does_not_touch_other_tools() -> None:
    with patch(
        "apprestore_core.command.Runner.run",
        return_value=CommandResult(("pymobiledevice3",), 3),
    ) as run:
        runner = KeychainRunner(lambda: "once-only")
        result = runner.run(["pymobiledevice3", "version"], timeout=5)
    assert result.returncode == 3
    run.assert_called_once()


def test_demo_installed_apps_keep_store_ids_for_library_export() -> None:
    service = GuiService(demo_mode=True)
    apps = service.installed_apps("DEMO-UDID-0001")
    by_id = {app.bundle_id: app for app in apps}
    assert by_id["com.google.chrome.ios"].store_id == "535886823"
    assert by_id["com.example.sideload"].store_id is None
    assert service.download_to_library(by_id["com.amazon.Kindle"]) == "Kindle.ipa"


def test_passphrase_stays_in_memory_for_the_session() -> None:
    service = GuiService(demo_mode=True)
    assert not service.keychain_ready()
    service.remember_keychain_passphrase("once-only\n")
    assert service.keychain_passphrase() == "once-only"
    service.clear_keychain_passphrase()
    assert service.keychain_passphrase() == ""


def test_missing_account_keeps_the_passphrase_without_marking_a_session() -> None:
    missing = (
        "failed to get account: failed to get item: "
        "The specified item could not be found in the keyring"
    )
    result = classify_keychain_unlock(1, missing)
    assert result.ok
    assert result.session_open is False
    assert "204" not in result.message

    class _Tools:
        _ipatool_session_authenticated = True

    class _Core:
        tools = _Tools()

    service = GuiService(demo_mode=True)
    service._service = _Core()  # type: ignore[assignment]
    service.remember_keychain_passphrase("local-secret", session_open=False)
    assert service.keychain_passphrase() == "local-secret"
    assert service._service.tools._ipatool_session_authenticated is False

    with patch("apprestore_gui.auth_pty.keychain_has_saved_account", return_value=False):
        accepted = unlock_keychain("local-secret")
    assert accepted.ok
    assert accepted.session_open is False
    assert not classify_keychain_unlock(1, "invalid passphrase").ok


def test_empty_store_login_is_not_described_as_a_redirect() -> None:
    message = (
        "authentication request failed after 3 attempts (HTTP 204, 204, 204): "
        "unexpected response from Apple (HTTP 204): empty or non-plist "
        "authentication response"
    )
    text = _explain_login_failure(1, message)
    assert "204" in text
    assert "301" not in text
    assert "icloud.com" in text


def test_apple_redirect_login_is_explained_in_russian() -> None:
    message = (
        'error="apple returned no usable authentication response (HTTP 301): '
        'missing account credentials or unexpected status; try again later '
        'or from another network"'
    )
    text = _explain_login_failure(1, message)
    # Ника §4 + R5 (Лена): short, no technical details, no VPN advice
    assert text == "Apple не приняла вход. Попробуйте ещё раз позже."
    assert "VPN" not in text
    assert "код 1" not in text


def test_demo_adapter_lists_apps() -> None:
    svc = GuiService(demo_mode=True)
    devices = svc.devices()
    assert devices and devices[0].name == "iPhone 14"
    apps = svc.offloaded(devices[0].udid)
    assert len(apps) >= 3
    assert svc.missing(devices[0].udid)
    assert svc.doctor()
    assert svc.search("spot")
