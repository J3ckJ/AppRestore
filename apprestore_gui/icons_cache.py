"""Fetch and cache App Store artwork via public iTunes Lookup API (no Apple ID)."""

from __future__ import annotations

import hashlib
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from PySide6.QtCore import QByteArray, QSize, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath, QPixmap

USER_AGENT = "AppRestoreGUI/0.3 (+https://github.com/J3ckJ/AppRestore)"
LOOKUP = "https://itunes.apple.com/lookup"


def default_cache_dir() -> Path:
    try:
        from apprestore_core.paths import cache_dir

        root = cache_dir() / "artwork"
    except Exception:
        root = Path.home() / ".cache" / "apprestore" / "artwork"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _safe_key(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", value)[:180]


class ArtworkCache:
    def __init__(self, cache_dir: Path | None = None, timeout: float = 12.0) -> None:
        self.cache_dir = cache_dir or default_cache_dir()
        self.timeout = timeout
        self._meta_path = self.cache_dir / "index.json"
        self._index: dict[str, Any] = {}
        if self._meta_path.is_file():
            try:
                self._index = json.loads(self._meta_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                self._index = {}

    def _save_index(self) -> None:
        try:
            self._meta_path.write_text(
                json.dumps(self._index, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError:
            pass

    def _http_get(self, url: str) -> bytes:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            return resp.read()

    def lookup(
        self,
        *,
        bundle_id: str | None = None,
        store_id: str | None = None,
        country: str = "us",
    ) -> dict[str, Any] | None:
        params: dict[str, str] = {"country": country}
        key: str
        if store_id:
            params["id"] = str(store_id)
            key = f"id:{store_id}:{country}"
        elif bundle_id:
            params["bundleId"] = bundle_id
            key = f"bundle:{bundle_id}:{country}"
        else:
            return None
        cached = self._index.get(key)
        if isinstance(cached, dict) and cached.get("artwork"):
            return cached
        try:
            raw = self._http_get(f"{LOOKUP}?{urllib.parse.urlencode(params)}")
            payload = json.loads(raw.decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
            return cached if isinstance(cached, dict) else None
        results = payload.get("results") or []
        if not results:
            self._index[key] = {"artwork": None}
            self._save_index()
            return self._index[key]
        item = results[0]
        info = {
            "artwork": item.get("artworkUrl512")
            or item.get("artworkUrl100")
            or item.get("artworkUrl60"),
            "name": item.get("trackName") or item.get("trackCensoredName"),
            "bundleId": item.get("bundleId"),
            "trackId": str(item.get("trackId") or "") or None,
        }
        self._index[key] = info
        self._save_index()
        return info

    def local_path_for(self, url: str) -> Path:
        digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]
        leaf = _safe_key(url.split("/")[-1].split("?")[0]) or "icon"
        if not leaf.lower().endswith((".jpg", ".jpeg", ".png", ".webp")):
            leaf += ".jpg"
        return self.cache_dir / f"{digest}-{leaf}"

    def fetch_file(self, url: str) -> Path | None:
        path = self.local_path_for(url)
        if path.is_file() and path.stat().st_size > 64:
            return path
        try:
            data = self._http_get(url)
        except (urllib.error.URLError, TimeoutError, OSError):
            return path if path.is_file() else None
        try:
            path.write_bytes(data)
        except OSError:
            return None
        return path

    def pixmap_for_app(
        self,
        *,
        bundle_id: str | None = None,
        store_id: str | None = None,
        name: str = "",
        size: int = 40,
    ) -> QPixmap:
        info = self.lookup(bundle_id=bundle_id, store_id=store_id)
        if info and info.get("artwork"):
            path = self.fetch_file(str(info["artwork"]))
            if path and path.is_file():
                image = QImage(str(path))
                if not image.isNull():
                    return _rounded_pixmap(image, size)
        return placeholder_pixmap(name or bundle_id or "App", size)


def _rounded_pixmap(image: QImage, size: int) -> QPixmap:
    scaled = image.scaled(
        size,
        size,
        Qt.AspectRatioMode.KeepAspectRatioByExpanding,
        Qt.TransformationMode.SmoothTransformation,
    )
    x = max(0, (scaled.width() - size) // 2)
    y = max(0, (scaled.height() - size) // 2)
    cropped = scaled.copy(x, y, size, size)
    out = QPixmap(size, size)
    out.fill(Qt.GlobalColor.transparent)
    painter = QPainter(out)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    path = QPainterPath()
    radius = size * 0.225
    path.addRoundedRect(0, 0, size, size, radius, radius)
    painter.setClipPath(path)
    painter.drawImage(0, 0, cropped)
    painter.end()
    return out


_PLACEHOLDER_COLORS = [
    "#E8873A",
    "#34C759",
    "#AF52DE",
    "#30B0C7",
    "#FF2D55",
    "#007AFF",
    "#5856D6",
    "#FF9500",
]


def placeholder_pixmap(label: str, size: int = 40) -> QPixmap:
    color = _PLACEHOLDER_COLORS[sum(ord(c) for c in label) % len(_PLACEHOLDER_COLORS)]
    out = QPixmap(size, size)
    out.fill(Qt.GlobalColor.transparent)
    painter = QPainter(out)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    path = QPainterPath()
    radius = size * 0.225
    path.addRoundedRect(0.5, 0.5, size - 1, size - 1, radius, radius)
    painter.fillPath(path, QColor(color))
    painter.setPen(QColor(255, 255, 255, 230))
    glyph = (label.strip()[:1] or "?").upper()
    from PySide6.QtGui import QFont

    font = QFont()
    font.setPixelSize(max(12, int(size * 0.42)))
    font.setBold(True)
    painter.setFont(font)
    painter.drawText(out.rect(), int(Qt.AlignmentFlag.AlignCenter), glyph)
    painter.end()
    return out


def prefetch_many(
    apps: list[tuple[str | None, str | None, str]],
    cache: ArtworkCache | None = None,
) -> ArtworkCache:
    """(bundle_id, store_id, name) list."""
    art = cache or ArtworkCache()
    for bundle_id, store_id, _name in apps:
        art.lookup(bundle_id=bundle_id, store_id=store_id)
        info = art.lookup(bundle_id=bundle_id, store_id=store_id)
        if info and info.get("artwork"):
            art.fetch_file(str(info["artwork"]))
    return art
