from __future__ import annotations

import hashlib
import os
import re
import tempfile
import uuid
from pathlib import Path

from .catalog import (
    installed_bundle_ids,
    load_imazing_app_records,
    load_imazing_catalog,
    lookup_itunes_store_id,
    parse_missing_apps,
    parse_offloaded_apps,
    search_app_catalogs,
)
from .ipa import IpaError, find_exact_ipa, read_ipa_metadata, scan_ipas, validate_bundle_id
from .known_apps import load_known_apps, remember_known_app
from .models import Device, IpaMetadata, MissingApp, OffloadedApp, VerifiedIpa
from .paths import (
    cache_dir,
    imazing_catalog_candidates,
    ipa_library_dir,
    ipa_search_roots,
)
from .tools import AppRestoreTools, ToolUnavailable


class AppRestoreError(RuntimeError):
    pass


class AppRestoreService:
    def __init__(
        self,
        tools: AppRestoreTools | None = None,
        *,
        library: Path | None = None,
        cache: Path | None = None,
    ) -> None:
        self.tools = tools or AppRestoreTools()
        self.library = (library or ipa_library_dir()).expanduser()
        self.cache = (cache or cache_dir()).expanduser()
        self.library.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.cache.mkdir(mode=0o700, parents=True, exist_ok=True)
        self._make_private(self.library, directory=True)
        self._make_private(self.cache, directory=True)

    @staticmethod
    def _make_private(path: Path, *, directory: bool = False) -> None:
        try:
            path.chmod(0o700 if directory else 0o600)
        except OSError:
            pass

    def devices(self) -> list[Device]:
        result: list[Device] = []
        for udid in self.tools.list_udids():
            try:
                result.append(self.tools.device_info(udid))
            except Exception:
                result.append(Device(udid=udid))
        return result

    def scan_local(self) -> tuple[list[IpaMetadata], list[tuple[Path, str]]]:
        return scan_ipas(ipa_search_roots(self.library))

    def offloaded(self, udid: str) -> list[OffloadedApp]:
        local, _ = self.scan_local()
        catalog = load_imazing_catalog(imazing_catalog_candidates())
        payload = self.tools.list_apps(udid)
        return parse_offloaded_apps(payload, local, catalog)

    def missing(self, udid: str) -> list[MissingApp]:
        """
        Apps absent from the phone entirely (no icon / no placeholder).

        Sources: iMazing Apps.plist history, local IPA library, and known-apps.json.
        Currently installed apps (including offloaded placeholders) are excluded.
        """
        local, _ = self.scan_local()
        records = load_imazing_app_records(imazing_catalog_candidates())
        payload = self.tools.list_apps(udid)
        present = installed_bundle_ids(payload)
        apps = parse_missing_apps(
            installed=present,
            imazing_records=records,
            local_ipas=local,
        )
        return self._merge_known_missing(apps, present)

    def _merge_known_missing(
        self,
        apps: list[MissingApp],
        installed: set[str],
    ) -> list[MissingApp]:
        by_bundle = {app.bundle_id: app for app in apps if app.bundle_id}
        by_store = {app.store_id: app for app in apps if app.store_id}
        merged = list(apps)
        for item in load_known_apps():
            store_id = str(item.get("storeId") or "")
            bundle_id = str(item.get("bundleId") or "").strip()
            if not store_id.isdigit():
                continue
            if bundle_id and bundle_id in installed:
                continue
            if bundle_id and bundle_id in by_bundle:
                existing = by_bundle[bundle_id]
                if existing.store_id is None:
                    enriched = MissingApp(
                        bundle_id=existing.bundle_id,
                        name=existing.name,
                        version=existing.version,
                        store_id=store_id,
                        store_match="known",
                        local_ipa=existing.local_ipa,
                        source=existing.source,
                    )
                    merged = [
                        enriched if app.bundle_id == bundle_id else app
                        for app in merged
                    ]
                    by_store[store_id] = enriched
                continue
            if store_id in by_store:
                continue
            app = MissingApp(
                bundle_id=bundle_id,
                name=str(item.get("name") or bundle_id or f"App Store {store_id}"),
                version=str(item.get("version") or "?"),
                store_id=store_id,
                store_match="known",
                local_ipa=self.find_local(bundle_id) if bundle_id else None,
                source="known",
            )
            merged.append(app)
            by_store[store_id] = app
            if bundle_id:
                by_bundle[bundle_id] = app
        merged.sort(key=lambda item: item.name.casefold())
        return merged

    def search_apps(self, term: str, *, limit: int = 10) -> list[dict[str, str]]:
        """Search by name via iTunes + IPA Filezone archive (no ipatool)."""
        try:
            return search_app_catalogs(term, limit=limit)
        except ValueError as exc:
            raise AppRestoreError(str(exc)) from exc

    def find_local(self, bundle_id: str) -> Path | None:
        entries, _ = self.scan_local()
        return find_exact_ipa(bundle_id, entries)

    def authenticate(self, email: str) -> None:
        normalized = email.strip()
        if not normalized or "@" not in normalized:
            raise AppRestoreError("a valid Apple ID email is required")
        self.tools.ipatool_login(normalized)

    def _build_download_attempts(
        self,
        bundle_id: str,
        store_id: str | None,
        *,
        lookup_store_id: bool = True,
    ) -> list[tuple[str, str, bool]]:
        """Return (kind, value, purchase) attempts in priority order."""
        resolved = store_id
        if not resolved and lookup_store_id:
            print(f"  looking up App Store ID for {bundle_id}…")
            resolved = lookup_itunes_store_id(bundle_id)
            if resolved:
                print(f"  found App Store ID {resolved}")
            else:
                print(
                    "  App Store lookup found nothing "
                    "(removed from store or unavailable in this region)"
                )

        attempts: list[tuple[str, str, bool]] = []
        # Prefer numeric App Store ID: skips bundle lookup and works for many
        # delisted apps that are still in purchase history.
        if resolved:
            attempts.append(("store", resolved, False))
            attempts.append(("store", resolved, True))
        attempts.append(("bundle", bundle_id, False))
        attempts.append(("bundle", bundle_id, True))
        return attempts

    def _verified_download(
        self,
        bundle_id: str,
        *,
        store_id: str | None,
        temp_dir: Path,
        lookup_store_id: bool = True,
    ) -> tuple[Path, IpaMetadata]:
        expected = validate_bundle_id(bundle_id)
        if not self.tools.ipatool_authenticated():
            raise AppRestoreError("ipatool is not authenticated; run `apprestore auth`")

        attempts = self._build_download_attempts(
            expected,
            store_id,
            lookup_store_id=lookup_store_id,
        )
        errors: list[str] = []

        for attempt_number, (kind, value, purchase) in enumerate(attempts, 1):
            label = (
                f"{kind}={value}"
                + (" with --purchase" if purchase else " without --purchase")
            )
            print(f"  try {attempt_number}/{len(attempts)}: {label}")
            attempt_dir = temp_dir / f"attempt-{attempt_number}-{uuid.uuid4().hex}"
            attempt_dir.mkdir(mode=0o700)
            output = attempt_dir / "download.ipa"
            try:
                ok = self.tools.download_ipa(
                    output,
                    store_id=value if kind == "store" else None,
                    bundle_id=value if kind == "bundle" else None,
                    purchase=purchase,
                )
            except (ToolUnavailable, ValueError) as exc:
                errors.append(f"{label}: {exc}")
                continue
            if not ok:
                errors.append(f"{label}: ipatool failed")
                continue

            candidates = (
                [output]
                if output.is_file() and not output.is_symlink()
                else []
            )
            if len(candidates) != 1:
                errors.append(f"{label}: IPA output missing or ambiguous")
                continue
            candidate = candidates[0]
            try:
                metadata = read_ipa_metadata(candidate)
            except IpaError as exc:
                errors.append(f"{label}: {exc}")
                continue
            if metadata.bundle_id != expected:
                errors.append(
                    f"{label}: got bundle ID {metadata.bundle_id!r}, "
                    f"expected {expected!r}"
                )
                continue
            return candidate, metadata

        detail = "; ".join(errors) if errors else "ipatool did not produce an IPA"
        raise AppRestoreError(
            f"could not download {expected} ({detail}). "
            "Typical causes: app removed from the App Store, unavailable for this "
            "Apple ID region, or no purchase/license. If you already have the IPA "
            "(e.g. from iMazing), put it in the AppRestore library and retry."
        )

    @staticmethod
    def _safe_version(value: str) -> str:
        cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip(".-")
        return cleaned[:48] or "unknown"

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def _verify_ipa(
        self,
        ipa: str | Path,
        *,
        expected_bundle_id: str | None = None,
    ) -> VerifiedIpa:
        path = Path(ipa).expanduser()
        metadata = read_ipa_metadata(path)
        before = metadata.path.stat()
        if expected_bundle_id is not None:
            expected = validate_bundle_id(expected_bundle_id)
            if metadata.bundle_id != expected:
                raise AppRestoreError(
                    f"refusing to install {metadata.bundle_id!r}; expected {expected!r}"
                )
        digest = self._sha256(metadata.path)
        after = metadata.path.stat()
        if (
            before.st_size != after.st_size
            or before.st_mtime_ns != after.st_mtime_ns
            or getattr(before, "st_ino", None) != getattr(after, "st_ino", None)
        ):
            raise AppRestoreError("IPA changed while it was being verified")
        confirmed = read_ipa_metadata(metadata.path)
        if (
            confirmed.bundle_id != metadata.bundle_id
            or confirmed.size != metadata.size
        ):
            raise AppRestoreError("IPA changed after it was verified")
        return VerifiedIpa(metadata=metadata, sha256=digest, modified_ns=after.st_mtime_ns)

    def download(
        self,
        bundle_id: str,
        store_id: str | None = None,
        *,
        lookup_store_id: bool = True,
    ) -> Path:
        expected = validate_bundle_id(bundle_id)
        with tempfile.TemporaryDirectory(
            prefix=".apprestore-download-",
            dir=self.library,
        ) as temporary_dir:
            temporary, metadata = self._verified_download(
                expected,
                store_id=store_id,
                temp_dir=Path(temporary_dir),
                lookup_store_id=lookup_store_id,
            )

            downloaded_hash = self._sha256(temporary)
            version = self._safe_version(metadata.version)
            target = self.library / (
                f"{expected}-{version}-{downloaded_hash[:12]}.ipa"
            )
            if target.is_symlink():
                raise AppRestoreError(f"refusing to replace symlink: {target}")
            if target.exists():
                if not target.is_file():
                    raise AppRestoreError(f"IPA target is not a file: {target}")
                if self._sha256(target) == downloaded_hash:
                    self._verify_ipa(target, expected_bundle_id=expected)
                    return target
                target = self.library / (
                    f"{expected}-{version}-{downloaded_hash[:12]}-"
                    f"{uuid.uuid4().hex[:8]}.ipa"
                )

            try:
                os.replace(temporary, target)
            except OSError as exc:
                raise AppRestoreError(
                    f"could not commit the verified IPA: {exc}"
                ) from exc
            self._make_private(target)
            verified = self._verify_ipa(target, expected_bundle_id=expected)
            if verified.metadata.bundle_id != expected:
                raise AppRestoreError("internal verification failed after moving the IPA")
            return target

    def download_by_store_id(self, store_id: str) -> Path:
        """Download an IPA by App Store ID; bundle ID is read from the IPA."""
        resolved = str(store_id).strip()
        if not resolved.isdigit() or int(resolved) <= 0:
            raise AppRestoreError("store_id must be a positive integer")
        if not self.tools.ipatool_authenticated():
            raise AppRestoreError("ipatool is not authenticated; run `apprestore auth`")

        with tempfile.TemporaryDirectory(
            prefix=".apprestore-download-",
            dir=self.library,
        ) as temporary_dir:
            temp_root = Path(temporary_dir)
            errors: list[str] = []
            temporary: Path | None = None
            metadata: IpaMetadata | None = None
            for attempt_number, purchase in enumerate((False, True), 1):
                label = (
                    f"store={resolved}"
                    + (" with --purchase" if purchase else " without --purchase")
                )
                print(f"  try {attempt_number}/2: {label}")
                attempt_dir = temp_root / f"attempt-{attempt_number}-{uuid.uuid4().hex}"
                attempt_dir.mkdir(mode=0o700)
                output = attempt_dir / "download.ipa"
                try:
                    ok = self.tools.download_ipa(
                        output,
                        store_id=resolved,
                        purchase=purchase,
                    )
                except (ToolUnavailable, ValueError) as exc:
                    errors.append(f"{label}: {exc}")
                    continue
                if not ok:
                    errors.append(f"{label}: ipatool failed")
                    continue
                if not output.is_file() or output.is_symlink():
                    errors.append(f"{label}: IPA output missing")
                    continue
                try:
                    metadata = read_ipa_metadata(output)
                except IpaError as exc:
                    errors.append(f"{label}: {exc}")
                    continue
                temporary = output
                break

            if temporary is None or metadata is None:
                detail = "; ".join(errors) if errors else "ipatool did not produce an IPA"
                raise AppRestoreError(
                    f"could not download App Store ID {resolved} ({detail}). "
                    "Typical causes: app removed from the App Store, unavailable "
                    "for this Apple ID region, or no purchase/license."
                )

            downloaded_hash = self._sha256(temporary)
            version = self._safe_version(metadata.version)
            expected = metadata.bundle_id
            target = self.library / (
                f"{expected}-{version}-{downloaded_hash[:12]}.ipa"
            )
            if target.is_symlink():
                raise AppRestoreError(f"refusing to replace symlink: {target}")
            if target.exists():
                if not target.is_file():
                    raise AppRestoreError(f"IPA target is not a file: {target}")
                if self._sha256(target) == downloaded_hash:
                    self._verify_ipa(target, expected_bundle_id=expected)
                    remember_known_app(
                        store_id=resolved,
                        bundle_id=expected,
                        name=metadata.name,
                        version=metadata.version,
                    )
                    return target
                target = self.library / (
                    f"{expected}-{version}-{downloaded_hash[:12]}-"
                    f"{uuid.uuid4().hex[:8]}.ipa"
                )

            try:
                os.replace(temporary, target)
            except OSError as exc:
                raise AppRestoreError(
                    f"could not commit the verified IPA: {exc}"
                ) from exc
            self._make_private(target)
            verified = self._verify_ipa(target, expected_bundle_id=expected)
            remember_known_app(
                store_id=resolved,
                bundle_id=verified.metadata.bundle_id,
                name=verified.metadata.name,
                version=verified.metadata.version,
            )
            return target

    def restore_by_store_id(self, udid: str, store_id: str) -> str:
        print(
            f"  downloading via App Store ID {store_id} "
            "(bundle ID will be read from the IPA)…"
        )
        ipa = self.download_by_store_id(store_id)
        print(f"  downloaded: {ipa}")
        print("  installing on iPhone…")
        metadata = self.install(udid, ipa)
        return f"installed {metadata.name} {metadata.version}"

    def install(
        self,
        udid: str,
        ipa: str | Path,
        *,
        expected_bundle_id: str | None = None,
    ) -> IpaMetadata:
        verified = self._verify_ipa(
            ipa,
            expected_bundle_id=expected_bundle_id,
        )
        self.tools.install_ipa(udid, verified.metadata.path)
        return verified.metadata

    def resolve_store_id(self, udid: str, app: OffloadedApp) -> str | None:
        if app.store_id:
            return app.store_id
        print("  reading App Store ID from iPhone metadata…")
        store_id = self.tools.lookup_store_id_on_device(udid, app.bundle_id)
        if store_id:
            print(f"  found App Store ID on device: {store_id}")
            return store_id
        print("  looking up App Store ID online…")
        store_id = lookup_itunes_store_id(app.bundle_id)
        if store_id:
            print(f"  found App Store ID online: {store_id}")
        else:
            print("  App Store ID not found on device or online")
        return store_id

    def restore_offloaded(
        self,
        udid: str,
        app: OffloadedApp,
        *,
        try_device_redownload: bool = True,
    ) -> str:
        """
        Restore one offloaded app.

        Returns a short human-readable result status.
        """
        if app.local_ipa:
            print(f"  using local IPA: {app.local_ipa}")
            metadata = self.install(
                udid,
                app.local_ipa,
                expected_bundle_id=app.bundle_id,
            )
            return f"installed {metadata.name} {metadata.version} from local IPA"

        if try_device_redownload:
            print("  asking iPhone to redownload the app…")
        if try_device_redownload and self.tools.device_request_redownload(
            udid,
            app.bundle_id,
        ):
            # Give the phone a moment to flip placeholder state.
            import time

            time.sleep(2)
            if not self.tools.app_still_offloaded(udid, app.bundle_id):
                return "iPhone started/finished redownload"

        if not self.tools.ipatool_authenticated():
            raise AppRestoreError("ipatool is not authenticated; run `apprestore auth`")

        store_id = self.resolve_store_id(udid, app)
        if store_id:
            print(
                f"  downloading via App Store ID {store_id} "
                "(works even when the app page is gone)…"
            )
        else:
            print("  downloading via bundle ID…")

        ipa = self.download(
            app.bundle_id,
            store_id,
            lookup_store_id=False,
        )
        print(f"  downloaded: {ipa}")
        print("  installing on iPhone…")
        metadata = self.install(
            udid,
            ipa,
            expected_bundle_id=app.bundle_id,
        )
        return f"installed {metadata.name} {metadata.version}"

    def resolve_missing_store_id(self, app: MissingApp) -> str | None:
        if app.store_id:
            return app.store_id
        print("  looking up App Store ID online…")
        store_id = lookup_itunes_store_id(app.bundle_id)
        if store_id:
            print(f"  found App Store ID online: {store_id}")
        else:
            print("  App Store ID not found online")
        return store_id

    def restore_missing(self, udid: str, app: MissingApp) -> str:
        """
        Restore an app that is not on the device at all.

        Unlike offloaded restore, there is no placeholder to ask iOS to
        redownload — only local IPA or App Store download via ipatool.
        """
        if app.local_ipa and app.bundle_id:
            print(f"  using local IPA: {app.local_ipa}")
            metadata = self.install(
                udid,
                app.local_ipa,
                expected_bundle_id=app.bundle_id,
            )
            return f"installed {metadata.name} {metadata.version} from local IPA"

        if not app.bundle_id:
            if not app.store_id:
                raise AppRestoreError("need a bundle ID or App Store ID")
            return self.restore_by_store_id(udid, app.store_id)

        if not self.tools.ipatool_authenticated():
            raise AppRestoreError("ipatool is not authenticated; run `apprestore auth`")

        store_id = self.resolve_missing_store_id(app)
        if store_id:
            print(
                f"  downloading via App Store ID {store_id} "
                "(works even when the app page is gone)…"
            )
        else:
            print("  downloading via bundle ID…")

        ipa = self.download(
            app.bundle_id,
            store_id,
            lookup_store_id=False,
        )
        print(f"  downloaded: {ipa}")
        print("  installing on iPhone…")
        metadata = self.install(
            udid,
            ipa,
            expected_bundle_id=app.bundle_id,
        )
        if store_id:
            remember_known_app(
                store_id=store_id,
                bundle_id=metadata.bundle_id,
                name=metadata.name,
                version=metadata.version,
            )
        return f"installed {metadata.name} {metadata.version}"
