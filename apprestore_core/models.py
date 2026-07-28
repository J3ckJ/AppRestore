from __future__ import annotations

from dataclasses import asdict, dataclass
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
class DoctorCheck:
    name: str
    ok: bool
    detail: str
    required: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
