"""Linux backends against a real file standing in for a drive.

The fake runner answers hdparm / nvme-cli like a drive would, and its
"erase" responders really overwrite the file, so the sample-read checks
run on real bytes.
"""

import os
import threading

import pytest

from ezwipe.audit.store import AuditStore
from ezwipe.backends.host_overwrite import HostOverwriteBackend
from ezwipe.backends.linux_ata import PASSWORD, LinuxAtaBackend
from ezwipe.backends.linux_nvme import LinuxNvmeBackend
from ezwipe.core.models import (Availability, BusType, ConnectionPath, Drive, DriveIdentity,
                                MethodId, OperationState, Protocol)
from ezwipe.discovery.linux_provider import LinuxDiscovery
from ezwipe.engine.engine import Engine
from ezwipe.platform.linux import parsers as P
from ezwipe.platform.linux.runner import CmdResult, FakeRunner
from ezwipe.verification.engine import classify
from tests import linux_samples as S

SIZE = 8 * 1024 * 1024


@pytest.fixture
def disk(tmp_path):
    p = tmp_path / "disk.img"
    p.write_bytes(os.urandom(SIZE))       # "old data" everywhere
    return str(p)


def fill(path, byte=0):
    with open(path, "r+b") as f:
        f.write(bytes([byte]) * SIZE)


def drive_at(path, protocol=Protocol.ATA, caps=None) -> Drive:
    ident = DriveIdentity(serial="SER123", model="Test Drive", capacity_bytes=SIZE)
    bus = BusType.NVME if protocol == Protocol.NVME else BusType.SATA
    conn = ConnectionPath.PCIE_NVME if protocol == Protocol.NVME else ConnectionPath.INTERNAL_SATA
    return Drive(device_path=path, disk_number=None, identity=ident, protocol=protocol,
                 bus_type=bus, connection=conn, capabilities=caps or {})


def no_progress(*a):
    pass


def assert_verified(v):
    """Checks passed; the only caveat left is the provisional NIST category."""
    assert v.state == OperationState.COMPLETED_WITH_CAVEATS
    assert v.level.name == "CRITERIA_MET"
    assert all("provisional" in c for c in v.caveats), v.caveats


# ------------------------------------------------------------------ host overwrite

@pytest.mark.parametrize("method", [MethodId.OVERWRITE_ZERO, MethodId.OVERWRITE_ADDRESSABLE,
                                    MethodId.OVERWRITE_DOD_3PASS])
def test_overwrite_writes_and_verifies(disk, method):
    b = HostOverwriteBackend(chunk=1024 * 1024)
    d = drive_at(disk)
    seen = []
    ev = b.execute(d, method, threading.Event(), lambda m, f: seen.append(f), {"verify_fraction": 1.0})
    assert ev.device_completed and not ev.errors
    assert seen and seen[-1] is not None and seen[-1] > 0.9
    checks = b.post_checks(d, method, ev)
    assert checks[0]["passed"] is True
    data = open(disk, "rb").read()
    if method == MethodId.OVERWRITE_ZERO:
        assert data == bytes(SIZE)
    elif method == MethodId.OVERWRITE_DOD_3PASS:
        assert data.count(0) < SIZE // 100 and data.count(0xFF) < SIZE // 100   # last pass random
    assert_verified(classify(method, ev, checks))


def test_overwrite_detects_bad_readback(disk, monkeypatch):
    b = HostOverwriteBackend(chunk=1024 * 1024)
    d = drive_at(disk)
    real_pread = os.pread if hasattr(os, "pread") else None

    def corrupt(fd, n, off):
        data = bytearray(real_pread(fd, n, off) if real_pread else b"")
        if off == 2 * 1024 * 1024:
            data[10] ^= 0xFF
        return bytes(data)

    if real_pread is None:
        pytest.skip("pread not available on this platform")
    monkeypatch.setattr(os, "pread", corrupt)
    ev = b.execute(d, MethodId.OVERWRITE_ZERO, threading.Event(), no_progress, {"verify_fraction": 1.0})
    checks = b.post_checks(d, MethodId.OVERWRITE_ZERO, ev)
    assert checks[0]["passed"] is False and "2,097,162" in checks[0]["shows"]
    assert classify(MethodId.OVERWRITE_ZERO, ev, checks).state == OperationState.FAILED


