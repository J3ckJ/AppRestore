"""Finish a PyInstaller CLI build into a release asset and verify it.

Used by .github/workflows/release.yml next to the GUI build of each platform:

    pyinstaller packaging/apprestore-cli.spec --noconfirm --clean \
        --distpath dist-cli --workpath build-cli
    python packaging/package_cli.py --dist dist-cli --out cli-dist \
        --ipatool-archive ARCHIVE [--expect-version 0.4.1]

Steps: bundle the locally built patched ipatool -> check that every Mach-O
matches the runner CPU (macOS) -> run --version, doctor and the
pymobiledevice3 dispatch -> zip as AppRestore-CLI-Windows.zip,
AppRestore-CLI-macOS.zip (Apple Silicon) or AppRestore-CLI-macOS-Intel.zip
-> unpack the zip and run the same checks on the unpacked copy.
No Apple ID is used.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packaging"))

from fetch_ipatool import IPATOOL_VERSION, current_asset, install_archive, install_license  # noqa: E402

FOLDER = "AppRestore-CLI"
REQUIRED_DOCTOR = ("Python runtime", "pymobiledevice3", "ipatool", "AppRestore runtime")

README = """AppRestore {version} — терминальная версия ({label})

Python устанавливать не нужно: всё нужное лежит в этой папке, включая
проверенный ipatool {ipatool}.

Запуск:
{run}

