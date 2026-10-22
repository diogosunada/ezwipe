"""Capability detection: declared by the drive vs reachable through the OS.

Never sends anything destructive. A method is offered only when the drive
declares it AND a backend confirms the route (spec 3.4, 4.1, 5.2).
Host overwrite needs no declaration: any drive the host can write to
"declares" it, and the overwrite backend decides whether it is reachable.
"""

from __future__ import annotations

from ..backends.base import backend_for
from ..core.models import (OVERWRITE_METHODS, Availability, BusType, ConnectionPath, Drive,
                           MethodId, MethodOption, Protocol)

NVME_SANITIZE_BITS = {
    MethodId.NVME_SANITIZE_CRYPTO: 0x1,
    MethodId.NVME_SANITIZE_BLOCK: 0x2,
    MethodId.NVME_SANITIZE_OVERWRITE: 0x4,
}

ATA_METHODS = (MethodId.ATA_SANITIZE_CRYPTO, MethodId.ATA_SANITIZE_BLOCK,
               MethodId.ATA_SANITIZE_OVERWRITE, MethodId.ATA_SECURITY_ERASE,
               MethodId.ATA_SECURITY_ERASE_ENHANCED)


def declared_methods(drive: Drive) -> dict[MethodId, bool | None]:
    """What the drive itself says. None = unknown (could not read)."""
    out: dict[MethodId, bool | None] = {}
    if drive.protocol == Protocol.NVME:
        nv = drive.capabilities.get("nvme")
        if nv is None:
            for m in (*NVME_SANITIZE_BITS, MethodId.NVME_FORMAT_USER_DATA, MethodId.NVME_FORMAT_CRYPTO):
                out[m] = None
        else:
            for m, bit in NVME_SANITIZE_BITS.items():
                out[m] = bool(nv["sanicap"] & bit)
            fmt = bool(nv["oacs"] & 0x2)
            out[MethodId.NVME_FORMAT_USER_DATA] = fmt
            out[MethodId.NVME_FORMAT_CRYPTO] = fmt and bool(nv["fna"] & 0x4)
    elif drive.protocol == Protocol.ATA:
        ata = drive.capabilities.get("ata")
        for m in ATA_METHODS:
            out[m] = None if ata is None else bool(ata.get(m.value))
    if drive.identity.capacity_bytes > 0:
        for m in (MethodId.OVERWRITE_ADDRESSABLE, MethodId.OVERWRITE_ZERO, MethodId.OVERWRITE_DOD_3PASS):
            out[m] = True
    return out


def evaluate(drive: Drive, backends: list) -> Drive:
    """Fill drive.methods and drive.limitations. Returns the same drive."""
    drive.methods = []

    if drive.bus_type == BusType.USB or drive.connection == ConnectionPath.USB_BRIDGE:
        drive.limitations.append(
            "Identified through a USB bridge. Firmware sanitize commands are usually not passed "
            "through, so only host overwrite is offered. For an NVMe enclosure such as the "
            "TerraMaster D4, connect it to a USB4 or Thunderbolt port so the drives show as NVMe.")
    if drive.protocol == Protocol.NVME and "nvme" not in drive.capabilities:
        drive.limitations.append(
            "Drive identified, but NVMe capabilities could not be read. "
            "Run with administrator/root rights, or check the connection.")
    ata = drive.capabilities.get("ata") or {}
    if ata.get("frozen"):
        drive.limitations.append(
            "Security is frozen by the BIOS. Use Unfreeze (sleep and wake) to unlock "
            "ATA Secure Erase and Sanitize.")
    if ata.get("hpa_hidden_sectors"):
        drive.limitations.append(
            f"Host protected area hides {ata['hpa_hidden_sectors']:,} sectors. "
            "Profiles that allow it open the HPA before erasing.")
    if ata.get("dco_hidden_sectors"):
        drive.limitations.append(
            f"Device configuration overlay hides {ata['dco_hidden_sectors']:,} sectors. "
            "EZWipe reports it but does not reset DCO yet.")

    for method, declared in declared_methods(drive).items():
        if declared is False:
            drive.methods.append(MethodOption(method, Availability.NOT_DECLARED,
                                              "The drive does not declare support"))
            continue
        backend = backend_for(backends, drive, method)
        if backend is None:
            if method in OVERWRITE_METHODS:
                continue      # this edition has no overwrite backend; don't clutter the list
            drive.methods.append(MethodOption(method, Availability.NOT_IMPLEMENTED,
                                              "No backend in this edition can send this method"))
            continue
        avail, reason = backend.reachability(drive, method)
        if declared is None and avail == Availability.AVAILABLE:
            # Never offer a destructive method the drive did not confirm.
            avail, reason = Availability.UNKNOWN, "Drive support could not be confirmed"
        drive.methods.append(MethodOption(method, avail, reason))

    if drive.is_system_disk:
        drive.limitations.append(drive.protection_reason
                                 or "Holds the running operating system. Protected.")
    return drive


RECOMMEND_ORDER = [
    (MethodId.NVME_SANITIZE_CRYPTO, "Fastest Purge-class method; erases all user data, "
                                    "including caches and unallocated blocks."),
    (MethodId.NVME_SANITIZE_BLOCK, "Purge-class method that erases every block; "
                                   "used when Crypto Erase is not available."),
    (MethodId.NVME_SANITIZE_OVERWRITE, "Purge-class device overwrite; slow, last NVMe option."),
    (MethodId.ATA_SANITIZE_CRYPTO, "ATA Sanitize Crypto Scramble; fastest SATA Purge."),
    (MethodId.ATA_SANITIZE_BLOCK, "ATA Sanitize Block Erase; SATA Purge for SSDs."),
    (MethodId.ATA_SANITIZE_OVERWRITE, "ATA Sanitize Overwrite; SATA Purge, drive-driven."),
    (MethodId.ATA_SECURITY_ERASE_ENHANCED, "ATA Enhanced Secure Erase; also erases "
                                           "reallocated sectors on most drives."),
    (MethodId.NVME_FORMAT_CRYPTO, "NVMe Format with crypto erase; no Sanitize support."),
    (MethodId.ATA_SECURITY_ERASE, "ATA Secure Erase (normal); Clear-class."),
    (MethodId.NVME_FORMAT_USER_DATA, "NVMe Format user data erase; Clear-class."),
    (MethodId.OVERWRITE_ADDRESSABLE, "No firmware method reachable: one random pass from "
                                     "the host, then read-back."),
    (MethodId.OVERWRITE_ZERO, "One pass of zeros from the host, then read-back."),
    (MethodId.OVERWRITE_DOD_3PASS, "Three host passes (legacy DoD 5220.22-M style)."),
]


def recommend(drive: Drive, allowed: list[MethodId] | None = None) -> tuple[MethodId | None, str]:
    """Pick a method and say why. The operator always sees the choice (spec 4.5)."""
    available = set(drive.available_methods)
    for method, why in RECOMMEND_ORDER:
        if method in available and (allowed is None or method in allowed):
            return method, why
    return None, "No sanitization method allowed by this profile is both supported by the drive " \
                 "and reachable."
