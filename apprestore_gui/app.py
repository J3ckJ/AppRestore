from __future__ import annotations

import argparse
import ctypes
import os
import sys


def _release_windows_console() -> None:
    """Close a python.exe console so ipatool cannot prompt in a black window.

    A windowed parent makes child tools use CREATE_NO_WINDOW. The keychain
    passphrase is then typed in the Qt dialog and written to a hidden ConPTY.
    """

    if sys.platform != "win32":
        return
    if os.environ.get("APPRESTORE_KEEP_CONSOLE") == "1":
        return
    kernel = ctypes.windll.kernel32
    try:
        if not kernel.GetConsoleWindow():
            return
        kernel.FreeConsole()
    except (AttributeError, OSError):
        return
    for name, mode in (("stdin", "r"), ("stdout", "w"), ("stderr", "w")):
        stream = open(os.devnull, mode, encoding="utf-8", errors="replace")  # noqa: SIM115
        setattr(sys, name, stream)
        setattr(sys, f"__{name}__", stream)


def main(argv: list[str] | None = None) -> int:
    raw = list(sys.argv[1:] if argv is None else argv)

    # A frozen bundle is also the "interpreter" for pymobiledevice3 child
    # processes.  Handle that before argparse or Qt so no window appears.
    from apprestore_core.frozen import ensure_std_streams, maybe_dispatch

    # Windowed builds have no console: sys.stdout/stderr may be None.
    ensure_std_streams()

    dispatched = maybe_dispatch(raw)
    if dispatched is not None:
        return dispatched

    # Used by the updater on the freshly installed copy before the old one is
    # removed: imports only, no window, no network.
    if raw[:1] == ["--update-health-check"]:
        from apprestore_gui.updater import health_check

        return health_check()

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
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="print JSON diagnostics of the bundle (no window) and exit",
    )
    parser.add_argument(
        "--output",
        type=str,
        default="",
        help="with --self-test: also write the JSON report to this file",
    )
    args = parser.parse_args(raw)

    if args.self_test:
        from pathlib import Path

        from apprestore_gui.selftest import run_self_test

        return run_self_test(Path(args.output) if args.output else None)

    if not args.screenshot_dir:
        _release_windows_console()

    # Prefer offscreen when capturing or when no display.
    if args.screenshot_dir and not os.environ.get("QT_QPA_PLATFORM"):
        os.environ["QT_QPA_PLATFORM"] = "offscreen"

    from PySide6.QtWidgets import QApplication

    from apprestore_gui.icons_cache import ArtworkCache, prefetch_many
    from apprestore_gui.main_window import MainWindow, NAV
    from apprestore_gui.service_adapter import GuiService
    from apprestore_gui.theme import STYLESHEET, load_fonts, pin_light_native_chrome
    from apprestore_gui.ui_icons import app_icon
    from apprestore_gui import demo

    app = QApplication(sys.argv)
    pin_light_native_chrome(app)
    app.setApplicationName("AppRestore")
    app.setWindowIcon(app_icon())
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
