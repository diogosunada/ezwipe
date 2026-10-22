"""NVMe backend for Windows.

Phase 1: capability reporting only. No destructive route is enabled until it
has been proven on real hardware in Phase 2 (see docs/technical-study.md).
Candidate routes, in the order they will be tested:

1. IOCTL_STORAGE_REINITIALIZE_MEDIA with STORAGE_REINITIALIZE_MEDIA
   (Crypto Erase / Block Erase). Data disks only.
2. IOCTL_SCSI_PASS_THROUGH with SCSI SANITIZE, translated by StorNVMe.
3. IOCTL_STORAGE_PROTOCOL_COMMAND with NVMe Sanitize (reported to fail
   outside WinPE with error 0x13D).
"""

from __future__ import annotations

import threading

from ..core.models import (Availability, BusType, Drive, DriveIdentity, ExecutionEvidence,
                           MethodId, Protocol)

NVME_METHODS = frozenset({
    MethodId.NVME_SANITIZE_CRYPTO, MethodId.NVME_SANITIZE_BLOCK, MethodId.NVME_SANITIZE_OVERWRITE,
    MethodId.NVME_FORMAT_USER_DATA, MethodId.NVME_FORMAT_CRYPTO,
})


class WindowsNvmeBackend:
    name = "windows-nvme"
    methods = NVME_METHODS

    def handles(self, drive: Drive) -> bool:
        return drive.protocol == Protocol.NVME and drive.bus_type == BusType.NVME

    def reachability(self, drive: Drive, method: MethodId) -> tuple[Availability, str]:
        if method not in NVME_METHODS:
            return Availability.NOT_DECLARED, "Not an NVMe method"
        return (Availability.NOT_IMPLEMENTED,
                "Phase 1 is read-only. Windows routes for this method are tested in Phase 2.")

    def read_identity(self, drive: Drive) -> DriveIdentity | None:
        from ..platform.windows import storage
        from ..discovery.windows_provider import drive_from_raw
        raw = storage.read_disk(drive.disk_number) if drive.disk_number is not None else None
        if raw is None:
            return None
        return drive_from_raw(raw, storage.map_volumes()).identity

    def execute(self, drive: Drive, method: MethodId, cancel: threading.Event,
                progress, options: dict | None = None) -> ExecutionEvidence:
        raise NotImplementedError("No destructive NVMe route is enabled in Phase 1")

    def post_checks(self, drive, method, evidence) -> list[dict]:
        return []
