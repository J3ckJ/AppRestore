"""R2 + "the gate cannot be skipped" (LEGAL.md, Лена).

* No ``ipatool download`` anywhere carries ``--purchase``.
* ``purchase_license`` is called from ``apprestore_core/license_gate.py`` only,
  and refuses to run without the gate's one-shot grant.
* Every front end (CLI ``--acquire-license``, the Widgets «Получить бесплатно»
  checkbox, Qt Quick) takes a license through the gate: paid apps and an
  exhausted limit never reach ``purchase``.
"""

from __future__ import annotations

import ast
import datetime as dt
import json
import shutil
from pathlib import Path
from unittest.mock import patch

import pytest

from apprestore_core.models import CommandResult, MissingApp
from apprestore_core.service import AppRestoreService
from apprestore_core.tools import AppRestoreTools
from tests.helpers import make_ipa

ROOT = Path(__file__).resolve().parents[1]
SHIPPED = ("apprestore_core", "apprestore_gui", "scripts", "packaging")
SHIPPED_FILES = ("apprestore.py",)
STORE = "1234567890"


def _python_files() -> list[Path]:
    files = [ROOT / name for name in SHIPPED_FILES if (ROOT / name).is_file()]
    for folder in SHIPPED:
        files.extend(sorted((ROOT / folder).rglob("*.py")))
    return files


def _trees():
    for path in _python_files():
        yield path, ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


# ------------------------------------------------------------------ static


def test_purchase_flag_appears_only_as_a_tripwire() -> None:
    """The literal ``"--purchase"`` may only be tested for (``in``/``not in``)."""

    offenders: list[str] = []
    for path, tree in _trees():
        allowed: set[int] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Compare) and all(
                isinstance(op, (ast.In, ast.NotIn)) for op in node.ops
            ) and isinstance(node.left, ast.Constant):
                allowed.add(id(node.left))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and node.value == "--purchase" and id(node) not in allowed:
                offenders.append(f"{path.relative_to(ROOT)}:{node.lineno}")
    assert offenders == []


