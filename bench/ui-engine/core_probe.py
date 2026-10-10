"""The "core call" every UI candidate makes in the engine benchmark.

It imports the real AppRestore core (apprestore_core.service/tools) and the
device stack the core drives (pymobiledevice3.lockdown), then answers with a
small JSON-friendly dict. No network, no Apple ID, no device access.
"""

from __future__ import annotations

import time


def core_call() -> dict:
    started = time.time()
    import apprestore_core
    import apprestore_core.service  # noqa: F401
    import apprestore_core.tools  # noqa: F401
    import pymobiledevice3.lockdown  # noqa: F401

    return {
        "core_version": apprestore_core.__version__,
        "core_import_ms": round((time.time() - started) * 1000, 1),
    }
