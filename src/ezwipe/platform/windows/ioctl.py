"""IOCTL codes, request builders and response parsers.

Everything here is pure Python on bytes, so the parsing can be unit tested on
any OS. Struct layouts follow ntddstor.h / winioctl.h (x64).
"""

from __future__ import annotations

import struct
from dataclasses import dataclass


def ctl_code(device: int, function: int, method: int, access: int) -> int:
    return (device << 16) | (access << 14) | (function << 2) | method


METHOD_BUFFERED = 0
FILE_ANY_ACCESS = 0
FILE_READ_ACCESS = 1
IOCTL_STORAGE_BASE = 0x2D
IOCTL_DISK_BASE = 0x07
IOCTL_VOLUME_BASE = 0x56

IOCTL_STORAGE_QUERY_PROPERTY = ctl_code(IOCTL_STORAGE_BASE, 0x500, METHOD_BUFFERED, FILE_ANY_ACCESS)
IOCTL_STORAGE_GET_DEVICE_NUMBER = ctl_code(IOCTL_STORAGE_BASE, 0x420, METHOD_BUFFERED, FILE_ANY_ACCESS)
IOCTL_DISK_GET_LENGTH_INFO = ctl_code(IOCTL_DISK_BASE, 0x17, METHOD_BUFFERED, FILE_READ_ACCESS)
IOCTL_VOLUME_GET_VOLUME_DISK_EXTENTS = ctl_code(IOCTL_VOLUME_BASE, 0, METHOD_BUFFERED, FILE_ANY_ACCESS)

assert IOCTL_STORAGE_QUERY_PROPERTY == 0x002D1400
assert IOCTL_STORAGE_GET_DEVICE_NUMBER == 0x002D1080
assert IOCTL_DISK_GET_LENGTH_INFO == 0x0007405C
assert IOCTL_VOLUME_GET_VOLUME_DISK_EXTENTS == 0x00560000

# STORAGE_PROPERTY_ID
StorageDeviceProperty = 0
StorageAdapterProperty = 1
StorageDeviceSeekPenaltyProperty = 7
StorageAdapterProtocolSpecificProperty = 49
StorageDeviceProtocolSpecificProperty = 50
PropertyStandardQuery = 0

# STORAGE_PROTOCOL_TYPE / STORAGE_PROTOCOL_NVME_DATA_TYPE
ProtocolTypeNvme = 3
NVMeDataTypeIdentify = 1
NVMeDataTypeLogPage = 2

NVME_IDENTIFY_CNS_NAMESPACE = 0
NVME_IDENTIFY_CNS_CONTROLLER = 1
NVME_LOG_PAGE_HEALTH_INFO = 0x02
NVME_LOG_PAGE_SANITIZE_STATUS = 0x81

STORAGE_BUS_TYPES = {
    0: "Unknown", 1: "SCSI", 2: "ATAPI", 3: "ATA", 4: "1394", 5: "SSA", 6: "Fibre",
    7: "USB", 8: "RAID", 9: "iSCSI", 10: "SAS", 11: "SATA", 12: "SD", 13: "MMC",
    14: "Virtual", 15: "FileBackedVirtual", 16: "Spaces", 17: "NVMe", 18: "SCM", 19: "UFS",
}

PROTOCOL_SPECIFIC_DATA_SIZE = 40   # STORAGE_PROTOCOL_SPECIFIC_DATA: 10 ULONGs
PROPERTY_QUERY_HEADER_SIZE = 8     # offsetof(STORAGE_PROPERTY_QUERY, AdditionalParameters)
PROTOCOL_DATA_DESCRIPTOR_HEADER = 8  # Version + Size before ProtocolSpecificData


def build_property_query(property_id: int) -> bytes:
    # PropertyId, QueryType, AdditionalParameters[1] (padded to 12 bytes)
    return struct.pack("<IIB3x", property_id, PropertyStandardQuery, 0)