def test_overwrite_cancel_is_interrupted(disk):
    b = HostOverwriteBackend(chunk=1024 * 1024)
    cancel = threading.Event()
    cancel.set()
    ev = b.execute(drive_at(disk), MethodId.OVERWRITE_ZERO, cancel, no_progress, {})
    assert not ev.device_completed
    v = classify(MethodId.OVERWRITE_ZERO, ev, [], cancelled=True)
    assert v.state == OperationState.INTERRUPTED


def test_overwrite_opens_hpa_when_profile_allows(disk):
    calls = []
    b = HostOverwriteBackend(chunk=1024 * 1024,
                             hpa_opener=lambda d: calls.append(d) or {"opened": True})
    d = drive_at(disk, caps={"ata": {"hpa_hidden_sectors": 2048}})
    ev = b.execute(d, MethodId.OVERWRITE_ZERO, threading.Event(), no_progress,
                   {"verify_fraction": 0.1, "reset_hidden_areas": True})
    assert calls and ev.parameters["hpa"]["opened"]
    ev2 = HostOverwriteBackend(chunk=1024 * 1024).execute(
        d, MethodId.OVERWRITE_ZERO, threading.Event(), no_progress, {"reset_hidden_areas": False})
    assert any("protected area" in e for e in ev2.errors)


# ------------------------------------------------------------------ ATA / hdparm

def ata_runner(disk, *, set_pass_ok=True, erase_ok=True, frozen=False):
    state = {"enabled": False}
    r = FakeRunner()

    def identify(argv):
        base = S.HDPARM_I_SSD_UNFROZEN if not frozen else S.HDPARM_I
        return S.HDPARM_I_ENABLED if state["enabled"] else base

    def set_pass(argv):
        if set_pass_ok:
            state["enabled"] = True
        return (0, "") if set_pass_ok else (5, "SG_IO: bad/missing sense data")

    def erase(argv):
        if not erase_ok:
            return CmdResult(argv, 5, "", "SECURITY_ERASE: Input/output error")
        fill(disk, 0)
        state["enabled"] = False
        return (0, "")

    def disable(argv):
        state["enabled"] = False
        return (0, "")

    r.on("hdparm -I", identify)
    r.on("hdparm --user-master u --security-set-pass", set_pass)
    r.on("hdparm --user-master u --security-erase", erase)
    r.on("hdparm --user-master u --security-disable", disable)
    r.on("hdparm -N", S.HDPARM_N_OPEN)
    return r, state


def ata_caps(**over):
    caps = P.parse_hdparm_identify(S.HDPARM_I_SSD_UNFROZEN)
    caps.update(over)
    return {"ata": caps}


def test_ata_enhanced_secure_erase_flow(disk):
    r, state = ata_runner(disk)
    b = LinuxAtaBackend(r, lambda d: d.identity)
    d = drive_at(disk, caps=ata_caps())
    m = MethodId.ATA_SECURITY_ERASE_ENHANCED
    assert b.reachability(d, m)[0] == Availability.AVAILABLE
    ev = b.execute(d, m, threading.Event(), no_progress, {})
    cmds = r.commands()
    i_set = next(i for i, c in enumerate(cmds) if "--security-set-pass" in c)
    i_erase = next(i for i, c in enumerate(cmds) if "--security-erase-enhanced" in c)
    assert i_set < i_erase and PASSWORD in cmds[i_erase]
    assert ev.device_completed and not ev.errors
    checks = b.post_checks(d, m, ev)
    assert [c["passed"] for c in checks] == [True, True]
    assert_verified(classify(m, ev, checks))