def test_no_shipped_code_passes_purchase_keyword_to_download() -> None:
    offenders = [
        f"{path.relative_to(ROOT)}:{node.lineno}"
        for path, tree in _trees()
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and (node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", ""))
        == "download_ipa"
        and any(kw.arg == "purchase" for kw in node.keywords)
    ]
    assert offenders == []


def test_purchase_license_is_called_from_the_gate_only() -> None:
    callers: list[str] = []
    for path, tree in _trees():
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
                if name == "purchase_license":
                    callers.append(str(path.relative_to(ROOT)))
            # getattr(tools, "purchase_license") would dodge the call check
            if isinstance(node, ast.Constant) and node.value == "purchase_license":
                callers.append(f"{path.relative_to(ROOT)} (string)")
    assert callers == ["apprestore_core/license_gate.py"]


def test_grants_are_minted_by_the_gate_only() -> None:
    minters = [
        str(path.relative_to(ROOT))
        for path, tree in _trees()
        for node in ast.walk(tree)
        if isinstance(node, (ast.Name, ast.Attribute))
        and (node.id if isinstance(node, ast.Name) else node.attr) in {"_mint", "_MINT_KEY"}
    ]
    assert set(minters) == {"apprestore_core/license_gate.py", "apprestore_core/purchase_grant.py"}


# ------------------------------------------------------------------ runtime


class FakeIpatool:
    """Recording runner: the license appears only after ``ipatool purchase``."""

    def __init__(self, ipa: Path) -> None:
        self.ipa = ipa
        self.calls: list[tuple[str, ...]] = []
        self.licensed = False
        self.on_output = None

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

    def purchases(self) -> list[tuple[str, ...]]:
        return [call for call in self.calls if "purchase" in call]

    def downloads(self) -> list[tuple[str, ...]]:
        return [call for call in self.calls if "download" in call]


@pytest.fixture()
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    runner = FakeIpatool(make_ipa(tmp_path / "good.ipa"))
    journal = tmp_path / "licenses_acquired.jsonl"
    monkeypatch.setenv("APPRESTORE_LICENSE_JOURNAL", str(journal))
    price = {"value": 0.0}
    from apprestore_core import license_gate

    monkeypatch.setattr(
        license_gate,
        "lookup_offer",
        lambda s, countries=None: {"storeId": s, "bundleId": "com.example.alpha",
                                   "price": price["value"], "country": "ru"},
    )
    with patch("apprestore_core.tools.resolve_tool", return_value="ipatool"), patch.object(
        AppRestoreTools, "_tool", return_value="ipatool"
    ), patch.object(AppRestoreTools, "_ipatool_env", return_value={}), patch(
        "apprestore_core.service.lookup_itunes_store_id", return_value=None
    ), patch("apprestore_core.service.remember_known_app"):
        yield runner, tmp_path, journal, price


def _fill_limit(journal: Path) -> None:
    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    journal.write_text(
        "".join(json.dumps({"time": now, "track_id": str(n), "status": "acquired"}) + "\n" for n in range(5)),
        encoding="utf-8",
    )


def _run_cli(runner: FakeIpatool, root: Path, *argv: str) -> int:
    from apprestore_core import cli

    with patch("apprestore_core.service.AppRestoreTools", lambda **_kw: AppRestoreTools(runner)):  # type: ignore[arg-type]
        return cli.main(["--ipa-dir", str(root / "lib"), "--cache-dir", str(root / "cache"), *argv])


@pytest.mark.parametrize(
    "argv",
    [
        ("download", STORE, "--acquire-license"),
        ("download", "com.example.alpha", "--store-id", STORE, "--acquire-license"),
    ],
)
def test_cli_acquire_license_free_app_goes_through_gate(world, argv) -> None:
    runner, root, journal, _price = world
    assert _run_cli(runner, root, *argv) == 0
    assert len(runner.purchases()) == 1
    assert all("download" not in call for call in runner.purchases())
    assert all("--purchase" not in call for call in runner.downloads())
    [entry] = [json.loads(line) for line in journal.read_text(encoding="utf-8").splitlines()]
    assert entry["track_id"] == STORE and entry["status"] == "acquired" and entry["mode"] is None


def test_cli_acquire_license_refuses_paid_app(world, capsys) -> None:
    runner, root, journal, price = world
    price["value"] = 4.99
    assert _run_cli(runner, root, "download", STORE, "--acquire-license") == 1
    assert runner.purchases() == []
    assert not journal.exists()
    assert "платное" in capsys.readouterr().err


def test_cli_acquire_license_refuses_over_limit(world, capsys) -> None:
    runner, root, journal, _price = world
    _fill_limit(journal)
    assert _run_cli(runner, root, "download", STORE, "--acquire-license") == 1
    assert runner.purchases() == []
    assert "Лимит" in capsys.readouterr().err
    assert len(journal.read_text(encoding="utf-8").splitlines()) == 5


def test_cli_without_flag_never_purchases(world) -> None:
    runner, root, _journal, _price = world
    assert _run_cli(runner, root, "download", STORE) == 1
    assert runner.purchases() == []


def _widgets_service(runner: FakeIpatool, root: Path):
    pytest.importorskip("PySide6")
    from apprestore_gui.service_adapter import GuiService

    gui = GuiService(demo_mode=True)
    gui.demo_mode = False
    gui._attach_service(AppRestoreService(tools=AppRestoreTools(runner), library=root / "lib", cache=root / "cache"))  # type: ignore[arg-type]
    gui.core.tools.runner = runner  # keep the recording runner, no keychain prompts
    return gui


def _widgets_calls(gui, which: str):
    """Exactly what the Widgets window calls with «Получить бесплатно» ticked."""

    from apprestore_core.models import InstalledApp

    if which == "install":  # main_window._install_selected_missing
        app = MissingApp(bundle_id="com.example.alpha", name="Alpha", store_id=STORE)
        return gui.restore_missing("UDID", app, acquire_license=True)
    app = InstalledApp(bundle_id="com.example.alpha", name="Alpha", version="1", store_id=STORE)
    return gui.download_to_library(app, acquire_license=True)  # export_dialog


@pytest.mark.parametrize("which", ["copy"])
def test_widgets_checkbox_free_app_goes_through_gate(world, which) -> None:
    runner, root, journal, _price = world
    gui = _widgets_service(runner, root)
    _widgets_calls(gui, which)
    assert len(runner.purchases()) == 1
    assert all("--purchase" not in call for call in runner.downloads())
    assert json.loads(journal.read_text(encoding="utf-8"))["mode"] == "gui"


@pytest.mark.parametrize("which", ["install", "copy"])
def test_widgets_checkbox_refuses_paid_app(world, which) -> None:
    from apprestore_core.license_gate import LicenseDenied

    runner, root, journal, price = world
    price["value"] = 0.99
    gui = _widgets_service(runner, root)
    with pytest.raises(LicenseDenied, match="платное"):
        _widgets_calls(gui, which)
    assert runner.purchases() == []
    assert not journal.exists()


@pytest.mark.parametrize("which", ["install", "copy"])
def test_widgets_checkbox_refuses_over_limit(world, which) -> None:
    from apprestore_core.license_gate import LicenseDenied

    runner, root, journal, _price = world
    _fill_limit(journal)
    gui = _widgets_service(runner, root)
    with pytest.raises(LicenseDenied, match="Лимит"):
        _widgets_calls(gui, which)
    assert runner.purchases() == []
