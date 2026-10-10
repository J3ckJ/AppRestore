from apprestore_gui.device_form import device_form, device_noun


def test_iphone_generations() -> None:
    assert device_form("iPhone10,1") == "iphone-home"
    assert device_form("iPhone10,3") == "iphone-notch"
    assert device_form("iPhone12,8") == "iphone-home"
    assert device_form("iPhone14,6") == "iphone-home"
    assert device_form("iPhone14,5") == "iphone-notch"
    assert device_form("iPhone14,7") == "iphone-notch"
    assert device_form("iPhone15,2") == "iphone-island"
    assert device_form("iPhone17,5") == "iphone-island"
    assert device_form("iPhone18,3") == "iphone-island"
    assert device_form("iPhone18,4") == "iphone-air"
    assert device_form("iPhone18,5") == "iphone-island"


def test_ipad_generations() -> None:
    assert device_form("iPad12,1") == "ipad-home"
    assert device_form("iPad7,5") == "ipad-home"
    assert device_form("iPad13,4") == "ipad"
    assert device_form("iPad14,1") == "ipad"
    assert device_form("iPad13,18") == "ipad-side"
    assert device_form("iPad16,3") == "ipad-side"
    assert device_form("iPad17,1") == "ipad-side"
    assert device_form("iPad15,7") == "ipad-side"
    assert device_form("iPad16,1") == "ipad"


def test_class_fallback_and_noun() -> None:
    assert device_form("", "iPad") == "ipad"
    assert device_form("iPod9,1") == "iphone-home"
    assert device_form("not-a-device") == "iphone-island"
    assert device_noun("ipad-side") == "iPad"
    assert device_noun("iphone-notch") == "iPhone"
