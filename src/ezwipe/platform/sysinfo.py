"""Host system information for certificates (vendor, model, serial, CPU, RAM).

Read-only. On Linux it reads /sys/class/dmi/id and /proc; elsewhere it
falls back to what the platform module offers. Missing values stay empty.
"""

from __future__ import annotations

import os
import platform
from pathlib import Path


def _read(p: str) -> str:
    try:
        return Path(p).read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return ""


def _cpu_linux() -> str:
    for line in _read("/proc/cpuinfo").splitlines():
        if line.lower().startswith("model name"):
            return line.split(":", 1)[1].strip()
    return ""


def _ram_linux() -> int:
    for line in _read("/proc/meminfo").splitlines():
        if line.startswith("MemTotal:"):
            return int(line.split()[1]) * 1024
    return 0


def collect() -> dict:
    if platform.system() == "Linux":
        dmi = "/sys/class/dmi/id/"
        info = {
            "vendor": _read(dmi + "sys_vendor"),
            "model": _read(dmi + "product_name"),
            "version": _read(dmi + "product_version"),
            "serial": _read(dmi + "product_serial"),     # needs root
            "board": _read(dmi + "board_name"),
            "bios": " ".join(x for x in (_read(dmi + "bios_vendor"), _read(dmi + "bios_version")) if x),
            "cpu": _cpu_linux(),
            "ram_bytes": _ram_linux(),
            "firmware_mode": "UEFI" if os.path.isdir("/sys/firmware/efi") else "BIOS",
        }
    else:
        info = {"vendor": "", "model": platform.node(), "version": "", "serial": "", "board": "",
                "bios": "", "cpu": platform.processor(), "ram_bytes": 0, "firmware_mode": ""}
    info["os"] = platform.platform()
    return info


def describe(info: dict) -> str:
    """One line for the interface header."""
    name = " ".join(x for x in (info.get("vendor"), info.get("model")) if x) or "Unknown system"
    sn = info.get("serial")
    return f"{name}  SN {sn}" if sn and sn.lower() not in ("to be filled by o.e.m.", "default string") \
        else name
