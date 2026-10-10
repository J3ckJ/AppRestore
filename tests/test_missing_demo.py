from apprestore_gui.missing_demo import missing_demo_apps


def test_missing_demo_is_a_choice_not_a_handful() -> None:
    apps = missing_demo_apps()
    assert len(apps) > 40
    assert apps[0]["name"] == "Сбер"
    assert apps[0]["storeId"] == "492224193"
    assert len({app["storeId"] for app in apps}) == len(apps)
    assert len({app["name"] for app in apps}) == len(apps)
