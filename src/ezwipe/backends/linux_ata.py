"""ATA/SATA backend for the boot USB (Linux, hdparm).

Security Erase flow (ACS-3):
  1. refuse if the drive is frozen, locked, or already has a password,
  2. set a temporary user password ("EZWipe"),
  3. confirm security is now enabled,
  4. SECURITY ERASE UNIT (normal or enhanced) and wait; the drive gives no progress,
  5. confirm security is disabled again (a successful erase clears the password).
If anything fails after step 2, EZWipe tries to remove the password and the
evidence records it, so the operator can unlock the drive with "EZWipe".

Sanitize flow: SANITIZE command, then poll SANITIZE STATUS until the drive
reports completion without error.

Frozen drives: the BIOS issues SECURITY FREEZE LOCK at boot. A sleep/wake
cycle (rtcwake) usually lifts it; the operator starts it from the interface.
"""

from __future__ import annotations

import threading
import time
from typing import Callable

from ..core.models import (Availability, BusType, ConnectionPath, Drive, DriveIdentity,
                           ExecutionEvidence, MethodId, Protocol)
from ..platform.linux.parsers import (parse_hdparm_identify, parse_hdparm_max_sectors,
                                      parse_hdparm_sanitize_status)
from ..platform.linux.runner import Runner
from . import sampling

M = MethodId
PASSWORD = "EZWipe"
SECURITY = {M.ATA_SECURITY_ERASE: "--security-erase",
            M.ATA_SECURITY_ERASE_ENHANCED: "--security-erase-enhanced"}
SANITIZE = {M.ATA_SANITIZE_CRYPTO: ["--sanitize-crypto-scramble"],
            M.ATA_SANITIZE_BLOCK: ["--sanitize-block-erase"],
            M.ATA_SANITIZE_OVERWRITE: ["--sanitize-overwrite", "hex:00000000"]}
DANGER = "--yes-i-know-what-i-am-doing"


def open_hpa(runner: Runner, drive: Drive) -> dict:
    """Set max sectors back to native so the host can reach the hidden area.

    The 'p' prefix makes the change permanent, as erasing requires. Returns what
    happened, for the evidence."""
    dev = drive.device_path
    before = parse_hdparm_max_sectors(runner.run(["hdparm", "-N", dev]).stdout)
    if not before or not before["hpa_hidden_sectors"]:
        return {"opened": False, "reason": "no HPA found", "before": before}
    r = runner.run(["hdparm", DANGER, "-N", f"p{before['native']}", dev], timeout=60)
    after = parse_hdparm_max_sectors(runner.run(["hdparm", "-N", dev]).stdout)
    runner.run(["blockdev", "--rereadpt", dev], timeout=30)
    opened = bool(after and after["hpa_hidden_sectors"] == 0)
    return {"opened": opened, "before": before, "after": after, "command": r.record()}


