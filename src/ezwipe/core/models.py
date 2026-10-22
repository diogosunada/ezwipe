"""Core data types shared by every module.

These types carry no behaviour that touches hardware. They describe drives,
methods, operations and evidence so the engine, the GUI and the audit store
all speak the same language.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from datetime import datetime, timezone


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Protocol(str, enum.Enum):
    NVME = "NVMe"
    ATA = "ATA"
    SCSI = "SCSI"
    UNKNOWN = "Unknown"


class BusType(str, enum.Enum):
    """How Windows reports the drive's bus (STORAGE_BUS_TYPE)."""

    NVME = "NVMe"
    SATA = "SATA"
    ATA = "ATA"
    USB = "USB"
    SCSI = "SCSI"
    SAS = "SAS"
    RAID = "RAID"
    VIRTUAL = "Virtual"
    SPACES = "Storage Spaces"
    SD = "SD"
    UNKNOWN = "Unknown"


class ConnectionPath(str, enum.Enum):
    """Best guess of the physical path. Unknown is a valid, honest answer."""

    PCIE_NVME = "PCIe NVMe (internal or USB4/Thunderbolt)"
    INTERNAL_NVME = "Internal NVMe (PCIe)"
    TUNNELED_PCIE = "USB4 / Thunderbolt (PCIe tunnel)"
    USB_BRIDGE = "USB bridge"
    INTERNAL_SATA = "Internal SATA"
    UNKNOWN = "Unknown"


class NistCategory(str, enum.Enum):
    CLEAR = "Clear"
    PURGE = "Purge"
    DESTROY = "Destroy"
    NONE = "Not a sanitization method"


class MethodId(str, enum.Enum):
    NVME_SANITIZE_CRYPTO = "nvme.sanitize.crypto_erase"
    NVME_SANITIZE_BLOCK = "nvme.sanitize.block_erase"
    NVME_SANITIZE_OVERWRITE = "nvme.sanitize.overwrite"
    NVME_FORMAT_USER_DATA = "nvme.format.user_data_erase"
    NVME_FORMAT_CRYPTO = "nvme.format.crypto_erase"
    ATA_SANITIZE_CRYPTO = "ata.sanitize.crypto_scramble"
    ATA_SANITIZE_BLOCK = "ata.sanitize.block_erase"
    ATA_SANITIZE_OVERWRITE = "ata.sanitize.overwrite"
    ATA_SECURITY_ERASE = "ata.security_erase"
    ATA_SECURITY_ERASE_ENHANCED = "ata.security_erase_enhanced"
    OVERWRITE_ADDRESSABLE = "host.overwrite_addressable"      # one random pass
    OVERWRITE_ZERO = "host.overwrite_zero"                    # one 0x00 pass
    OVERWRITE_DOD_3PASS = "host.overwrite_dod_3pass"          # 0x00, 0xFF, random


OVERWRITE_METHODS = frozenset({MethodId.OVERWRITE_ADDRESSABLE, MethodId.OVERWRITE_ZERO,
                               MethodId.OVERWRITE_DOD_3PASS})


class Availability(str, enum.Enum):
    """Declared by the drive vs reachable through Windows (spec 3.4)."""

    AVAILABLE = "Available"                 # declared and reachable
    DECLARED_NOT_REACHABLE = "Blocked by connection or driver"
    NOT_DECLARED = "Not supported by drive"
    UNKNOWN = "Unknown"                     # could not read capabilities
    NOT_IMPLEMENTED = "Not implemented yet"


@dataclass(frozen=True)
class DriveIdentity:
    """Everything used to make sure the target is the same physical drive.

    The Windows device path is deliberately not part of equality: it can
    change after a reconnect (spec 7.2).
    """

    serial: str
    model: str
    capacity_bytes: int
    vendor: str = ""
    firmware: str = ""
    nvme_eui64: str = ""
    nvme_nguid: str = ""

    def key(self) -> str:
        """Stable key for history and audit records."""
        return f"{self.model.strip()}|{self.serial.strip()}"

    def mismatches(self, other: "DriveIdentity") -> list[str]:
        """Fields that differ. Empty values on either side are not compared."""
        problems = []
        for name in ("serial", "model", "capacity_bytes", "nvme_eui64", "nvme_nguid"):
            a, b = getattr(self, name), getattr(other, name)
            if a in ("", 0) or b in ("", 0):
                if name in ("serial", "model", "capacity_bytes"):
                    problems.append(f"{name} missing")
                continue
            if str(a).strip() != str(b).strip():
                problems.append(f"{name} changed: {a!r} -> {b!r}")
        return problems


