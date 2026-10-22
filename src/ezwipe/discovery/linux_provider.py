"""Read-only drive discovery on Linux (the EZWipe boot USB).

Sources: lsblk for the disk list, then per drive nvme-cli (NVMe) or
hdparm + smartctl (SATA). Nothing here writes to a drive.

Protection: a disk is protected when any of its partitions is mounted at
one of the protected mount points: the live medium (the EZWipe USB itself),
the EZWipe data partition, or / when EZWipe runs on an installed system.
"""

from __future__ import annotations

import re

from ..core.models import BusType, ConnectionPath, Drive, DriveIdentity, Protocol
from ..platform.linux import parsers as P
from ..platform.linux.runner import Runner

LIVE_MEDIUM = "/run/live/medium"

TRAN = {
    "nvme": (Protocol.NVME, BusType.NVME, ConnectionPath.PCIE_NVME),
    "sata": (Protocol.ATA, BusType.SATA, ConnectionPath.INTERNAL_SATA),
    "ata": (Protocol.ATA, BusType.ATA, ConnectionPath.INTERNAL_SATA),
    "usb": (Protocol.UNKNOWN, BusType.USB, ConnectionPath.USB_BRIDGE),
    "sas": (Protocol.SCSI, BusType.SAS, ConnectionPath.UNKNOWN),
    "spi": (Protocol.SCSI, BusType.SCSI, ConnectionPath.UNKNOWN),
    "mmc": (Protocol.UNKNOWN, BusType.SD, ConnectionPath.UNKNOWN),
}


