"""Host overwrite backend: EZWipe writes the patterns itself.

Used when no firmware method is reachable (USB sticks, drives behind
bridges, old disks) or when a profile names an overwrite standard.

- Patterns: zeros, ones, or random data from SHAKE-256 keyed by a per-run
  seed and the chunk index, so the random pass can be verified without
  storing it.
- Progress is measured by EZWipe (bytes written), so a percentage is shown.
- After the last pass the page cache is dropped and the pattern is read
  back (all of it, or an evenly spread fraction, as the profile sets).
- Only host-visible blocks are reached; reports say so.
"""

from __future__ import annotations

import hashlib
import os
import secrets
import threading
import time
from typing import Callable

from ..core.methods import method_info
from ..core.models import (OVERWRITE_METHODS, Availability, Drive, DriveIdentity,
                           ExecutionEvidence, MethodId)

MiB = 1024 * 1024
PATTERN_NAMES = {"zero": "0x00", "one": "0xFF", "random": "random (SHAKE-256 stream)"}


def human_bytes(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1000 or unit == "TB":
            return f"{n:,.1f} {unit}" if unit != "B" else f"{n:.0f} B"
        n /= 1000
    return f"{n:.1f} TB"


def human_time(s: float) -> str:
    s = int(s)
    if s < 60:
        return f"{s} s"
    if s < 3600:
        return f"{s // 60} min"
    return f"{s // 3600} h {s % 3600 // 60:02d} min"


class PatternSource:
    def __init__(self, kind: str, seed: bytes, chunk: int):
        self.kind, self.seed, self.chunk = kind, seed, chunk
        self._fixed = None
        if kind == "zero":
            self._fixed = bytes(chunk)
        elif kind == "one":
            self._fixed = b"\xff" * chunk

    def block(self, index: int, length: int) -> bytes:
        if self._fixed is not None:
            return self._fixed if length == self.chunk else self._fixed[:length]
        return hashlib.shake_256(self.seed + index.to_bytes(8, "little")).digest(length)


def _sync(fd: int) -> None:
    (getattr(os, "fdatasync", None) or os.fsync)(fd)


def _drop_cache(fd: int) -> None:
    if hasattr(os, "posix_fadvise"):
        try:
            os.posix_fadvise(fd, 0, 0, os.POSIX_FADV_DONTNEED)
        except OSError:
            pass


def device_size(path: str) -> int:
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_BINARY", 0))
    try:
        return os.lseek(fd, 0, os.SEEK_END)
    finally:
        os.close(fd)


class HostOverwriteBackend:
    name = "host-overwrite"
    methods = OVERWRITE_METHODS

    def __init__(self, identity_reader: Callable[[Drive], DriveIdentity | None] | None = None,
                 hpa_opener: Callable[[Drive], dict] | None = None,
                 chunk: int = 4 * MiB, sync_every: int = 1024 * MiB):
        self.identity_reader = identity_reader
        self.hpa_opener = hpa_opener
        self.chunk = chunk
        self.sync_every = sync_every
        self._last_verify: dict[str, dict] = {}

    def handles(self, drive: Drive) -> bool:
        return bool(drive.device_path)

    def reachability(self, drive: Drive, method: MethodId) -> tuple[Availability, str]:
        if method not in self.methods:
            return Availability.NOT_DECLARED, "Not an overwrite method"
        if drive.capabilities.get("read_only"):
            return Availability.DECLARED_NOT_REACHABLE, "The drive is write-protected"
        if drive.identity.capacity_bytes <= 0:
            return Availability.UNKNOWN, "Drive size unknown"
        return Availability.AVAILABLE, "EZWipe writes every host-visible block"

    def read_identity(self, drive: Drive) -> DriveIdentity | None:
        if self.identity_reader is None:
            return drive.identity if os.path.exists(drive.device_path) else None
        return self.identity_reader(drive)

    # ------------------------------------------------------------------ execute
    def execute(self, drive: Drive, method: MethodId, cancel: threading.Event, progress,
                options: dict | None = None) -> ExecutionEvidence:
        options = options or {}
        info = method_info(method)
        passes = list(info.passes)
        verify_fraction = float(options.get("verify_fraction", 0.1))
        seed = secrets.token_bytes(16)
        ev = ExecutionEvidence(route="host-overwrite (pwrite)",
                               command=f"overwrite {drive.device_path}: " +
                                       ", ".join(PATTERN_NAMES[p] for p in passes),
                               parameters={"passes": passes, "chunk_bytes": self.chunk,
                                           "verify_fraction": verify_fraction,
                                           "seed_sha256": hashlib.sha256(seed).hexdigest()})

        hidden = (drive.capabilities.get("ata") or {}).get("hpa_hidden_sectors") or 0
        if hidden and options.get("reset_hidden_areas") and self.hpa_opener:
            res = self.hpa_opener(drive)
            ev.parameters["hpa"] = res
            if not res.get("opened"):
                ev.errors.append("Host protected area could not be opened; hidden sectors "
                                 "will not be overwritten.")
        elif hidden:
            ev.errors.append(f"Host protected area left closed ({hidden:,} sectors not reached).")

        try:
            fd = os.open(drive.device_path, os.O_RDWR | getattr(os, "O_BINARY", 0))
        except OSError as e:
            ev.communication_failure = True
            ev.errors.append(f"Could not open {drive.device_path} for writing: {e}")
            return ev
        try:
            size = os.lseek(fd, 0, os.SEEK_END)
            ev.parameters["bytes"] = size
            ev.accepted = True
            ev.device_started = True
            total_work = size * len(passes) + int(size * verify_fraction)
            done_before = 0
            t0 = time.monotonic()
            for n, kind in enumerate(passes, 1):
                src = PatternSource(kind, seed, self.chunk)
                label = f"Pass {n}/{len(passes)} ({PATTERN_NAMES[kind]})"
                ok = self._write_pass(fd, size, src, cancel, ev, lambda b: progress(
                    self._msg(label, b, size, done_before + b, total_work, t0),
                    (done_before + b) / total_work if total_work else None))
                if not ok:
                    return ev
                done_before += size
            _sync(fd)
            _drop_cache(fd)
            ev.device_completed = True
            ev.device_status = f"All {len(passes)} pass(es) written and flushed ({human_bytes(size)})"
            if verify_fraction > 0:
                src = PatternSource(passes[-1], seed, self.chunk)
                vwork = total_work - done_before
                result = self._verify(fd, size, src, verify_fraction, cancel, lambda b, t: progress(
                    self._msg(f"Verifying {verify_fraction:.0%}", b, t, done_before + vwork * b / t,
                              total_work, t0),
                    min(1.0, (done_before + vwork * b / t) / total_work) if total_work and t else None))
            else:
                result = {"fraction": 0, "checked_bytes": 0, "mismatch_at": None, "skipped": True}
            ev.device_raw["verify"] = result
            self._last_verify[drive.identity.key()] = result
            ev.parameters["duration_s"] = round(time.monotonic() - t0, 1)
        except OSError as e:
            ev.errors.append(f"I/O error: {e}")
        finally:
            os.close(fd)
        return ev

    @staticmethod
    def _msg(label: str, b: int, size: int, done: int, total: int, t0: float) -> str:
        el = time.monotonic() - t0
        rate = done / el if el > 0.5 else 0
        eta = f", about {human_time((total - done) / rate)} left" if rate > 0 else ""
        speed = f", {human_bytes(rate)}/s" if rate > 0 else ""
        return f"{label}: {human_bytes(b)} of {human_bytes(size)}{speed}{eta}"

    def _write_pass(self, fd, size, src: PatternSource, cancel, ev, tick) -> bool:
        off, idx, since_sync, last_tick = 0, 0, 0, 0.0
        while off < size:
            if cancel.is_set():
                ev.errors.append(f"Cancelled by operator at byte {off:,}")
                return False
            n = min(self.chunk, size - off)
            data = src.block(idx, n)
            written = os.pwrite(fd, data, off) if hasattr(os, "pwrite") else _pwrite(fd, data, off)
            if written != n:
                ev.errors.append(f"Short write at byte {off:,}: {written} of {n}")
                return False
            off += n
            idx += 1
            since_sync += n
            if since_sync >= self.sync_every:
                _sync(fd)
                since_sync = 0
            now = time.monotonic()
            if now - last_tick > 0.5 or off >= size:
                tick(off)
                last_tick = now
        return True

    def _verify(self, fd, size, src: PatternSource, fraction: float, cancel, tick) -> dict:
        chunks = (size + self.chunk - 1) // self.chunk
        every = max(1, round(1 / fraction)) if fraction < 1 else 1
        pick = sorted({0, chunks - 1, *range(0, chunks, every)})
        target = len(pick) * self.chunk
        checked, last_tick = 0, 0.0
        for idx in pick:
            if cancel.is_set():
                return {"fraction": fraction, "checked_bytes": checked, "mismatch_at": None,
                        "cancelled": True}
            off = idx * self.chunk
            n = min(self.chunk, size - off)
            got = os.pread(fd, n, off) if hasattr(os, "pread") else _pread(fd, n, off)
            if got != src.block(idx, n):
                first = next(i for i, (a, b) in enumerate(zip(got, src.block(idx, n))) if a != b) \
                    if len(got) == n else 0
                return {"fraction": fraction, "checked_bytes": checked, "mismatch_at": off + first}
            checked += n
            now = time.monotonic()
            if now - last_tick > 0.5:
                tick(checked, target)
                last_tick = now
        tick(checked, target)
        return {"fraction": fraction, "checked_bytes": checked, "mismatch_at": None,
                "chunks_checked": len(pick), "chunks_total": chunks}

    def post_checks(self, drive: Drive, method: MethodId, evidence: ExecutionEvidence) -> list[dict]:
        v = evidence.device_raw.get("verify")
        if not v:
            return []
        if v.get("skipped"):
            return [{"check": "Read-back of written pattern", "passed": None,
                     "shows": "Verification was switched off in this profile."}]
        if v.get("cancelled"):
            return [{"check": "Read-back of written pattern", "passed": None,
                     "shows": "Verification was cancelled."}]
        name = f"Read-back of written pattern ({v['fraction']:.0%} of the drive)"
        if v["mismatch_at"] is not None:
            return [{"check": name, "passed": False,
                     "shows": f"Data read back differs from the pattern at byte {v['mismatch_at']:,}."}]
        return [{"check": name, "passed": True,
                 "shows": f"{human_bytes(v['checked_bytes'])} read back and matched the last pattern. "
                          "Covers host-visible blocks only."}]


def _pwrite(fd: int, data: bytes, off: int) -> int:     # Windows test fallback
    os.lseek(fd, off, os.SEEK_SET)
    return os.write(fd, data)


def _pread(fd: int, n: int, off: int) -> bytes:
    os.lseek(fd, off, os.SEEK_SET)
    return os.read(fd, n)