class LinuxAtaBackend:
    name = "linux-hdparm"
    methods = frozenset(SECURITY) | frozenset(SANITIZE)

    def __init__(self, runner: Runner, identity_reader: Callable[[Drive], DriveIdentity | None],
                 poll_s: float = 5.0, sample_count: int = 64):
        self.runner = runner
        self.identity_reader = identity_reader
        self.poll_s = poll_s
        self.sample_count = sample_count
        self._samples: dict[str, dict] = {}

    def handles(self, drive: Drive) -> bool:
        return (drive.protocol == Protocol.ATA and drive.bus_type in (BusType.SATA, BusType.ATA)
                and drive.connection != ConnectionPath.USB_BRIDGE)

    def reachability(self, drive: Drive, method: MethodId) -> tuple[Availability, str]:
        if method not in self.methods:
            return Availability.NOT_DECLARED, "Not an ATA method"
        ata = drive.capabilities.get("ata")
        if not ata:
            return Availability.UNKNOWN, "ATA identify data could not be read"
        if ata.get("locked"):
            return Availability.DECLARED_NOT_REACHABLE, "Drive is locked with an unknown password"
        if ata.get("frozen"):
            return Availability.DECLARED_NOT_REACHABLE, "Frozen by the BIOS; use Unfreeze"
        if method in SECURITY and ata.get("security_enabled"):
            return Availability.DECLARED_NOT_REACHABLE, "A security password is already set"
        return Availability.AVAILABLE, f"hdparm on {drive.device_path}"

    def read_identity(self, drive: Drive) -> DriveIdentity | None:
        return self.identity_reader(drive)

    def unfreeze(self) -> str:
        r = self.runner.run(["rtcwake", "-m", "mem", "-s", "6"], timeout=180)
        if r.ok:
            time.sleep(3)          # let the disks come back
            return "Sleep and wake completed."
        return f"Sleep and wake failed (rc {r.returncode}): {(r.stderr or r.stdout).strip()[:200]}"

    def _identify(self, dev: str) -> dict | None:
        return parse_hdparm_identify(self.runner.run(["hdparm", "-I", dev]).stdout)

    # ------------------------------------------------------------------ execute
    def execute(self, drive: Drive, method: MethodId, cancel: threading.Event, progress,
                options: dict | None = None) -> ExecutionEvidence:
        options = options or {}
        dev = drive.device_path
        ev = ExecutionEvidence(route="hdparm")
        before = sampling.read_samples(dev, drive.identity.capacity_bytes, self.sample_count)
        self._samples[drive.identity.key()] = before
        ev.parameters["samples_before"] = {"count": len(before["offsets"]), "error": before["error"]}

        hidden = (drive.capabilities.get("ata") or {}).get("hpa_hidden_sectors") or 0
        if hidden and options.get("reset_hidden_areas"):
            ev.parameters["hpa"] = open_hpa(self.runner, drive)
            if not ev.parameters["hpa"].get("opened"):
                ev.errors.append("Host protected area could not be opened before the erase.")
        if method in SECURITY:
            return self._security_erase(drive, method, ev, progress)
        return self._sanitize(drive, method, ev, cancel, progress)

    def _security_erase(self, drive, method, ev, progress) -> ExecutionEvidence:
        dev = drive.device_path
        ata = drive.capabilities.get("ata") or {}
        est = ata.get("enhanced_erase_minutes" if method == M.ATA_SECURITY_ERASE_ENHANCED
                      else "erase_minutes")
        ev.parameters.update({"temporary_password": PASSWORD, "drive_estimate_min": est})

        setp = self.runner.run(["hdparm", "--user-master", "u", "--security-set-pass", PASSWORD, dev])
        ev.device_raw["set_password"] = setp.record()
        idf = self._identify(dev)
        if not setp.ok or not idf or not idf["security_enabled"]:
            ev.communication_failure = setp.missing_tool
            ev.errors.append("Could not set the temporary security password; nothing was erased.")
            if idf and idf["security_enabled"]:
                self._disable_password(dev, ev)
            return ev

        argv = ["hdparm", "--user-master", "u", SECURITY[method], PASSWORD, dev]
        ev.command = " ".join(argv)
        ev.accepted = ev.device_started = True
        limit = max(3600, (est or 120) * 60 * 3)
        estimate = f"; the drive estimates {est} min" if est else ""

        def tick(elapsed: float):
            progress(f"Drive erasing{estimate}. Elapsed {int(elapsed // 60)} min. "
                     "The drive gives no progress.", None)

        r = self.runner.run_long(argv, timeout=limit, tick=tick)
        ev.device_raw["erase"] = r.record()
        after = self._identify(dev)
        ev.device_raw["identify_after"] = {k: after[k] for k in ("security_enabled", "locked",
                                                                  "frozen")} if after else None
        if r.timed_out:
            ev.timed_out = True
            ev.errors.append(f"No answer within {limit // 60} min")
            return ev
        if after is None:
            ev.disconnected = ev.communication_failure = True
            ev.errors.append("Drive did not answer after the erase")
            return ev
        if r.ok and not after["security_enabled"]:
            ev.device_completed = True
            ev.device_status = "Security erase completed; drive cleared its password"
            return ev
        ev.errors.append(f"Security erase failed (rc {r.returncode}): "
                         f"{(r.stderr or r.stdout).strip()[:300]}")
        self._disable_password(dev, ev)
        return ev

    def _disable_password(self, dev, ev):
        r = self.runner.run(["hdparm", "--user-master", "u", "--security-disable", PASSWORD, dev])
        ev.device_raw["disable_password"] = r.record()
        if not r.ok:
            ev.errors.append(f"Temporary password could not be removed. Unlock the drive with "
                             f"password '{PASSWORD}' (user).")

    def _sanitize(self, drive, method, ev, cancel, progress) -> ExecutionEvidence:
        dev = drive.device_path
        argv = ["hdparm", DANGER, *SANITIZE[method], dev]
        ev.command = " ".join(argv)
        r = self.runner.run(argv, timeout=120)
        ev.device_raw["command"] = r.record()
        if not r.ok:
            ev.communication_failure = r.missing_tool or r.timed_out
            ev.errors.append(f"Sanitize rejected (rc {r.returncode}): "
                             f"{(r.stderr or r.stdout).strip()[:300]}")
            return ev
        ev.accepted = True
        deadline = time.monotonic() + 48 * 3600
        misses = 0
        while True:
            if cancel.is_set():
                ev.errors.append("Operator stopped following the sanitize. The drive keeps "
                                 "sanitizing internally; check its status before reuse.")
                return ev
            s = self.runner.run(["hdparm", "--sanitize-status", dev], timeout=30)
            st = parse_hdparm_sanitize_status(s.stdout)
            ev.device_raw["sanitize_status"] = st
            if not s.ok or st["state"] is None:
                misses += 1
                if misses >= 6:
                    ev.disconnected = ev.communication_failure = True
                    ev.errors.append("Sanitize status stopped answering")
                    return ev
            elif st["state"] == "SD2":
                misses = 0
                ev.device_started = True
                progress("Drive sanitizing (progress reported by the drive)", st["progress"])
            elif st["succeeded"] is True:
                ev.device_started = ev.device_completed = True
                ev.device_status = "Last sanitize completed without error"
                return ev
            elif st["succeeded"] is False:
                ev.device_started = True
                ev.device_status = "Sanitize completed with error"
                ev.errors.append("Drive reports the sanitize failed")
                return ev
            if time.monotonic() > deadline:
                ev.timed_out = True
                ev.errors.append("No completion reported within 48 hours")
                return ev
            time.sleep(self.poll_s)

    def post_checks(self, drive: Drive, method: MethodId, evidence: ExecutionEvidence) -> list[dict]:
        checks = []
        if method in SECURITY:
            after = evidence.device_raw.get("identify_after") or {}
            checks.append({"check": "Drive cleared its security password after the erase",
                           "passed": after.get("security_enabled") is False,
                           "shows": "A completed SECURITY ERASE UNIT disables security; "
                                    "this is the drive's own confirmation."})
        else:
            st = evidence.device_raw.get("sanitize_status") or {}
            checks.append({"check": "Sanitize status reports completion without error",
                           "passed": st.get("succeeded") is True,
                           "shows": f"Last state {st.get('state') or '?'}"})
        before = self._samples.pop(drive.identity.key(), None)
        after = sampling.read_samples(drive.device_path, drive.identity.capacity_bytes,
                                      self.sample_count)
        checks.append(sampling.compare(before, after))
        return checks
