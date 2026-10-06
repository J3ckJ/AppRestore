from __future__ import annotations

import struct
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ICONS = ROOT / "packaging" / "icons"
RELEASE = ROOT / ".github" / "workflows" / "release.yml"
GUI_NAMES = ("AppRestore-GUI-Windows.zip", "AppRestore-GUI-macOS.zip")


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


def test_updater_asset_names_match_release_workflow():
    from apprestore_gui.updater import ASSET_BY_PLATFORM

    assert tuple(ASSET_BY_PLATFORM[p] for p in ("win32", "darwin")) == GUI_NAMES


def test_release_builds_both_gui_apps_and_publishes_them_with_checksums():
    workflow = RELEASE.read_text(encoding="utf-8")
    assert "  gui-windows:\n" in workflow and "  gui-macos:\n" in workflow
    assert workflow.count("python packaging/package_gui.py --out gui-dist --expect-version") == 2
    publish = workflow.split("  publish:\n", 1)[1]
    assert "      - gui-windows\n" in publish and "      - gui-macos\n" in publish
    assert 'gui_windows="AppRestore-GUI-Windows.zip"' in publish
    assert 'gui_macos="AppRestore-GUI-macOS.zip"' in publish
    assert '== 6 ]]' in publish
    assert 'sha256sum "$gui_windows" "$gui_macos" >> SHA256SUMS.txt' in publish
    assert "sha256sum -c SHA256SUMS.txt" in publish
    upload = publish[publish.index('gh release upload "$tag"'):publish.index('gh release edit "$tag"')]
    assert '"${ASSET_DIR}/${gui_windows}"' in upload
    assert '"${ASSET_DIR}/${gui_macos}"' in upload
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
    assert "Программа с окном" in readme
    assert "Для терминала" in readme
    for name in GUI_NAMES:
        assert name in readme