def build_nvme_query(property_id: int, data_type: int, request_value: int,
                     request_subvalue: int, data_length: int) -> bytes:
    """STORAGE_PROPERTY_QUERY + STORAGE_PROTOCOL_SPECIFIC_DATA + data area."""
    header = struct.pack("<II", property_id, PropertyStandardQuery)
    spec = struct.pack(
        "<10I",
        ProtocolTypeNvme,
        data_type,
        request_value,
        request_subvalue,
        PROTOCOL_SPECIFIC_DATA_SIZE,   # ProtocolDataOffset, relative to this struct
        data_length,                   # ProtocolDataLength
        0, 0, 0, 0,                    # FixedProtocolReturnData, SubValue2..4
    )
    return header + spec + bytes(data_length)


def parse_nvme_query_response(buf: bytes) -> bytes:
    """Return the protocol data area from a STORAGE_PROTOCOL_DATA_DESCRIPTOR."""
    if len(buf) < PROTOCOL_DATA_DESCRIPTOR_HEADER + PROTOCOL_SPECIFIC_DATA_SIZE:
        raise ValueError("protocol data descriptor too short")
    version, size = struct.unpack_from("<II", buf, 0)
    expected = PROTOCOL_DATA_DESCRIPTOR_HEADER + PROTOCOL_SPECIFIC_DATA_SIZE
    if version != expected or size != expected:
        raise ValueError(f"unexpected descriptor version/size {version}/{size}")
    fields = struct.unpack_from("<10I", buf, PROTOCOL_DATA_DESCRIPTOR_HEADER)
    offset, length = fields[4], fields[5]
    start = PROTOCOL_DATA_DESCRIPTOR_HEADER + offset
    if offset < PROTOCOL_SPECIFIC_DATA_SIZE or start + length > len(buf):
        raise ValueError(f"protocol data out of range (offset {offset}, length {length})")
    return buf[start:start + length]


def _cstr_at(buf: bytes, offset: int) -> str:
    if offset == 0 or offset >= len(buf):
        return ""
    end = buf.find(b"\x00", offset)
    raw = buf[offset:] if end < 0 else buf[offset:end]
    return raw.decode("ascii", "replace").strip()


@dataclass
class DeviceDescriptor:
    device_type: int
    removable: bool
    vendor: str
    product: str
    revision: str
    serial: str
    bus_type: str


def parse_device_descriptor(buf: bytes) -> DeviceDescriptor:
    if len(buf) < 36:
        raise ValueError("STORAGE_DEVICE_DESCRIPTOR too short")
    (_v, _s, dev_type, _mod, removable, _cq, vend_off, prod_off, rev_off,
     ser_off, bus) = struct.unpack_from("<IIBBBBIIIII", buf, 0)
    return DeviceDescriptor(
        device_type=dev_type,
        removable=bool(removable),
        vendor=_cstr_at(buf, vend_off),
        product=_cstr_at(buf, prod_off),
        revision=_cstr_at(buf, rev_off),
        serial=_cstr_at(buf, ser_off),
        bus_type=STORAGE_BUS_TYPES.get(bus, f"Unknown({bus})"),
    )


def parse_seek_penalty(buf: bytes) -> bool | None:
    if len(buf) < 9:
        return None
    return bool(buf[8])


def parse_device_number(buf: bytes) -> tuple[int, int, int]:
    return struct.unpack_from("<III", buf, 0)


def parse_length_info(buf: bytes) -> int:
    return struct.unpack_from("<q", buf, 0)[0]


def parse_volume_disk_extents(buf: bytes) -> list[tuple[int, int, int]]:
    """Return (disk_number, starting_offset, length) per extent."""
    (count,) = struct.unpack_from("<I", buf, 0)
    extents = []
    for i in range(count):
        base = 8 + i * 24    # DISK_EXTENT is 24 bytes, array starts 8-byte aligned
        if base + 24 > len(buf):
            break
        disk, start, length = struct.unpack_from("<I4xqq", buf, base)
        extents.append((disk, start, length))
    return extents


# ---------------------------------------------------------------- NVMe parsing

def _ascii(data: bytes) -> str:
    return data.decode("ascii", "replace").strip().strip("\x00").strip()


