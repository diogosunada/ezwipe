"""NVMe backend for the boot USB (Linux, nvme-cli).

Sanitize goes to the controller (/dev/nvme0) and is followed through the
Sanitize Status log until the drive itself reports success or failure.
Format goes to the namespace (/dev/nvme0n1) and completes synchronously.
"""

from __future__ import annotations

import threading
import time
from typing import Callable

from ..core.models import Availability, Drive, DriveIdentity, ExecutionEvidence, MethodId, Protocol
from ..platform.linux.parsers import parse_nvme_sanitize_log
from ..platform.linux.runner import Runner
from . import sampling

M = MethodId
SANACT = {M.NVME_SANITIZE_BLOCK: 2, M.NVME_SANITIZE_OVERWRITE: 3, M.NVME_SANITIZE_CRYPTO: 4}
SES = {M.NVME_FORMAT_USER_DATA: 1, M.NVME_FORMAT_CRYPTO: 2}
# Generous ceilings; the drive's own status ends the wait long before these.
TIMEOUT_S = {M.NVME_SANITIZE_CRYPTO: 2 * 3600, M.NVME_SANITIZE_BLOCK: 8 * 3600,
             M.NVME_SANITIZE_OVERWRITE: 48 * 3600}


class LinuxNvmeBackend:
    name = "linux-nvme-cli"
    methods = frozenset(SANACT) | frozenset(SES)

    def __init__(self, runner: Runner, identity_reader: Callable[[Drive], DriveIdentity | None],
                 poll_s: float = 5.0, sample_count: int = 64):
        self.runner = runner
        self.identity_reader = identity_reader
        self.poll_s = poll_s
        self.sample_count = sample_count
        self._samples: dict[str, dict] = {}

    def handles(self, drive: Drive) -> bool:
        return drive.protocol == Protocol.NVME and bool((drive.capabilities.get("nvme") or {})
                                                        .get("controller"))

    def reachability(self, drive: Drive, method: MethodId) -> tuple[Availability, str]:
        if method not in self.methods:
            return Availability.NOT_DECLARED, "Not an NVMe method"
        nv = drive.capabilities.get("nvme")
        if not nv:
            return Availability.UNKNOWN, "NVMe capabilities could not be read"
        if method in SANACT and nv.get("sanitize_in_progress"):
            return Availability.DECLARED_NOT_REACHABLE, "A sanitize is already running on this drive"
        where = nv["controller"] if method in SANACT else drive.device_path
        return Availability.AVAILABLE, f"nvme-cli on {where}"

    def read_identity(self, drive: Drive) -> DriveIdentity | None:
        return self.identity_reader(drive)

    def _sanitize_log(self, ctrl: str) -> dict | None:
        r = self.runner.run(["nvme", "sanitize-log", ctrl, "-o", "json"], timeout=30)
        return parse_nvme_sanitize_log(r.stdout) if r.ok else None

    def execute(self, drive: Drive, method: MethodId, cancel: threading.Event, progress,
                options: dict | None = None) -> ExecutionEvidence:
        ctrl = drive.capabilities["nvme"]["controller"]
        size = drive.identity.capacity_bytes
        before = sampling.read_samples(drive.device_path, size, self.sample_count)
        self._samples[drive.identity.key()] = before
        ev = ExecutionEvidence(route="nvme-cli")
        ev.parameters["samples_before"] = {"count": len(before["offsets"]), "error": before["error"]}

        if method in SANACT:
            prev = self._sanitize_log(ctrl)
            ev.device_raw["sanitize_log_before"] = prev
            argv = ["nvme", "sanitize", ctrl, f"--sanact={SANACT[method]}"]
            if method == M.NVME_SANITIZE_OVERWRITE:
                argv += ["--owpass=1", "--ovrpat=0"]
            ev.command = " ".join(argv)
            r = self.runner.run(argv, timeout=120)
            ev.device_raw["command"] = r.record()
            if not r.ok:
                ev.communication_failure = r.missing_tool or r.timed_out
                ev.errors.append(f"nvme sanitize rejected (rc {r.returncode}): "
                                 f"{(r.stderr or r.stdout).strip()[:300]}")
                return ev
            ev.accepted = True
            return self._follow_sanitize(ctrl, method, ev, cancel, progress)

        argv = ["nvme", "format", drive.device_path, f"--ses={SES[method]}", "--force"]
        ev.command = " ".join(argv)
        progress("Formatting (the drive gives no progress for Format)", None)
        r = self.runner.run(argv, timeout=4 * 3600)
        ev.device_raw["command"] = r.record()
        if r.timed_out:
            ev.accepted = ev.device_started = True
            ev.timed_out = True
            ev.errors.append("Format did not return within 4 hours")
            return ev
        if not r.ok:
            ev.communication_failure = r.missing_tool
            ev.errors.append(f"nvme format failed (rc {r.returncode}): "
                             f"{(r.stderr or r.stdout).strip()[:300]}")
            return ev
        ev.accepted = ev.device_started = ev.device_completed = True
        ev.device_status = "Format NVM completed with status 0"
        return ev

    def _follow_sanitize(self, ctrl, method, ev, cancel, progress) -> ExecutionEvidence:
        t0 = time.monotonic()
        deadline = t0 + TIMEOUT_S[method]
        seen_running = False
        misses = 0
        stale_success = 0
        while True:
            if cancel.is_set():
                ev.errors.append("Operator stopped following the sanitize. The drive keeps "
                                 "sanitizing internally; check its status before reuse.")
                return ev
            log = self._sanitize_log(ctrl)
            if log is None:
                misses += 1
                if misses >= 6:
                    ev.communication_failure = ev.disconnected = True
                    ev.errors.append("Sanitize log stopped answering (drive gone or reset)")
                    return ev
            else:
                misses = 0
                ev.device_raw["sanitize_log"] = log
                if log["status"] == 2:
                    seen_running = ev.device_started = True
                    frac = log["progress"]
                    ev.progress_samples.append((f"{time.monotonic() - t0:.0f}s", frac or 0.0))
                    progress("Drive sanitizing (progress reported by the drive)", frac)
                elif log["succeeded"] is True and (seen_running or self._is_new(ev, log)):
                    ev.device_started = ev.device_completed = True
                    ev.device_status = log["text"]
                    return ev
                elif log["succeeded"] is True:
                    # Same success status as before we started. A fast crypto erase can
                    # finish between polls; accept after 3 polls, but say so.
                    stale_success += 1
                    if stale_success >= 3:
                        ev.device_started = ev.device_completed = True
                        ev.device_status = log["text"]
                        ev.errors.append("The drive finished before EZWipe saw it running; the "
                                         "success status cannot be told apart from an earlier "
                                         "sanitize. Sample reads decide.")
                        return ev
                elif log["succeeded"] is False:
                    ev.device_started = True
                    ev.device_status = log["text"]
                    ev.errors.append("Drive reports: " + log["text"])
                    return ev
            if time.monotonic() > deadline:
                ev.timed_out = True
                ev.errors.append("No completion reported before the time limit")
                return ev
            time.sleep(self.poll_s)

    @staticmethod
    def _is_new(ev, log) -> bool:
        """A success status counts at once only if the log changed since before we started."""
        prev = ev.device_raw.get("sanitize_log_before")
        return prev is None or prev.get("succeeded") is not True or prev.get("sstat") != log.get("sstat")

    def post_checks(self, drive: Drive, method: MethodId, evidence: ExecutionEvidence) -> list[dict]:
        checks = []
        if method in SANACT:
            log = evidence.device_raw.get("sanitize_log") or {}
            checks.append({"check": "Sanitize Status log reports success",
                           "passed": log.get("succeeded") is True,
                           "shows": log.get("text", "No log read")})
        before = self._samples.pop(drive.identity.key(), None)
        after = sampling.read_samples(drive.device_path, drive.identity.capacity_bytes,
                                      self.sample_count)
        checks.append(sampling.compare(before, after))
        return checks
