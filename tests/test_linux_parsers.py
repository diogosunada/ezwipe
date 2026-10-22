from ezwipe.platform.linux import parsers as P
from tests import linux_samples as S


def test_lsblk_keeps_disks_and_collects_mounts():
    disks = P.parse_lsblk(S.LSBLK)
    assert [d["name"] for d in disks] == ["sda", "sdb", "nvme0n1"]
    usb = disks[1]
    assert usb["tran"] == "usb" and usb["removable"]
    assert set(usb["mounts"]) == {"/run/live/medium", "/run/ezwipe-data"}
    assert "EZWIPE-DATA" in usb["labels"]
    assert disks[0]["vendor"] == "ATA" and disks[0]["rota"] is True


def test_lsblk_bad_input_is_empty():
    assert P.parse_lsblk("not json") == []


def test_hdparm_identify_security_and_sanitize():
    d = P.parse_hdparm_identify(S.HDPARM_I)
    assert d["serial"] == "WD-WCC4M0FZ6LS5"
    assert d["security_supported"] and not d["security_enabled"] and not d["locked"]
    assert d["frozen"] is True
    assert d["erase_minutes"] == 214 and d["enhanced_erase_minutes"] == 214
    assert d["ata.security_erase_enhanced"] is True
    assert d["ata.sanitize.overwrite"] is True and d["ata.sanitize.crypto_scramble"] is False
    assert d["ssd"] is False and d["rotation_rpm"] == 5400


def test_hdparm_identify_unfrozen_ssd():
    d = P.parse_hdparm_identify(S.HDPARM_I_SSD_UNFROZEN)
    assert d["frozen"] is False and d["ssd"] is True
    assert d["enhanced_erase_minutes"] == 8


def test_hpa_dco_and_sanitize_status():
    hpa = P.parse_hdparm_max_sectors(S.HDPARM_N_HPA)
    assert hpa["hpa_hidden_sectors"] == 2048 and hpa["hpa_enabled"]
    assert P.parse_hdparm_dco(S.HDPARM_DCO) == 3907029168
    run = P.parse_hdparm_sanitize_status(S.SANITIZE_RUNNING)
    assert run["state"] == "SD2" and run["progress"] == 0.35 and run["succeeded"] is None
    assert P.parse_hdparm_sanitize_status(S.SANITIZE_DONE)["succeeded"] is True


def test_nvme_parsers():
    c = P.parse_nvme_id_ctrl(S.NVME_ID_CTRL)
    assert c["serial"] == "22195V800123" and c["sanicap"] == 3 and c["fna"] == 4
    ns = P.parse_nvme_id_ns(S.NVME_ID_NS)
    assert ns["lba_size"] == 512 and ns["eui64"] == "e8238fa6bf530001" and ns["nguid"] == ""
    sm = P.parse_nvme_smart_log(S.NVME_SMART)
    assert sm["temperature_c"] == 45 and sm["percent_used"] == 2 and sm["power_on_hours"] == 1531
    log = P.parse_nvme_sanitize_log(S.nvme_sanlog(2, 32768))
    assert log["status"] == 2 and log["progress"] == 0.5
    assert P.parse_nvme_sanitize_log(S.nvme_sanlog(0x101))["succeeded"] is True
    assert P.parse_nvme_sanitize_log(S.nvme_sanlog(3))["succeeded"] is False


def test_smartctl():
    sm = P.parse_smartctl(S.SMARTCTL)
    assert sm["power_on_hours"] == 751 and sm["temperature_c"] == 41 and sm["smart_passed"]
