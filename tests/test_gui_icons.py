from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication

from apprestore_gui.icons_cache import ArtworkCache, placeholder_pixmap, _safe_key


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    return app


def test_safe_key_strips_odd_chars() -> None:
    assert " " not in _safe_key("a b/c?")


def test_placeholder_pixmap_not_null(qapp) -> None:
    pix = placeholder_pixmap("Spotify", 40)
    assert not pix.isNull()
    assert pix.width() == 40


def test_artwork_cache_uses_fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, qapp) -> None:
    cache = ArtworkCache(cache_dir=tmp_path)
    payload = {
        "results": [
            {
                "trackName": "Spotify",
                "bundleId": "com.spotify.client",
                "trackId": 324684580,
                "artworkUrl100": "https://example.invalid/art.jpg",
            }
        ]
    }

    def fake_get(url: str) -> bytes:
        if "lookup" in url:
            return json.dumps(payload).encode()
        return b"not-an-image"

    monkeypatch.setattr(cache, "_http_get", fake_get)
    info = cache.lookup(bundle_id="com.spotify.client")
    assert info is not None
    assert info["name"] == "Spotify"
    assert info["artwork"].endswith("art.jpg")


def test_rounded_icon_file_prefers_store_artwork(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, qapp
) -> None:
    from PySide6.QtCore import QBuffer, QIODevice
    from PySide6.QtGui import QColor, QImage

    cache = ArtworkCache(cache_dir=tmp_path)
    picture = QImage(24, 24, QImage.Format.Format_RGB32)
    picture.fill(QColor("#25D366"))
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    assert picture.save(buffer, "PNG")
    png = bytes(buffer.data())
    buffer.close()
    calls: list[str] = []

    def fake_get(url: str) -> bytes:
        calls.append(url)
        if "lookup" in url:
            payload = {
                "results": [
                    {
                        "trackName": "WhatsApp",
                        "bundleId": "net.whatsapp.WhatsApp",
                        "trackId": 310633997,
                        "artworkUrl100": "https://example.invalid/art.png",
                    }
                ]
            }
            return json.dumps(payload).encode()
        return png

    monkeypatch.setattr(cache, "_http_get", fake_get)
    path = cache.rounded_icon_file(store_id="310633997", site="example.test")
    assert path is not None and path.is_file()
    saved = QImage(str(path))
    assert not saved.isNull()
    assert saved.width() == 192
    assert all("example.test" not in url for url in calls)
