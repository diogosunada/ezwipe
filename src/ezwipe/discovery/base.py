"""Discovery interface. Providers only read; they never write to a drive."""

from __future__ import annotations

from typing import Protocol as TypingProtocol

from ..core.models import Drive


class DiscoveryProvider(TypingProtocol):
    name: str

    def discover(self) -> list[Drive]:
        """Return every drive currently visible, with protection flags set."""
        ...

    def diagnostics(self) -> dict:
        """Raw probe results for the last discovery, for the diagnostic report."""
        ...
