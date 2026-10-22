"""Picks discovery and backends for the running edition.

- simulate: simulated lab, safe anywhere.
- Linux (the EZWipe boot USB, or any Linux run as root): lsblk + nvme-cli +
  hdparm, firmware erase and host overwrite.
- Windows: native IOCTL discovery; NVMe routes still read-only (Phase 2).
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field


@dataclass
class Edition:
    name: str                      # "simulated", "linux", "windows"
    discovery: object
    backends: list
    admin: bool | None             # None = not applicable
    notes: list[str] = field(default_factory=list)


def build_edition(simulate: bool, data_mount: str | None = None) -> Edition:
    if simulate:
        from .backends.simulated import SimulatedBackend, SimulatedDiscovery, default_lab
        lab = default_lab()
        return Edition("simulated", SimulatedDiscovery(lab), [SimulatedBackend(lab)], None)

    if sys.platform.startswith("linux"):
        from functools import partial

        from .backends.host_overwrite import HostOverwriteBackend
        from .backends.linux_ata import LinuxAtaBackend, open_hpa
        from .backends.linux_nvme import LinuxNvmeBackend
        from .discovery.linux_provider import LinuxDiscovery
        from .platform.linux.runner import Runner
        runner = Runner()
        disc = LinuxDiscovery(runner)
        if data_mount:
            disc.protect_mount(data_mount, "Holds the EZWipe reports partition.")
        reader = disc.read_identity
        backends = [LinuxNvmeBackend(runner, reader), LinuxAtaBackend(runner, reader),
                    HostOverwriteBackend(reader, hpa_opener=partial(open_hpa, runner))]
        admin = os.geteuid() == 0
        notes = [] if admin else ["Not running as root: drives can be listed but not erased."]
        return Edition("linux", disc, backends, admin, notes)

    if sys.platform == "win32":
        from .backends.windows_nvme import WindowsNvmeBackend
        from .discovery.windows_provider import WindowsDiscovery
        from .platform.windows.winapi import is_admin
        admin = is_admin()
        notes = ["Windows edition is read-only until Phase 2. Boot the EZWipe USB to erase drives."]
        return Edition("windows", WindowsDiscovery(), [WindowsNvmeBackend()], admin, notes)

    from .backends.simulated import SimulatedBackend, SimulatedDiscovery, default_lab
    lab = default_lab()
    return Edition("simulated", SimulatedDiscovery(lab), [SimulatedBackend(lab)], None,
                   [f"No drive support on {sys.platform}; showing simulated drives."])
