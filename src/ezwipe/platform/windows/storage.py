"""Read-only Windows storage queries.

Every function here only reads. No call in this file can modify a disk:
handles are opened with access 0 (query only) or GENERIC_READ, and only
query IOCTLs are issued. Each probe records what worked and what failed so
the diagnostic tool can report it (spec 5.2).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from . import ioctl
from .winapi import (GENERIC_READ, GENERIC_WRITE, WinError, device_io_control,
                     drive_type, logical_drives, open_device, windows_directory)

MAX_PHYSICAL_DRIVES = 64
DRIVE_FIXED = 3
DRIVE_REMOVABLE = 2


@dataclass
class ProbeResult:
    name: str
    ok: bool
    detail: str = ""
    win32_error: int | None = None


@dataclass
class RawDisk:
    """Everything read about one \\\\.\\PhysicalDriveN, plus the probe log."""

    disk_number: int
    path: str
    descriptor: ioctl.DeviceDescriptor | None = None
    length_bytes: int | None = None
    seek_penalty: bool | None = None
    nvme_controller: ioctl.NvmeController | None = None
    nvme_namespace: ioctl.NvmeNamespace | None = None
    nvme_sanitize_status: ioctl.NvmeSanitizeStatus | None = None
    nvme_health: ioctl.NvmeHealth | None = None
    probes: list[ProbeResult] = field(default_factory=list)

    def probe(self, name: str, fn):
        try:
            value = fn()
            self.probes.append(ProbeResult(name, True))
            return value
        except WinError as e:
            self.probes.append(ProbeResult(name, False, str(e), e.code))
        except (ValueError, OSError) as e:
            self.probes.append(ProbeResult(name, False, str(e)))
        return None


def _query(handle, property_id: int, out_size: int = 1024) -> bytes:
    return device_io_control(handle, ioctl.IOCTL_STORAGE_QUERY_PROPERTY,
                             ioctl.build_property_query(property_id), out_size)


def _nvme_query(handle, property_id: int, data_type: int, value: int, subvalue: int,
                length: int) -> bytes:
    req = ioctl.build_nvme_query(property_id, data_type, value, subvalue, length)
    resp = device_io_control(handle, ioctl.IOCTL_STORAGE_QUERY_PROPERTY, req, len(req))
    return ioctl.parse_nvme_query_response(resp)


def _nvme_log(handle, log_id: int, length: int) -> bytes:
    """Get Log Page. Microsoft samples use the device property; fall back to adapter."""
    try:
        return _nvme_query(handle, ioctl.StorageDeviceProtocolSpecificProperty,
                           ioctl.NVMeDataTypeLogPage, log_id, 0, length)
    except WinError:
        return _nvme_query(handle, ioctl.StorageAdapterProtocolSpecificProperty,
                           ioctl.NVMeDataTypeLogPage, log_id, 0, length)


def read_disk(disk_number: int) -> RawDisk | None:
    """Read one physical disk. Returns None if the disk number does not exist."""
    path = rf"\\.\PhysicalDrive{disk_number}"
    raw = RawDisk(disk_number, path)
    try:
        with open_device(path, 0) as handle:
            raw.descriptor = raw.probe(
                "device descriptor",
                lambda: ioctl.parse_device_descriptor(_query(handle, ioctl.StorageDeviceProperty)))
            raw.seek_penalty = raw.probe(
                "seek penalty",
                lambda: ioctl.parse_seek_penalty(
                    _query(handle, ioctl.StorageDeviceSeekPenaltyProperty, 64)))
    except WinError as e:
        if e.code == 2:   # ERROR_FILE_NOT_FOUND: no disk with this number
            return None
        raw.probes.append(ProbeResult("open (query access)", False, str(e), e.code))
        return raw

    # Length and NVMe protocol queries need read access (admin for raw disks).
    try:
        with open_device(path, GENERIC_READ | GENERIC_WRITE) as h:
            # GENERIC_WRITE is required by StorNVMe for protocol-specific
            # queries (as in Microsoft's samples). No write I/O is issued.
            raw.length_bytes = raw.probe(
                "length",
                lambda: ioctl.parse_length_info(
                    device_io_control(h, ioctl.IOCTL_DISK_GET_LENGTH_INFO, None, 8)))
            if raw.descriptor and raw.descriptor.bus_type == "NVMe":
                _read_nvme(raw, h)
    except WinError as e:
        raw.probes.append(ProbeResult("open (read access)", False, str(e), e.code))
    return raw


def _read_nvme(raw: RawDisk, h) -> None:
    raw.nvme_controller = raw.probe(
        "NVMe Identify Controller",
        lambda: ioctl.parse_nvme_identify_controller(_nvme_query(
            h, ioctl.StorageAdapterProtocolSpecificProperty, ioctl.NVMeDataTypeIdentify,
            ioctl.NVME_IDENTIFY_CNS_CONTROLLER, 0, 4096)))
    raw.nvme_namespace = raw.probe(
        "NVMe Identify Namespace",
        lambda: ioctl.parse_nvme_identify_namespace(_nvme_query(
            h, ioctl.StorageDeviceProtocolSpecificProperty, ioctl.NVMeDataTypeIdentify,
            ioctl.NVME_IDENTIFY_CNS_NAMESPACE, 1, 4096)))
    raw.nvme_health = raw.probe(
        "NVMe SMART / Health log (0x02)",
        lambda: ioctl.parse_nvme_health(_nvme_log(h, ioctl.NVME_LOG_PAGE_HEALTH_INFO, 512)))
    raw.nvme_sanitize_status = raw.probe(
        "NVMe Sanitize Status log (0x81)",
        lambda: ioctl.parse_nvme_sanitize_status(_nvme_log(h, ioctl.NVME_LOG_PAGE_SANITIZE_STATUS, 512)))


def enumerate_disks() -> list[RawDisk]:
    disks = []
    for n in range(MAX_PHYSICAL_DRIVES):
        d = read_disk(n)
        if d is not None:
            disks.append(d)
    return disks


@dataclass
class VolumeMap:
    """Which disk numbers back which volumes, and which hold Windows."""

    volumes_by_disk: dict[int, list[str]] = field(default_factory=dict)
    system_disks: set[int] = field(default_factory=set)
    errors: list[str] = field(default_factory=list)
    complete: bool = True


def map_volumes() -> VolumeMap:
    """Map drive letters to physical disks via volume disk extents.

    The Windows volume is found from the Windows directory, not by assuming
    C:. A spanned volume marks every disk it touches (spec 7.1).
    """
    vm = VolumeMap()
    win_root = windows_directory()[:3].upper()
    for root in logical_drives():
        if drive_type(root) not in (DRIVE_FIXED, DRIVE_REMOVABLE):
            continue
        letter = root[:2]
        try:
            with open_device(rf"\\.\{letter}", 0) as h:
                buf = device_io_control(h, ioctl.IOCTL_VOLUME_GET_VOLUME_DISK_EXTENTS, None, 8 + 24 * 32)
            extents = ioctl.parse_volume_disk_extents(buf)
        except WinError as e:
            vm.errors.append(f"{letter}: {e}")
            if root.upper() == win_root:
                vm.complete = False
            continue
        for disk, _start, _len in extents:
            vm.volumes_by_disk.setdefault(disk, []).append(letter)
            if root.upper() == win_root:
                vm.system_disks.add(disk)
    if not vm.system_disks:
        vm.complete = False
        vm.errors.append("Could not find the disk that holds Windows")
    return vm
