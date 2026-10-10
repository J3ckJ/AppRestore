"""Writes the benchmark marks to the file named by BENCH_OUT."""

from __future__ import annotations

import json
import os
import time

_marks: dict = {}


def mark(name: str, **extra) -> None:
    _marks[name] = time.time() * 1000.0
    _marks.update(extra)


def flush() -> None:
    path = os.environ.get("BENCH_OUT")
    if path:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(_marks, fh)
