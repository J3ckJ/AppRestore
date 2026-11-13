"""Synthetic local workloads; no Apple account, network or real device.

Example: python scripts/performance_benchmark.py --source . --output results.json
Run the same script against both source trees with other heavy work stopped.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import statistics
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import patch


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    sys.path.insert(0, str(args.source.resolve()))
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    from PySide6.QtWidgets import QApplication
    from apprestore_core.service import AppRestoreService
    from apprestore_gui.icons_cache import IconBook
    from apprestore_gui.ui4b.catalog import ACTION_STORE, GROUP_ORDER, RestoreItem
    from apprestore_gui.ui4b.qt_bridge import PickerModel, Restore4b
    from apprestore_gui.ui4b.selection import Selection

    app = QApplication.instance() or QApplication([])

    def timed(fn, repeats=7):
        values = []
        for _ in range(repeats):
            start = time.perf_counter()
            fn()
            values.append(time.perf_counter() - start)
        return round(statistics.median(values) * 1000, 3)

    items = [
        RestoreItem(key=str(i), name=f"Application {i:05d}", group=GROUP_ORDER[i % 4],
                    action=ACTION_STORE, store_id=str(1000000 + i),
                    bundle_id=f"com.example.app{i}", size_bytes=i * 1000)
        for i in range(5000)
    ]
    selection = Selection(items)
    selection.set_query("application")
    results = {"picker_5000_rows_ms": timed(selection.rows)}
    serial = 0

    def changed_rows():
        nonlocal serial
        serial += 1
        selection.set_query("application" + (" " if serial % 2 else ""))
        return selection.rows()

    results["picker_changed_query_5000_rows_ms"] = timed(changed_rows)
    model = PickerModel()
    model.set_rows(selection.rows())
    controller = SimpleNamespace(picker=model)
    results["sticky_header_2000_reads_ms"] = timed(
        lambda: [Restore4b.headerAt(controller, 4999) for _ in range(2000)], 3)

    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)

        class Tools:
            def __init__(self):
                self.calls = 0
                self.transport = threading.Lock()

            def list_apps(self, udid):
                with self.transport:
                    self.calls += 1
                    time.sleep(0.04)  # a simulated serial USB transport
                    return {"com.example.app": {"CFBundleIdentifier": "com.example.app",
                            "CFBundleDisplayName": "Example", "IsPlaceholder": True}}

        tools = Tools()
        service = AppRestoreService(tools=tools, library=root / "library", cache=root / "cache")
        service.device_inventory_ttl = 5.0
        with (
            patch("apprestore_core.service.ipa_search_roots", return_value=[service.library]),
            patch("apprestore_core.service.imazing_catalog_candidates", return_value=[]),
            patch.object(service, "_merge_known_missing", side_effect=lambda apps, *_: apps),
            ThreadPoolExecutor(max_workers=3) as pool,
        ):
            start = time.perf_counter()
            jobs = [pool.submit(fn, "test-device")
                    for fn in (service.offloaded, service.installed, service.missing)]
            for job in jobs:
                job.result(timeout=10)
            results["device_three_views_ms"] = round((time.perf_counter() - start) * 1000, 3)
            results["device_inventory_reads"] = tools.calls

        class Artwork:
            def prime_store_ids(self, ids):
                pass

            def rounded_icon_file(self, **kwargs):
                time.sleep(0.025)  # simulated independent icon downloads
                return root / "icon.png"

            def artwork_info(self, **kwargs):
                return {}

        book = IconBook(cache=Artwork())
        start = time.perf_counter()
        book.consider_async([(str(10000 + i), "", "") for i in range(24)])
        deadline = start + 10
        while book.stats["found"] < 24 and time.perf_counter() < deadline:
            app.processEvents()
            time.sleep(0.001)
        assert book.stats["found"] == 24, book.stats
        results["icons_24_simulated_downloads_ms"] = round((time.perf_counter() - start) * 1000, 3)
        while getattr(book, "_worker", None) is not None and time.perf_counter() < deadline:
            app.processEvents()
            time.sleep(0.001)

    report = {
        "schemaVersion": 2, "python": sys.version.split()[0], "source": args.source.resolve().name,
        "workload": "5000 synthetic rows; serial USB reads 40ms; 24 independent icons 25ms each; no external requests",
        "results": results,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report))


if __name__ == "__main__":
    main()
