"""R2 (LEGAL.md): no ``ipatool download`` anywhere carries ``--purchase``.

Two checks: a static scan of every shipped Python file, and a run of the real
``AppRestoreTools`` under every license-taking path with a recording runner.
"""

from __future__ import annotations

import ast
import json
import shutil
from pathlib import Path
from unittest.mock import patch

import pytest

from apprestore_core.models import CommandResult
from apprestore_core.service import AppRestoreService
from apprestore_core.tools import AppRestoreTools
from tests.helpers import make_ipa

ROOT = Path(__file__).resolve().parents[1]
SHIPPED = ("apprestore_core", "apprestore_gui", "scripts", "packaging")
SHIPPED_FILES = ("apprestore.py",)


def _python_files() -> list[Path]:
    files = [ROOT / name for name in SHIPPED_FILES if (ROOT / name).is_file()]
    for folder in SHIPPED:
        files.extend(sorted((ROOT / folder).rglob("*.py")))
    return files


def test_purchase_flag_appears_only_as_a_tripwire() -> None:
    """The literal ``"--purchase"`` may only be tested for (``in``/``not in``)."""

    offenders: list[str] = []
    for path in _python_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        allowed: set[int] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Compare) and all(
                isinstance(op, (ast.In, ast.NotIn)) for op in node.ops
            ):
                if isinstance(node.left, ast.Constant):
                    allowed.add(id(node.left))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Constant)
                and node.value == "--purchase"
                and id(node) not in allowed
            ):
                offenders.append(f"{path.relative_to(ROOT)}:{node.lineno}")
    assert offenders == []


def test_no_shipped_code_passes_purchase_keyword_to_download() -> None:
    offenders: list[str] = []
    for path in _python_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            if name == "download_ipa" and any(kw.arg == "purchase" for kw in node.keywords):
                offenders.append(f"{path.relative_to(ROOT)}:{node.lineno}")
    assert offenders == []


class FakeIpatool:
    """Recording runner: the license appears only after ``ipatool purchase``."""

    def __init__(self, ipa: Path) -> None:
        self.ipa = ipa
        self.calls: list[tuple[str, ...]] = []
        self.licensed = False

    def run(self, args, **_kwargs) -> CommandResult:
        command = tuple(str(arg) for arg in args)
        self.calls.append(command)
        if "purchase" in command:
            self.licensed = True
            return CommandResult(command, 0, json.dumps({"success": True}) + "\n", "")
        if "download" in command:
            if not self.licensed:
                return CommandResult(command, 1, "", 'ERR error="license is required" success=false')
            output = Path(command[command.index("--output") + 1])
            output.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(self.ipa, output)
            return CommandResult(command, 0, "", "")
        if "auth" in command:
            return CommandResult(command, 0, json.dumps({"email": "x", "success": True}), "")
        return CommandResult(command, 0, "", "")

    def downloads(self) -> list[tuple[str, ...]]:
        return [call for call in self.calls if "download" in call]


@pytest.fixture()
def real_tools(tmp_path: Path):
    runner = FakeIpatool(make_ipa(tmp_path / "good.ipa"))
    tools = AppRestoreTools(runner)  # type: ignore[arg-type]
    with patch("apprestore_core.tools.resolve_tool", return_value="ipatool"), patch.object(
        AppRestoreTools, "_tool", return_value="ipatool"
    ), patch.object(AppRestoreTools, "_ipatool_env", return_value={}), patch(
        "apprestore_core.service.lookup_itunes_store_id", return_value=None
    ), patch("apprestore_core.service.remember_known_app"):
        yield tools, runner, tmp_path


def _service(tools: AppRestoreTools, root: Path) -> AppRestoreService:
    return AppRestoreService(tools=tools, library=root / "lib", cache=root / "cache")


@pytest.mark.parametrize("path", ["by_store_id", "by_bundle"])
def test_cli_acquire_license_paths_use_separate_purchase(real_tools, path: str) -> None:
    tools, runner, root = real_tools
    service = _service(tools, root)
    if path == "by_store_id":
        service.download_by_store_id("1234567890", acquire_license=True)
    else:
        service.download("com.example.alpha", "1234567890", acquire_license=True)
    purchases = [call for call in runner.calls if "purchase" in call]
    assert purchases and all("download" not in call for call in purchases)
    assert all("--format" in call and "json" in call for call in purchases)
    assert runner.downloads()
    assert all("--purchase" not in call for call in runner.downloads())


def test_gui_gate_uses_separate_purchase(real_tools, monkeypatch: pytest.MonkeyPatch) -> None:
    from apprestore_gui.license_gate import run_with_free_license

    tools, runner, root = real_tools
    service = _service(tools, root)
    monkeypatch.setattr(tools, "account_country", lambda: "ru")
    run_with_free_license(
        "1234567890",
        lambda: service.download_by_store_id("1234567890"),
        tools=tools,
        lookup=lambda _s, _c: {"price": 0.0, "bundleId": "com.example.alpha", "country": "ru"},
        journal=root / "j.jsonl",
    )
    assert [call for call in runner.calls if "purchase" in call]
    assert all("--purchase" not in call for call in runner.downloads())
