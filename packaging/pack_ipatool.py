"""Pack one ipatool binary into a byte-for-byte reproducible .tar.gz.

GNU tar (Linux, Git Bash on Windows) and BSD tar (macOS) disagree on flags
and defaults, and both store the build time. Python's tarfile/gzip behave the
same everywhere, so ``build-ipatool.sh`` packs through this script:

* one member ``bin/<name>``, regular file, mode 0755;
* mtime from ``--mtime`` (the script passes SOURCE_DATE_EPOCH or the commit
  time of the pinned ipatool commit);
* uid/gid 0, empty user/group names, USTAR format (no PAX timestamps);
* gzip header: no file name, mtime = the same fixed time, level 9, OS byte
  fixed to 255 ("unknown") so the archive does not depend on the host.

Same binary + same mtime = same archive SHA-256.

Usage: python pack_ipatool.py --binary PATH --name NAME --mtime EPOCH --out ARCHIVE
"""

from __future__ import annotations

import argparse
import gzip
import io
import os
import tarfile
from pathlib import Path


def pack(binary: Path, name: str, mtime: int, out: Path) -> None:
    if "/" in name or "\\" in name or not name:
        raise SystemExit(f"bad member name: {name!r}")
    if mtime < 0:
        raise SystemExit("mtime must be >= 0")
    data = binary.read_bytes()

    info = tarfile.TarInfo(f"bin/{name}")
    info.size = len(data)
    info.mtime = mtime
    info.mode = 0o755
    info.type = tarfile.REGTYPE
    info.uid = info.gid = 0
    info.uname = info.gname = ""

    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w", format=tarfile.USTAR_FORMAT) as tar:
        tar.addfile(info, io.BytesIO(data))

    compressed = io.BytesIO()
    with gzip.GzipFile(filename="", mode="wb", fileobj=compressed, compresslevel=9, mtime=mtime) as gz:
        gz.write(raw.getvalue())
    blob = bytearray(compressed.getvalue())
    # Byte 9 of the gzip header is the OS that wrote it; CPython sets 255 on
    # every platform already, but pin it so the result never depends on that.
    blob[9] = 0xFF

    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(out.name + ".tmp")
    tmp.write_bytes(bytes(blob))
    os.replace(tmp, out)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", required=True, type=Path)
    parser.add_argument("--name", required=True)
    parser.add_argument("--mtime", required=True, type=int)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    pack(args.binary, args.name, args.mtime, args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
