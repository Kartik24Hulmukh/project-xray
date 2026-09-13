"""Central fail-closed operational capability policy.

Kill switches are intentionally configuration-driven so an operator can
contain an incident without mutating evidence data.  Values are parsed
strictly: malformed values never silently enable a capability.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping


TRUE_VALUES = frozenset({"1", "true", "yes", "on"})
FALSE_VALUES = frozenset({"0", "false", "no", "off", ""})
FLAG_NAMES = (
    "DISABLE_WRITES",
    "DISABLE_UPLOADS",
    "DISABLE_PUBLICATION",
    "READ_ONLY_MODE",
    "MAINTENANCE_MODE",
    "DISABLE_PUBLIC_READS",
)


def _strict_bool(value: object) -> tuple[bool, bool]:
    normalized = str(value if value is not None else "").strip().lower()
    if normalized in TRUE_VALUES:
        return True, True
    if normalized in FALSE_VALUES:
        return False, True
    return True, False  # invalid configuration fails closed


@dataclass(frozen=True)
class CapabilityPolicy:
    maintenance: bool
    public_reads: bool
    writes: bool
    uploads: bool
    publication: bool
    valid: bool
    invalid_flags: tuple[str, ...] = ()

    @classmethod
    def from_mapping(cls, values: Mapping[str, object]) -> "CapabilityPolicy":
        parsed: dict[str, bool] = {}
        invalid: list[str] = []
        for name in FLAG_NAMES:
            parsed[name], valid = _strict_bool(values.get(name, ""))
            if not valid:
                invalid.append(name)

        maintenance = parsed["MAINTENANCE_MODE"] or bool(invalid)
        writes = not (
            maintenance
            or parsed["READ_ONLY_MODE"]
            or parsed["DISABLE_WRITES"]
        )
        public_reads = not (maintenance or parsed["DISABLE_PUBLIC_READS"])
        uploads = writes and not parsed["DISABLE_UPLOADS"]
        publication = writes and not parsed["DISABLE_PUBLICATION"]
        return cls(
            maintenance=maintenance,
            public_reads=public_reads,
            writes=writes,
            uploads=uploads,
            publication=publication,
            valid=not invalid,
            invalid_flags=tuple(invalid),
        )

    def as_public_dict(self) -> dict[str, bool]:
        return {
            "public_reads": self.public_reads,
            "writes": self.writes,
            "uploads": self.uploads,
            "publication": self.publication,
        }

    @property
    def degraded(self) -> bool:
        return not all(self.as_public_dict().values())


def is_publication_path(method: str, path: str) -> bool:
    return method == "POST" and path.rstrip("/").endswith("/publish")


def is_upload_path(method: str, path: str) -> bool:
    if method != "POST":
        return False
    parts = [part for part in path.split("/") if part]
    return (
        len(parts) == 4
        and parts[0] == "api"
        and parts[1] == "projects"
        and parts[3] == "documents"
    )


def has_authentication_headers(headers: Mapping[str, str]) -> bool:
    return bool(
        str(headers.get("Authorization", "")).strip()
        or str(headers.get("X-Auth-Signature", "")).strip()
    )


def denial_reason(
    policy: CapabilityPolicy,
    method: str,
    path: str,
    headers: Mapping[str, str],
) -> str | None:
    """Return an internal denial reason before any request side effect."""
    if path in {"/health", "/livez", "/ready", "/readyz"}:
        return None
    if not policy.valid:
        return "invalid_configuration"
    if policy.maintenance:
        return "maintenance"
    if method in {"GET", "HEAD"}:
        if not has_authentication_headers(headers) and not policy.public_reads:
            return "public_reads_disabled"
        return None
    if method == "POST":
        if not policy.writes:
            return "writes_disabled"
        if is_upload_path(method, path) and not policy.uploads:
            return "uploads_disabled"
        if is_publication_path(method, path) and not policy.publication:
            return "publication_disabled"
    return None
