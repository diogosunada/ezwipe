"""Parsers for lsblk, hdparm, nvme-cli and smartctl output.

Pure functions, no I/O, so they are tested against captured output.
Anything a parser cannot read comes back as None, never as a guess.
"""

from __future__ import annotations

import json
import re

# --------------------------------------------------------------------------- lsblk

LSBLK_COLUMNS = ("NAME,PATH,TYPE,SIZE,MODEL,SERIAL,VENDOR,REV,TRAN,ROTA,RM,RO,"
                 "MOUNTPOINTS,LABEL,FSTYPE,WWN")


def _mounts(node: dict) -> list[str]:
    mps = node.get("mountpoints")
    if mps is None:
        mp = node.get("mountpoint")
        mps = [mp] if mp else []
    return [m for m in mps if m]


def _all_mounts(node: dict) -> list[str]:
    out = list(_mounts(node))
    for c in node.get("children") or []:
        out += _all_mounts(c)
    return out


def _all_labels(node: dict) -> list[str]:
    out = [node["label"]] if node.get("label") else []
    for c in node.get("children") or []:
        out += _all_labels(c)
    return out


def _flag(v) -> bool:
    return v in (True, 1, "1", "true")


def parse_lsblk(text: str) -> list[dict]:
    """Whole disks from `lsblk -J -b -o <LSBLK_COLUMNS>`.

    Skipped: loop, rom and zram devices, floppy drives (fd*), and drives with no
    media (size 0, e.g. an empty card reader)."""
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return []
    disks = []
    for n in data.get("blockdevices", []):
        if n.get("type") != "disk" or str(n.get("name", "")).startswith(("zram", "loop", "fd")):
            continue
        if not int(n.get("size") or 0):
            continue
        name = n.get("name", "")
        disks.append({
            "name": name,
            "path": n.get("path") or f"/dev/{name}",
            "size": int(n.get("size") or 0),
            "model": (n.get("model") or "").strip(),
            "serial": (n.get("serial") or "").strip(),
            "vendor": (n.get("vendor") or "").strip(),
            "rev": (n.get("rev") or "").strip(),
            "tran": (n.get("tran") or "").strip().lower(),
            "rota": _flag(n.get("rota")),
            "removable": _flag(n.get("rm")),
            "read_only": _flag(n.get("ro")),
            "wwn": (n.get("wwn") or "").strip(),
            "mounts": _all_mounts(n),
            "labels": _all_labels(n),
        })
    return disks


# --------------------------------------------------------------------------- hdparm


def _field(text: str, label: str) -> str:
    m = re.search(rf"^\s*{re.escape(label)}:\s*(.+?)\s*$", text, re.M)
    return m.group(1) if m else ""


def parse_hdparm_identify(text: str) -> dict | None:
    """`hdparm -I /dev/sdX`. Returns capability flags keyed by method id value."""
    if not text or "ATA device" not in text and "Model Number" not in text:
        return None
    sec_block = ""
    m = re.search(r"^Security:\s*\n(.*?)(?:\n\S|\Z)", text, re.M | re.S)
    if m:
        sec_block = m.group(1)

    def sec(word: str) -> bool | None:
        mm = re.search(rf"^\s*(not\s+)?{word}\b(?!:)", sec_block, re.M)
        return None if mm is None else mm.group(1) is None

    supported = bool(sec("supported"))
    enhanced = bool(re.search(r"supported:\s*enhanced erase", sec_block))
    t_norm = re.search(r"(>?\d+)min for SECURITY ERASE UNIT", sec_block)
    t_enh = re.search(r"(>?\d+)min for ENHANCED SECURITY ERASE UNIT", sec_block)

    def mins(mm):
        return int(mm.group(1).lstrip(">")) if mm else None

    has = lambda pat: bool(re.search(pat, text, re.M))  # noqa: E731
    sanitize = has(r"^\s*\*?\s*SANITIZE feature set")
    rate = _field(text, "Nominal Media Rotation Rate")
    lba48 = re.search(r"LBA48\s+user addressable sectors:\s*(\d+)", text)
    lss = re.search(r"Logical\s+Sector size:\s*(\d+)", text)
    return {
        "model": _field(text, "Model Number"),
        "serial": _field(text, "Serial Number"),
        "firmware": _field(text, "Firmware Revision"),
        "security_supported": supported,
        "security_enabled": bool(sec("enabled")),
        "locked": bool(sec("locked")),
        "frozen": bool(sec("frozen")),
        "erase_minutes": mins(t_norm),
        "enhanced_erase_minutes": mins(t_enh),
        "sanitize_supported": sanitize,
        "trim": has(r"Data Set Management TRIM supported"),
        "ssd": ("Solid State" in rate) if rate else None,
        "rotation_rpm": int(rate) if rate.isdigit() else None,
        "lba48_sectors": int(lba48.group(1)) if lba48 else None,
        "logical_sector_size": int(lss.group(1)) if lss else 512,
        "ata.security_erase": supported,
        "ata.security_erase_enhanced": supported and enhanced,
        "ata.sanitize.crypto_scramble": sanitize and has(r"CRYPTO_SCRAMBLE_EXT command"),
        "ata.sanitize.block_erase": sanitize and has(r"BLOCK_ERASE_EXT command"),
        "ata.sanitize.overwrite": sanitize and has(r"OVERWRITE_EXT command"),
    }


def parse_hdparm_max_sectors(text: str) -> dict | None:
    """`hdparm -N /dev/sdX` -> current and native max sectors (HPA)."""
    m = re.search(r"max sectors\s*=\s*(\d+)\s*/\s*(\d+)", text or "")
    if not m:
        return None
    cur, native = int(m.group(1)), int(m.group(2))
    return {"current": cur, "native": native, "hpa_hidden_sectors": max(0, native - cur),
            "hpa_enabled": "HPA is enabled" in text}


