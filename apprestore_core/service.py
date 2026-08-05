from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
import hashlib
import os
import re
import stat
import sys
import tempfile
import time
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
from .ipa_index import IpaIndex, IpaIndexError, IpaIndexRecord
from .known_apps import load_known_apps, parse_app_store_id, remember_known_app
from .models import (
    Device,
    DeviceAppState,
    IpaMetadata,
    MissingApp,
    OffloadedApp,
    RedownloadRequestState,
    VerifiedIpa,
)
from .paths import (
    cache_dir,
    imazing_catalog_candidates,
    ipa_library_dir,
    ipa_search_roots,
)
from .tools import AppRestoreTools, InstallRequestState, ToolUnavailable


class AppRestoreError(RuntimeError):
    pass


class DownloadIdentityMismatch(AppRestoreError):
    """Downloaded IPA provably belongs to a different application."""


class AppRestoreService:
    REDOWNLOAD_START_TIMEOUT = 15.0
    REDOWNLOAD_COMPLETE_TIMEOUT = 300.0
    INSTALL_VERIFY_TIMEOUT = 90.0

    def __init__(
        self,
        tools: AppRestoreTools | None = None,
        *,
        library: Path | None = None,
        cache: Path | None = None,
        json_output: bool = False,
    ) -> None:
        self.tools = tools or AppRestoreTools(json_output=json_output)
        self.library = (library or ipa_library_dir()).expanduser()
        self.cache = (cache or cache_dir()).expanduser()
        self.library.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.cache.mkdir(mode=0o700, parents=True, exist_ok=True)
        self._make_private(self.library, directory=True)
        self._make_private(self.cache, directory=True)
        try:
            self._ipa_index: IpaIndex | None = IpaIndex(
                self.cache / "ipa-index.sqlite3"
            )
        except IpaIndexError:
            # Cache failures may cost performance, never functionality.
            self._ipa_index = None
        self._local_scan_cache: tuple[
            list[IpaMetadata],
            list[tuple[Path, str]],
        ] | None = None

    @staticmethod
    def _make_private(path: Path, *, directory: bool = False) -> None:
        try:
            path.chmod(0o700 if directory else 0o600)
        except OSError:
            pass

    def _cache_verified_ipa(self, verified: VerifiedIpa) -> None:
        if self._ipa_index is None:
            return
        try:
            observed = verified.metadata.path.lstat()
            if (
                verified.metadata.path.is_symlink()
                or not stat.S_ISREG(observed.st_mode)
                or observed.st_size != verified.metadata.size
                or observed.st_mtime_ns != verified.modified_ns
            ):
                return
            self._ipa_index.upsert(
                IpaIndexRecord.from_metadata(
                    verified.metadata,
                    mtime_ns=observed.st_mtime_ns,
                    sha256=verified.sha256,
                )
            )
        except (IpaIndexError, OSError, ValueError):
            pass

    @staticmethod
    def _remember_app_best_effort(
        *,
        store_id: str | None,
        bundle_id: str | None,
        name: str | None,
        version: str | None,
        provenance: str,
        status: str,
    ) -> None:
        """Persist useful history without changing an already-successful action."""

        try:
            remember_known_app(
                store_id=store_id,
                bundle_id=bundle_id,
                name=name,
                version=version,
                provenance=provenance,
                status=status,
            )
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            print(
                "  warning: the app operation succeeded, but local history "
                f"could not be updated: {exc}",
                file=sys.stderr,
            )

    def devices(self) -> list[Device]:
        result: list[Device] = []
        for udid in self.tools.list_udids():
            try:
                result.append(self.tools.device_info(udid))
            except Exception:
                result.append(Device(udid=udid))
        return result

    def scan_local(
        self,
        *,
        refresh: bool = False,
    ) -> tuple[list[IpaMetadata], list[tuple[Path, str]]]:
        # One CLI/menu operation often asks for the same inventory several
        # times.  Cache it for the service lifetime; an explicit scan refreshes
        # the view after users add/remove IPA files.
        if refresh or self._local_scan_cache is None:
            entries, errors = scan_ipas(
                ipa_search_roots(self.library),
                index=self._ipa_index,
            )
            self._local_scan_cache = (entries, errors)
        return self._local_scan_cache

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
        return self._merge_known_missing(apps, present, local)

    def _merge_known_missing(
        self,
        apps: list[MissingApp],
        installed: set[str],
        local_ipas: list[IpaMetadata] | None = None,
    ) -> list[MissingApp]:
        by_bundle = {app.bundle_id: app for app in apps if app.bundle_id}
        by_store = {app.store_id: app for app in apps if app.store_id}
        merged = list(apps)
        local_by_bundle: dict[str, Path] = {}
        for local_ipa in local_ipas or []:
            # scan_ipas is newest-first; keep its first exact match.
            local_by_bundle.setdefault(local_ipa.bundle_id, local_ipa.path)
        for known_app in load_known_apps():
            if str(known_app.get("status") or "confirmed").casefold() == "candidate":
                continue
            raw_store_id = str(known_app.get("storeId") or "").strip()
            store_id = raw_store_id if raw_store_id.isdigit() else None
            bundle_id = str(known_app.get("bundleId") or "").strip()
            if store_id is None and not bundle_id:
                continue
            if bundle_id and bundle_id in installed:
                continue
            if bundle_id and bundle_id in by_bundle:
                existing = by_bundle[bundle_id]
                if existing.store_id is None and store_id is not None:
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
                        enriched if candidate is existing else candidate
                        for candidate in merged
                    ]
                    by_bundle[bundle_id] = enriched
                    by_store[store_id] = enriched
                continue
            if store_id is not None and store_id in by_store:
                existing = by_store[store_id]
                if not existing.bundle_id and bundle_id:
                    enriched = MissingApp(
                        bundle_id=bundle_id,
                        name=existing.name,
                        version=existing.version,
                        store_id=store_id,
                        store_match="known",
                        local_ipa=local_by_bundle.get(bundle_id),
                        source=existing.source,
                    )
                    merged = [
                        enriched if candidate is existing else candidate
                        for candidate in merged
                    ]
                    by_store[store_id] = enriched
                    by_bundle[bundle_id] = enriched
                continue
            app = MissingApp(
                bundle_id=bundle_id,
                name=str(
                    known_app.get("name")
                    or bundle_id
                    or f"App Store {store_id}"
                ),
                version=str(known_app.get("version") or "?"),
                store_id=store_id,
                store_match="known",
                local_ipa=local_by_bundle.get(bundle_id) if bundle_id else None,
                source="known",
            )
            merged.append(app)
            if store_id is not None:
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
        acquire_license: bool = False,
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
        attempts.append(("bundle", bundle_id, False))
        # `--purchase` changes the Apple account's license history, so it must
        # never be an implicit retry.
        if acquire_license:
            if resolved:
                attempts.append(("store", resolved, True))
            attempts.append(("bundle", bundle_id, True))
        return attempts

    def _verified_download(
        self,
        bundle_id: str,
        *,
        store_id: str | None,
        temp_dir: Path,
        lookup_store_id: bool = True,
        acquire_license: bool = False,
    ) -> tuple[Path, VerifiedIpa, str | None]:
        expected = validate_bundle_id(bundle_id)
        if not self.tools.ipatool_authenticated():
            raise AppRestoreError("ipatool is not authenticated; run `apprestore auth`")

        attempts = self._build_download_attempts(
            expected,
            store_id,
            lookup_store_id=lookup_store_id,
            acquire_license=acquire_license,
        )
        errors: list[str] = []
        identity_mismatches: set[tuple[str, str]] = set()

        for attempt_number, (kind, value, purchase) in enumerate(attempts, 1):
            label = (
                f"{kind}={value}"
                + (" with --purchase" if purchase else " without --purchase")
            )
            print(f"  try {attempt_number}/{len(attempts)}: {label}")
            if purchase and (kind, value) in identity_mismatches:
                errors.append(
                    f"{label}: skipped because the read-only download "
                    "proved this identity resolves to another app"
                )
                continue
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
                verified = self._verify_ipa(
                    candidate,
                    expected_bundle_id=expected,
                )
            except IpaError as exc:
                errors.append(f"{label}: {exc}")
                continue
            except DownloadIdentityMismatch as exc:
                identity_mismatches.add((kind, value))
                errors.append(f"{label}: {exc}")
                continue
            except AppRestoreError as exc:
                errors.append(
                    f"{label}: {exc}"
                )
                continue
            verified_store_id = value if kind == "store" else None
            return candidate, verified, verified_store_id

        detail = "; ".join(errors) if errors else "ipatool did not produce an IPA"
        raise AppRestoreError(
            f"could not download {expected} ({detail}). "
            "Typical causes: app removed from the App Store, unavailable for this "
            "Apple ID region, or no purchase/license. To explicitly acquire a "
            "license, retry with --acquire-license. If you already have the IPA "
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

    @staticmethod
    def _sha256_fd(descriptor: int) -> str:
        """Hash the already-open file object and restore its seek position."""

        position = os.lseek(descriptor, 0, os.SEEK_CUR)
        digest = hashlib.sha256()
        try:
            os.lseek(descriptor, 0, os.SEEK_SET)
            for chunk in iter(lambda: os.read(descriptor, 1024 * 1024), b""):
                digest.update(chunk)
        finally:
            os.lseek(descriptor, position, os.SEEK_SET)
        return digest.hexdigest()

    @staticmethod
    def _same_file_snapshot(left: os.stat_result, right: os.stat_result) -> bool:
        """Compare one regular-file snapshot without relying on timestamps alone."""

        if (
            left.st_size != right.st_size
            or left.st_mtime_ns != right.st_mtime_ns
        ):
            return False
        left_identity = (
            int(getattr(left, "st_dev", 0)),
            int(getattr(left, "st_ino", 0)),
        )
        right_identity = (
            int(getattr(right, "st_dev", 0)),
            int(getattr(right, "st_ino", 0)),
        )
        if all(left_identity) and all(right_identity):
            return left_identity == right_identity
        return True

    @staticmethod
    def _same_file_object(left: os.stat_result, right: os.stat_result) -> bool:
        """Return whether two stats identify the same object, when supported."""

        left_identity = (
            int(getattr(left, "st_dev", 0)),
            int(getattr(left, "st_ino", 0)),
        )
        right_identity = (
            int(getattr(right, "st_dev", 0)),
            int(getattr(right, "st_ino", 0)),
        )
        return (
            all(left_identity)
            and all(right_identity)
            and left_identity == right_identity
        )

    @staticmethod
    def _regular_lstat(path: Path, *, message: str) -> os.stat_result:
        try:
            observed = path.lstat()
        except OSError as exc:
            raise AppRestoreError(message) from exc
        if (
            path.is_symlink()
            or not stat.S_ISREG(observed.st_mode)
            or bool(int(getattr(observed, "st_file_attributes", 0)) & 0x400)
        ):
            raise AppRestoreError(message)
        return observed

    def _verify_ipa(
        self,
        ipa: str | Path,
        *,
        expected_bundle_id: str | None = None,
    ) -> VerifiedIpa:
        path = Path(ipa).expanduser()
        before = self._regular_lstat(
            path,
            message=f"refusing non-regular IPA path: {path}",
        )
        metadata = read_ipa_metadata(path)
        after_metadata = self._regular_lstat(
            path,
            message="IPA changed while it was being verified",
        )
        if not self._same_file_snapshot(before, after_metadata):
            raise AppRestoreError("IPA changed while it was being verified")
        if expected_bundle_id is not None:
            expected = validate_bundle_id(expected_bundle_id)
            if metadata.bundle_id != expected:
                raise DownloadIdentityMismatch(
                    f"refusing to install {metadata.bundle_id!r}; expected {expected!r}"
                )
        confirmed = read_ipa_metadata(path)
        before_hash = self._regular_lstat(
            path,
            message="IPA changed while it was being verified",
        )
        if not self._same_file_snapshot(after_metadata, before_hash):
            raise AppRestoreError("IPA changed while it was being verified")
        if (
            confirmed.bundle_id != metadata.bundle_id
            or confirmed.name != metadata.name
            or confirmed.version != metadata.version
            or confirmed.size != metadata.size
        ):
            raise AppRestoreError("IPA changed while it was being verified")
        digest = self._sha256(path)
        after = self._regular_lstat(
            path,
            message="IPA changed while it was being verified",
        )
        if not self._same_file_snapshot(before_hash, after):
            raise AppRestoreError("IPA changed while it was being verified")
        return VerifiedIpa(
            metadata=confirmed,
            sha256=digest,
            modified_ns=after.st_mtime_ns,
        )

    def _reverify_same_ipa(
        self,
        expected: VerifiedIpa,
        *,
        context: str,
        path: Path | None = None,
    ) -> VerifiedIpa:
        """Re-open a path and prove that it still contains the verified bytes."""

        candidate = path or expected.metadata.path
        try:
            current = self._verify_ipa(
                candidate,
                expected_bundle_id=expected.metadata.bundle_id,
            )
        except (IpaError, AppRestoreError) as exc:
            raise AppRestoreError(f"{context}: {exc}") from exc
        if (
            current.sha256 != expected.sha256
            or current.metadata.size != expected.metadata.size
        ):
            raise AppRestoreError(f"{context}: IPA bytes changed after verification")
        return current

    def _commit_downloaded_ipa(
        self,
        temporary: Path,
        downloaded: VerifiedIpa,
        *,
        expected_bundle_id: str,
    ) -> tuple[Path, VerifiedIpa]:
        """Atomically move and then verify the exact bytes stored in the library."""

        expected = validate_bundle_id(expected_bundle_id)
        version = self._safe_version(downloaded.metadata.version)
        target = self.library / (
            f"{expected}-{version}-{downloaded.sha256[:12]}.ipa"
        )
        if target.is_symlink():
            raise AppRestoreError(f"refusing to replace symlink: {target}")
        if target.exists():
            if not target.is_file():
                raise AppRestoreError(f"IPA target is not a file: {target}")
            try:
                existing = self._verify_ipa(
                    target,
                    expected_bundle_id=expected,
                )
            except (IpaError, AppRestoreError):
                existing = None
            if existing is not None and existing.sha256 == downloaded.sha256:
                self._cache_verified_ipa(existing)
                return target, existing
            target = self.library / (
                f"{expected}-{version}-{downloaded.sha256[:12]}-"
                f"{uuid.uuid4().hex[:8]}.ipa"
            )
            if target.exists() or target.is_symlink():
                raise AppRestoreError(
                    "could not allocate a unique verified IPA target"
                )

        source_verified = self._reverify_same_ipa(
            downloaded,
            context="downloaded IPA changed before commit",
            path=temporary,
        )
        source_before = self._regular_lstat(
            temporary,
            message="downloaded IPA changed before commit",
        )
        try:
            os.replace(temporary, target)
        except OSError as exc:
            raise AppRestoreError(
                f"could not commit the verified IPA: {exc}"
            ) from exc
        self._make_private(target)
        target_after_move = self._regular_lstat(
            target,
            message="committed IPA is not a regular file",
        )
        try:
            if (
                all(
                    int(getattr(item, attribute, 0))
                    for item in (source_before, target_after_move)
                    for attribute in ("st_dev", "st_ino")
                )
                and not self._same_file_object(source_before, target_after_move)
            ):
                raise AppRestoreError("IPA identity changed while it was committed")

            committed = self._reverify_same_ipa(
                source_verified,
                context="committed IPA failed final verification",
                path=target,
            )
        except AppRestoreError:
            # Never leave bytes that failed the final trust check under a
            # hash-derived library name. Only remove the object we observed
            # immediately after our own replace; do not delete a later winner.
            try:
                current = target.lstat()
                if self._same_file_object(target_after_move, current):
                    target.unlink()
            except OSError:
                pass
            raise
        self._cache_verified_ipa(committed)
        return target, committed

    @contextmanager
    def _staged_ipa(
        self,
        ipa: str | Path,
        *,
        expected_bundle_id: str | None = None,
    ) -> Iterator[VerifiedIpa]:
        """Copy an untrusted IPA into a private, AppRestore-owned snapshot.

        The installer never receives the caller-controlled path. The private
        copy is held open and its digest, metadata, and identity are checked at
        the installer boundary. A malicious process running as the same user
        can still ignore POSIX permissions, so post-read verification remains
        part of the boundary contract.
        """

        source = Path(ipa).expanduser()
        try:
            before = source.lstat()
        except OSError as exc:
            raise IpaError(f"IPA not found: {source}") from exc
        if (
            source.is_symlink()
            or not stat.S_ISREG(before.st_mode)
            or bool(int(getattr(before, "st_file_attributes", 0)) & 0x400)
        ):
            raise IpaError(f"refusing non-regular IPA path: {source}")

        staging_root = self.cache / "staging"
        staging_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        self._make_private(staging_root, directory=True)
        with tempfile.TemporaryDirectory(
            prefix="install-",
            dir=staging_root,
        ) as temporary_dir:
            directory = Path(temporary_dir)
            self._make_private(directory, directory=True)
            staged = directory / "verified.ipa"
            source_flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
            source_flags |= getattr(os, "O_NOFOLLOW", 0)
            try:
                source_fd = os.open(source, source_flags)
            except OSError as exc:
                raise IpaError(f"could not open IPA safely: {source}") from exc

            try:
                opened = os.fstat(source_fd)
                if not stat.S_ISREG(opened.st_mode):
                    raise IpaError(f"refusing non-regular IPA path: {source}")
                if not self._same_file_snapshot(before, opened):
                    raise AppRestoreError("IPA path changed while it was opened")

                destination_flags = (
                    os.O_WRONLY
                    | os.O_CREAT
                    | os.O_EXCL
                    | getattr(os, "O_BINARY", 0)
                )
                destination_fd = os.open(staged, destination_flags, 0o600)
                staged_digest = hashlib.sha256()
                copied_size = 0
                try:
                    with (
                        os.fdopen(source_fd, "rb", closefd=False) as source_handle,
                        os.fdopen(
                            destination_fd,
                            "wb",
                            closefd=False,
                        ) as destination_handle,
                    ):
                        for chunk in iter(
                            lambda: source_handle.read(1024 * 1024),
                            b"",
                        ):
                            destination_handle.write(chunk)
                            staged_digest.update(chunk)
                            copied_size += len(chunk)
                        destination_handle.flush()
                        os.fsync(destination_handle.fileno())
                finally:
                    os.close(destination_fd)

                after = os.fstat(source_fd)
                if not self._same_file_snapshot(opened, after):
                    raise AppRestoreError("IPA changed while it was staged")
            finally:
                os.close(source_fd)

            try:
                staged.chmod(0o400)
            except OSError:
                pass
            verified = self._verify_ipa(
                staged,
                expected_bundle_id=expected_bundle_id,
            )
            if (
                verified.metadata.size != copied_size
                or verified.sha256 != staged_digest.hexdigest()
            ):
                raise AppRestoreError("staged IPA changed during verification")

            held_flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
            held_flags |= getattr(os, "O_NOFOLLOW", 0)
            try:
                held_fd = os.open(staged, held_flags)
            except OSError as exc:
                raise AppRestoreError(
                    "could not hold the staged IPA open for installation"
                ) from exc
            try:
                held_before = os.fstat(held_fd)
                path_before = self._regular_lstat(
                    staged,
                    message="staged IPA changed before installation",
                )
                if not self._same_file_snapshot(held_before, path_before):
                    raise AppRestoreError("staged IPA changed before installation")
                if self._sha256_fd(held_fd) != verified.sha256:
                    raise AppRestoreError("staged IPA changed before installation")
                try:
                    directory.chmod(0o500)
                except OSError:
                    pass
                try:
                    yield verified
                finally:
                    held_digest = self._sha256_fd(held_fd)
                    held_after = os.fstat(held_fd)
                    path_after = self._regular_lstat(
                        staged,
                        message="staged IPA changed during installation",
                    )
                    if (
                        held_digest != verified.sha256
                        or not self._same_file_snapshot(held_before, held_after)
                        or not self._same_file_snapshot(held_after, path_after)
                    ):
                        raise AppRestoreError(
                            "staged IPA changed during installation"
                        )
            finally:
                os.close(held_fd)
                try:
                    directory.chmod(0o700)
                except OSError:
                    pass
                try:
                    staged.chmod(0o600)
                except OSError:
                    pass

    def _wait_until_installed(
        self,
        udid: str,
        bundle_id: str,
        *,
        expected_version: str | None = None,
        require_change_from: tuple[DeviceAppState, str | None] | None = None,
        timeout: float | None = None,
    ) -> DeviceAppState:
        deadline = time.monotonic() + (
            self.INSTALL_VERIFY_TIMEOUT if timeout is None else max(timeout, 0.0)
        )
        delay = 0.5
        last = DeviceAppState.UNKNOWN
        last_version: str | None = None
        while True:
            last, last_version = self.tools.device_app_snapshot(udid, bundle_id)
            version_matches = (
                expected_version is None
                or last_version is None
                or last_version == expected_version
            )
            changed = True
            if require_change_from is not None:
                prior_state, prior_version = require_change_from
                changed = prior_state in {
                    DeviceAppState.ABSENT,
                    DeviceAppState.OFFLOADED,
                    DeviceAppState.DOWNLOADING,
                } or (
                    prior_state is DeviceAppState.INSTALLED
                    and prior_version is not None
                    and last_version is not None
                    and prior_version != last_version
                )
            if last is DeviceAppState.INSTALLED and version_matches and changed:
                return last
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                version_detail = (
                    f", version: {last_version}"
                    if last_version is not None
                    else ""
                )
                unchanged_detail = (
                    "; state was already present before an indeterminate request"
                    if require_change_from is not None and not changed
                    else ""
                )
                raise AppRestoreError(
                    "the install command finished, but iPhone did not confirm "
                    f"{bundle_id} as installed at version {expected_version} "
                    f"(last state: {last.value}{version_detail})"
                    f"{unchanged_detail}"
                )
            time.sleep(min(delay, remaining))
            delay = min(delay * 1.6, 4.0)

    def _wait_for_native_redownload(self, udid: str, bundle_id: str) -> None:
        start_deadline = time.monotonic() + self.REDOWNLOAD_START_TIMEOUT
        completion_deadline: float | None = None
        delay = 0.5
        last = DeviceAppState.UNKNOWN
        while True:
            last = self.tools.device_app_state(udid, bundle_id)
            if last is DeviceAppState.INSTALLED:
                return
            now = time.monotonic()
            if completion_deadline is not None and now >= completion_deadline:
                raise AppRestoreError(
                    f"iPhone did not finish downloading {bundle_id} "
                    f"(last state: {last.value}); refusing a competing IPA install. "
                    "After confirming that iPhone is no longer downloading, retry "
                    "with --skip-device-redownload"
                )
            if last is DeviceAppState.DOWNLOADING:
                if completion_deadline is None:
                    completion_deadline = now + self.REDOWNLOAD_COMPLETE_TIMEOUT
            elif completion_deadline is None and now >= start_deadline:
                raise AppRestoreError(
                    "could not safely determine whether native redownload "
                    f"started (last state: {last.value}); refusing a competing "
                    "IPA install. After confirming that iPhone is not downloading, "
                    "retry with --skip-device-redownload"
                )
            time.sleep(delay)
            delay = min(delay * 1.6, 4.0)

    def download(
        self,
        bundle_id: str,
        store_id: str | None = None,
        *,
        lookup_store_id: bool = True,
        acquire_license: bool = False,
    ) -> Path:
        expected = validate_bundle_id(bundle_id)
        if store_id is not None:
            parsed_store_id = parse_app_store_id(str(store_id))
            if not parsed_store_id:
                raise AppRestoreError(
                    "store_id must be an 8-12 digit positive integer"
                )
            store_id = parsed_store_id
        with tempfile.TemporaryDirectory(
            prefix=".apprestore-download-",
            dir=self.library,
        ) as temporary_dir:
            temporary, downloaded, verified_store_id = self._verified_download(
                expected,
                store_id=store_id,
                temp_dir=Path(temporary_dir),
                lookup_store_id=lookup_store_id,
                acquire_license=acquire_license,
            )

            target, committed = self._commit_downloaded_ipa(
                temporary,
                downloaded,
                expected_bundle_id=expected,
            )
            self._remember_app_best_effort(
                store_id=verified_store_id,
                bundle_id=committed.metadata.bundle_id,
                name=committed.metadata.name,
                version=committed.metadata.version,
                provenance="verified-download",
                status="confirmed",
            )
            self._local_scan_cache = None
            return target

    def download_by_store_id(
        self,
        store_id: str,
        *,
        acquire_license: bool = False,
    ) -> Path:
        """Download an IPA by App Store ID; bundle ID is read from the IPA."""
        resolved = parse_app_store_id(str(store_id))
        if not resolved:
            raise AppRestoreError("store_id must be an 8-12 digit positive integer")
        if not self.tools.ipatool_authenticated():
            raise AppRestoreError("ipatool is not authenticated; run `apprestore auth`")

        with tempfile.TemporaryDirectory(
            prefix=".apprestore-download-",
            dir=self.library,
        ) as temporary_dir:
            temp_root = Path(temporary_dir)
            errors: list[str] = []
            temporary: Path | None = None
            downloaded: VerifiedIpa | None = None
            purchase_modes = (False, True) if acquire_license else (False,)
            for attempt_number, purchase in enumerate(purchase_modes, 1):
                label = (
                    f"store={resolved}"
                    + (" with --purchase" if purchase else " without --purchase")
                )
                print(f"  try {attempt_number}/{len(purchase_modes)}: {label}")
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
                    downloaded = self._verify_ipa(output)
                except (IpaError, AppRestoreError) as exc:
                    errors.append(f"{label}: {exc}")
                    continue
                temporary = output
                break

            if temporary is None or downloaded is None:
                detail = "; ".join(errors) if errors else "ipatool did not produce an IPA"
                raise AppRestoreError(
                    f"could not download App Store ID {resolved} ({detail}). "
                    "Typical causes: app removed from the App Store, unavailable "
                    "for this Apple ID region, or no purchase/license. To "
                    "explicitly acquire a license, retry with --acquire-license."
                )

            expected = downloaded.metadata.bundle_id
            target, committed = self._commit_downloaded_ipa(
                temporary,
                downloaded,
                expected_bundle_id=expected,
            )
            self._remember_app_best_effort(
                store_id=resolved,
                bundle_id=committed.metadata.bundle_id,
                name=committed.metadata.name,
                version=committed.metadata.version,
                provenance="verified-download",
                status="confirmed",
            )
            self._local_scan_cache = None
            return target

    def restore_by_store_id(
        self,
        udid: str,
        store_id: str,
        *,
        acquire_license: bool = False,
    ) -> str:
        print(
            f"  downloading via App Store ID {store_id} "
            "(bundle ID will be read from the IPA)…"
        )
        if acquire_license:
            ipa = self.download_by_store_id(
                store_id,
                acquire_license=True,
            )
        else:
            ipa = self.download_by_store_id(store_id)
        print(f"  downloaded: {ipa}")
        print("  installing on iPhone…")
        metadata = self.install(udid, ipa)
        self._remember_app_best_effort(
            store_id=store_id,
            bundle_id=metadata.bundle_id,
            name=metadata.name,
            version=metadata.version,
            provenance="device-install",
            status="restored",
        )
        return f"installed {metadata.name} {metadata.version}"

    def install(
        self,
        udid: str,
        ipa: str | Path,
        *,
        expected_bundle_id: str | None = None,
    ) -> IpaMetadata:
        source = Path(ipa).expanduser()
        with self._staged_ipa(
            source,
            expected_bundle_id=expected_bundle_id,
        ) as verified:
            pre_state, pre_version = self.tools.device_app_snapshot(
                udid,
                verified.metadata.bundle_id,
            )
            ready = self._reverify_same_ipa(
                verified,
                context="staged IPA changed before the installer opened it",
            )
            try:
                request_state = self.tools.install_ipa(
                    udid,
                    ready.metadata.path,
                )
            finally:
                self._reverify_same_ipa(
                    ready,
                    context="staged IPA changed while the installer was reading it",
                )
            if not isinstance(request_state, InstallRequestState):
                raise AppRestoreError("IPA backend returned an invalid request state")
            if request_state is InstallRequestState.FAILED_BEFORE_REQUEST:
                precondition = f"pre-install state: {pre_state.value}"
                if pre_version is not None:
                    precondition += f", version: {pre_version}"
                raise AppRestoreError(
                    "IPA backend failed before it could submit the install "
                    f"request ({precondition})"
                )
            try:
                self._wait_until_installed(
                    udid,
                    verified.metadata.bundle_id,
                    expected_version=verified.metadata.version,
                    require_change_from=(pre_state, pre_version)
                    if request_state is InstallRequestState.INDETERMINATE
                    else None,
                )
            except AppRestoreError as exc:
                if request_state is InstallRequestState.INDETERMINATE:
                    raise AppRestoreError(
                        "IPA install request may have reached iPhone, but its "
                        f"result could not be confirmed; {exc}"
                    ) from exc
                raise
            return IpaMetadata(
                path=source.resolve(),
                bundle_id=verified.metadata.bundle_id,
                name=verified.metadata.name,
                version=verified.metadata.version,
                size=verified.metadata.size,
            )

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
        acquire_license: bool = False,
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
            self._remember_app_best_effort(
                store_id=app.store_id,
                bundle_id=metadata.bundle_id,
                name=metadata.name,
                version=metadata.version,
                provenance="local-ipa-install",
                status="restored",
            )
            return f"installed {metadata.name} {metadata.version} from local IPA"

        if try_device_redownload:
            print("  asking iPhone to redownload the app…")
        if try_device_redownload:
            request_state = self.tools.device_request_redownload(
                udid,
                app.bundle_id,
            )
            if request_state is not RedownloadRequestState.FAILED_BEFORE_REQUEST:
                self._wait_for_native_redownload(udid, app.bundle_id)
                self._remember_app_best_effort(
                    store_id=app.store_id,
                    bundle_id=app.bundle_id,
                    name=app.name,
                    version=app.version,
                    provenance="native-redownload",
                    status="restored",
                )
                return "iPhone confirmed the app as installed"

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

        download_options: dict[str, bool] = {"lookup_store_id": False}
        if acquire_license:
            download_options["acquire_license"] = True
        ipa = self.download(app.bundle_id, store_id, **download_options)
        print(f"  downloaded: {ipa}")
        print("  installing on iPhone…")
        metadata = self.install(
            udid,
            ipa,
            expected_bundle_id=app.bundle_id,
        )
        self._remember_app_best_effort(
            store_id=store_id,
            bundle_id=metadata.bundle_id,
            name=metadata.name,
            version=metadata.version,
            provenance="device-install",
            status="restored",
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

    def restore_missing(
        self,
        udid: str,
        app: MissingApp,
        *,
        acquire_license: bool = False,
    ) -> str:
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
            self._remember_app_best_effort(
                store_id=app.store_id,
                bundle_id=metadata.bundle_id,
                name=metadata.name,
                version=metadata.version,
                provenance="local-ipa-install",
                status="restored",
            )
            return f"installed {metadata.name} {metadata.version} from local IPA"

        if not app.bundle_id:
            if not app.store_id:
                raise AppRestoreError("need a bundle ID or App Store ID")
            if acquire_license:
                return self.restore_by_store_id(
                    udid,
                    app.store_id,
                    acquire_license=True,
                )
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

        download_options: dict[str, bool] = {"lookup_store_id": False}
        if acquire_license:
            download_options["acquire_license"] = True
        ipa = self.download(app.bundle_id, store_id, **download_options)
        print(f"  downloaded: {ipa}")
        print("  installing on iPhone…")
        metadata = self.install(
            udid,
            ipa,
            expected_bundle_id=app.bundle_id,
        )
        self._remember_app_best_effort(
            store_id=store_id,
            bundle_id=metadata.bundle_id,
            name=metadata.name,
            version=metadata.version,
            provenance="device-install",
            status="restored",
        )
        return f"installed {metadata.name} {metadata.version}"
