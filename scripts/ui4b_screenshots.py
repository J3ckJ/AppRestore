"""Offscreen PNGs of the 4b window with fake data, next to the concept PNGs.

    QT_QPA_PLATFORM=offscreen python scripts/ui4b_screenshots.py \
        --out screenshots --concepts ../design/concepts

No device, no network, no Apple ID: everything comes from ui4b.fake_data.
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

#: our screen name → (scenario, concept file, extra setup)
SHOTS: tuple[tuple[str, str, str | None], ...] = (
    ("home-missing", "missing", "variant-4b-missing.png"),
    ("home-many", "many", "variant-4b-picker-main500.png"),
    ("picker-default", "picker", "variant-4b-picker-default.png"),
    ("picker-search", "picker-search", "variant-4b-picker-search.png"),
    ("picker-nospace", "picker-nospace", "variant-4b-picker-nospace.png"),
    ("home-installing", "installing", "variant-4b-installing.png"),
    ("home-done", "done", "variant-4b-done.png"),
    ("home-disconnected", "disconnected", "variant-4b-disconnected.png"),
    ("home-signin", "signin", "variant-4b-error.png"),
    ("home-relogin", "relogin", None),
    ("signin-sheet", "relogin", None),
    ("signin-code", "signin", None),
    ("account-sheet", "missing", None),
    ("account-signout", "missing", None),
    ("home-region", "region", "variant-4b-region.png"),
    ("home-needs-component", "unpatched", "variant-4b-needs-component.png"),
    ("onboarding-1", "onboarding-1", "variant-4b-onboarding-1.png"),
    ("onboarding-2", "onboarding-2", "variant-4b-onboarding-2.png"),
    ("onboarding-3", "onboarding-3", "variant-4b-onboarding-3.png"),
    ("onboarding-4", "onboarding-4", "variant-4b-onboarding-4.png"),
)


def prepare(controller, source, scenario: str, name: str = "") -> None:
    """Bring the fake run to the concept's moment."""

    if name == "signin-sheet":
        source.account_email = "marina@example.com"
        source.auth_status = "Сессия Apple ID истекла. Войдите заново."
        controller.openSignIn()
        return
    if name in ("account-sheet", "account-signout"):
        source.auth_phase = "in"
        source.account_email = "marina.konstantinopolskaya.long-address@example.com"
        source.changed.emit()
        controller.openAccount()
        if name == "account-signout":
            controller.askSignOut()
        return
    if name == "signin-code":
        controller.openSignIn()
        source.login("marina@example.com", "not-a-real-password")
        return

    from apprestore_gui.ui4b.space import DeviceSpace

    sel = controller.selection
    if scenario.startswith("onboarding"):
        step = int(scenario[-1])
        controller.onboarding.finished = False
        controller.onboarding.started = step > 1
        controller.onboarding.apple_id_skipped = False
        source.changed.emit()
        # the concept's moment: half a minute into the check
        controller.scan.started_at = controller.scan.clock() - 30
        controller._refresh()
        return
    if scenario in ("picker", "picker-search", "picker-nospace"):
        offloaded = [it for it in sel.items if it.group == "offloaded"]
        by_size = sorted(offloaded, key=lambda it: -(it.size_bytes or 0))
        n = {"picker": 3, "picker-search": 0, "picker-nospace": 14}[scenario]
        for item in by_size[:n]:
            sel.set_selected(item.key, True)
        if scenario == "picker-search":
            for item in sel.items:
                sel.set_selected(item.key, False)
            for key in ("bundle.422689480", "bundle.585027354"):
                sel.set_selected(key, True)
            sel.set_query("google")
        if scenario == "picker-nospace":
            sel.set_rail("offloaded")
            sel.set_rail("all")
            controller._picker_open = True
            # concept shows the offloaded group on top while scrolled
        controller._picker_open = True
        controller._refresh()
        return
    if scenario in ("installing", "done"):
        source._space = DeviceSpace(128 * 10**9, 7 * 10**9)
        controller.flow.begin(sel.selected_items(), source.space())
        q = controller.flow.queue
        keys = [e.item for e in q.entries]
        if scenario == "installing":
            q.settle(keys[0].key, True)
            q.settle(keys[1].key, True)
            q.progress(64, "Ставим на iPhone")
        else:
            for item in keys:
                q.settle(item.key, True)
        controller._refresh()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=str(ROOT / "screenshots"))
    parser.add_argument("--concepts", default=str(ROOT.parent / "design" / "concepts"))
    parser.add_argument("--only", default="")
    parser.add_argument("--scale", type=float, default=2.0)
    parser.add_argument("--font", default="Inter", help="stand-in for SF Pro on Linux")
    args = parser.parse_args()

    os.environ.setdefault("QT_SCALE_FACTOR", str(args.scale))
    from PySide6.QtGui import QFont, QGuiApplication, QImage
    from PySide6.QtQml import QQmlApplicationEngine
    from PySide6.QtQuickControls2 import QQuickStyle

    from apprestore_gui.ui4b.fake_data import FakeIconBook, FakeSource
    from apprestore_gui.ui4b.qt_bridge import Restore4b
    from apprestore_gui.ui4b.window import load

    QQuickStyle.setStyle("Basic")
    app = QGuiApplication([sys.argv[0]])
    if args.font:
        app.setFont(QFont(args.font))
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    concepts = Path(args.concepts)
    icons = FakeIconBook(concepts / "assets" / "icons", Path(tempfile.gettempdir()) / "ui4b-icons")
    for name, scenario, concept in SHOTS:
        if args.only and args.only not in name:
            continue
        source = FakeSource(scenario)
        controller = Restore4b(source, onboarded=True)
        prepare(controller, source, scenario, name)
        engine = QQmlApplicationEngine()
        warnings: list[str] = []
        engine.warnings.connect(lambda ws: warnings.extend(w.toString() for w in ws))
        if not load(engine, controller, icons):
            print(f"{name}: QML failed to load", file=sys.stderr)
            return 1
        window = engine.rootObjects()[0]
        if scenario == "picker-nospace":
            app.processEvents()
            view = window.findChild(QObjectType(), "pickerList")
            if view is not None:
                # scrolled into the offloaded group: its header sticks on top
                view.setProperty("contentY", 7 * 46 + 3 * 50 - 44)
        for _ in range(5):
            app.processEvents()
        image: QImage = window.grabWindow()
        image.save(str(out / f"{name}.png"))
        src = concepts / concept if concept else concepts / "-"
        if src.is_file():
            # The concept is 1440×900 @2x with a grey margin; crop to the window.
            cimg = QImage(str(src))
            k = cimg.width() / 1440
            crop = cimg.copy(round(24 * k), round(24 * k), round(1392 * k), round(852 * k))
            crop.save(str(out / f"{name}.concept.png"))
        for text in warnings:
            print(f"{name}: {text}", file=sys.stderr)
        print(f"wrote {out / (name + '.png')}")
        window.close()
        del window
        del engine
        app.processEvents()
        del controller, source
    return 0


def QObjectType():
    from PySide6.QtQuick import QQuickItem

    return QQuickItem


if __name__ == "__main__":
    raise SystemExit(main())