@dataclass
class MethodOption:
    method: MethodId
    availability: Availability
    reason: str = ""


@dataclass
class Drive:
    """A drive as seen by discovery at one moment in time."""

    device_path: str                 # \\.\PhysicalDrive3 or /dev/sda (not an identity)
    disk_number: int | None
    identity: DriveIdentity
    protocol: Protocol
    bus_type: BusType
    connection: ConnectionPath
    is_ssd: bool | None = None
    removable: bool = False
    # Protection flags set by discovery / safety manager.
    is_system_disk: bool = False     # holds the running OS (Windows, or the EZWipe boot USB)
    hosts_page_file: bool = False
    mounted_volumes: list[str] = field(default_factory=list)
    identity_confident: bool = True
    protection_reason: str = ""      # shown instead of the generic system-disk text
    # Optional health data. Absence is never treated as a fault (spec 10).
    temperature_c: int | None = None
    power_on_hours: int | None = None
    wear_percent: int | None = None  # NVMe "percentage used" or SSD wear, 0-100+
    health_notes: list[str] = field(default_factory=list)
    # Raw capability data, protocol specific (e.g. SANICAP bits).
    capabilities: dict = field(default_factory=dict)
    methods: list[MethodOption] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)
    discovered_at: datetime = field(default_factory=utc_now)

    @property
    def available_methods(self) -> list[MethodId]:
        return [m.method for m in self.methods if m.availability == Availability.AVAILABLE]

    @property
    def is_protected(self) -> bool:
        return self.is_system_disk or self.hosts_page_file or not self.identity_confident

    @property
    def display_name(self) -> str:
        return f"{self.identity.model} ({self.identity.serial})"

    @property
    def kind(self) -> str:
        """Short media label for the interface: NVMe, SSD, HDD, USB or Disk."""
        if self.bus_type == BusType.USB or self.connection == ConnectionPath.USB_BRIDGE:
            return "USB"
        if self.protocol == Protocol.NVME:
            return "NVMe"
        if self.is_ssd is True:
            return "SSD"
        if self.is_ssd is False:
            return "HDD"
        return "Disk"


class OperationState(str, enum.Enum):
    """Spec 6.1. A progress bar alone never stands for the result."""

    WAITING = "Waiting"
    CHECKING = "Checking compatibility"
    READY = "Ready to run"
    RUNNING = "Running"
    WAITING_DEVICE = "Waiting for device to finish"
    VERIFYING = "Verifying result"
    COMPLETED_VERIFIED = "Completed and verified"
    COMPLETED_WITH_CAVEATS = "Completed with caveats"
    FAILED = "Failed"
    INTERRUPTED = "Interrupted"
    INCONCLUSIVE = "Inconclusive: sanitization not verified"
    BLOCKED = "Blocked before execution"

    @property
    def is_final(self) -> bool:
        return self in FINAL_STATES


FINAL_STATES = {
    OperationState.COMPLETED_VERIFIED,
    OperationState.COMPLETED_WITH_CAVEATS,
    OperationState.FAILED,
    OperationState.INTERRUPTED,
    OperationState.INCONCLUSIVE,
    OperationState.BLOCKED,
}


class EvidenceLevel(int, enum.Enum):
    """Spec 8: these are not equivalent, and each needs its own evidence."""

    NONE = 0
    COMMAND_ACCEPTED = 1       # Windows accepted the request
    DEVICE_STARTED = 2         # the device reports the operation started
    DEVICE_COMPLETED = 3       # the device reports completion
    CRITERIA_MET = 4           # our post-checks passed for this method


@dataclass
class ExecutionEvidence:
    """What a backend observed. Filled in step by step, never guessed."""

    route: str = ""                       # OS interface or tool used (IOCTL, hdparm, nvme-cli...)
    command: str = ""                     # human-readable command sent
    parameters: dict = field(default_factory=dict)
    accepted: bool = False
    win32_error: int | None = None
    device_started: bool = False
    device_completed: bool = False
    device_status: str = ""               # e.g. NVMe SSTAT decoded
    device_raw: dict = field(default_factory=dict)
    progress_samples: list[tuple[str, float]] = field(default_factory=list)
    post_checks: list[dict] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    communication_failure: bool = False   # vs a failure of the method itself
    disconnected: bool = False
    timed_out: bool = False
