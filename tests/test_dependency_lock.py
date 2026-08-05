from __future__ import annotations

import hashlib
import re
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REQUIREMENTS = ROOT / "requirements"
RUNTIME_LOCK = REQUIREMENTS / "runtime.lock"
BUILD_LOCK = REQUIREMENTS / "build.lock"
TEST_LOCK = REQUIREMENTS / "test.lock"
WHEEL_BUILD_LOCK = REQUIREMENTS / "wheel-build.lock"
HEXDUMP_WHEEL = REQUIREMENTS / "wheels" / "hexdump-3.3-py3-none-any.whl"
HEXDUMP_SOURCE = REQUIREMENTS / "sources" / "hexdump-3.3.zip"
HEXDUMP_SOURCE_SHA256 = (
    "d781a43b0c16ace3f9366aade73e8ad3"
    "a7bd5137d58f0b45ab2d3f54876f20db"
)
HEXDUMP_WHEEL_SHA256 = (
    "2041be582c1021ec900d7496e204553b1"
    "f7bd0c650b6d0f294a6d413125d8acb"
)


def _logical_requirements(path: Path) -> list[str]:
    logical: list[str] = []
    pending = ""
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        pending = f"{pending} {stripped}".strip()
        if pending.endswith("\\"):
            pending = pending[:-1].rstrip()
            continue
        logical.append(pending)
        pending = ""
    assert not pending
    return logical


def _assert_fully_hashed_lock(path: Path, *, minimum_packages: int) -> None:
    requirements = _logical_requirements(path)
    assert len(requirements) >= minimum_packages
    for requirement in requirements:
        assert " @ " not in requirement
        assert re.match(
            r"^[A-Za-z0-9][A-Za-z0-9_.-]*==[^\s;]+(?:\s*;\s*.*?)?\s+--hash=sha256:",
            requirement,
        ), requirement
        hashes = re.findall(r"--hash=sha256:([0-9a-f]{64})(?:\s|$)", requirement)
        assert hashes, requirement
        assert requirement.count("--hash=") == len(hashes), requirement


def test_project_python_range_matches_supported_runtime() -> None:
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert 'requires-python = ">=3.10,<3.14"' in pyproject
    assert '"pymobiledevice3==10.1.0"' in pyproject


def test_dependency_locks_are_exact_and_hash_complete() -> None:
    _assert_fully_hashed_lock(RUNTIME_LOCK, minimum_packages=100)
    _assert_fully_hashed_lock(BUILD_LOCK, minimum_packages=3)
    _assert_fully_hashed_lock(TEST_LOCK, minimum_packages=9)
    _assert_fully_hashed_lock(WHEEL_BUILD_LOCK, minimum_packages=3)

    runtime = RUNTIME_LOCK.read_text(encoding="utf-8")
    build = BUILD_LOCK.read_text(encoding="utf-8")
    tests = TEST_LOCK.read_text(encoding="utf-8")
    wheel_build = WHEEL_BUILD_LOCK.read_text(encoding="utf-8")
    assert "pymobiledevice3==10.1.0" in runtime
    assert f"--hash=sha256:{HEXDUMP_WHEEL_SHA256}" in runtime
    assert "d781a43b0c16ace3f9366aade73e8ad3" not in runtime
    assert "setuptools==82.0.1" in build
    assert "wheel==0.46.3" in build
    assert "pytest==9.0.3" in tests
    assert "setuptools==83.0.0" in wheel_build
    assert "wheel==0.46.3" in wheel_build


def test_vendored_hexdump_wheel_matches_lock_and_is_platform_neutral() -> None:
    assert hashlib.sha256(HEXDUMP_WHEEL.read_bytes()).hexdigest() == (
        HEXDUMP_WHEEL_SHA256
    )
    with zipfile.ZipFile(HEXDUMP_WHEEL) as archive:
        names = archive.namelist()
        assert names
        assert all(not name.startswith(("/", "\\")) for name in names)
        assert all(".." not in Path(name).parts for name in names)
        wheel = archive.read("hexdump-3.3.dist-info/WHEEL").decode("utf-8")
        metadata = archive.read("hexdump-3.3.dist-info/METADATA").decode("utf-8")
        assert all(info.compress_type == zipfile.ZIP_STORED for info in archive.infolist())
        assert all(info.create_system == 3 for info in archive.infolist())
    assert "Tag: py3-none-any" in wheel
    assert "Name: hexdump" in metadata
    assert "Version: 3.3" in metadata
    assert "License: Public Domain" in metadata


def test_vendored_hexdump_source_is_the_exact_documented_pypi_archive() -> None:
    assert hashlib.sha256(HEXDUMP_SOURCE.read_bytes()).hexdigest() == (
        HEXDUMP_SOURCE_SHA256
    )
    with zipfile.ZipFile(HEXDUMP_SOURCE) as archive:
        assert set(archive.namelist()) == {
            "PKG-INFO",
            "README.txt",
            "__main__.py",
            "data/hexfile.bin",
            "hexdump.py",
            "setup.py",
        }


def test_lock_regeneration_and_vendored_wheel_are_documented() -> None:
    runtime = RUNTIME_LOCK.read_text(encoding="utf-8")
    readme = (REQUIREMENTS / "README.md").read_text(encoding="utf-8")
    recipe = (ROOT / "scripts" / "rebuild-vendored-wheel.py").read_text(
        encoding="utf-8"
    )
    assert "uv 0.12.1 pip compile" in runtime.splitlines()[1]
    assert "--exclude-newer 2026-08-05T00:00:00Z" in runtime.splitlines()[1]
    assert "setuptools 83.0.0" in readme
    assert "reproducible byte-for-byte" in readme
    assert 'SOURCE_DATE_EPOCH = "1453484416"' in recipe
    assert "zipfile.ZIP_STORED" in recipe
    assert "SOURCE_SHA256" in recipe
    assert "VENDORED_SOURCE" in recipe
    assert HEXDUMP_WHEEL_SHA256 in readme
    assert HEXDUMP_SOURCE_SHA256 in readme
