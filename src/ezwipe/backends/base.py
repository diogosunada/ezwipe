"""Sanitization backend interface.

Only the engine calls a backend. The GUI never imports this package's
execute paths directly (spec 11.3).

A drive can be served by more than one backend: on the boot USB, the NVMe
or ATA backend sends firmware commands while the host-overwrite backend
writes patterns. Each backend lists the methods it knows in `methods`, and
the engine picks the backend that handles the drive AND knows the method.
"""

from __future__ import annotations

import threading
from typing import Callable, Protocol as TypingProtocol

from ..core.models import Availability, Drive, DriveIdentity, ExecutionEvidence, MethodId

ProgressCallback = Callable[[str, float | None], None]
"""(message, fraction 0..1 or None when nobody measured progress)."""


class SanitizeBackend(TypingProtocol):
    name: str
    methods: frozenset[MethodId]

    def handles(self, drive: Drive) -> bool:
        ...

    def reachability(self, drive: Drive, method: MethodId) -> tuple[Availability, str]:
        """Can this backend send the method to this drive?

        Must not send anything destructive. May use read-only probes.
        """
        ...

    def read_identity(self, drive: Drive) -> DriveIdentity | None:
        """Re-read identity right before execution. None if the drive is gone."""
        ...

    def execute(self, drive: Drive, method: MethodId, cancel: threading.Event,
                progress: ProgressCallback, options: dict | None = None) -> ExecutionEvidence:
        """Send the command and follow it to the device's own completion.

        options come from the profile: verify_fraction, reset_hidden_areas.
        """
        ...

    def post_checks(self, drive: Drive, method: MethodId,
                    evidence: ExecutionEvidence) -> list[dict]:
        """Method-specific checks after completion (spec 8.2).

        Each check: {"check": str, "passed": True | False | None, "shows": str}.
        passed=None means the check ran but could not tell either way.
        """
        ...


def backend_for(backends: list, drive: Drive, method: MethodId | None = None):
    """First backend that handles the drive (and knows the method, when given)."""
    for b in backends:
        if not b.handles(drive):
            continue
        if method is None or method in getattr(b, "methods", frozenset()):
            return b
    return None
