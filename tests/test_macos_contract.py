from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tests.test_release_bootstrap import _copy_release_inputs, _serve_directory


ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "install-macos.sh"
BOOTSTRAP_TEMPLATE = ROOT / "scripts" / "install.sh.in"
README = ROOT / "README.md"

PRIMARY_COMMAND = (
    'curl -fsSL '
    'https://github.com/J3ckJ/AppRestore/releases/latest/download/install.sh '
    '| /bin/bash && export PATH="$HOME/.local/bin:$PATH"'
)
PATH_LINE = 'export PATH="$HOME/.local/bin:$PATH"'


def shlex_quote(value: str) -> str:
    if not value:
        return "''"
    return "'" + value.replace("'", "'\"'\"'") + "'"


def _fake_macos_environment(
    tmp_path: Path,
) -> tuple[Path, dict[str, str]]:
    home = tmp_path / "home"
    library = home / "Library"
    fake_prefix = tmp_path / "homebrew"
    fake_bin = fake_prefix / "bin"
    home.mkdir()
    library.mkdir()
    fake_bin.mkdir(parents=True)

    fake_python = fake_bin / "python3.12"
    fake_python.write_text(
        """#!/usr/bin/env bash
set -eu
if [[ "${1:-}" == "-c" ]]; then
  exit 0
fi
if [[ "${1:-}" == "-m" && "${2:-}" == "venv" ]]; then
  target=""
  for argument in "$@"; do
    target="$argument"
  done
  mkdir -p "$target/bin"
  cp "$0" "$target/bin/python"
  chmod 755 "$target/bin/python"
  exit 0
fi
if [[ "${1:-}" == "-m" && "${2:-}" == "pip" ]]; then
  if [[ "${APPRESTORE_TEST_PIP_FAIL:-}" == "1" ]]; then
    exit 23
  fi
  exit 0
fi
if [[ "${1:-}" == "-m" && "${2:-}" == "apprestore_core.cli" ]]; then
  if [[ "${3:-}" == "--version" ]]; then
    printf '%s\\n' '0.1.4'
  else
    printf '%s\\n' 'APPRESTORE_MENU_OK'
  fi
  exit 0
fi
exit 64
""",
        encoding="utf-8",
        newline="\n",
    )
    fake_python.chmod(0o755)

    fake_brew = fake_bin / "brew"
    fake_brew.write_text(
        f"""#!/usr/bin/env bash
set -eu
if [[ "${{1:-}}" == "--prefix" && "${{2:-}}" == "python@3.12" ]]; then
  printf '%s\\n' {shlex_quote(str(fake_prefix))}
  exit 0
fi
if [[ "${{1:-}}" == "install" ]]; then
  exit 0
fi
exit 64
""",
        encoding="utf-8",
        newline="\n",
    )
    fake_brew.chmod(0o755)
    (fake_bin / "ipatool").write_text(
        "#!/bin/sh\nexit 0\n",
        encoding="utf-8",
        newline="\n",
    )
    (fake_bin / "ipatool").chmod(0o755)
    (fake_bin / "rm").write_text(
        """#!/usr/bin/env bash
set -eu
if [[ "${APPRESTORE_TEST_BACKUP_CLEANUP_FAIL:-}" == "1" ]]; then
  for argument in "$@"; do
    case "$argument" in
      *"/.venv-backup."*"/venv") exit 91 ;;
    esac
  done
fi
exec /bin/rm "$@"
""",
        encoding="utf-8",
        newline="\n",
    )
    (fake_bin / "rm").chmod(0o755)

    environment = os.environ.copy()
    environment.update(
        {
            "HOME": str(home),
            "SHELL": "/bin/zsh",
            "PATH": f"{fake_bin}:/usr/bin:/bin",
        }
    )
    return home, environment


