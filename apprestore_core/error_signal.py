"""Find the failure that actually explains an ipatool/core error message.

Shared by the window (``apprestore_gui.errors``) and the license gate in the
core, so both read "license is required" the same way.
"""

from __future__ import annotations

import re

_NETWORK = (
    "tls handshake",
    "handshake timeout",
    "dial tcp",
    "failed to get bag",
    "connection refused",
    "i/o timeout",
    "net/http",
    "no such host",
    "context deadline exceeded",
    "urlopen",
    "timed out",
    "init.itunes.apple.com",
)
_LICENSE = (
    "license not found",
    "license is required",
    "license required",
    "9610",
    "no purchase",
)
# Apple named the store country. 2059 / "temporarily unavailable" is a
# different answer: the card is not being issued, often after it left the
# public catalog. That must not be told as "wrong country".
_REGION = (
    "another store",
    "другого магазина",
    "unavailable in this region",
    "unavailable for this apple id",
    "not available in your country",
)
_UNAVAILABLE = (
    "temporarily unavailable",
    "2059",
)
_AUTH = (
    "not authenticated",
    "passphrase is required",
    "keychain passphrase",
    "password token",
    "password has changed",
    "sign in to",
    "handle is invalid",
)
_DEVICE = (
    "device not found",
    "usbmux",
    "connectionterminated",
    "connection terminated",
    "device is not connected",
    "not connected",
)


def _body(message: str) -> str:
    text = " ".join(message.split())
    marker = text.casefold().find("typical causes")
    if marker != -1:
        text = text[:marker].rstrip(" .")
    return text


def _rank(fragment: str) -> int:
    low = fragment.casefold()
    if any(hint in low for hint in _AUTH):
        return 100
    if any(hint in low for hint in _REGION):
        return 90
    if any(hint in low for hint in _UNAVAILABLE):
        return 85
    if "failed to purchase" in low or "purchasing paid apps" in low:
        return 80
    if any(hint in low for hint in _LICENSE):
        return 70
    if any(hint in low for hint in _NETWORK):
        return 60
    if "refusing to install" in low:
        return 50
    if "app not found" in low or "failed to find" in low:
        return 40
    if fragment.strip():
        return 10
    return 0


def _signal(message: str) -> str:
    """The attempt that actually explains the failure.

    A later bundle-id retry often says only ``app not found``. That must not
    hide an earlier purchase refusal from the store id.
    """

    text = _body(message)
    quoted = re.findall(r'error="([^"]*)"', text, flags=re.IGNORECASE)
    fragments = [item.strip() for item in quoted if item.strip()]
    if not fragments:
        parts = re.split(r"(?:with|without) --purchase:", text, flags=re.IGNORECASE)
        fragments = [part.strip(" ;") for part in parts if part.strip(" ;")]
    if not fragments:
        return text
    best = max(range(len(fragments)), key=lambda index: (_rank(fragments[index]), index))
    return fragments[best]


def is_license_missing(message: str) -> bool:
    """True when the failure means "no license on this Apple ID".

    Only for attempts made without ``--purchase``: only then may AppRestore
    go through the license gate (price==0, limits, journal) and retry.
    """

    raw = (message or "").strip()
    if not raw or "with --purchase:" in raw.casefold():
        return False
    low = _signal(raw).casefold()
    if any(hint in low for hint in _AUTH + _NETWORK + _DEVICE + _REGION):
        return False
    if any(hint in low for hint in _LICENSE):
        return True
    # A download that has not purchased yet. Apple often answers 2059
    # instead of "license is required" when the card is gone from the
    # public catalog. The gate still checks the price and the limits.
    return any(hint in low for hint in _UNAVAILABLE)