Команды: `apprestore doctor`, `apprestore devices`, `apprestore restore` и
другие — `apprestore --help`. Без аргументов открывается меню.
Пароль Apple ID и код 2FA вводятся только в самом ipatool.
{extra}"""


def cli_asset_name(platform_name: str | None = None, machine: str | None = None) -> str | None:
    platform_name = platform_name or sys.platform
    machine = (machine or platform.machine()).lower()
    if platform_name == "win32":
        return "AppRestore-CLI-Windows.zip"
    if platform_name == "darwin":
        if machine in {"x86_64", "amd64", "i386"}:
            return "AppRestore-CLI-macOS-Intel.zip"
        return "AppRestore-CLI-macOS.zip"
    return None


def run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    print("+", " ".join(cmd), flush=True)
    return subprocess.run(cmd, **kw)


def mach_o_files(folder: Path) -> list[Path]:
    found = []
    for path in sorted(folder.rglob("*")):
        if path.is_file() and not path.is_symlink():
            with path.open("rb") as stream:
                magic = stream.read(4)
            if magic in (b"\xcf\xfa\xed\xfe", b"\xca\xfe\xba\xbe", b"\xce\xfa\xed\xfe"):
                found.append(path)
    return found


def check_architecture(folder: Path) -> None:
    expected = platform.machine()
    wrong = []
    files = mach_o_files(folder)
    for path in files:
        archs = run(["lipo", "-archs", str(path)], capture_output=True, text=True, check=True).stdout.split()
        if expected not in archs:
            wrong.append(f"{path.relative_to(folder)}: {archs}")
    if not files or wrong:
        raise SystemExit(f"Mach-O files without {expected}: {wrong or 'none found'}")
    print(f"{len(files)} Mach-O files contain {expected}")


def verify(folder: Path, expected_version: str) -> str:
    exe = folder / ("apprestore.exe" if sys.platform == "win32" else "apprestore")
    tool = folder / ("ipatool.exe" if sys.platform == "win32" else "ipatool")
    for path in (exe, tool):
        if not path.is_file():
            raise SystemExit(f"missing {path}")

    result = run([str(exe), "--version"], capture_output=True, text=True, timeout=300)
    version = (result.stdout or result.stderr).strip()
    print(f"apprestore --version: {version!r}")
    if result.returncode != 0 or not version:
        raise SystemExit(f"--version failed (exit {result.returncode}): {result.stderr}")
    if expected_version and version != expected_version:
        raise SystemExit(f"built version {version!r} != expected {expected_version!r}")

    result = run([str(tool), "--version"], capture_output=True, text=True, timeout=120)
    if result.returncode != 0 or IPATOOL_VERSION not in result.stdout:
        raise SystemExit(f"bundled ipatool is not {IPATOOL_VERSION}: {result.stdout} {result.stderr}")

    result = run([str(exe), "--json", "doctor"], capture_output=True, text=True, timeout=300)
    print(result.stdout)
    checks = {check["name"]: check for check in json.loads(result.stdout)}
    failed = [name for name in REQUIRED_DOCTOR if not checks.get(name, {}).get("ok")]
    if failed:
        raise SystemExit(f"doctor checks failed: {failed}")
    detail = str(checks["ipatool"]["detail"])
    if folder.name not in detail or IPATOOL_VERSION not in detail:
        raise SystemExit(f"doctor does not use the bundled ipatool: {detail}")

    result = run(
        [str(exe), "--apprestore-run-module", "pymobiledevice3", "version"],
        capture_output=True,
        text=True,
        timeout=300,
    )
    if result.returncode != 0:
        raise SystemExit(f"pymobiledevice3 dispatch failed: {result.stdout} {result.stderr}")
    return version


def write_readme(folder: Path, version: str) -> None:
    if sys.platform == "win32":
        label, run_hint, extra = (
            "Windows x64",
            "  Откройте PowerShell в этой папке и выполните `.\\apprestore.exe`.",
            "\nДля USB нужен Apple Devices или iTunes: `.\\apprestore.exe setup`.\n",
        )
    else:
        intel = platform.machine() == "x86_64"
        label = "macOS Intel" if intel else "macOS Apple Silicon"
        run_hint = (
            "  xattr -dr com.apple.quarantine AppRestore-CLI\n"
            "  ./AppRestore-CLI/apprestore"
        )
        extra = "\nСборка подписана ad-hoc, поэтому сначала снимите карантин командой выше.\n"
    (folder / "README.txt").write_text(
        README.format(version=version, label=label, ipatool=IPATOOL_VERSION, run=run_hint, extra=extra),
        encoding="utf-8",
    )


def make_zip(folder: Path, target: Path) -> None:
    target.unlink(missing_ok=True)
    if sys.platform == "win32":
        with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
            for path in sorted(folder.rglob("*")):
                archive.write(path, (Path(folder.name) / path.relative_to(folder)).as_posix())
    else:
        run(["/usr/bin/ditto", "-c", "-k", "--sequesterRsrc", "--keepParent", str(folder), str(target)], check=True)


def unpack(target: Path, destination: Path) -> None:
    if sys.platform == "win32":
        with zipfile.ZipFile(target) as archive:
            archive.extractall(destination)
    else:
        run(["/usr/bin/ditto", "-x", "-k", str(target), str(destination)], check=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dist", default=str(ROOT / "dist-cli"))
    parser.add_argument("--out", required=True)
    parser.add_argument("--expect-version", default="")
    parser.add_argument("--ipatool-archive", type=Path, required=True)
    args = parser.parse_args()

    asset = cli_asset_name()
    if not asset:
        print("CLI release assets are built on Windows and macOS only", file=sys.stderr)
        return 2
    folder = Path(args.dist) / FOLDER
    if not folder.is_dir():
        print(f"missing build output: {folder}", file=sys.stderr)
        return 1
    archive = args.ipatool_archive.resolve()
    if not archive.is_file():
        print(f"missing locally built ipatool archive: {archive}", file=sys.stderr)
        return 1

    install_archive(archive, current_asset(), [folder])
    install_license([folder])
    if sys.platform == "darwin":
        check_architecture(folder)
        run(["codesign", "--verify", "--strict", str(folder / "apprestore")], check=True)
    version = verify(folder, args.expect_version)
    write_readme(folder, version)

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    target = out / asset
    make_zip(folder, target)
    with tempfile.TemporaryDirectory(prefix="apprestore-cli-unpack-") as tmp:
        unpack(target, Path(tmp))
        unpacked = Path(tmp) / FOLDER
        if sys.platform == "darwin":
            check_architecture(unpacked)
        verify(unpacked, args.expect_version)

    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    (out / f"{asset}.sha256").write_text(f"{digest}  {asset}\n", encoding="utf-8")
    print(f"{asset}: {target.stat().st_size} bytes, sha256 {digest}, version {version}")
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as fh:
            fh.write(f"* `{asset}` {version}: sha256 `{digest}`\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