class LinuxDiscovery:
    name = "linux"

    def __init__(self, runner: Runner | None = None, protected_mounts: dict[str, str] | None = None):
        self.runner = runner or Runner()
        self.protected_mounts = protected_mounts or {
            LIVE_MEDIUM: "This is the EZWipe boot USB.",
            "/": "Holds the running Linux system.",
        }
        self._raw: dict = {}

    def protect_mount(self, mountpoint: str, reason: str) -> None:
        self.protected_mounts[mountpoint] = reason

    # ------------------------------------------------------------------ discover
    def _lsblk(self, *paths: str) -> list[dict]:
        r = self.runner.run(["lsblk", "-J", "-b", "-o", P.LSBLK_COLUMNS, *paths], timeout=30)
        self._raw.setdefault("lsblk", []).append(r.record(20000))
        return P.parse_lsblk(r.stdout) if r.ok else []

    def discover(self) -> list[Drive]:
        self._raw = {}
        return [self._build(d) for d in self._lsblk()]

    def diagnostics(self) -> dict:
        return self._raw

    def _build(self, d: dict) -> Drive:
        name = d["name"]
        tran = d["tran"] or ("nvme" if name.startswith("nvme") else
                             "mmc" if name.startswith("mmcblk") else "")
        protocol, bus, conn = TRAN.get(tran, (Protocol.UNKNOWN, BusType.UNKNOWN,
                                              ConnectionPath.UNKNOWN))
        ident = DriveIdentity(serial=d["serial"], model=d["model"], capacity_bytes=d["size"],
                              vendor=d["vendor"], firmware=d["rev"])
        drive = Drive(device_path=d["path"], disk_number=None, identity=ident, protocol=protocol,
                      bus_type=bus, connection=conn, is_ssd=not d["rota"], removable=d["removable"])
        if d["read_only"]:
            drive.capabilities["read_only"] = True
        for mp in d["mounts"]:
            if mp in self.protected_mounts:
                drive.is_system_disk = True
                drive.protection_reason = self.protected_mounts[mp]
        drive.mounted_volumes = [m for m in d["mounts"] if m not in self.protected_mounts]
        if "EZWIPE-DATA" in d["labels"] and not drive.is_system_disk:
            drive.is_system_disk = True
            drive.protection_reason = "Holds the EZWipe reports partition."

        if protocol == Protocol.NVME:
            self._nvme(drive, name)
        elif protocol == Protocol.ATA:
            self._ata(drive)
        drive.identity_confident = bool(drive.identity.serial) and drive.identity.capacity_bytes > 0
        if not drive.identity.serial:
            drive.limitations.append("No serial number reported; EZWipe cannot re-check the target.")
        return drive

    def _nvme(self, drive: Drive, name: str) -> None:
        ctrl = "/dev/" + re.sub(r"n\d+$", "", name)
        r = self.runner.run(["nvme", "id-ctrl", ctrl, "-o", "json"])
        idc = P.parse_nvme_id_ctrl(r.stdout) if r.ok else None
        self._raw.setdefault("nvme", {})[name] = {"id_ctrl": r.record()}
        if r.missing_tool:
            drive.limitations.append("nvme-cli is not installed.")
        if idc is None:
            return
        ns = P.parse_nvme_id_ns(self.runner.run(["nvme", "id-ns", drive.device_path, "-o", "json"]).stdout)
        san = P.parse_nvme_sanitize_log(self.runner.run(["nvme", "sanitize-log", ctrl, "-o", "json"]).stdout)
        drive.capabilities["nvme"] = {"sanicap": idc["sanicap"], "oacs": idc["oacs"], "fna": idc["fna"],
                                      "nn": idc["nn"], "controller": ctrl,
                                      "sanitize_log_readable": san is not None,
                                      "sanitize_in_progress": bool(san and san["status"] == 2),
                                      "last_sanitize": san["text"] if san else None}
        i = drive.identity
        drive.identity = DriveIdentity(serial=idc["serial"] or i.serial, model=idc["model"] or i.model,
                                       capacity_bytes=i.capacity_bytes, vendor=i.vendor,
                                       firmware=idc["firmware"] or i.firmware,
                                       nvme_eui64=(ns or {}).get("eui64", ""),
                                       nvme_nguid=(ns or {}).get("nguid", ""))
        if idc["nn"] > 1:
            drive.limitations.append(f"Controller has {idc['nn']} namespaces; Sanitize erases all "
                                     "of them, Format may erase only this one.")
        sm = P.parse_nvme_smart_log(self.runner.run(["nvme", "smart-log", ctrl, "-o", "json"]).stdout)
        if sm:
            drive.temperature_c = sm["temperature_c"]
            drive.power_on_hours = sm["power_on_hours"]
            drive.wear_percent = sm["percent_used"]
            if sm["critical_warning"]:
                drive.health_notes.append(f"NVMe critical warning bits 0x{sm['critical_warning']:02X}")
            if sm["media_errors"]:
                drive.health_notes.append(f"{sm['media_errors']} media errors logged")

    def _ata(self, drive: Drive) -> None:
        dev = drive.device_path
        r = self.runner.run(["hdparm", "-I", dev])
        self._raw.setdefault("hdparm", {})[dev] = r.record(20000)
        if r.missing_tool:
            drive.limitations.append("hdparm is not installed.")
        idf = P.parse_hdparm_identify(r.stdout) if r.ok else None
        if idf:
            hpa = P.parse_hdparm_max_sectors(self.runner.run(["hdparm", "-N", dev]).stdout)
            dco_real = P.parse_hdparm_dco(self.runner.run(["hdparm", "--dco-identify", dev]).stdout)
            caps = dict(idf)
            caps["hpa_hidden_sectors"] = hpa["hpa_hidden_sectors"] if hpa else 0
            caps["native_max_sectors"] = hpa["native"] if hpa else None
            if dco_real and hpa and dco_real > hpa["native"]:
                caps["dco_hidden_sectors"] = dco_real - hpa["native"]
            drive.capabilities["ata"] = caps
            if idf["ssd"] is not None:
                drive.is_ssd = idf["ssd"]
            i = drive.identity
            drive.identity = DriveIdentity(serial=idf["serial"] or i.serial, model=idf["model"] or i.model,
                                           capacity_bytes=i.capacity_bytes, vendor=i.vendor,
                                           firmware=idf["firmware"] or i.firmware)
        sm = P.parse_smartctl(self.runner.run(["smartctl", "-j", "-a", dev]).stdout)
        if sm:
            drive.temperature_c = sm["temperature_c"]
            drive.power_on_hours = sm["power_on_hours"]
            drive.wear_percent = sm["percent_used"]
            if sm["smart_passed"] is False:
                drive.health_notes.append("SMART overall health check FAILED")
            if sm["reallocated_sectors"]:
                drive.health_notes.append(f"{sm['reallocated_sectors']} reallocated sectors")

    # ------------------------------------------------------------------ re-check
    def read_identity(self, drive: Drive) -> DriveIdentity | None:
        """Identity of whatever is at drive.device_path right now (spec 7.2)."""
        found = self._lsblk(drive.device_path)
        if not found:
            return None
        fresh = self._build(found[0])
        return fresh.identity
