"""Run ``--self-test`` on the packaged zip and require the hidden-terminal login check.

The release zip is unpacked with the in-app updater's own code, then the
windowed AppRestore binary inside it runs ``--self-test``. The step fails
unless the real ``ipatool auth info`` started on the hidden ConPTY (Windows,
needs OpenConsole.exe) or pty (macOS) and its answer was readable. No Apple
ID is involved: "not signed in" is the expected answer.

    python packaging/verify_hidden_terminal.py --out gui-dist
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from apprestore_gui.updater import ASSET_BY_PLATFORM, extract_zip  # noqa: E402

CHECK = "ipatool auth info on the hidden terminal"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True, help="folder with the packaged zip")
    args = parser.parse_args()
    asset = ASSET_BY_PLATFORM.get(sys.platform)
    if not asset:
        print("GUI assets exist on Windows and macOS only", file=sys.stderr)
        return 2
    target = Path(args.out) / asset
    if not target.is_file():
        print(f"missing packaged asset: {target.name}", file=sys.stderr)
        return 1
    if sys.platform == "win32":
        binary_rel = Path("AppRestore") / "AppRestore.exe"
    else:
        binary_rel = Path("AppRestore.app") / "Contents" / "MacOS" / "AppRestore"
    with tempfile.TemporaryDirectory(prefix="apprestore-artifact-") as tmp:
        extract_zip(target, Path(tmp))
        binary = Path(tmp) / binary_rel
        report_path = Path(tmp) / "selftest.json"
        print(f"+ <unpacked {asset}>/{binary_rel.as_posix()} --self-test", flush=True)
        result = subprocess.run(
            [str(binary), "--self-test", "--output", str(report_path)],
            timeout=600,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if not report_path.is_file():
            print(f"self-test wrote no report (exit {result.returncode:#x})", file=sys.stderr)
            return 1
        report = json.loads(report_path.read_text(encoding="utf-8"))
    checks = {c["name"]: c for c in report.get("checks", [])}
    for name in ("hidden terminal host", "winpty (Apple ID login)", "pty (Apple ID login)", CHECK):
        if name in checks:
            c = checks[name]
            state = "ok" if c["ok"] else "FAILED"
            print(f"{name}: {state} {json.dumps(c.get('detail') or c.get('error'), ensure_ascii=False)}")
    check = checks.get(CHECK)
    if check is None:
        print(f"self-test has no '{CHECK}' check", file=sys.stderr)
        return 1
    if not check["ok"] or result.returncode != 0 or not report.get("ok"):
        print(f"hidden-terminal self-test failed (exit {result.returncode:#x})", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