def test_ata_erase_failure_removes_temporary_password(disk):
    r, state = ata_runner(disk, erase_ok=False)
    b = LinuxAtaBackend(r, lambda d: d.identity)
    d = drive_at(disk, caps=ata_caps())
    ev = b.execute(d, MethodId.ATA_SECURITY_ERASE, threading.Event(), no_progress, {})
    assert not ev.device_completed
    assert any("--security-disable" in c for c in r.commands())
    assert state["enabled"] is False
    assert classify(MethodId.ATA_SECURITY_ERASE, ev, []).state == OperationState.FAILED


def test_ata_set_password_failure_sends_no_erase(disk):
    r, _ = ata_runner(disk, set_pass_ok=False)
    b = LinuxAtaBackend(r, lambda d: d.identity)
    ev = b.execute(drive_at(disk, caps=ata_caps()), MethodId.ATA_SECURITY_ERASE,
                   threading.Event(), no_progress, {})
    assert not ev.accepted
    assert not any("--security-erase" in c for c in r.commands())


def test_ata_frozen_and_password_set_are_not_reachable(disk):
    b = LinuxAtaBackend(FakeRunner(), lambda d: d.identity)
    assert b.reachability(drive_at(disk, caps=ata_caps(frozen=True)),
                          MethodId.ATA_SECURITY_ERASE)[0] == Availability.DECLARED_NOT_REACHABLE
    assert b.reachability(drive_at(disk, caps=ata_caps(security_enabled=True)),
                          MethodId.ATA_SECURITY_ERASE)[0] == Availability.DECLARED_NOT_REACHABLE


def test_ata_sanitize_polls_until_done(disk):
    polls = iter([S.SANITIZE_RUNNING, S.SANITIZE_DONE])
    r = FakeRunner()

    def go(argv):
        fill(disk, 0)
        return (0, "")

    r.on("hdparm --yes-i-know-what-i-am-doing --sanitize-overwrite", go)
    r.on("hdparm --sanitize-status", lambda a: next(polls))
    b = LinuxAtaBackend(r, lambda d: d.identity, poll_s=0)
    d = drive_at(disk, caps=ata_caps(**{"ata.sanitize.overwrite": True}))
    seen = []
    ev = b.execute(d, MethodId.ATA_SANITIZE_OVERWRITE, threading.Event(),
                   lambda m, f: seen.append(f), {})
    assert ev.device_completed and 0.35 in seen
    assert "hex:00000000" in ev.command
    checks = b.post_checks(d, MethodId.ATA_SANITIZE_OVERWRITE, ev)
    assert all(c["passed"] for c in checks)


# ------------------------------------------------------------------ NVMe / nvme-cli

def test_nvme_crypto_sanitize_follows_log(disk):
    logs = iter([S.nvme_sanlog(0), S.nvme_sanlog(2, 16384), S.nvme_sanlog(0x101)])
    r = FakeRunner()

    def go(argv):
        fill(disk, 0xA5)
        return (0, "")

    r.on("nvme sanitize-log", lambda a: next(logs))
    r.on("nvme sanitize /dev/nvme0 --sanact=4", go)
    b = LinuxNvmeBackend(r, lambda d: d.identity, poll_s=0)
    d = drive_at(disk, Protocol.NVME, {"nvme": {"sanicap": 3, "oacs": 2, "fna": 4,
                                                "controller": "/dev/nvme0"}})
    seen = []
    ev = b.execute(d, MethodId.NVME_SANITIZE_CRYPTO, threading.Event(), lambda m, f: seen.append(f), {})
    assert ev.device_completed and 0.25 in seen
    checks = b.post_checks(d, MethodId.NVME_SANITIZE_CRYPTO, ev)
    assert [c["passed"] for c in checks] == [True, True]


def test_nvme_sanitize_failure_reported_by_drive(disk):
    logs = iter([S.nvme_sanlog(0), S.nvme_sanlog(3)])
    r = FakeRunner().on("nvme sanitize-log", lambda a: next(logs)).on("nvme sanitize ", (0, ""))
    b = LinuxNvmeBackend(r, lambda d: d.identity, poll_s=0)
    d = drive_at(disk, Protocol.NVME, {"nvme": {"sanicap": 3, "oacs": 2, "fna": 4,
                                                "controller": "/dev/nvme0"}})
    ev = b.execute(d, MethodId.NVME_SANITIZE_BLOCK, threading.Event(), no_progress, {})
    assert classify(MethodId.NVME_SANITIZE_BLOCK, ev, []).state == OperationState.FAILED