def parse_hdparm_dco(text: str) -> int | None:
    """`hdparm --dco-identify /dev/sdX` -> real max sectors, or None."""
    m = re.search(r"Real max sectors:\s*(\d+)", text or "")
    return int(m.group(1)) if m else None


def parse_hdparm_sanitize_status(text: str) -> dict:
    """`hdparm --sanitize-status /dev/sdX`.

    state: SD0 idle, SD1 frozen, SD2 operation in process.
    succeeded: True when the last sanitize completed without error, False when it
    reports an error, None when the output says neither.
    """
    t = text or ""
    st = re.search(r"State:\s*(SD\d)", t)
    pr = re.search(r"Progress:\s*0x([0-9a-fA-F]+)\s*\((\d+)%\)", t)
    succeeded = None
    if re.search(r"Completed Without Error", t, re.I):
        succeeded = True
    elif re.search(r"(Completed With Error|failed)", t, re.I):
        succeeded = False
    return {"state": st.group(1) if st else None,
            "progress": int(pr.group(2)) / 100 if pr else None,
            "succeeded": succeeded}


# --------------------------------------------------------------------------- nvme-cli


def _json(text: str):
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return None


def _find_key(obj, key):
    """First value for key anywhere in nested dicts (nvme-cli nests some logs by device)."""
    if isinstance(obj, dict):
        if key in obj:
            return obj[key]
        for v in obj.values():
            r = _find_key(v, key)
            if r is not None:
                return r
    elif isinstance(obj, list):
        for v in obj:
            r = _find_key(v, key)
            if r is not None:
                return r
    return None


def parse_nvme_id_ctrl(text: str) -> dict | None:
    d = _json(text)
    if not isinstance(d, dict) or "sanicap" not in d:
        return None
    return {"serial": str(d.get("sn", "")).strip(), "model": str(d.get("mn", "")).strip(),
            "firmware": str(d.get("fr", "")).strip(), "sanicap": int(d.get("sanicap", 0)),
            "oacs": int(d.get("oacs", 0)), "fna": int(d.get("fna", 0)), "nn": int(d.get("nn", 1)),
            "vid": int(d.get("vid", 0))}


def parse_nvme_id_ns(text: str) -> dict | None:
    d = _json(text)
    if not isinstance(d, dict) or "nsze" not in d:
        return None

    def hexid(v) -> str:
        s = (f"{v:x}" if isinstance(v, int) else str(v or "")).strip().lower()
        s = s[2:] if s.startswith("0x") else s
        return s if s.strip("0") else ""       # all zeros = not reported

    lbafs = d.get("lbafs") or d.get("lbaf") or []
    flbas = int(d.get("flbas", 0)) & 0xF
    ds = lbafs[flbas].get("ds", 9) if len(lbafs) > flbas and isinstance(lbafs[flbas], dict) else 9
    return {"nsze": int(d["nsze"]), "lba_size": 2 ** int(ds),
            "eui64": hexid(d.get("eui64")), "nguid": hexid(d.get("nguid"))}


def parse_nvme_smart_log(text: str) -> dict | None:
    d = _json(text)
    if not isinstance(d, dict):
        return None
    temp = _find_key(d, "temperature")
    used = _find_key(d, "percent_used")
    if used is None:
        used = _find_key(d, "percentage_used")
    poh = _find_key(d, "power_on_hours")
    if temp is None and used is None and poh is None:
        return None
    t = int(temp) if temp is not None else None
    if t is not None and t > 200:          # nvme-cli reports Kelvin
        t -= 273
    return {"temperature_c": t, "percent_used": int(used) if used is not None else None,
            "power_on_hours": int(poh) if poh is not None else None,
            "critical_warning": int(_find_key(d, "critical_warning") or 0),
            "media_errors": int(_find_key(d, "media_errors") or 0)}


SSTAT_TEXT = {0: "Never sanitized", 1: "Most recent sanitize completed successfully",
              2: "Sanitize in progress", 3: "Most recent sanitize failed",
              4: "Completed successfully (no-deallocate)"}


def parse_nvme_sanitize_log(text: str) -> dict | None:
    d = _json(text)
    sstat = _find_key(d, "sstat")
    if sstat is None:
        return None
    sprog = _find_key(d, "sprog")
    code = int(sstat) & 0x7
    return {"sstat": int(sstat), "status": code, "text": SSTAT_TEXT.get(code, f"Status {code}"),
            "progress": (int(sprog) / 65536) if sprog is not None and code == 2 else None,
            "succeeded": True if code in (1, 4) else False if code == 3 else None}


# --------------------------------------------------------------------------- smartctl


def parse_smartctl(text: str) -> dict | None:
    """`smartctl -j -a /dev/sdX` -> health summary."""
    d = _json(text)
    if not isinstance(d, dict):
        return None
    poh = (d.get("power_on_time") or {}).get("hours")
    temp = (d.get("temperature") or {}).get("current")
    used = (d.get("endurance_used") or {}).get("current_percent")
    passed = (d.get("smart_status") or {}).get("passed")
    realloc = None
    for a in (d.get("ata_smart_attributes") or {}).get("table", []):
        if a.get("id") == 5:
            realloc = (a.get("raw") or {}).get("value")
    if poh is None and temp is None and passed is None:
        return None
    return {"power_on_hours": poh, "temperature_c": temp, "percent_used": used,
            "smart_passed": passed, "reallocated_sectors": realloc,
            "rotation_rate": d.get("rotation_rate")}
