"""Demo fixtures with real popular App Store bundle IDs for icon prefetch."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from apprestore_core.models import Device, DoctorCheck, MissingApp, OffloadedApp


@dataclass(frozen=True)
class DemoApp:
    bundle_id: str
    name: str
    version: str
    store_id: str | None = None


# Real public App Store identities (icons via iTunes Lookup, no Apple ID).
DEMO_OFFLOADED: list[DemoApp] = [
    DemoApp("com.burbn.instagram", "Instagram", "340.0", "389801252"),
    DemoApp("com.atebits.Tweetie2", "X", "10.0", "333903271"),
    DemoApp("com.spotify.client", "Spotify", "9.0", "324684580"),
    DemoApp("net.whatsapp.WhatsApp", "WhatsApp", "25.1", "310633997"),
]

DEMO_MISSING: list[DemoApp] = [
    DemoApp("com.google.chrome.ios", "Chrome", "128.0", "535886823"),
    DemoApp("com.apple.mobilenotes", "Notes", "3.0", None),
    DemoApp("com.amazon.Kinder", "Kindle", "7.0", "405399194"),
]

DEMO_LIBRARY: list[tuple[str, str, str]] = [
    ("Instagram.ipa", "com.burbn.instagram", "Instagram"),
    ("WhatsApp.ipa", "net.whatsapp.WhatsApp", "WhatsApp"),
    ("Spotify.ipa", "com.spotify.client", "Spotify"),
]


def demo_device() -> Device:
    return Device(udid="DEMO-UDID-0001", name="iPhone 14", ios_version="18.2")


def demo_offloaded() -> list[OffloadedApp]:
    return [
        OffloadedApp(
            bundle_id=a.bundle_id,
            name=a.name,
            version=a.version,
            store_id=a.store_id,
            store_match="demo" if a.store_id else "none",
        )
        for a in DEMO_OFFLOADED
    ]


def demo_missing() -> list[MissingApp]:
    return [
        MissingApp(
            bundle_id=a.bundle_id,
            name=a.name,
            version=a.version,
            store_id=a.store_id,
            store_match="demo" if a.store_id else "none",
            source="demo",
        )
        for a in DEMO_MISSING
    ]


def demo_doctor() -> list[DoctorCheck]:
    return [
        DoctorCheck("Связь с телефоном", True, "демо · iPhone 14"),
        DoctorCheck("Инструменты установки", True, "демо"),
        DoctorCheck("Загрузка из магазина", True, "демо"),
        DoctorCheck("Вход в Apple ID", False, "нет сессии (демо)"),
        DoctorCheck("Сеть до магазинов Apple", True, "доступен lookup"),
    ]


def demo_prefetch_targets() -> list[tuple[str | None, str | None, str]]:
    apps = [*DEMO_OFFLOADED, *DEMO_MISSING]
    for _fname, bundle, name in DEMO_LIBRARY:
        apps.append(DemoApp(bundle, name, "1.0"))
    return [(a.bundle_id, a.store_id, a.name) for a in apps]
