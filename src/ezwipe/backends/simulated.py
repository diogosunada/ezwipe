"""Simulated drives and backend for tests and demos (spec 14.2).

Nothing here touches hardware. Results from this module are never evidence
of compatibility with a real drive, and every record it produces is tagged
"SIMULATED".
"""

from __future__ import annotations

import enum
import threading
import time
from dataclasses import dataclass, field, replace

from ..core.methods import method_info
from ..core.models import (OVERWRITE_METHODS, Availability, BusType, ConnectionPath, Drive,
                           DriveIdentity, ExecutionEvidence, MethodId, Protocol)


class Scenario(str, enum.Enum):
    SUCCESS = "success"
    FAIL = "fail"
    TIMEOUT = "timeout"
    DISCONNECT = "disconnect"
    IDENTITY_CHANGE = "identity_change"
    INCONCLUSIVE = "inconclusive"
    UNEXPECTED_RESPONSE = "unexpected_response"


@dataclass
class SimDrive:
    drive: Drive
    scenario: Scenario = Scenario.SUCCESS
    present: bool = True
    sanitized: bool = False


def _nvme(n: int, serial: str, model: str, size_gb: int, scenario=Scenario.SUCCESS,
          sanicap: int = 0b011, system=False, volumes=None, reason="") -> SimDrive:
    ident = DriveIdentity(serial=serial, model=model, capacity_bytes=size_gb * 10**9,
                          vendor=model.split()[0], firmware="SIM1.0",
                          nvme_eui64=f"00000000000{n:05x}")
    d = Drive(
        device_path=f"/dev/sim/nvme{n}n1", disk_number=n, identity=ident,
        protocol=Protocol.NVME, bus_type=BusType.NVME, connection=ConnectionPath.PCIE_NVME,
        is_ssd=True, is_system_disk=system, protection_reason=reason, mounted_volumes=volumes or [],
        temperature_c=38 + n, power_on_hours=1200 * n + 310, wear_percent=3 * n,
        capabilities={"nvme": {"sanicap": sanicap, "oacs": 0x2, "fna": 0x4, "controller": f"/dev/sim/nvme{n}",
                               "nn": 1, "sanitize_log_readable": True}},
    )
    return SimDrive(d, scenario)


def _sata(n: int, serial: str, model: str, size_gb: int, ssd: bool, *, frozen=False,
          sanitize=False, hpa=0) -> SimDrive:
    ident = DriveIdentity(serial=serial, model=model, capacity_bytes=size_gb * 10**9,
                          vendor=model.split()[0], firmware="SIM2.0")
    ata = {"frozen": frozen, "locked": False, "security_enabled": False,
           "erase_minutes": 2 if ssd else 240, "enhanced_erase_minutes": 2 if ssd else 240,
           "hpa_hidden_sectors": hpa,
           "ata.security_erase": True, "ata.security_erase_enhanced": True,
           "ata.sanitize.crypto_scramble": sanitize, "ata.sanitize.block_erase": sanitize,
           "ata.sanitize.overwrite": sanitize}
    d = Drive(device_path=f"/dev/sim/sd{chr(96 + n)}", disk_number=n, identity=ident,
              protocol=Protocol.ATA, bus_type=BusType.SATA, connection=ConnectionPath.INTERNAL_SATA,
              is_ssd=ssd, temperature_c=34 + n, power_on_hours=751 * n, wear_percent=12 if ssd else None,
              capabilities={"ata": ata})
    return SimDrive(d)


