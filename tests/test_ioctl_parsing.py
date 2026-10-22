import struct

from ezwipe.platform.windows import ioctl


def test_ctl_codes():
    assert ioctl.IOCTL_STORAGE_QUERY_PROPERTY == 0x2D1400


def test_property_query_is_12_bytes():
    assert len(ioctl.build_property_query(0)) == 12


def test_nvme_query_layout_and_response_roundtrip():
    req = ioctl.build_nvme_query(49, ioctl.NVMeDataTypeIdentify, 1, 0, 4096)
    assert len(req) == 8 + 40 + 4096
    fields = struct.unpack_from("<10I", req, 8)
    assert fields[0] == ioctl.ProtocolTypeNvme and fields[4] == 40 and fields[5] == 4096
    # Fake response: descriptor header (Version=Size=48) + spec data + payload.
    payload = bytes(range(256)) * 16
    resp = struct.pack("<II", 48, 48) + struct.pack("<10I", 3, 1, 1, 0, 40, 4096, 0, 0, 0, 0) + payload
    assert ioctl.parse_nvme_query_response(resp) == payload


def test_device_descriptor():
    strings = b"VENDOR\x00PRODUCT X\x00REV1\x00SER123\x00"
    base = 36
    buf = struct.pack("<IIBBBBIIIII", 1, base + len(strings), 0, 0, 1, 0,
                      base, base + 7, base + 17, base + 22, 17) + struct.pack("<I", 0) + strings
    d = ioctl.parse_device_descriptor(buf)
    assert (d.vendor, d.product, d.revision, d.serial, d.bus_type) == ("VENDOR", "PRODUCT X", "REV1", "SER123", "NVMe")
    assert d.removable


def test_identify_controller_fields():
    data = bytearray(4096)
    struct.pack_into("<H", data, 0, 0x144D)
    data[4:24] = b"S6XNNS0T123456      "
    data[24:64] = b"Samsung SSD 990 PRO 1TB".ljust(40)
    data[64:72] = b"4B2QJXD7"
    struct.pack_into("<H", data, 256, 0x17)
    struct.pack_into("<I", data, 328, 0x3)
    data[524] = 0x4
    c = ioctl.parse_nvme_identify_controller(bytes(data))
    assert c.serial == "S6XNNS0T123456" and c.model == "Samsung SSD 990 PRO 1TB"
    assert c.sanitize_crypto and c.sanitize_block and not c.sanitize_overwrite
    assert c.format_supported and c.format_crypto


def test_sanitize_status_log():
    data = struct.pack("<HHIIII", 32768, 0x0102, 0, 10, 20, 30) + bytes(492)
    s = ioctl.parse_nvme_sanitize_status(data)
    assert s.progress == 0.5 and s.status_code == 2 and s.global_data_erased
    assert s.status_text == "Sanitize in progress"


def test_volume_extents():
    buf = struct.pack("<I4x", 2) + struct.pack("<I4xqq", 3, 1048576, 500) + struct.pack("<I4xqq", 5, 0, 10)
    assert ioctl.parse_volume_disk_extents(buf) == [(3, 1048576, 500), (5, 0, 10)]