def test_sampling_cannot_decide_on_blank_drive(disk):
    fill(disk, 0)
    logs = iter([S.nvme_sanlog(0), S.nvme_sanlog(0x101)])
    r = FakeRunner().on("nvme sanitize-log", lambda a: next(logs)).on("nvme sanitize ", (0, ""))
    b = LinuxNvmeBackend(r, lambda d: d.identity, poll_s=0)
    d = drive_at(disk, Protocol.NVME, {"nvme": {"sanicap": 3, "oacs": 2, "fna": 4,
                                                "controller": "/dev/nvme0"}})
    ev = b.execute(d, MethodId.NVME_SANITIZE_CRYPTO, threading.Event(), no_progress, {})
    checks = b.post_checks(d, MethodId.NVME_SANITIZE_CRYPTO, ev)
    assert checks[1]["passed"] is None
    v = classify(MethodId.NVME_SANITIZE_CRYPTO, ev, checks)
    assert v.state == OperationState.COMPLETED_WITH_CAVEATS
    assert any("could not decide" in c for c in v.caveats)


# ------------------------------------------------------------------ discovery

def discovery_runner():
    r = FakeRunner()
    r.on("lsblk", S.LSBLK)
    r.on("nvme id-ctrl", S.NVME_ID_CTRL).on("nvme id-ns", S.NVME_ID_NS)
    r.on("nvme sanitize-log", S.nvme_sanlog(0)).on("nvme smart-log", S.NVME_SMART)
    r.on("hdparm -I", S.HDPARM_I).on("hdparm -N", S.HDPARM_N_HPA).on("hdparm --dco-identify", S.HDPARM_DCO)
    r.on("smartctl", S.SMARTCTL)
    return r


def test_linux_discovery_builds_drives_and_protects_boot_usb():
    disc = LinuxDiscovery(discovery_runner())
    drives = {d.device_path: d for d in disc.discover()}
    assert set(drives) == {"/dev/sda", "/dev/sdb", "/dev/nvme0n1"}
    usb = drives["/dev/sdb"]
    assert usb.is_system_disk and "boot USB" in usb.protection_reason
    hdd = drives["/dev/sda"]
    assert hdd.kind == "HDD" and hdd.capabilities["ata"]["frozen"]
    assert hdd.capabilities["ata"]["hpa_hidden_sectors"] == 2048
    assert hdd.power_on_hours == 751 and hdd.temperature_c == 41
    nv = drives["/dev/nvme0n1"]
    assert nv.capabilities["nvme"]["controller"] == "/dev/nvme0"
    assert nv.identity.serial == "22195V800123" and nv.identity.nvme_eui64 == "e8238fa6bf530001"
    assert nv.temperature_c == 45 and nv.wear_percent == 2


def test_linux_engine_plans_real_drive_set(tmp_path):
    from ezwipe.backends.host_overwrite import HostOverwriteBackend
    r = discovery_runner()
    disc = LinuxDiscovery(r)
    backends = [LinuxNvmeBackend(r, disc.read_identity), LinuxAtaBackend(r, disc.read_identity),
                HostOverwriteBackend(disc.read_identity)]
    store = AuditStore(tmp_path / "a.db")
    eng = Engine(disc, backends, store)
    eng.refresh()
    plan = {i.drive.device_path: i for i in eng.plan_all("auto-purge").items}
    assert "/dev/sdb" not in plan                       # boot USB never planned
    assert plan["/dev/nvme0n1"].method == MethodId.NVME_SANITIZE_CRYPTO
    assert not plan["/dev/sda"].allowed                 # frozen HDD: no Purge reachable yet
    clear = {i.drive.device_path: i for i in eng.plan_all("nist-clear").items}
    assert clear["/dev/sda"].method == MethodId.OVERWRITE_ADDRESSABLE
    store.close()
