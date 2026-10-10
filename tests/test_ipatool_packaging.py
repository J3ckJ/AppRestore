"""packaging/pack_ipatool.py writes a reproducible ipatool archive."""

from __future__ import annotations

import gzip
import hashlib
import importlib.util
import os
import struct
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MTIME = 1791308622  # commit time of majd/ipatool cde7d00


def _pack_module():
    spec = importlib.util.spec_from_file_location("pack_ipatool", ROOT / "packaging" / "pack_ipatool.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_same_binary_gives_same_archive_regardless_of_file_metadata(tmp_path: Path) -> None:
    pack = _pack_module().pack
    payload = os.urandom(4096) + b"appstore.CountryCodeFromStoreFront"
    first_bin = tmp_path / "a" / "ipatool"
    second_bin = tmp_path / "b" / "other-name"
    for path, stamp, mode in ((first_bin, 1_000_000_000, 0o600), (second_bin, 1_700_000_000, 0o777)):
        path.parent.mkdir()
        path.write_bytes(payload)
        os.utime(path, (stamp, stamp))
        path.chmod(mode)
    first = tmp_path / "one.tar.gz"
    second = tmp_path / "out" / "two.tar.gz"
    pack(first_bin, "ipatool-2.6.0-windows-amd64.exe", MTIME, first)
    pack(second_bin, "ipatool-2.6.0-windows-amd64.exe", MTIME, second)
    assert hashlib.sha256(first.read_bytes()).digest() == hashlib.sha256(second.read_bytes()).digest()

    other_time = tmp_path / "three.tar.gz"
    pack(first_bin, "ipatool-2.6.0-windows-amd64.exe", MTIME + 1, other_time)
    assert other_time.read_bytes() != first.read_bytes()


def test_archive_layout_and_headers_are_fixed(tmp_path: Path) -> None:
    pack = _pack_module().pack
    binary = tmp_path / "ipatool"
    binary.write_bytes(b"\x7fELF fake")
    archive = tmp_path / "ipatool.tar.gz"
    pack(binary, "ipatool-2.6.0-macos-arm64", MTIME, archive)

    head = archive.read_bytes()[:10]
    assert head[:3] == b"\x1f\x8b\x08"
    assert head[3] == 0  # no FNAME/FCOMMENT/FEXTRA: no host file name inside
    assert struct.unpack("<I", head[4:8])[0] == MTIME
    assert head[9] == 0xFF

    with tarfile.open(archive, "r:gz") as tar:
        members = tar.getmembers()
        assert [m.name for m in members] == ["bin/ipatool-2.6.0-macos-arm64"]
        member = members[0]
        assert member.isreg() and member.mode == 0o755
        assert (member.uid, member.gid, member.uname, member.gname) == (0, 0, "", "")
        assert member.mtime == MTIME
        assert not member.pax_headers
        extracted = tar.extractfile(member)
        assert extracted is not None and extracted.read() == b"\x7fELF fake"
    # The tar stream is plain USTAR: exactly header + one data block + 2 zero blocks,
    # padded to tarfile's 10240-byte record.
    assert len(gzip.decompress(archive.read_bytes())) == 10240


def test_build_script_packs_through_python_with_fixed_mtime() -> None:
    script = (ROOT / "packaging" / "build-ipatool.sh").read_text(encoding="utf-8")
    assert "packaging/pack_ipatool.py" in script
    assert "SOURCE_DATE_EPOCH" in script
    assert 'log -1 --format=%ct "$commit"' in script
    assert "tar -C" not in script and "-czf" not in script
    assert "ipatool-auth-info-country.patch:" in script


def test_go_toolchain_pin_matches_ci_workflow() -> None:
    import re

    script = (ROOT / "packaging" / "build-ipatool.sh").read_text(encoding="utf-8")
    workflow = (ROOT / ".github" / "workflows" / "build-ipatool.yml").read_text(encoding="utf-8")
    pinned = re.search(r'IPATOOL_GOTOOLCHAIN:-go([0-9.]+)\}', script)
    assert pinned, "build-ipatool.sh must pin GOTOOLCHAIN"
    assert 'export GOTOOLCHAIN="$go_toolchain"' in script
    versions = set(re.findall(r'go-version:\s*"([0-9.]+)"', workflow))
    assert versions == {pinned.group(1)}