def default_lab() -> "SimulatedLab":
    """A bench: the EZWipe boot USB, four NVMe drives, two SATA drives, a USB stick."""
    drives = [
        _nvme(0, "SIM-SYS-0001", "Samsung SSD 990 PRO 1TB", 1000, system=True, volumes=[],
              reason="This is the EZWipe boot drive (simulated)."),
        _nvme(1, "SIM-D4-0001", "WD_BLACK SN770 500GB", 500),
        _nvme(2, "SIM-D4-0002", "Crucial P3 1TB", 1000, sanicap=0b001),
        _nvme(3, "SIM-D4-0003", "Kingston NV2 500GB", 500, scenario=Scenario.INCONCLUSIVE),
        _nvme(4, "SIM-D4-0004", "Samsung SSD 980 1TB", 1000, scenario=Scenario.FAIL),
        _sata(5, "SIM-HDD-0005", "WDC WD20EFRX-68EUZN0", 2000, ssd=False, hpa=2048),
        _sata(6, "SIM-SSD-0006", "Samsung SSD 870 EVO 500GB", 500, ssd=True, frozen=True, sanitize=True),
    ]
    usb_ident = DriveIdentity(serial="SIM-USB-0001", model="SanDisk Ultra USB 3.0", capacity_bytes=64 * 10**9,
                              vendor="SanDisk")
    usb = Drive(device_path="/dev/sim/sdh", disk_number=7, identity=usb_ident,
                protocol=Protocol.UNKNOWN, bus_type=BusType.USB, connection=ConnectionPath.USB_BRIDGE,
                is_ssd=True, removable=True)
    drives.append(SimDrive(usb))
    return SimulatedLab(drives)


@dataclass
class SimulatedLab:
    drives: list[SimDrive] = field(default_factory=list)
    step_delay: float = 0.15
    steps: int = 10

    def find(self, drive: Drive) -> SimDrive | None:
        for s in self.drives:
            if s.drive.disk_number == drive.disk_number:
                return s
        return None


class SimulatedDiscovery:
    name = "simulated"

    def __init__(self, lab: SimulatedLab):
        self.lab = lab

    def discover(self) -> list[Drive]:
        return [replace(s.drive, methods=[], limitations=list(s.drive.limitations),
                        capabilities={k: dict(v) if isinstance(v, dict) else v
                                      for k, v in s.drive.capabilities.items()})
                for s in self.lab.drives if s.present]

    def diagnostics(self) -> dict:
        return {"simulated": True,
                "drives": [{"disk": s.drive.disk_number, "scenario": s.scenario.value}
                           for s in self.lab.drives]}


NVME_BITS = {MethodId.NVME_SANITIZE_CRYPTO: 0b001, MethodId.NVME_SANITIZE_BLOCK: 0b010,
             MethodId.NVME_SANITIZE_OVERWRITE: 0b100}
ATA_SIM = {MethodId.ATA_SECURITY_ERASE, MethodId.ATA_SECURITY_ERASE_ENHANCED,
           MethodId.ATA_SANITIZE_CRYPTO, MethodId.ATA_SANITIZE_BLOCK, MethodId.ATA_SANITIZE_OVERWRITE}


