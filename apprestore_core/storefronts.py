"""App Store storefront id → country code for the public iTunes lookup.

The table is ported from majd/ipatool ``pkg/appstore/storefront.go`` (MIT,
commit cde7d00; see THIRD_PARTY_NOTICES.md for the ipatool licence). A stored
value looks like ``143469-16,29``: the number before ``-``/``,`` is the
storefront id.
"""

from __future__ import annotations

from collections.abc import Mapping

STOREFRONT_COUNTRIES: dict[str, str] = {
    "143481": "ae",
    "143540": "ag",
    "143538": "ai",
    "143575": "al",
    "143524": "am",
    "143564": "ao",
    "143505": "ar",
    "143445": "at",
    "143460": "au",
    "143568": "az",
    "143541": "bb",
    "143490": "bd",
    "143446": "be",
    "143526": "bg",
    "143559": "bh",
    "143542": "bm",
    "143560": "bn",
    "143556": "bo",
    "143503": "br",
    "143539": "bs",
    "143525": "bw",
    "143565": "by",
    "143555": "bz",
    "143455": "ca",
    "143459": "ch",
    "143527": "ci",
    "143483": "cl",
    "143465": "cn",
    "143501": "co",
    "143495": "cr",
    "143557": "cy",
    "143489": "cz",
    "143443": "de",
    "143458": "dk",
    "143545": "dm",
    "143508": "do",
    "143563": "dz",
    "143509": "ec",
    "143518": "ee",
    "143516": "eg",
    "143454": "es",
    "143447": "fi",
    "143442": "fr",
    "143444": "gb",
    "143546": "gd",
    "143615": "ge",
    "143573": "gh",
    "143448": "gr",
    "143504": "gt",
    "143553": "gy",
    "143463": "hk",
    "143510": "hn",
    "143494": "hr",
    "143482": "hu",
    "143476": "id",
    "143449": "ie",
    "143491": "il",
    "143467": "in",
    "143617": "iq",
    "143558": "is",
    "143450": "it",
    "143511": "jm",
    "143528": "jo",
    "143462": "jp",
    "143529": "ke",
    "143548": "kn",
    "143466": "kr",
    "143493": "kw",
    "143544": "ky",
    "143517": "kz",
    "143497": "lb",
    "143549": "lc",
    "143522": "li",
    "143486": "lk",
    "143520": "lt",
    "143451": "lu",
    "143519": "lv",
    "143523": "md",
    "143531": "mg",
    "143530": "mk",
    "143532": "ml",
    "143592": "mn",
    "143515": "mo",
    "143547": "ms",
    "143521": "mt",
    "143533": "mu",
    "143488": "mv",
    "143468": "mx",
    "143473": "my",
    "143534": "ne",
    "143561": "ng",
    "143512": "ni",
    "143452": "nl",
    "143457": "no",
    "143484": "np",
    "143461": "nz",
    "143562": "om",
    "143485": "pa",
    "143507": "pe",
    "143474": "ph",
    "143477": "pk",
    "143478": "pl",
    "143453": "pt",
    "143513": "py",
    "143498": "qa",
    "143487": "ro",
    "143500": "rs",
    "143469": "ru",
    "143479": "sa",
    "143456": "se",
    "143464": "sg",
    "143499": "si",
    "143496": "sk",
    "143535": "sn",
    "143554": "sr",
    "143506": "sv",
    "143552": "tc",
    "143475": "th",
    "143536": "tn",
    "143480": "tr",
    "143551": "tt",
    "143470": "tw",
    "143572": "tz",
    "143492": "ua",
    "143537": "ug",
    "143441": "us",
    "143514": "uy",
    "143566": "uz",
    "143550": "vc",
    "143502": "ve",
    "143543": "vg",
    "143471": "vn",
    "143571": "ye",
    "143472": "za",
}

_STOREFRONT_KEYS = ("storeFront", "storefront", "store_front", "storeFrontId", "storefrontId")
_COUNTRY_KEYS = ("countryCode", "country_code", "country")


def country_from_storefront(value: object) -> str:
    """``"143469-16,29"`` → ``"ru"``; ``""`` when unknown."""

    text = str(value or "").strip()
    if not text:
        return ""
    head = text.split("-", 1)[0].split(",", 1)[0].strip()
    return STOREFRONT_COUNTRIES.get(head, "")


def account_country(payload: object) -> str:
    """Country code of the signed-in account from ``auth info --format json``.

    AppRestore's ipatool build (Макс's patch over cde7d00) prints
    ``countryCode`` (ISO, e.g. ``US``) and the raw ``storeFront``
    (``143441-1,34``). ``countryCode`` wins; otherwise the storefront id is
    mapped here. Plain cde7d00 prints neither: ``""`` = country unknown, and
    the license gate then refuses to buy rather than guess.
    """

    if not isinstance(payload, Mapping):
        return ""
    known = set(STOREFRONT_COUNTRIES.values())
    for key in _COUNTRY_KEYS:
        raw = str(payload.get(key) or "").strip().lower()
        if raw in known:
            return raw
    for key in _STOREFRONT_KEYS:
        code = country_from_storefront(payload.get(key))
        if code:
            return code
    account = payload.get("account")
    if isinstance(account, Mapping):
        return account_country(account)
    return ""
