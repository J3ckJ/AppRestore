"""Python core as a sidecar process (for Tauri/Electron/Flutter shells).

Prints one JSON line when the core has answered, then exits. A real sidecar
would stay alive and speak JSON lines over stdio; startup cost is the same.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

started = time.time() * 1000.0

from core_probe import core_call  # noqa: E402

answer = core_call()
answer["sidecar_start"] = started
answer["sidecar_answer"] = time.time() * 1000.0
sys.stdout.write(json.dumps(answer) + "\n")
sys.stdout.flush()
