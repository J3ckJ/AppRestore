from __future__ import annotations

import hashlib
import os
import re
import stat
import zipfile
from pathlib import Path
from urllib.parse import urlsplit

VERSION = "0.2.2"
ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"
ARCHIVE = DIST / f"AppRestore-{VERSION}-source.zip"
WINDOWS_BOOTSTRAP = DIST / "install.ps1"
MACOS_BOOTSTRAP = DIST / "install.sh"
CHECKSUMS = DIST / "SHA256SUMS.txt"
ARCHIVE_ROOT = f"AppRestore-{VERSION}"
FIXED_TIME = (2026, 8, 5, 0, 0, 0)
WINDOWS_BOOTSTRAP_TEMPLATE = ROOT / "scripts" / "install.ps1.in"
MACOS_BOOTSTRAP_TEMPLATE = ROOT / "scripts" / "install.sh.in"
ARCHIVE_URL_TEMPLATE = (
    "https://github.com/J3ckJ/AppRestore/releases/download/"
    "v{version}/{archive_name}"
)

WINDOWS_BOOTSTRAP_VERSION_TOKEN = "@@APPRESTORE_VERSION@@"
WINDOWS_BOOTSTRAP_ARCHIVE_URL_TOKEN = "@@APPRESTORE_ARCHIVE_URL@@"
WINDOWS_BOOTSTRAP_ARCHIVE_SHA256_TOKEN = "@@APPRESTORE_ARCHIVE_SHA256@@"
MACOS_BOOTSTRAP_VERSION_TOKEN = "@@APPRESTORE_VERSION_SH@@"
MACOS_BOOTSTRAP_ARCHIVE_URL_TOKEN = "@@APPRESTORE_ARCHIVE_URL_SH@@"
MACOS_BOOTSTRAP_ARCHIVE_SHA256_TOKEN = "@@APPRESTORE_ARCHIVE_SHA256_SH@@"

ROOT_FILES = [
    ".gitattributes",
    ".github/workflows/ci.yml",
    ".github/workflows/release.yml",
    ".gitignore",
    "apprestore.py",
    "apprestore.ps1",
    "apprestore.sh",
    "CHANGELOG.md",
    "CONTRIBUTING.md",
    "docs/RELEASING.md",
    "install-macos.sh",
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
    "scripts/install.sh.in",
    "scripts/rebuild-vendored-wheel.py",
]
REQUIREMENT_FILES = [
    "requirements/README.md",
    "requirements/build.in",
    "requirements/build.lock",
    "requirements/runtime.in",
    "requirements/runtime.lock",
    "requirements/test.in",
    "requirements/test.lock",
    "requirements/wheel-build.in",
    "requirements/wheel-build.lock",
    "requirements/sources/hexdump-3.3.zip",
    "requirements/wheels/hexdump-3.3-py3-none-any.whl",
]


def release_files() -> list[Path]:
    files = [ROOT / name for name in ROOT_FILES]
    files.extend(sorted((ROOT / "apprestore_core").glob("*.py")))
    files.extend(sorted((ROOT / "tests").glob("*.py")))
    files.extend(ROOT / name for name in SCRIPT_FILES)
    files.extend(ROOT / name for name in REQUIREMENT_FILES)
    return files


def _plain_file(path: Path) -> bool:
    try:
        metadata = path.lstat()
    except OSError:
        return False
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        return False
    return not bool(getattr(metadata, "st_file_attributes", 0) & 0x400)


def validate(files: list[Path]) -> None:
    unsafe = [path for path in files if not _plain_file(path)]
    if unsafe:
        raise SystemExit(f"missing or unsafe release files: {unsafe}")
    for path in files:
        relative = path.relative_to(ROOT)
        if (
            path.suffix.lower() == ".ipa"
            or "__pycache__" in relative.parts
            or ".env" in relative.parts
        ):
            raise SystemExit(f"forbidden release path: {relative}")
        if path.lstat().st_size > 2 * 1024 * 1024:
            raise SystemExit(f"unexpectedly large release file: {relative}")


def _version_literal(path: Path, pattern: str, label: str) -> str:
    match = re.search(pattern, path.read_text(encoding="utf-8"))
    if match is None:
        raise SystemExit(f"missing {label} version literal in {path}")
    return match.group(1)


