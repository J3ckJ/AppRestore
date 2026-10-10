"""Which silhouette to draw for a connected Apple device.

ProductType is the model. DeviceClass is only a fallback when the identifier
is missing. The shapes are the ones a person can tell apart at a glance:
home button, notch, Dynamic Island, the thinner iPhone Air, and iPads with
the front camera on the top or the side.
"""

from __future__ import annotations

import re

_IDENTIFIER = re.compile(r"^(iPhone|iPad|iPod)(\d+),(\d+)$")

# Front camera on the short edge, so it sits on top when the iPad is upright.
_IPAD_PORTRAIT_CAMERA = {
    (13, 1),
    (13, 2),  # Air 4
    (13, 16),
    (13, 17),  # Air 5
    (14, 1),
    (14, 2),  # mini 6
    (14, 3),
    (14, 4),
    (14, 5),
    (14, 6),  # Pro M2
    (16, 1),
    (16, 2),  # mini A17 Pro
}
for _minor in range(1, 13):
    _IPAD_PORTRAIT_CAMERA.add((8, _minor))  # Pro 2018–2020
for _minor in range(4, 12):
    _IPAD_PORTRAIT_CAMERA.add((13, _minor))  # Pro M1

# Circle under the screen.
_IPAD_HOME_BUTTON = {
    (4, 1),
    (4, 2),
    (4, 3),
    (4, 4),
    (4, 5),
    (4, 6),
    (4, 7),
    (4, 8),
    (4, 9),
    (5, 1),
    (5, 2),
    (5, 3),
    (5, 4),
    (6, 3),
    (6, 4),
    (6, 7),
    (6, 8),
    (6, 11),
    (6, 12),
    (7, 1),
    (7, 2),
    (7, 3),
    (7, 4),
    (7, 5),
    (7, 6),
    (7, 11),
    (7, 12),
    (11, 1),
    (11, 2),
    (11, 3),
    (11, 4),
    (11, 6),
    (11, 7),
    (12, 1),
    (12, 2),
}


def device_form(product_type: str, device_class: str = "") -> str:
    """Return a silhouette id the window can draw."""

    kind, major, minor = _parse(product_type)
    family = device_class.strip().lower()
    if kind == "iPad" or family == "ipad":
        return _ipad_form(major, minor)
    if kind == "iPod" or family == "ipod":
        return "iphone-home"
    return _iphone_form(major, minor)


def device_noun(form: str) -> str:
    return "iPad" if form.startswith("ipad") else "iPhone"


def _parse(product_type: str) -> tuple[str, int, int]:
    match = _IDENTIFIER.match(product_type.strip())
    if not match:
        return "", 0, 0
    return match.group(1), int(match.group(2)), int(match.group(3))


def _ipad_form(major: int, minor: int) -> str:
    if major <= 0:
        return "ipad"
    if major < 8 or (major, minor) in _IPAD_HOME_BUTTON:
        return "ipad-home"
    if (major, minor) in _IPAD_PORTRAIT_CAMERA:
        return "ipad"
    return "ipad-side"


def _iphone_form(major: int, minor: int) -> str:
    if major <= 0:
        return "iphone-island"
    if major < 10:
        return "iphone-home"
    if major == 10:
        return "iphone-notch" if minor in (3, 6) else "iphone-home"
    if (major, minor) in {(12, 8), (14, 6)}:
        return "iphone-home"
    if (major, minor) == (18, 4):
        return "iphone-air"
    if major < 15:
        return "iphone-notch"
    return "iphone-island"
