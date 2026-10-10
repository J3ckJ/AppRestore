"""User-facing error sentences stay specific and do not repeat the boilerplate."""

from __future__ import annotations

import unittest

from apprestore_gui.errors import explain_update_error, explain_user_error
from apprestore_gui.main_window import friendly_restore_error

_ESSAY = (
    "Typical causes: a network failure reaching Apple (a TLS handshake "
    "timeout means ipatool could not connect - check `apprestore doctor`), "
    "app removed from the App Store, unavailable for this Apple ID "
    "region, or no purchase/license. To explicitly acquire a license, "
    "retry with --acquire-license."
)


class ExplainUserErrorTests(unittest.TestCase):
    def test_closed_session_points_at_apple_id(self) -> None:
        text = friendly_restore_error(
            "ipatool is not authenticated; run `apprestore auth`"
        )
        self.assertIn("Apple ID", text)
        self.assertNotIn("apprestore auth", text)

    def test_license_inside_the_essay_is_not_called_a_network_failure(self) -> None:
        text = explain_user_error(
            "could not download com.example (store=1: license not found). " + _ESSAY
        )
        self.assertIn("лиценз", text)
        self.assertNotIn("doctor", text)
        self.assertNotIn("--acquire", text)
        self.assertNotIn("TLS", text)

    def test_purchase_failure_is_not_hidden_behind_the_missing_license(self) -> None:
        text = explain_user_error(
            "could not download App Store ID 1 ("
            "store=1 without --purchase: license not found; "
            "store=1 with --purchase: failed to purchase item with param 'GAME': "
            "item is temporarily unavailable). " + _ESSAY
        )
        self.assertIn("страны", text)
        self.assertNotIn("Получить", text)

    def test_purchase_refusal_names_the_refusal(self) -> None:
        text = explain_user_error(
            "could not download App Store ID 1 ("
            "store=1 without --purchase: license is required; "
            "store=1 with --purchase: failed to purchase item with param 'STDQ': "
            "failed to purchase app). " + _ESSAY
        )
        self.assertIn("не выдала лицензию", text)
        self.assertNotIn("Операции", text)

    def test_timeout_inside_the_essay_is_not_called_a_region_block(self) -> None:
        text = explain_user_error(
            "could not download com.example "
            "(store=1: net/http: TLS handshake timeout). " + _ESSAY
        )
        self.assertIn("не ответил", text)
        self.assertNotIn("лиценз", text.casefold())
        self.assertNotIn("стран", text)

    def test_temporary_unavailability_is_the_store_country(self) -> None:
        text = explain_user_error(
            "failed to purchase item with param 'GAME': item is temporarily unavailable"
        )
        self.assertIn("страны", text)
        self.assertIn("не меняет", text)

    def test_paid_app_and_phone_and_russian_text(self) -> None:
        self.assertIn("Платное", explain_user_error("purchasing paid apps is not supported"))
        self.assertIn("кабел", explain_user_error("device not found via usbmux"))
        self.assertEqual(
            explain_user_error("Выберите хотя бы одно приложение."),
            "Выберите хотя бы одно приложение.",
        )

    def test_later_bundle_miss_does_not_hide_the_purchase_refusal(self) -> None:
        text = explain_user_error(
            "could not download ru.doublegis.grymmobile ("
            "store=481627348 without --purchase: error=\"license is required\"; "
            "bundle=ru.doublegis.grymmobile without --purchase: error=\"app not found\"; "
            "store=481627348 with --purchase: error=\"failed to purchase app\"; "
            "bundle=ru.doublegis.grymmobile with --purchase: error=\"app not found\"). "
            + _ESSAY
        )
        self.assertIn("не выдала лицензию", text)
        self.assertNotIn("Операции", text)
        self.assertNotIn("не нашла", text)

    def test_unknown_tool_error_is_quoted_instead_of_a_missing_log(self) -> None:
        text = explain_user_error(
            'could not download com.example (store=1 with --purchase: error="boom from apple"). '
            + _ESSAY
        )
        self.assertIn("boom from apple", text)
        self.assertNotIn("Операции", text)

    def test_closed_console_is_a_closed_session(self) -> None:
        text = explain_user_error(
            "could not download App Store ID 1 "
            "(store=1 without --purchase: The handle is invalid.). " + _ESSAY
        )
        self.assertIn("Сессия", text)
        self.assertNotIn("Файл не скачался", text)

    def test_update_failure_does_not_talk_about_apple(self) -> None:
        text = explain_update_error("WinError 32: the process cannot access the file")
        self.assertEqual(text, "Обновление не установилось.")
        self.assertNotIn("Apple", text)
