from __future__ import annotations

import struct
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ICONS = ROOT / "packaging" / "icons"
RELEASE = ROOT / ".github" / "workflows" / "release.yml"
GUI_NAMES = ("AppRestore-GUI-Windows.zip", "AppRestore-GUI-macOS.zip")
INTEL_MAC_NAME = "AppRestore-GUI-macOS-Intel.zip"


def test_windows_ico_has_all_sizes_16_to_256():
    data = (ICONS / "AppRestore.ico").read_bytes()
    reserved, kind, count = struct.unpack_from("<HHH", data)
    assert (reserved, kind) == (0, 1)
    sizes = set()
    for i in range(count):
        w, h, *_rest, size, offset = struct.unpack_from("<BBBBHHII", data, 6 + 16 * i)
        sizes.add(w or 256)
        assert data[offset:offset + 8] == b"\x89PNG\r\n\x1a\n"
        assert offset + size <= len(data)
    assert {16, 24, 32, 48, 64, 128, 256} <= sizes


def test_macos_icns_has_all_retina_sizes():
    data = (ICONS / "AppRestore.icns").read_bytes()
    assert data[:4] == b"icns"
    assert struct.unpack_from(">I", data, 4)[0] == len(data)
    types, pos = set(), 8
    while pos < len(data):
        code, length = data[pos:pos + 4].decode(), struct.unpack_from(">I", data, pos + 4)[0]
        assert data[pos + 8:pos + 16] == b"\x89PNG\r\n\x1a\n"
        types.add(code)
        pos += length
    assert {"icp4", "icp5", "icp6", "ic07", "ic08", "ic09", "ic10", "ic11", "ic12", "ic13", "ic14"} <= types


def test_preview_png_is_1024():
    head = (ICONS / "AppRestore-1024.png").read_bytes()[:24]
    assert struct.unpack(">II", head[16:24]) == (1024, 1024)


def test_spec_uses_icons_and_window_icon_is_bundled():
    spec = (ROOT / "packaging" / "apprestore-gui.spec").read_text(encoding="utf-8")
    assert '"AppRestore.ico"' in spec and '"AppRestore.icns"' in spec
    assert "icon=None" not in spec
    assert (ROOT / "apprestore_gui" / "resources" / "icons" / "app-icon-256.png").is_file()
    app = (ROOT / "apprestore_gui" / "app.py").read_text(encoding="utf-8")
    assert "setWindowIcon(app_icon())" in app
    assert "OpenConsole.exe" in spec and "winpty-agent.exe" in spec


def test_updater_asset_names_match_release_workflow():
    from apprestore_gui.updater import ASSET_BY_PLATFORM

    assert tuple(ASSET_BY_PLATFORM[p] for p in ("win32", "darwin")) == GUI_NAMES
    from apprestore_gui.updater import INTEL_MAC_ASSET, asset_name_for

    assert INTEL_MAC_ASSET == INTEL_MAC_NAME
    assert asset_name_for("darwin", "x86_64") == INTEL_MAC_NAME
    assert asset_name_for("darwin", "arm64") == GUI_NAMES[1]
    assert asset_name_for("darwin") == GUI_NAMES[1]
    workflow = RELEASE.read_text(encoding="utf-8")
    assert "asset: " + INTEL_MAC_NAME in workflow and "runner: macos-15-intel" in workflow


def test_release_builds_both_gui_apps_and_publishes_them_with_checksums():
    workflow = RELEASE.read_text(encoding="utf-8")
    assert "  gui-windows:\n" in workflow and "  gui-macos:\n" in workflow
    assert workflow.count("python packaging/package_gui.py --out gui-dist --expect-version") == 2
    publish = workflow.split("  publish:\n", 1)[1]
    assert "      - gui-windows\n" in publish and "      - gui-macos\n" in publish
    assert 'gui_windows="AppRestore-GUI-Windows.zip"' in publish
    assert 'gui_macos="AppRestore-GUI-macOS.zip"' in publish
    assert f'gui_macos_intel="{INTEL_MAC_NAME}"' in publish
    assert '== 10 ]]' in publish
    assert (
        'sha256sum "$gui_windows" "$gui_macos" "$gui_macos_intel" '
        '"$cli_windows" "$cli_macos" "$cli_macos_intel" >> SHA256SUMS.txt'
    ) in publish
    assert "sha256sum -c SHA256SUMS.txt" in publish
    upload = publish[publish.index('gh release upload "$tag"'):publish.index('gh release edit "$tag"')]
    assert '"${ASSET_DIR}/${gui_windows}"' in upload
    assert '"${ASSET_DIR}/${gui_macos}"' in upload
    assert '"${ASSET_DIR}/${gui_macos_intel}"' in upload
    # The checksum file is extended before anything is uploaded.
    assert publish.index(">> SHA256SUMS.txt") < publish.index('gh release create "$tag"')


