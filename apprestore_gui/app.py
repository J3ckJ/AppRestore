from __future__ import annotations

import argparse
import os
import sys


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="apprestore-gui")
    parser.add_argument(
        "--demo",
        action="store_true",
        help="run with demo device/apps and prefetch real App Store icons",
    )
    parser.add_argument(
        "--screenshot-dir",
        type=str,
        default="",
        help="capture offscreen PNGs of main pages into this directory and exit",
    )
    args = parser.parse_args(argv)

    # Prefer offscreen when capturing or when no display.
    if args.screenshot_dir and not os.environ.get("QT_QPA_PLATFORM"):
        os.environ["QT_QPA_PLATFORM"] = "offscreen"

    from PySide6.QtWidgets import QApplication

    from apprestore_gui.icons_cache import ArtworkCache, prefetch_many
    from apprestore_gui.main_window import MainWindow, NAV
    from apprestore_gui.service_adapter import GuiService
    from apprestore_gui.theme import STYLESHEET, load_fonts
    from apprestore_gui import demo

    app = QApplication(sys.argv)
    family = load_fonts()
    app.setStyle("Fusion")
    app.setStyleSheet(STYLESHEET)
    font = app.font()
    font.setFamily(family)
    font.setPixelSize(13)
    app.setFont(font)

    artwork = ArtworkCache()
    if args.demo:
        prefetch_many(demo.demo_prefetch_targets(), artwork)

    service = GuiService(demo_mode=args.demo)
    window = MainWindow(service, artwork)
    window.show()

    if args.screenshot_dir:
        from pathlib import Path

        out = Path(args.screenshot_dir)
        out.mkdir(parents=True, exist_ok=True)
        # Ensure data loaded
        window.refresh_device()
        window.reload_offloaded()
        window.reload_missing()
        window.reload_library()
        window.reload_doctor()
        app.processEvents()
        for key, label in NAV:
            window._show_page(key)
            app.processEvents()
            pix = window.grab()
            safe = key.replace("/", "-")
            path = out / f"{safe}.png"
            pix.save(str(path), "PNG")
            print(f"wrote {path}")
        # hero overview
        window._show_page("overview")
        app.processEvents()
        window.grab().save(str(out / "hero.png"), "PNG")
        print(f"wrote {out / 'hero.png'}")
        return 0

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