class SimulatedBackend:
    name = "simulated"
    methods = frozenset(NVME_BITS) | frozenset(ATA_SIM) | OVERWRITE_METHODS
    METHODS = NVME_BITS       # kept for older callers

    def __init__(self, lab: SimulatedLab):
        self.lab = lab

    def handles(self, drive: Drive) -> bool:
        return True

    def reachability(self, drive: Drive, method: MethodId) -> tuple[Availability, str]:
        if method not in self.methods:
            return Availability.NOT_IMPLEMENTED, "Not simulated"
        if method in NVME_BITS and drive.protocol != Protocol.NVME:
            return Availability.NOT_DECLARED, "Not an NVMe drive"
        if method in ATA_SIM:
            if drive.protocol != Protocol.ATA:
                return Availability.NOT_DECLARED, "Not an ATA drive"
            if (drive.capabilities.get("ata") or {}).get("frozen"):
                return Availability.DECLARED_NOT_REACHABLE, "Frozen by the BIOS; use Unfreeze"
        return Availability.AVAILABLE, "SIMULATED route"

    def unfreeze(self) -> str:
        for s in self.lab.drives:
            ata = s.drive.capabilities.get("ata")
            if ata:
                ata["frozen"] = False
        return "Sleep and wake completed (simulated)."

    def read_identity(self, drive: Drive) -> DriveIdentity | None:
        s = self.lab.find(drive)
        if s is None or not s.present:
            return None
        if s.scenario == Scenario.IDENTITY_CHANGE:
            return replace(s.drive.identity, serial=s.drive.identity.serial + "-OTHER")
        return s.drive.identity

    def execute(self, drive: Drive, method: MethodId, cancel: threading.Event,
                progress, options: dict | None = None) -> ExecutionEvidence:
        s = self.lab.find(drive)
        overwrite = method in OVERWRITE_METHODS
        ev = ExecutionEvidence(route="SIMULATED" + (" host-overwrite" if overwrite else ""),
                               command=f"SIMULATED {method.value}",
                               parameters={"method": method.value, **(options or {})})
        if s is None or not s.present:
            ev.communication_failure = True
            ev.disconnected = True
            ev.errors.append("Drive not present")
            return ev
        ev.accepted = True
        if s.scenario == Scenario.FAIL:
            ev.errors.append("Device reported: Sanitize failed (simulated SSTAT=3)")
            ev.device_started = True
            ev.device_status = "Most recent sanitize failed"
            return ev
        if s.scenario == Scenario.UNEXPECTED_RESPONSE:
            ev.errors.append("Unexpected response buffer (simulated)")
            return ev
        hpa = (drive.capabilities.get("ata") or {}).get("hpa_hidden_sectors")
        if hpa and (options or {}).get("reset_hidden_areas"):
            ev.parameters["hpa"] = {"opened": True, "hidden_sectors": hpa, "simulated": True}
        ev.device_started = True
        passes = method_info(method).passes or ("device",)
        total = self.lab.steps * len(passes)
        k = 0
        for p_idx, pat in enumerate(passes, 1):
            for i in range(1, self.lab.steps + 1):
                k += 1
                if cancel.is_set():
                    ev.errors.append("Cancelled by operator while device was working")
                    return ev
                time.sleep(self.lab.step_delay)
                if s.scenario == Scenario.DISCONNECT and i == self.lab.steps // 2:
                    s.present = False
                    ev.disconnected = True
                    ev.communication_failure = True
                    ev.errors.append("Drive disconnected during operation (simulated)")
                    return ev
                if s.scenario == Scenario.TIMEOUT and i == self.lab.steps // 2:
                    ev.timed_out = True
                    ev.errors.append("No completion reported before timeout (simulated)")
                    return ev
                frac = k / total
                ev.progress_samples.append((f"t{k}", frac))
                label = (f"Pass {p_idx}/{len(passes)} ({pat}), simulated" if overwrite
                         else "Device sanitizing (simulated)")
                progress(label, frac)
        if s.scenario == Scenario.INCONCLUSIVE:
            ev.device_status = ""     # device gives no status at all
            return ev
        ev.device_completed = True
        ev.device_status = ("All passes written (simulated)" if overwrite
                            else "Most recent sanitize completed successfully")
        ev.device_raw = {"sstat": 1, "simulated": True}
        s.sanitized = True
        return ev

    def post_checks(self, drive, method, evidence) -> list[dict]:
        s = self.lab.find(drive)
        if s is None or s.scenario == Scenario.INCONCLUSIVE or not evidence.device_completed:
            return []
        if method in OVERWRITE_METHODS:
            return [{"check": "Read-back of written pattern (simulated)", "passed": True,
                     "shows": "Simulated read-back matched. Covers host-visible blocks only."}]
        return [
            {"check": "Device status reports success", "passed": True,
             "shows": "The device itself reports the erase completed."},
            {"check": "Sample reads (64 blocks spread over the drive)", "passed": True,
             "shows": "Sampled blocks no longer hold the pre-erase data. "
                      "Does not prove every physical block was erased."},
        ]
