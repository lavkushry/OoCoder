"""Default-deny network allowlist with logged egress decisions."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from urllib.parse import urlparse


@dataclass
class EgressLog:
    entries: list[dict[str, str]] = field(default_factory=list)

    def record(self, host: str, allowed: bool, reason: str) -> None:
        self.entries.append({"host": host, "allowed": str(allowed), "reason": reason})


def host_from_target(target: str) -> str:
    if "://" in target:
        return (urlparse(target).hostname or "").lower()
    # host:port or bare host
    return target.split("/")[0].split(":")[0].lower()


def is_allowed(host: str, allowlist: Sequence[str]) -> bool:
    host = host.lower().rstrip(".")
    if not host:
        return False
    for pattern in allowlist:
        p = pattern.lower().rstrip(".")
        if host == p or host.endswith("." + p):
            return True
    return False


def check_egress(target: str, allowlist: Sequence[str], log: EgressLog) -> bool:
    host = host_from_target(target)
    allowed = is_allowed(host, allowlist)
    log.record(host, allowed, "allowlist" if allowed else "default_deny")
    return allowed