def validate_release_versions() -> None:
    versions = {
        "builder": VERSION,
        "pyproject.toml": _version_literal(
            ROOT / "pyproject.toml",
            r'(?m)^\s*version\s*=\s*"([^"]+)"\s*$',
            "project",
        ),
        "apprestore_core": _version_literal(
            ROOT / "apprestore_core" / "__init__.py",
            r'(?m)^\s*__version__\s*=\s*"([^"]+)"\s*$',
            "package",
        ),
        "Windows installer": _version_literal(
            ROOT / "install-windows.ps1",
            r'(?m)^\s*\$AppRestoreVersion\s*=\s*"([^"]+)"\s*$',
            "Windows installer",
        ),
        "macOS installer": _version_literal(
            ROOT / "install-macos.sh",
            r'(?m)^\s*APPRESTORE_VERSION="([^"]+)"\s*$',
            "macOS installer",
        ),
    }
    if set(versions.values()) != {VERSION}:
        raise SystemExit(f"release version drift: {versions}")


def validate_dependency_locks() -> None:
    runtime = (ROOT / "requirements" / "runtime.lock").read_text(
        encoding="utf-8"
    )
    build = (ROOT / "requirements" / "build.lock").read_text(
        encoding="utf-8"
    )
    tests = (ROOT / "requirements" / "test.lock").read_text(
        encoding="utf-8"
    )
    wheel_build = (ROOT / "requirements" / "wheel-build.lock").read_text(
        encoding="utf-8"
    )
    for label, lock in (
        ("runtime", runtime),
        ("build", build),
        ("test", tests),
        ("wheel-build", wheel_build),
    ):
        logical_requirements: list[str] = []
        pending = ""
        for raw_line in lock.splitlines():
            stripped = raw_line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            pending = f"{pending} {stripped}".strip()
            if pending.endswith("\\"):
                pending = pending[:-1].rstrip()
                continue
            logical_requirements.append(pending)
            pending = ""
        if pending or not logical_requirements:
            raise SystemExit(f"{label} dependency lock is malformed")
        for requirement in logical_requirements:
            hashes = re.findall(
                r"--hash=sha256:([0-9a-f]{64})(?:\s|$)",
                requirement,
            )
            if (
                " @ " in requirement
                or re.match(
                    r"^[A-Za-z0-9][A-Za-z0-9_.-]*==[^\s;]+",
                    requirement,
                )
                is None
                or not hashes
                or requirement.count("--hash=") != len(hashes)
            ):
                raise SystemExit(
                    f"{label} dependency lock has an unhashed requirement: "
                    f"{requirement}"
                )
    if "pymobiledevice3==10.1.0" not in runtime:
        raise SystemExit("runtime lock does not pin pymobiledevice3 10.1.0")
    if "pytest==9.0.3" not in tests:
        raise SystemExit("test lock does not pin pytest 9.0.3")
    if (
        "setuptools==83.0.0" not in wheel_build
        or "wheel==0.46.3" not in wheel_build
    ):
        raise SystemExit("wheel-build lock does not pin the rebuild toolchain")

    wheel = ROOT / "requirements" / "wheels" / "hexdump-3.3-py3-none-any.whl"
    wheel_digest = hashlib.sha256(wheel.read_bytes()).hexdigest()
    if f"--hash=sha256:{wheel_digest}" not in runtime:
        raise SystemExit("vendored hexdump wheel hash is absent from runtime.lock")
    source = ROOT / "requirements" / "sources" / "hexdump-3.3.zip"
    source_digest = hashlib.sha256(source.read_bytes()).hexdigest()
    expected_source_digest = (
        "d781a43b0c16ace3f9366aade73e8ad3"
        "a7bd5137d58f0b45ab2d3f54876f20db"
    )
    if source_digest != expected_source_digest:
        raise SystemExit("vendored hexdump source ZIP has an unexpected hash")


def add_file(archive: zipfile.ZipFile, path: Path) -> None:
    if not _plain_file(path):
        raise SystemExit(f"release input became unsafe before read: {path}")
    relative = path.relative_to(ROOT).as_posix()
    info = zipfile.ZipInfo(f"{ARCHIVE_ROOT}/{relative}", FIXED_TIME)
    info.create_system = 3
    executable = relative in {
        "apprestore.sh",
        "install-macos.sh",
        "scripts/build-release.py",
    }
    mode = 0o755 if executable else 0o644
    info.external_attr = (stat.S_IFREG | mode) << 16
    # Stored entries make the release byte-identical across zlib builds.
    info.compress_type = zipfile.ZIP_STORED
    archive.writestr(info, path.read_bytes())


