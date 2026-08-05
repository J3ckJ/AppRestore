from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import io
import os
import stat
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from importlib.metadata import version as distribution_version
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "requirements" / "wheels" / "hexdump-3.3-py3-none-any.whl"
VENDORED_SOURCE = ROOT / "requirements" / "sources" / "hexdump-3.3.zip"
PYTHON_VERSION = (3, 12, 13)
SETUPTOOLS_VERSION = "83.0.0"
WHEEL_VERSION = "0.46.3"
SOURCE_URL = (
    "https://files.pythonhosted.org/packages/source/h/hexdump/hexdump-3.3.zip"
)
SOURCE_SHA256 = (
    "d781a43b0c16ace3f9366aade73e8ad3"
    "a7bd5137d58f0b45ab2d3f54876f20db"
)
SOURCE_DATE_EPOCH = "1453484416"
FIXED_ZIP_TIME = (2016, 1, 22, 17, 40, 16)
MAX_SOURCE_BYTES = 1024 * 1024
SOURCE_MEMBERS = {
    "PKG-INFO",
    "README.txt",
    "__main__.py",
    "data/hexfile.bin",
    "hexdump.py",
    "setup.py",
}
WHEEL_MEMBERS = (
    "hexdump.py",
    "hexdump-3.3.data/data/data/hexfile.bin",
    "hexdump-3.3.dist-info/METADATA",
    "hexdump-3.3.dist-info/WHEEL",
    "hexdump-3.3.dist-info/top_level.txt",
    "hexdump-3.3.dist-info/RECORD",
)


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def plain_file(path: Path) -> bool:
    try:
        metadata = path.lstat()
    except OSError:
        return False
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        return False
    return not bool(getattr(metadata, "st_file_attributes", 0) & 0x400)