def test_gui_release_jobs_use_locked_runtime():
    workflow = RELEASE.read_text(encoding="utf-8")
    for job, end in (("gui-windows", "gui-macos"), ("gui-macos", "release-assets")):
        section = workflow.split(f"  {job}:\n", 1)[1].split(f"  {end}:\n", 1)[0]
        assert "--require-hashes" in section
        assert "requirements/runtime.lock" in section
        assert "requirements/gui-build.txt" in section
        assert "python scripts/build-release.py" not in section


def test_readme_offers_both_install_paths():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "Графическая версия" in readme
    assert "Терминальная версия" in readme
    for name in GUI_NAMES:
        assert name in readme


CLI_NAMES = {
    ("win32", "AMD64"): "AppRestore-CLI-Windows.zip",
    ("darwin", "arm64"): "AppRestore-CLI-macOS.zip",
    ("darwin", "x86_64"): "AppRestore-CLI-macOS-Intel.zip",
}


def test_cli_asset_names_cover_every_platform():
    import importlib.util
    import sys

    spec = importlib.util.spec_from_file_location("package_cli", ROOT / "packaging" / "package_cli.py")
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(ROOT / "packaging"))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(ROOT / "packaging"))
    for (platform_name, machine), name in CLI_NAMES.items():
        assert module.cli_asset_name(platform_name, machine) == name
    assert module.cli_asset_name("linux", "x86_64") is None


def test_release_builds_and_publishes_a_standalone_cli_for_every_platform():
    workflow = RELEASE.read_text(encoding="utf-8")
    assert workflow.count("pyinstaller packaging/apprestore-cli.spec") == 2
    assert workflow.count("python packaging/package_cli.py --dist dist-cli --out cli-dist --expect-version") == 2
    windows = workflow.split("  gui-windows:\n", 1)[1].split("  gui-macos:\n", 1)[0]
    assert "cli-dist/AppRestore-CLI-Windows.zip" in windows
    macos = workflow.split("  gui-macos:\n", 1)[1].split("  release-assets:\n", 1)[0]
    assert "cli_asset: AppRestore-CLI-macOS.zip" in macos
    assert "cli_asset: AppRestore-CLI-macOS-Intel.zip" in macos
    publish = workflow.split("  publish:\n", 1)[1]
    upload = publish[publish.index('gh release upload "$tag"'):publish.index('gh release edit "$tag"')]
    for variable, name in (
        ("cli_windows", "AppRestore-CLI-Windows.zip"),
        ("cli_macos", "AppRestore-CLI-macOS.zip"),
        ("cli_macos_intel", "AppRestore-CLI-macOS-Intel.zip"),
    ):
        assert f'{variable}="{name}"' in publish
        assert f'"${{ASSET_DIR}}/${{{variable}}}"' in upload
    for artifact in ("apprestore-cli-windows", "apprestore-cli-macos", "apprestore-cli-macos-intel"):
        assert f"name: {artifact}-${{{{ needs.validate-tag.outputs.version }}}}" in publish


def test_cli_spec_is_a_console_build_without_qt():
    spec = (ROOT / "packaging" / "apprestore-cli.spec").read_text(encoding="utf-8")
    assert "console=True" in spec
    assert '"PySide6"' in spec and '"apprestore_gui"' in spec
    entry = (ROOT / "packaging" / "apprestore_cli_entry.py").read_text(encoding="utf-8")
    # pymobiledevice3 children must be dispatched before the CLI parses argv.
    assert entry.index("maybe_dispatch") < entry.index("cli_main()")
