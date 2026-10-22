"""Turns raw Windows disk data into Drive objects (read-only)."""

from __future__ import annotations

from dataclasses import asdict

from ..core.models import BusType, ConnectionPath, Drive, DriveIdentity, Protocol

_BUS_MAP = {
    "NVMe": BusType.NVME, "SATA": BusType.SATA, "ATA": BusType.ATA, "USB": BusType.USB,
    "SCSI": BusType.SCSI, "SAS": BusType.SAS, "RAID": BusType.RAID, "Virtual": BusType.VIRTUAL,
    "FileBackedVirtual": BusType.VIRTUAL, "Spaces": BusType.SPACES, "SD": BusType.SD,
}


def drive_from_raw(raw, volumes) -> Drive:
    """Build a Drive from storage.RawDisk and storage.VolumeMap."""
    desc = raw.descriptor
    ctrl = raw.nvme_controller
    ns = raw.nvme_namespace
    bus = _BUS_MAP.get(desc.bus_type, BusType.UNKNOWN) if desc else BusType.UNKNOWN

    if bus == BusType.NVME:
        protocol, connection = Protocol.NVME, ConnectionPath.PCIE_NVME
    elif bus in (BusType.SATA, BusType.ATA):
        protocol, connection = Protocol.ATA, ConnectionPath.INTERNAL_SATA
    elif bus == BusType.USB:
        protocol, connection = Protocol.UNKNOWN, ConnectionPath.USB_BRIDGE
    else:
        protocol, connection = Protocol.UNKNOWN, ConnectionPath.UNKNOWN

    # Prefer protocol-level identity (NVMe Identify) over the Windows descriptor.
    serial = (ctrl.serial if ctrl else "") or (desc.serial if desc else "")
    model = (ctrl.model if ctrl else "") or " ".join(
        x for x in ((desc.vendor if desc else ""), (desc.product if desc else "")) if x)
    identity = DriveIdentity(
        serial=serial,
        model=model,
        capacity_bytes=raw.length_bytes or (ns.size_bytes if ns else 0),
        vendor=(desc.vendor if desc else ""),
        firmware=(ctrl.firmware if ctrl else (desc.revision if desc else "")),
        nvme_eui64=(ns.eui64 if ns else ""),
        nvme_nguid=(ns.nguid if ns else ""),
    )

    caps: dict = {}
    if ctrl:
        caps["nvme"] = {
            "sanicap": ctrl.sanicap, "oacs": ctrl.oacs, "fna": ctrl.fna, "nn": ctrl.nn,
            "sanitize_log_readable": raw.nvme_sanitize_status is not None,
        }
        if raw.nvme_sanitize_status:
            caps["nvme"]["sanitize_status"] = asdict(raw.nvme_sanitize_status)

    drive = Drive(
        device_path=raw.path,
        disk_number=raw.disk_number,
        identity=identity,
        protocol=protocol,
        bus_type=bus,
        connection=connection,
        is_ssd=(not raw.seek_penalty) if raw.seek_penalty is not None else None,
        removable=bool(desc and desc.removable),
        mounted_volumes=sorted(volumes.volumes_by_disk.get(raw.disk_number, [])),
        is_system_disk=raw.disk_number in volumes.system_disks,
        protection_reason=("This drive holds the running Windows installation. "
                           "Boot the EZWipe USB to erase it.")
        if raw.disk_number in volumes.system_disks else "",
        capabilities=caps,
    )
    if raw.nvme_health:
        drive.temperature_c = raw.nvme_health.temperature_c
        drive.power_on_hours = raw.nvme_health.power_on_hours
        drive.wear_percent = raw.nvme_health.percentage_used
        if raw.nvme_health.critical_warning:
            drive.health_notes.append(f"NVMe critical warning bits 0x{raw.nvme_health.critical_warning:02X}")
        drive.health_notes.append(f"Wear {raw.nvme_health.percentage_used}% used, "
                                  f"{raw.nvme_health.power_on_hours} power-on hours")

    # Identity confidence: without serial, model and size we cannot re-check the
    # target before a destructive step, so the drive is blocked (spec 7.1).
    if not identity.serial or not identity.model or not identity.capacity_bytes:
        drive.identity_confident = False
        drive.limitations.append("Identity incomplete (serial, model or capacity missing).")
    if not volumes.complete:
        drive.identity_confident = False
        drive.limitations.append("System disk could not be located with certainty; all drives blocked.")
    return drive


class WindowsDiscovery:
    name = "windows"

    def __init__(self):
        self._last: dict = {}

    def discover(self) -> list[Drive]:
        from ..platform.windows import storage
        volumes = storage.map_volumes()
        raws = storage.enumerate_disks()
        self._last = {
            "volume_map": {"volumes_by_disk": volumes.volumes_by_disk,
                           "system_disks": sorted(volumes.system_disks),
                           "complete": volumes.complete, "errors": volumes.errors},
            "disks": [_raw_to_dict(r) for r in raws],
        }
        return [drive_from_raw(r, volumes) for r in raws]

    def diagnostics(self) -> dict:
        return self._last


def _raw_to_dict(raw) -> dict:
    def dc(x):
        return asdict(x) if x is not None else None
    return {
        "disk_number": raw.disk_number,
        "path": raw.path,
        "descriptor": dc(raw.descriptor),
        "length_bytes": raw.length_bytes,
        "seek_penalty": raw.seek_penalty,
        "nvme_controller": dc(raw.nvme_controller),
        "nvme_namespace": dc(raw.nvme_namespace),
        "nvme_sanitize_status": dc(raw.nvme_sanitize_status),
        "nvme_health": dc(raw.nvme_health),
        "probes": [asdict(p) for p in raw.probes],
    }
