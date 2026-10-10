"""Offscreen shot of the REAL 4b window (window.build: QuickSession, pymobiledevice3,
ipatool_api, purchases cache, license_guard, icons_cache, region_probe; no fakes).

    QT_QPA_PLATFORM=offscreen python scripts/ui4b_live_shot.py OUT.png [--onboarded] [--wait 8]

Meant for a machine without a phone and without an Apple ID session: the window
must show honest «не подключён» / «войдите» states and not crash.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QSettings, QTimer  # noqa: E402
from PySide6.QtGui import QGuiApplication  # noqa: E402
from PySide6.QtQuickControls2 import QQuickStyle  # noqa: E402

from apprestore_gui.ui4b.window import ONBOARDED_KEY, build  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("out", type=Path)
    ap.add_argument("--onboarded", action="store_true")
    ap.add_argument("--wait", type=float, default=8.0)
    args = ap.parse_args()
    QQuickStyle.setStyle("Basic")
    app = QGuiApplication([sys.argv[0]])
    settings = QSettings(str(args.out.with_suffix(".ini")), QSettings.Format.IniFormat)
    settings.setValue(ONBOARDED_KEY, args.onboarded)
    warnings: list[str] = []
    engine, keep = build(settings)
    if engine is None:
        print("QML failed to load", file=sys.stderr)
        return 1
    engine.warnings.connect(lambda ws: warnings.extend(w.toString() for w in ws))
    window = engine.rootObjects()[0]
    window.resize(1440, 900)
    controller = keep[2]
    result = {"code": 1}

    def shoot() -> None:
        window.grabWindow().save(str(args.out))
        print("screen", controller.screen, "state", controller.home.get("state"), "title", controller.home.get("title"))
        print("warnings", len(warnings))
        for w in warnings:
            print("  ", w)
        result["code"] = 0 if not warnings else 2
        app.quit()

    QTimer.singleShot(int(args.wait * 1000), shoot)
    app.exec()
    args.out.with_suffix(".ini").unlink(missing_ok=True)
    del keep, engine
    return result["code"]


if __name__ == "__main__":
    raise SystemExit(main())