def read_source(path: Path | None) -> bytes:
    if path is not None:
        if not plain_file(path) or path.stat().st_size > MAX_SOURCE_BYTES:
            raise SystemExit(f"unsafe or oversized source archive: {path}")
        payload = path.read_bytes()
    else:
        request = urllib.request.Request(
            SOURCE_URL,
            headers={"User-Agent": "AppRestore-wheel-rebuilder/1"},
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            final_url = urlsplit(response.geturl())
            if (
                final_url.scheme.lower() != "https"
                or final_url.hostname != "files.pythonhosted.org"
            ):
                raise SystemExit(f"unexpected source redirect: {response.geturl()}")
            payload = response.read(MAX_SOURCE_BYTES + 1)
        if len(payload) > MAX_SOURCE_BYTES:
            raise SystemExit("source archive exceeds the 1 MiB safety limit")

    actual = sha256_bytes(payload)
    if actual != SOURCE_SHA256:
        raise SystemExit(
            f"hexdump source SHA-256 mismatch: expected {SOURCE_SHA256}, got {actual}"
        )
    return payload


def extract_source(payload: bytes, destination: Path) -> None:
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        infos = archive.infolist()
        names = {info.filename for info in infos}
        if names != SOURCE_MEMBERS or len(infos) != len(SOURCE_MEMBERS):
            raise SystemExit(f"unexpected hexdump source members: {sorted(names)}")
        for info in infos:
            member = PurePosixPath(info.filename)
            mode = info.external_attr >> 16
            if (
                info.is_dir()
                or info.flag_bits & 0x1
                or member.is_absolute()
                or ".." in member.parts
                or "\\" in info.filename
                or stat.S_ISLNK(mode)
                or info.file_size > MAX_SOURCE_BYTES
            ):
                raise SystemExit(f"unsafe hexdump source member: {info.filename}")
            target = destination.joinpath(*member.parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            data = archive.read(info)
            if len(data) != info.file_size:
                raise SystemExit(f"truncated hexdump source member: {info.filename}")
            with target.open("xb") as stream:
                stream.write(data)


def validate_toolchain() -> None:
    actual_python = sys.version_info[:3]
    if actual_python != PYTHON_VERSION:
        raise SystemExit(
            "wheel rebuild requires CPython "
            f"{'.'.join(map(str, PYTHON_VERSION))}; got "
            f"{'.'.join(map(str, actual_python))}"
        )
    actual_setuptools = distribution_version("setuptools")
    actual_wheel = distribution_version("wheel")
    if actual_setuptools != SETUPTOOLS_VERSION or actual_wheel != WHEEL_VERSION:
        raise SystemExit(
            "install requirements/wheel-build.lock first; got "
            f"setuptools {actual_setuptools}, wheel {actual_wheel}"
        )


def verify_record(archive: zipfile.ZipFile) -> None:
    record_name = "hexdump-3.3.dist-info/RECORD"
    record = archive.read(record_name).decode("utf-8")
    rows = list(csv.reader(io.StringIO(record)))
    if {row[0] for row in rows} != set(WHEEL_MEMBERS):
        raise SystemExit("built wheel RECORD does not cover the exact wheel payload")
    for name, digest, size in rows:
        if name == record_name:
            if digest or size:
                raise SystemExit("built wheel RECORD must leave its own hash empty")
            continue
        payload = archive.read(name)
        encoded = base64.urlsafe_b64encode(hashlib.sha256(payload).digest())
        expected = "sha256=" + encoded.rstrip(b"=").decode("ascii")
        if digest != expected or size != str(len(payload)):
            raise SystemExit(f"invalid RECORD entry for {name}")


def normalize_wheel(raw_wheel: Path) -> bytes:
    with zipfile.ZipFile(raw_wheel) as source:
        if (
            set(source.namelist()) != set(WHEEL_MEMBERS)
            or len(source.infolist()) != len(WHEEL_MEMBERS)
        ):
            raise SystemExit(
                f"unexpected built wheel members: {source.namelist()}"
            )
        verify_record(source)
        metadata = source.read("hexdump-3.3.dist-info/METADATA")
        wheel_metadata = source.read("hexdump-3.3.dist-info/WHEEL")
        normalized_metadata = metadata.replace(b"\r\n", b"\n")
        if (
            b"\nName: hexdump\n" not in normalized_metadata
            or b"\nVersion: 3.3\n" not in normalized_metadata
        ):
            raise SystemExit("built wheel contains unexpected package metadata")
        if (
            b"Generator: setuptools (83.0.0)\n" not in wheel_metadata
            or b"Tag: py3-none-any\n" not in wheel_metadata
        ):
            raise SystemExit("built wheel contains unexpected wheel metadata")
        contents = {name: source.read(name) for name in WHEEL_MEMBERS[:-1]}

    for name in (
        "hexdump-3.3.dist-info/METADATA",
        "hexdump-3.3.dist-info/WHEEL",
        "hexdump-3.3.dist-info/top_level.txt",
    ):
        contents[name] = contents[name].replace(b"\r\n", b"\n")

    record_stream = io.StringIO(newline="")
    record_writer = csv.writer(record_stream, lineterminator="\n")
    for name in WHEEL_MEMBERS[:-1]:
        payload = contents[name]
        encoded = base64.urlsafe_b64encode(hashlib.sha256(payload).digest())
        digest = "sha256=" + encoded.rstrip(b"=").decode("ascii")
        record_writer.writerow((name, digest, str(len(payload))))
    record_writer.writerow((WHEEL_MEMBERS[-1], "", ""))
    contents[WHEEL_MEMBERS[-1]] = record_stream.getvalue().encode("utf-8")

    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_STORED) as archive:
        for name in WHEEL_MEMBERS:
            info = zipfile.ZipInfo(name, FIXED_ZIP_TIME)
            info.create_system = 3
            info.create_version = 20
            info.extract_version = 20
            info.external_attr = (stat.S_IFREG | 0o644) << 16
            info.compress_type = zipfile.ZIP_STORED
            info.extra = b""
            info.comment = b""
            archive.writestr(info, contents[name])
    return output.getvalue()


def build_wheel(source_payload: bytes) -> bytes:
    validate_toolchain()
    with tempfile.TemporaryDirectory(prefix="AppRestore-hexdump-build-") as raw:
        temporary = Path(raw)
        source_root = temporary / "source"
        wheel_root = temporary / "wheel"
        source_root.mkdir()
        wheel_root.mkdir()
        extract_source(source_payload, source_root)
        environment = os.environ.copy()
        environment.pop("PYTHONHOME", None)
        environment.pop("PYTHONPATH", None)
        environment.update(
            {
                "PYTHONHASHSEED": "0",
                "PYTHONUTF8": "1",
                "SOURCE_DATE_EPOCH": SOURCE_DATE_EPOCH,
            }
        )
        result = subprocess.run(
            [
                sys.executable,
                "setup.py",
                "bdist_wheel",
                "--dist-dir",
                str(wheel_root),
            ],
            cwd=source_root,
            env=environment,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        if result.returncode != 0:
            raise SystemExit(result.stdout + result.stderr)
        wheels = list(wheel_root.glob("*.whl"))
        if len(wheels) != 1 or wheels[0].name != TARGET.name:
            raise SystemExit(f"unexpected build output: {wheels}")
        return normalize_wheel(wheels[0])


def write_atomic(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("wb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Rebuild the deterministic vendored hexdump wheel.",
    )
    source_mode = parser.add_mutually_exclusive_group()
    source_mode.add_argument(
        "--source",
        type=Path,
        help="use another local pinned hexdump-3.3.zip",
    )
    source_mode.add_argument(
        "--download",
        action="store_true",
        help="download the official source instead of using the vendored copy",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="compare with the vendored wheel")
    mode.add_argument("--write", action="store_true", help="replace the vendored wheel atomically")
    arguments = parser.parse_args()

    source = None if arguments.download else (arguments.source or VENDORED_SOURCE)
    rebuilt = build_wheel(read_source(source))
    digest = sha256_bytes(rebuilt)
    if arguments.write:
        write_atomic(TARGET, rebuilt)
        print(f"wrote {TARGET}")
        print(f"SHA-256 {digest}")
        return 0
    if not plain_file(TARGET) or TARGET.read_bytes() != rebuilt:
        print(
            f"vendored wheel differs from deterministic rebuild (SHA-256 {digest})",
            file=sys.stderr,
        )
        return 1
    print(f"vendored wheel is reproducible: SHA-256 {digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