def _powershell_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _validate_release_metadata(
    *, version: str, archive_url: str, archive_sha256: str
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
    return normalized_sha256


def render_bootstrap(
    *,
    version: str,
    archive_url: str,
    archive_sha256: str,
    template_path: Path = WINDOWS_BOOTSTRAP_TEMPLATE,
) -> str:
    normalized_sha256 = _validate_release_metadata(
        version=version,
        archive_url=archive_url,
        archive_sha256=archive_sha256,
    )

    rendered = template_path.read_text(encoding="utf-8")
    replacements = {
        WINDOWS_BOOTSTRAP_VERSION_TOKEN: _powershell_literal(version),
        WINDOWS_BOOTSTRAP_ARCHIVE_URL_TOKEN: _powershell_literal(archive_url),
        WINDOWS_BOOTSTRAP_ARCHIVE_SHA256_TOKEN: _powershell_literal(
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


def _shell_literal(value: str) -> str:
    return "'" + value.replace("'", "'\"'\"'") + "'"


def render_macos_bootstrap(
    *,
    version: str,
    archive_url: str,
    archive_sha256: str,
    template_path: Path = MACOS_BOOTSTRAP_TEMPLATE,
) -> str:
    normalized_sha256 = _validate_release_metadata(
        version=version,
        archive_url=archive_url,
        archive_sha256=archive_sha256,
    )
    rendered = template_path.read_text(encoding="utf-8")
    replacements = {
        MACOS_BOOTSTRAP_VERSION_TOKEN: _shell_literal(version),
        MACOS_BOOTSTRAP_ARCHIVE_URL_TOKEN: _shell_literal(archive_url),
        MACOS_BOOTSTRAP_ARCHIVE_SHA256_TOKEN: _shell_literal(
            normalized_sha256
        ),
    }
    for token, replacement in replacements.items():
        count = rendered.count(token)
        if count != 1:
            raise ValueError(
                f"macOS bootstrap template must contain {token!r} "
                f"exactly once; found {count}"
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
    validate_release_versions()
    validate_dependency_locks()
    DIST.mkdir(exist_ok=True)
    temporary = ARCHIVE.with_suffix(".zip.tmp")
    if temporary.exists():
        temporary.unlink()
    with zipfile.ZipFile(
        temporary,
        "w",
        compression=zipfile.ZIP_STORED,
    ) as archive:
        for path in files:
            add_file(archive, path)
    os.replace(temporary, ARCHIVE)

    digest = hashlib.sha256(ARCHIVE.read_bytes()).hexdigest()
    archive_url = ARCHIVE_URL_TEMPLATE.format(
        version=VERSION,
        archive_name=ARCHIVE.name,
    )
    windows_bootstrap_text = render_bootstrap(
        version=VERSION,
        archive_url=archive_url,
        archive_sha256=digest,
    )
    macos_bootstrap_text = render_macos_bootstrap(
        version=VERSION,
        archive_url=archive_url,
        archive_sha256=digest,
    )
    write_text_atomic(WINDOWS_BOOTSTRAP, windows_bootstrap_text)
    write_text_atomic(MACOS_BOOTSTRAP, macos_bootstrap_text)
    os.chmod(MACOS_BOOTSTRAP, 0o755)
    windows_bootstrap_digest = hashlib.sha256(
        WINDOWS_BOOTSTRAP.read_bytes()
    ).hexdigest()
    macos_bootstrap_digest = hashlib.sha256(
        MACOS_BOOTSTRAP.read_bytes()
    ).hexdigest()
    write_text_atomic(
        CHECKSUMS,
        (
            f"{digest}  {ARCHIVE.name}\n"
            f"{windows_bootstrap_digest}  {WINDOWS_BOOTSTRAP.name}\n"
            f"{macos_bootstrap_digest}  {MACOS_BOOTSTRAP.name}\n"
        ),
    )
    print(ARCHIVE)
    print(f"SHA-256 {digest}")
    print(WINDOWS_BOOTSTRAP)
    print(f"SHA-256 {windows_bootstrap_digest}")
    print(MACOS_BOOTSTRAP)
    print(f"SHA-256 {macos_bootstrap_digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
