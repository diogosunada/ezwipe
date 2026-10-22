from ezwipe.core.models import OVERWRITE_METHODS, Availability, MethodId
from tests.conftest import by_serial


def test_system_disk_is_blocked(engine):
    sys_drive = by_serial(engine, "SIM-SYS-0001")
    plan = engine.plan([sys_drive], "nvme-purge")
    assert not plan.items[0].allowed
    assert any("boot drive" in r for r in plan.items[0].blockers)


def test_usb_bridge_gets_only_host_overwrite_and_explains(engine):
    usb = by_serial(engine, "SIM-USB-0001")
    assert set(usb.available_methods) == set(OVERWRITE_METHODS)
    assert any("USB4" in l for l in usb.limitations)
    # Best Purge needs a firmware method, so a USB stick is refused, not downgraded.
    assert not engine.plan([usb], "auto-purge").items[0].allowed
    item = engine.plan([usb], "nist-clear").items[0]
    assert item.allowed and item.method == MethodId.OVERWRITE_ADDRESSABLE
    assert item.options["verify_fraction"] == 1.0


def test_frozen_sata_ssd_needs_unfreeze(engine):
    ssd = by_serial(engine, "SIM-SSD-0006")
    states = {m.method: m.availability for m in ssd.methods}
    assert states[MethodId.ATA_SANITIZE_CRYPTO] == Availability.DECLARED_NOT_REACHABLE
    assert any("Unfreeze" in l for l in ssd.limitations)
    assert engine.frozen_drives() and engine.can_unfreeze()
    engine.unfreeze()
    ssd = by_serial(engine, "SIM-SSD-0006")
    assert MethodId.ATA_SANITIZE_CRYPTO in ssd.available_methods
    assert engine.plan([ssd], "auto-purge").items[0].method == MethodId.ATA_SANITIZE_CRYPTO
    kinds = [e["kind"] for e in engine.store.events()]
    assert "maintenance.unfreeze.start" in kinds and "maintenance.unfreeze.end" in kinds


def test_hdd_without_sanitize_gets_enhanced_secure_erase(engine):
    hdd = by_serial(engine, "SIM-HDD-0005")
    assert engine.plan([hdd], "auto-purge").items[0].method == MethodId.ATA_SECURITY_ERASE_ENHANCED
    assert any("Host protected area" in l for l in hdd.limitations)


def test_declared_vs_reachable(engine):
    crucial = by_serial(engine, "SIM-D4-0002")   # declares only crypto erase
    states = {m.method: m.availability for m in crucial.methods}
    assert states[MethodId.NVME_SANITIZE_CRYPTO] == Availability.AVAILABLE
    assert states[MethodId.NVME_SANITIZE_BLOCK] == Availability.NOT_DECLARED


def test_recommendation_and_profile(engine):
    wd = by_serial(engine, "SIM-D4-0001")
    plan = engine.plan([wd], "nvme-purge")
    assert plan.items[0].allowed and plan.items[0].method == MethodId.NVME_SANITIZE_CRYPTO
    assert plan.items[0].confirmation_code == "0001"


def test_profile_refuses_weaker_method(engine):
    wd = by_serial(engine, "SIM-D4-0001")
    plan = engine.plan([wd], "nvme-purge", MethodId.NVME_FORMAT_USER_DATA)
    assert not plan.items[0].allowed


def test_windows_backend_offers_nothing_destructive_in_phase1():
    from ezwipe.backends.windows_nvme import WindowsNvmeBackend
    from ezwipe.backends.simulated import default_lab
    d = default_lab().drives[1].drive
    for m in MethodId:
        avail, _ = WindowsNvmeBackend().reachability(d, m)
        assert avail != Availability.AVAILABLE
