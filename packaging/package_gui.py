"""Finish a PyInstaller GUI build into the release asset and verify it.

Used by both .github/workflows/gui-build.yml (branch builds) and
.github/workflows/release.yml (tagged releases), so testers get exactly the
zip that the in-app updater will later download.

Steps: bundle pinned ipatool -> (macOS) ad-hoc re-sign -> run --self-test ->
check version -> zip as AppRestore-GUI-Windows.zip / AppRestore-GUI-macOS.zip
-> unpack the zip with the updater's own code and run --update-health-check
on the unpacked copy.

    python packaging/package_gui.py --out gui-dist [--expect-version 0.3.1]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "packaging"))

from apprestore_gui.updater import ASSET_BY_PLATFORM, HEALTH_FLAG, extract_zip  # noqa: E402
from fetch_ipatool import current_asset, fetch  # noqa: E402


def run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    print("+", " ".join(cmd), flush=True)
    return subprocess.run(cmd, **kw)


def zip_windows_folder(folder: Path, target: Path) -> None:
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in sorted(folder.rglob("*")):
            rel = Path(folder.name) / path.relative_to(folder)
            archive.write(path, rel.as_posix())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dist", default=str(ROOT / "dist"))
    parser.add_argument("--out", required=True)
    parser.add_argument("--expect-version", default="")
    args = parser.parse_args()

    dist = Path(args.dist)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    asset = ASSET_BY_PLATFORM.get(sys.platform)
    if not asset:
        print("GUI release assets are built on Windows and macOS only", file=sys.stderr)
        return 2

    if sys.platform == "win32":
        app = dist / "AppRestore"
        binary_rel = Path("AppRestore") / "AppRestore.exe"
        tool_dir = app
    else:
        app = dist / "AppRestore.app"
        binary_rel = Path("AppRestore.app") / "Contents" / "MacOS" / "AppRestore"
        tool_dir = app / "Contents" / "MacOS"
    binary = dist / binary_rel
    if not binary.is_file():
        print(f"missing build output: {binary}", file=sys.stderr)
        return 1

    fetch(current_asset(), [tool_dir])
    if sys.platform == "darwin":
        # Apple's own tool must accept the generated .icns.
        with tempfile.TemporaryDirectory() as tmp:
            run(
                ["iconutil", "-c", "iconset", str(ROOT / "packaging" / "icons" / "AppRestore.icns"),
                 "-o", str(Path(tmp) / "AppRestore.iconset")],
                check=True,
            )
            print("iconset:", sorted(p.name for p in (Path(tmp) / "AppRestore.iconset").iterdir()))
        if not (app / "Contents" / "Resources" / "AppRestore.icns").is_file():
            print("AppRestore.icns is missing from the bundle", file=sys.stderr)
            return 1
        # Adding ipatool invalidates PyInstaller's ad-hoc signature.
        run(["codesign", "--force", "--deep", "--sign", "-", str(app)], check=True)
        run(["codesign", "--verify", "--deep", "--strict", str(app)], check=True)

    report_path = out / f"selftest-{'windows' if sys.platform == 'win32' else 'macos'}.json"
    result = run([str(binary), "--self-test", "--output", str(report_path)], timeout=600)
    report = json.loads(report_path.read_text(encoding="utf-8"))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if result.returncode != 0 or not report.get("ok"):
        print(f"self-test failed (exit {result.returncode})", file=sys.stderr)
        return 1
    version = next(
        (c["detail"]["apprestore"] for c in report["checks"] if c["name"] == "metadata" and c["ok"]),
        "",
    )
    if args.expect_version and version != args.expect_version:
        print(f"built version {version!r} != expected {args.expect_version!r}", file=sys.stderr)
        return 1

    target = out / asset
    target.unlink(missing_ok=True)
    if sys.platform == "win32":
        zip_windows_folder(app, target)
    else:
        run(
            ["/usr/bin/ditto", "-c", "-k", "--sequesterRsrc", "--keepParent", str(app), str(target)],
            check=True,
        )

    # Unpack exactly like the in-app updater does and health-check the copy.
    with tempfile.TemporaryDirectory(prefix="apprestore-unpack-") as tmp:
        extract_zip(target, Path(tmp))
        unpacked = Path(tmp) / binary_rel
        if not unpacked.is_file():
            print(f"zip layout is wrong: {binary_rel} not found", file=sys.stderr)
            return 1
        health = run([str(unpacked), HEALTH_FLAG], timeout=300)
        if health.returncode != 0:
            print(f"health check of unpacked asset failed: {health.returncode}", file=sys.stderr)
            return 1

    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    (out / f"{asset}.sha256").write_text(f"{digest}  {asset}\n", encoding="utf-8")
    print(f"{asset}: {target.stat().st_size} bytes, sha256 {digest}, version {version}")
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as fh:
            fh.write(f"* `{asset}` {version}: sha256 `{digest}`\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
