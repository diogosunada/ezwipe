"""Runs only on Windows: real read-only discovery must work and must find
and protect the disk that holds Windows."""

import sys

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows only")


def test_read_only_discovery_finds_protected_system_disk():
    from ezwipe.backends.windows_nvme import WindowsNvmeBackend
    from ezwipe.capability import detector
    from ezwipe.discovery.windows_provider import WindowsDiscovery
    disc = WindowsDiscovery()
    drives = disc.discover()
    assert drives, disc.diagnostics()
    for d in drives:
        detector.evaluate(d, [WindowsNvmeBackend()])
    system = [d for d in drives if d.is_system_disk]
    assert system, disc.diagnostics()
    assert all(d.is_protected for d in system)
    # Phase 1 must never offer a destructive method on real hardware.
    assert all(not d.available_methods for d in drives)
