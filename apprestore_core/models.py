from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class CommandResult:
    args: tuple[str, ...]
    returncode: int
    stdout: str = ""
    stderr: str = ""


@dataclass(frozen=True)
class Device:
    udid: str
    name: str = "iPhone"
    ios_version: str = "?"

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass(frozen=True)
class IpaMetadata:
    path: Path
    bundle_id: str
    name: str
    version: str
    size: int

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["path"] = str(self.path)
        return data


@dataclass(frozen=True)
class VerifiedIpa:
    metadata: IpaMetadata
    sha256: str
    modified_ns: int


class DeviceAppState(str, Enum):
    """Observed installation state for one bundle on a connected device."""

    OFFLOADED = "offloaded"
    DOWNLOADING = "downloading"
    INSTALLED = "installed"
    ABSENT = "absent"
    UNKNOWN = "unknown"


class RedownloadRequestState(str, Enum):
    """How certain AppRestore is that iOS received a restore request."""

    COMPLETED = "completed"
    FAILED_BEFORE_REQUEST = "failed-before-request"
    INDETERMINATE = "indeterminate"


@dataclass(frozen=True)
class OffloadedApp:
    bundle_id: str
    name: str
    version: str
    static_size: int = 0
    dynamic_size: int = 0
    store_id: str | None = None
    store_match: str = "none"
    local_ipa: Path | None = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["local_ipa"] = str(self.local_ipa) if self.local_ipa else None
        return data


@dataclass(frozen=True)
class MissingApp:
    """App known from history/IPA but absent from the device (no placeholder)."""

    bundle_id: str
    name: str
    version: str = "?"
    store_id: str | None = None
    store_match: str = "none"
    local_ipa: Path | None = None
    source: str = "unknown"

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["local_ipa"] = str(self.local_ipa) if self.local_ipa else None
        return data


@dataclass(frozen=True)
class DoctorCheck:
    name: str
    ok: bool
    detail: str
    required: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
