from __future__ import annotations

import hashlib
import os
import re
import zipfile
from pathlib import Path
from urllib.parse import urlsplit

VERSION = "0.1.3"
ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"
ARCHIVE = DIST / f"AppRestore-{VERSION}-source.zip"
BOOTSTRAP = DIST / "install.ps1"
CHECKSUMS = DIST / "SHA256SUMS.txt"
ARCHIVE_ROOT = f"AppRestore-{VERSION}"
FIXED_TIME = (2026, 7, 28, 0, 0, 0)
BOOTSTRAP_TEMPLATE = ROOT / "scripts" / "install.ps1.in"
ARCHIVE_URL_TEMPLATE = (
    "https://github.com/J3ckJ/AppRestore/releases/download/"
    "v{version}/{archive_name}"
)

BOOTSTRAP_VERSION_TOKEN = "@@APPRESTORE_VERSION@@"
BOOTSTRAP_ARCHIVE_URL_TOKEN = "@@APPRESTORE_ARCHIVE_URL@@"
BOOTSTRAP_ARCHIVE_SHA256_TOKEN = "@@APPRESTORE_ARCHIVE_SHA256@@"

ROOT_FILES = [
    ".gitignore",
    "apprestore.py",
    "apprestore.ps1",
    "apprestore.sh",
    "CHANGELOG.md",
    "CONTRIBUTING.md",
    "install-windows.ps1",
    "LICENSE",
    "pyproject.toml",
    "README.md",
    "SECURITY.md",
    "THIRD_PARTY_NOTICES.md",
    "uninstall-windows.ps1",
]
SCRIPT_FILES = [
    "scripts/build-release.py",
    "scripts/install.ps1.in",
]


def release_files() -> list[Path]:
    files = [ROOT / name for name in ROOT_FILES]
    files.extend(sorted((ROOT / "apprestore_core").glob("*.py")))
    files.extend(sorted((ROOT / "tests").glob("*.py")))
    files.extend(ROOT / name for name in SCRIPT_FILES)
    return files


def validate(files: list[Path]) -> None:
    missing = [path for path in files if not path.is_file()]
    if missing:
        raise SystemExit(f"missing release files: {missing}")
    for path in files:
        relative = path.relative_to(ROOT)
        if (
            path.suffix.lower() == ".ipa"
            or "__pycache__" in relative.parts
            or ".env" in relative.parts
        ):
            raise SystemExit(f"forbidden release path: {relative}")
        if path.stat().st_size > 2 * 1024 * 1024:
            raise SystemExit(f"unexpectedly large release file: {relative}")


def add_file(archive: zipfile.ZipFile, path: Path) -> None:
    relative = path.relative_to(ROOT).as_posix()
    info = zipfile.ZipInfo(f"{ARCHIVE_ROOT}/{relative}", FIXED_TIME)
    info.create_system = 3
    executable = relative in {"apprestore.sh", "scripts/build-release.py"}
    mode = 0o755 if executable else 0o644
    info.external_attr = (mode & 0xFFFF) << 16
    info.compress_type = zipfile.ZIP_DEFLATED
    archive.writestr(info, path.read_bytes())


def _powershell_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def render_bootstrap(
    *,
    version: str,
    archive_url: str,
    archive_sha256: str,
    template_path: Path = BOOTSTRAP_TEMPLATE,
) -> str:
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version):
        raise ValueError(f"unsupported release version: {version!r}")

    parsed_url = urlsplit(archive_url)
    if (
        parsed_url.scheme.lower() != "https"
        or not parsed_url.netloc
        or parsed_url.username is not None
        or parsed_url.password is not None
    ):
        raise ValueError("the release archive URL must be an absolute HTTPS URL")

    normalized_sha256 = archive_sha256.lower()
    if not re.fullmatch(r"[0-9a-f]{64}", normalized_sha256):
        raise ValueError("archive_sha256 must contain exactly 64 hexadecimal characters")

    rendered = template_path.read_text(encoding="utf-8")
    replacements = {
        BOOTSTRAP_VERSION_TOKEN: _powershell_literal(version),
        BOOTSTRAP_ARCHIVE_URL_TOKEN: _powershell_literal(archive_url),
        BOOTSTRAP_ARCHIVE_SHA256_TOKEN: _powershell_literal(
            normalized_sha256
        ),
    }
    for token, replacement in replacements.items():
        count = rendered.count(token)
        if count != 1:
            raise ValueError(
                f"bootstrap template must contain {token!r} exactly once; found {count}"
            )
        rendered = rendered.replace(token, replacement)

    if not rendered.endswith("\n"):
        rendered += "\n"
    return rendered


def write_text_atomic(path: Path, text: str) -> None:
    temporary = path.with_name(path.name + ".tmp")
    if temporary.exists():
        temporary.unlink()
    with temporary.open("w", encoding="utf-8", newline="\n") as stream:
        stream.write(text)
    os.replace(temporary, path)


def main() -> int:
    files = release_files()
    validate(files)
    DIST.mkdir(exist_ok=True)
    temporary = ARCHIVE.with_suffix(".zip.tmp")
    if temporary.exists():
        temporary.unlink()
    with zipfile.ZipFile(
        temporary,
        "w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=9,
    ) as archive:
        for path in files:
            add_file(archive, path)
    os.replace(temporary, ARCHIVE)

    digest = hashlib.sha256(ARCHIVE.read_bytes()).hexdigest()
    archive_url = ARCHIVE_URL_TEMPLATE.format(
        version=VERSION,
        archive_name=ARCHIVE.name,
    )
    bootstrap_text = render_bootstrap(
        version=VERSION,
        archive_url=archive_url,
        archive_sha256=digest,
    )
    write_text_atomic(BOOTSTRAP, bootstrap_text)
    bootstrap_digest = hashlib.sha256(BOOTSTRAP.read_bytes()).hexdigest()
    write_text_atomic(
        CHECKSUMS,
        (
            f"{digest}  {ARCHIVE.name}\n"
            f"{bootstrap_digest}  {BOOTSTRAP.name}\n"
        ),
    )
    print(ARCHIVE)
    print(f"SHA-256 {digest}")
    print(BOOTSTRAP)
    print(f"SHA-256 {bootstrap_digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