@dataclass
class NvmeController:
    vid: int
    serial: str
    model: str
    firmware: str
    oacs: int
    sanicap: int
    fna: int
    nn: int

    @property
    def format_supported(self) -> bool:
        return bool(self.oacs & 0x2)

    @property
    def security_send_recv(self) -> bool:
        return bool(self.oacs & 0x1)

    @property
    def sanitize_crypto(self) -> bool:
        return bool(self.sanicap & 0x1)

    @property
    def sanitize_block(self) -> bool:
        return bool(self.sanicap & 0x2)

    @property
    def sanitize_overwrite(self) -> bool:
        return bool(self.sanicap & 0x4)

    @property
    def format_crypto(self) -> bool:
        return bool(self.fna & 0x4)

    @property
    def format_applies_all_namespaces(self) -> bool:
        return bool(self.fna & 0x1) or bool(self.fna & 0x2)


def parse_nvme_identify_controller(data: bytes) -> NvmeController:
    if len(data) < 528:
        raise ValueError("Identify Controller data too short")
    return NvmeController(
        vid=struct.unpack_from("<H", data, 0)[0],
        serial=_ascii(data[4:24]),
        model=_ascii(data[24:64]),
        firmware=_ascii(data[64:72]),
        oacs=struct.unpack_from("<H", data, 256)[0],
        sanicap=struct.unpack_from("<I", data, 328)[0],
        nn=struct.unpack_from("<I", data, 516)[0],
        fna=data[524],
    )


@dataclass
class NvmeNamespace:
    size_blocks: int
    block_size: int
    nguid: str
    eui64: str

    @property
    def size_bytes(self) -> int:
        return self.size_blocks * self.block_size


def parse_nvme_identify_namespace(data: bytes) -> NvmeNamespace:
    if len(data) < 192:
        raise ValueError("Identify Namespace data too short")
    nsze = struct.unpack_from("<Q", data, 0)[0]
    flbas = data[26] & 0x0F
    lbaf = struct.unpack_from("<I", data, 128 + 4 * flbas)[0]
    lbads = (lbaf >> 16) & 0xFF
    nguid = data[104:120]
    eui = data[120:128]
    return NvmeNamespace(
        size_blocks=nsze,
        block_size=(1 << lbads) if lbads else 0,
        nguid="" if not any(nguid) else nguid.hex(),
        eui64="" if not any(eui) else eui.hex(),
    )


SANITIZE_STATUS_TEXT = {
    0: "Never sanitized",
    1: "Most recent sanitize completed successfully",
    2: "Sanitize in progress",
    3: "Most recent sanitize failed",
    4: "Completed successfully with No-Deallocate",
}


@dataclass
class NvmeSanitizeStatus:
    progress: float          # 0.0 .. 1.0, only meaningful while in progress
    status_code: int
    overwrite_passes: int
    global_data_erased: bool
    cdw10: int
    est_overwrite_s: int
    est_block_s: int
    est_crypto_s: int

    @property
    def status_text(self) -> str:
        return SANITIZE_STATUS_TEXT.get(self.status_code, f"Reserved status {self.status_code}")


def parse_nvme_sanitize_status(data: bytes) -> NvmeSanitizeStatus:
    if len(data) < 20:
        raise ValueError("Sanitize Status log too short")
    sprog, sstat, cdw10, et_ow, et_be, et_ce = struct.unpack_from("<HHIIII", data, 0)
    return NvmeSanitizeStatus(
        progress=sprog / 65536.0,
        status_code=sstat & 0x7,
        overwrite_passes=(sstat >> 3) & 0x1F,
        global_data_erased=bool(sstat & 0x100),
        cdw10=cdw10,
        est_overwrite_s=et_ow,
        est_block_s=et_be,
        est_crypto_s=et_ce,
    )


@dataclass
class NvmeHealth:
    critical_warning: int
    temperature_c: int | None
    percentage_used: int
    power_on_hours: int
    media_errors: int


def parse_nvme_health(data: bytes) -> NvmeHealth:
    if len(data) < 192:
        raise ValueError("SMART / Health log too short")
    kelvin = struct.unpack_from("<H", data, 1)[0]
    poh = int.from_bytes(data[128:144], "little")
    media = int.from_bytes(data[160:176], "little")
    return NvmeHealth(
        critical_warning=data[0],
        temperature_c=(kelvin - 273) if kelvin else None,
        percentage_used=data[5],
        power_on_hours=poh,
        media_errors=media,
    )
