"""The Find shelf only lists real, distinct App Store ids."""

from __future__ import annotations

import unittest

from apprestore_gui.popular_apps import POPULAR_APPS


class PopularAppsTests(unittest.TestCase):
    def test_shelf_is_russian_banks_and_domclick(self) -> None:
        names = [app["name"] for app in POPULAR_APPS]
        self.assertEqual(
            names,
            [
                "Сбер",
                "Т-Банк",
                "ВТБ",
                "Альфа",
                "Газпромбанк",
                "Россельхоз",
                "Открытие",
                "Домклик",
                "Совкомбанк",
                "ПСБ",
                "МКБ",
                "Росбанк",
                "ДОМ.РФ",
                "Уралсиб",
                "Почта Банк",
                "Ак Барс",
                "Банк СПБ",
                "МТС Банк",
                "Хоум Банк",
                "Ренессанс",
                "VK",
                "MAX",
                "OK",
                "Почта",
                "Облако",
                "VK Музыка",
                "VK Видео",
                "VK Мессенджер",
                "VK Почта",
                "Дзен",
                "Юла",
                "Маруся",
                "Знакомства",
            ],
        )

    def test_store_ids_are_unique_positive_integers(self) -> None:
        ids = [app["storeId"] for app in POPULAR_APPS]
        self.assertEqual(len(ids), len(set(ids)))
        for store_id in ids:
            self.assertTrue(store_id.isdigit())
            self.assertGreater(int(store_id), 0)

    def test_sber_is_the_latest_known_release(self) -> None:
        sber = POPULAR_APPS[0]
        self.assertEqual(sber["storeId"], "492224193")
        self.assertEqual(sber["detail"], "Онлайн 17.6.1")

    def test_later_reuploads_replace_the_original_cards(self) -> None:
        by_name = {app["name"]: app for app in POPULAR_APPS}
        self.assertEqual(by_name["Т-Банк"]["storeId"], "6755181069")
        self.assertEqual(by_name["Т-Банк"]["detail"], "Spotluma 2.8.2")
        self.assertEqual(by_name["Альфа"]["storeId"], "6473656113")
        self.assertEqual(by_name["Альфа"]["detail"], "Апгрейд")
        self.assertEqual(by_name["ВТБ"]["storeId"], "6749962031")
        self.assertEqual(by_name["ВТБ"]["detail"], "Сириус")
        self.assertEqual(by_name["Домклик"]["storeId"], "1660762523")
        self.assertEqual(by_name["Домклик"]["detail"], "ДКлик")
        self.assertEqual(by_name["Совкомбанк"]["storeId"], "1208055056")
        self.assertEqual(by_name["Банк СПБ"]["storeId"], "531096347")
        self.assertEqual(by_name["Ренессанс"]["storeId"], "1083085558")
        self.assertEqual(by_name["VK"]["storeId"], "564177498")
        self.assertEqual(by_name["MAX"]["storeId"], "6739530834")
        self.assertEqual(by_name["MAX"]["detail"], "мессенджер")
        self.assertEqual(by_name["Почта"]["storeId"], "511310430")
        self.assertEqual(by_name["Почта"]["detail"], "Mail.ru")
        self.assertEqual(by_name["Облако"]["storeId"], "696551382")
        self.assertEqual(by_name["Дзен"]["storeId"], "1343242452")
        self.assertEqual(by_name["Юла"]["storeId"], "1016489154")
        self.assertEqual(by_name["Знакомства"]["storeId"], "6449036810")