def _run_payload(environment: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["/bin/bash", str(INSTALLER)],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


def test_documented_macos_contract_is_two_commands() -> None:
    readme = README.read_text(encoding="utf-8")
    assert PRIMARY_COMMAND in readme
    assert "el-system-tools.j3ckj.chatgpt.site/install/apprestore.ps1" not in readme
    assert re.search(
        re.escape(PRIMARY_COMMAND) + r"\napprestore(?:\n|$)",
        readme,
    )


def test_payload_installer_is_user_space_and_transactional() -> None:
    installer = INSTALLER.read_text(encoding="utf-8")

    assert '$HOME/Library/Application Support/AppRestore' in installer
    assert '$HOME/.local/bin' in installer
    assert 'APPRESTORE_VERSION="0.1.4"' in installer
    assert f"path_line='{PATH_LINE}'" in installer
    assert "-m venv --copies" in installer
    assert "VENV_STAGING" in installer
    assert "VENV_BACKUP" in installer
    assert "OLD_VENV_MOVED" in installer
    assert "trap cleanup EXIT" in installer
    assert "rm -rf -- \"$VENV_DIR\"" in installer
    assert "mv \"$VENV_BACKUP/venv\" \"$VENV_DIR\"" in installer
    assert "prepare_install_directories" in installer
    assert '"$VENV_DIR/bin/python" -m apprestore_core.cli --version' in installer
    assert "exec %q -m apprestore_core.cli" in installer
    assert "$VENV_DIR/bin/apprestore" not in installer
    assert 'VENV_MARKER_NAME=".apprestore-managed"' in installer
    assert "venv_is_managed" in installer
    assert "grep -Fqx" in installer
    assert "INSTALL_COMPLETE=true" in installer
    assert 'installed_version="$("$COMMAND_PATH" --version)"' in installer
    assert '[[ "$installed_version" == "$APPRESTORE_VERSION" ]]' in installer
    assert re.search(r"^\s*sudo(?:\s|$)", installer, re.MULTILINE) is None


def test_bootstrap_pins_archive_and_rejects_unsafe_zip_shapes() -> None:
    bootstrap = BOOTSTRAP_TEMPLATE.read_text(encoding="utf-8")

    assert "@@APPRESTORE_ARCHIVE_URL_SH@@" in bootstrap
    assert "@@APPRESTORE_ARCHIVE_SHA256_SH@@" in bootstrap
    assert "APPRESTORE_BOOTSTRAP_ARCHIVE_URL" in bootstrap
    assert "APPRESTORE_BOOTSTRAP_ARCHIVE_SHA" not in bootstrap
    assert "installer_count" in bootstrap
    assert "duplicate target" in bootstrap
    assert "max_entry_bytes=8388608" in bootstrap
    assert "total_bytes=67108864" in bootstrap
    assert "symbolic links запрещены" in bootstrap
    assert "path traversal" in bootstrap
    assert "trap cleanup EXIT" in bootstrap


@pytest.mark.skipif(
    sys.platform != "darwin",
    reason="macOS installer E2E requires the native shell and filesystem",
)
def test_payload_installs_apprestore_for_the_same_terminal_path(
    tmp_path: Path,
) -> None:
    home, environment = _fake_macos_environment(tmp_path)
    result = _run_payload(environment)
    assert result.returncode == 0, result.stdout + result.stderr

    current_environment = environment.copy()
    current_environment["PATH"] = (
        f"{home / '.local' / 'bin'}:{environment['PATH']}"
    )
    resolved = shutil.which("apprestore", path=current_environment["PATH"])
    assert resolved == str(home / ".local" / "bin" / "apprestore")
    launched = subprocess.run(
        ["apprestore", "--version"],
        env=current_environment,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert launched.returncode == 0, launched.stdout + launched.stderr
    assert launched.stdout.strip() == "0.1.4"
    assert PATH_LINE in (home / ".zprofile").read_text(encoding="utf-8").splitlines()
    assert not list(
        (home / "Library" / "Application Support" / "AppRestore").glob(
            ".venv-*"
        )
    )


@pytest.mark.skipif(
    sys.platform != "darwin",
    reason="macOS installer E2E requires the native shell and filesystem",
)
def test_failed_first_install_rolls_back_venv_and_command(
    tmp_path: Path,
) -> None:
    home, environment = _fake_macos_environment(tmp_path)
    environment["APPRESTORE_TEST_PIP_FAIL"] = "1"

    result = _run_payload(environment)
    assert result.returncode != 0

    app_support = home / "Library" / "Application Support" / "AppRestore"
    assert not (app_support / "venv").exists()
    assert not list(app_support.glob(".venv-*"))
    assert not (home / ".local" / "bin" / "apprestore").exists()


@pytest.mark.skipif(
    sys.platform != "darwin",
    reason="macOS installer E2E requires the native shell and filesystem",
)
def test_failed_update_restores_previous_working_venv(
    tmp_path: Path,
) -> None:
    home, environment = _fake_macos_environment(tmp_path)
    first = _run_payload(environment)
    assert first.returncode == 0, first.stdout + first.stderr

    environment["APPRESTORE_TEST_PIP_FAIL"] = "1"
    failed_update = _run_payload(environment)
    assert failed_update.returncode != 0

    current_environment = environment.copy()
    current_environment.pop("APPRESTORE_TEST_PIP_FAIL")
    current_environment["PATH"] = (
        f"{home / '.local' / 'bin'}:{environment['PATH']}"
    )
    launched = subprocess.run(
        ["apprestore", "--version"],
        env=current_environment,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert launched.returncode == 0, launched.stdout + launched.stderr
    assert launched.stdout.strip() == "0.1.4"
    app_support = home / "Library" / "Application Support" / "AppRestore"
    assert not list(app_support.glob(".venv-*"))


@pytest.mark.skipif(
    sys.platform != "darwin",
    reason="macOS installer E2E requires the native shell and filesystem",
)
def test_unmarked_existing_venv_is_refused_without_modification(
    tmp_path: Path,
) -> None:
    home, environment = _fake_macos_environment(tmp_path)
    venv = home / "Library" / "Application Support" / "AppRestore" / "venv"
    venv.mkdir(parents=True)
    sentinel = venv / "do-not-delete.txt"
    sentinel.write_text("unowned", encoding="utf-8")

    result = _run_payload(environment)

    assert result.returncode != 0
    assert sentinel.read_text(encoding="utf-8") == "unowned"
    assert not list(venv.parent.glob(".venv-backup.*"))


@pytest.mark.skipif(
    sys.platform != "darwin",
    reason="macOS installer E2E requires the native shell and filesystem",
)
def test_marker_substring_does_not_claim_foreign_command(
    tmp_path: Path,
) -> None:
    home, environment = _fake_macos_environment(tmp_path)
    command = home / ".local" / "bin" / "apprestore"
    command.parent.mkdir(parents=True)
    foreign_content = (
        "#!/bin/sh\n"
        "echo 'prefix # Managed by AppRestore install-macos.sh suffix'\n"
    )
    command.write_text(foreign_content, encoding="utf-8", newline="\n")
    command.chmod(0o755)

    result = _run_payload(environment)

    assert result.returncode != 0
    assert command.read_text(encoding="utf-8") == foreign_content
    app_support = home / "Library" / "Application Support" / "AppRestore"
    assert not (app_support / "venv").exists()


@pytest.mark.skipif(
    sys.platform != "darwin",
    reason="macOS installer E2E requires the native shell and filesystem",
)
def test_backup_cleanup_failure_keeps_successful_new_install(
    tmp_path: Path,
) -> None:
    home, environment = _fake_macos_environment(tmp_path)
    first = _run_payload(environment)
    assert first.returncode == 0, first.stdout + first.stderr

    environment["APPRESTORE_TEST_BACKUP_CLEANUP_FAIL"] = "1"
    update = _run_payload(environment)

    assert update.returncode == 0, update.stdout + update.stderr
    app_support = home / "Library" / "Application Support" / "AppRestore"
    backups = list(app_support.glob(".venv-backup.*"))
    assert len(backups) == 1
    assert (backups[0] / "venv").is_dir()
    launched = subprocess.run(
        [str(home / ".local" / "bin" / "apprestore"), "--version"],
        env=environment,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert launched.returncode == 0, launched.stdout + launched.stderr
    assert launched.stdout.strip() == "0.1.4"
    assert "старый venv сохранён" in update.stderr


@pytest.mark.skipif(
    sys.platform != "darwin",
    reason="relocatable venv contract requires native macOS",
)
def test_real_moved_venv_launches_through_final_python_module(
    tmp_path: Path,
) -> None:
    staging = tmp_path / "venv-staging"
    live = tmp_path / "venv-live"
    create = subprocess.run(
        [sys.executable, "-m", "venv", "--copies", str(staging)],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert create.returncode == 0, create.stdout + create.stderr
    install = subprocess.run(
        [
            str(staging / "bin" / "python"),
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            "--no-input",
            "--no-deps",
            str(ROOT),
        ],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert install.returncode == 0, install.stdout + install.stderr
    staging.rename(live)

    wrapper = tmp_path / "apprestore"
    wrapper.write_text(
        (
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n"
            f"exec {shlex_quote(str(live / 'bin' / 'python'))} "
            '-m apprestore_core.cli "$@"\n'
        ),
        encoding="utf-8",
        newline="\n",
    )
    wrapper.chmod(0o755)
    launched = subprocess.run(
        [str(wrapper), "--version"],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert launched.returncode == 0, launched.stdout + launched.stderr
    assert launched.stdout.strip() == "0.1.4"


@pytest.mark.skipif(
    sys.platform != "darwin",
    reason="macOS bootstrap E2E requires native curl, ditto and zipinfo",
)
def test_generated_bootstrap_installs_then_command_launches(
    tmp_path: Path,
) -> None:
    release_root = tmp_path / "release"
    _copy_release_inputs(release_root)
    build = subprocess.run(
        [sys.executable, str(release_root / "scripts" / "build-release.py")],
        cwd=release_root,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert build.returncode == 0, build.stdout + build.stderr

    archive = release_root / "dist" / "AppRestore-0.1.4-source.zip"
    bootstrap = release_root / "dist" / "install.sh"
    assert archive.is_file()
    assert bootstrap.is_file()

    home, environment = _fake_macos_environment(tmp_path)
    runtime_temp = tmp_path / "runtime-temp"
    runtime_temp.mkdir()
    environment["TMPDIR"] = str(runtime_temp)
    with _serve_directory(release_root / "dist") as base_url:
        environment["APPRESTORE_BOOTSTRAP_ARCHIVE_URL"] = (
            f"{base_url}/{archive.name}"
        )
        installed = subprocess.run(
            ["/bin/bash", str(bootstrap)],
            cwd=release_root,
            env=environment,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    assert installed.returncode == 0, installed.stdout + installed.stderr
    assert not list(runtime_temp.glob("AppRestore-bootstrap.*"))

    launch_environment = environment.copy()
    launch_environment["PATH"] = (
        f"{home / '.local' / 'bin'}:{environment['PATH']}"
    )
    launched = subprocess.run(
        ["apprestore", "--version"],
        env=launch_environment,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert launched.returncode == 0, launched.stdout + launched.stderr
    assert launched.stdout.strip() == "0.1.4"
