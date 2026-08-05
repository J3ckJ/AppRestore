from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "release.yml"
CI_WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"


def _workflow() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def test_release_can_only_start_from_a_tag_push() -> None:
    workflow = _workflow()

    assert re.search(r"(?m)^on:\n  push:\n    tags:\n      - \"v\*\"$", workflow)
    assert "workflow_dispatch:" not in workflow
    assert "release:" not in workflow


def test_release_tag_must_match_version_and_main() -> None:
    workflow = _workflow()

    assert "^v([0-9]+\\.[0-9]+\\.[0-9]+)$" in workflow
    assert '[[ "$tag" == "v${version}" ]]' in workflow
    assert "git merge-base --is-ancestor" in workflow
    assert '"$GITHUB_SHA" refs/remotes/origin/main' in workflow


def test_assets_wait_for_both_platform_gates_and_are_reproducible() -> None:
    workflow = _workflow()
    assets = workflow.split("  release-assets:\n", 1)[1].split(
        "  publish:\n", 1
    )[0]

    assert "      - windows-tests\n" in assets
    assert "      - macos-tests\n" in assets
    assert assets.count("python scripts/build-release.py") == 3
    assert 'cmp "$snapshot/$asset" "dist/$asset"' in assets
    assert "shasum -a 256 -c SHA256SUMS.txt" in assets
    assert "unzip -tq" in assets
    assert 'test ! -e "$source_root/.git"' in assets
    assert '(cd "$source_root" && python scripts/build-release.py)' in assets
    assert 'cmp "$snapshot/$asset" "$source_root/dist/$asset"' in assets


def test_windows_gate_parses_the_rendered_bootstrap() -> None:
    workflow = _workflow()
    windows = workflow.split("  windows-tests:\n", 1)[1].split(
        "  macos-tests:\n", 1
    )[0]

    assert "python scripts/build-release.py" in windows
    assert '"dist/install.ps1"' in windows
    assert '"scripts/install.ps1.in"' not in windows
    assert '          - "3.13"' in windows
    assert "--require-hashes" in windows
    assert "requirements/runtime.lock" in windows


def test_both_platform_gates_install_the_locked_runtime() -> None:
    workflow = _workflow()
    for job, end in (
        ("windows-tests", "macos-tests"),
        ("macos-tests", "release-assets"),
    ):
        section = workflow.split(f"  {job}:\n", 1)[1].split(
            f"  {end}:\n", 1
        )[0]
        assert "--require-hashes" in section
        assert "--only-binary=:all:" in section
        assert "requirements/build.lock" in section
        assert "requirements/runtime.lock" in section
        assert "requirements/test.lock" in section
        assert "--no-build-isolation" in section


def test_ci_verifies_the_deterministic_vendored_wheel_recipe() -> None:
    for path in (CI_WORKFLOW, WORKFLOW):
        workflow = path.read_text(encoding="utf-8")
        assert "requirements/wheel-build.lock" in workflow
        assert "scripts/rebuild-vendored-wheel.py --check" in workflow


def test_publish_only_consumes_verified_artifact_and_never_overwrites() -> None:
    workflow = _workflow()
    publish = workflow.split("  publish:\n", 1)[1]

    assert "      - release-assets\n" in publish
    assert "      contents: write\n" in publish
    assert (
        "actions/download-artifact@"
        "634f93cb2916e3fdff6788551b99b062d0335ce0 # v5"
    ) in publish
    assert "Release $tag already exists; refusing to overwrite it" in publish
    assert "gh release create" in publish
    assert "gh release upload" in publish
    assert "--draft" in publish
    assert "gh release edit" in publish
    assert "--draft=false" in publish
    assert "--json isDraft" in publish
    assert '[[ "$draft_state" == "true" ]]' in publish
    create = publish.index('gh release create "$tag"')
    ownership = publish.index("created_draft=true")
    upload = publish.index('gh release upload "$tag"')
    make_public = publish.index('gh release edit "$tag"')
    assert create < ownership < upload < make_public

    create_block = publish[create:ownership]
    assert '"${ASSET_DIR}/${archive}"' not in create_block
    upload_block = publish[upload:make_public]
    for asset in (
        '"${ASSET_DIR}/${archive}"',
        '"${ASSET_DIR}/install.ps1"',
        '"${ASSET_DIR}/install.sh"',
        '"${ASSET_DIR}/SHA256SUMS.txt"',
    ):
        assert asset in upload_block


def test_every_external_action_is_pinned_to_a_full_commit() -> None:
    for path in (CI_WORKFLOW, WORKFLOW):
        workflow = path.read_text(encoding="utf-8")
        uses = re.findall(r"(?m)^\s+uses:\s+([^\s]+)(?:\s+#.*)?$", workflow)

        assert uses
        for action in uses:
            assert re.fullmatch(r"[^@]+@[0-9a-f]{40}", action), (
                f"{path.name} contains an unpinned action: {action}"
            )
