"""Read-only diagnostic for step 3 of the plan.

Lists every physical disk, what Windows reports about it, which NVMe data
could be read, and which sanitize methods each drive declares. It sends no
destructive command and writes nothing to any disk. The only file written
is the JSON report in the chosen folder.

Usage (in an administrator terminal):
    ezwipe-diag                 # print summary and save JSON in the current folder
    ezwipe-diag --out C:\\temp
    ezwipe-diag --simulate      # demo without real hardware
"""

from __future__ import annotations

import argparse
import json
import platform
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from .. import __version__
from ..capability import detector


def _gb(n: int | None) -> str:
    return f"{n / 10**9:,.0f} GB" if n else "?"


def collect(simulate: bool) -> tuple[list, dict, dict]:
    from ..editions import build_edition
    ed = build_edition(simulate)
    disc, backends, admin = ed.discovery, ed.backends, ed.admin
    drives = disc.discover()
    from ..safety.manager import SafetyManager
    SafetyManager.flag_shared_identities(drives)
    for d in drives:
        detector.evaluate(d, backends)
    env = {
        "ezwipe_version": __version__,
        "python": sys.version.split()[0],
        "os": platform.platform(),
        "windows_build": platform.version(),
        "machine": platform.node(),
        "is_admin": admin,
        "edition": ed.name,
        "simulated": ed.name == "simulated",
        "time_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    return drives, disc.diagnostics(), env


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="EZWipe read-only drive diagnostic")
    ap.add_argument("--out", default=".", help="folder for the JSON report")
    ap.add_argument("--simulate", action="store_true", help="use simulated drives")
    args = ap.parse_args(argv)

    drives, raw, env = collect(args.simulate)
    print(f"EZWipe {env['ezwipe_version']} read-only diagnostic  |  {env['os']}")
    if env["is_admin"] is False:
        print("WARNING: not running as administrator. NVMe data and disk sizes will be missing.")
    print()
    for d in drives:
        flag = "PROTECTED" if d.is_protected else ""
        print(f"[{d.disk_number}] {d.identity.model}  SN {d.identity.serial or '?'}  "
              f"{_gb(d.identity.capacity_bytes)}  bus={d.bus_type.value}  {flag}")
        print(f"     path: {d.connection.value}; volumes: {', '.join(d.mounted_volumes) or 'none'}")
        nv = d.capabilities.get("nvme")
        if nv:
            print(f"     NVMe SANICAP=0x{nv['sanicap']:08X} OACS=0x{nv['oacs']:04X} FNA=0x{nv['fna']:02X} "
                  f"sanitize log readable={nv['sanitize_log_readable']}")
        for m in d.methods:
            print(f"     - {m.method.value:32} {m.availability.value}")
        for lim in d.limitations:
            print(f"     ! {lim}")
        print()

    report = {
        "environment": env,
        "drives": [{**asdict(d), "is_protected": d.is_protected,
                    "available_methods": [m.value for m in d.available_methods]} for d in drives],
        "raw": raw,
    }
    out = Path(args.out) / f"ezwipe-diagnostic-{datetime.now():%Y%m%d-%H%M%S}.json"
    out.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print(f"Report saved to {out.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
