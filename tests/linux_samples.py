"""Tool output in the shape real tools print it, for parser and backend tests."""

import json

LSBLK = json.dumps({"blockdevices": [
    {"name": "sda", "path": "/dev/sda", "type": "disk", "size": 2000398934016,
     "model": "WDC WD20EFRX-68EUZN0", "serial": "WD-WCC4M0FZ6LS5", "vendor": "ATA     ",
     "rev": "0A82", "tran": "sata", "rota": True, "rm": False, "ro": False,
     "mountpoints": [None], "label": None, "fstype": None, "wwn": "0x50014ee2b7a1",
     "children": [{"name": "sda1", "path": "/dev/sda1", "type": "part", "size": 2000397885440,
                   "mountpoints": [None], "label": "DATA", "fstype": "ntfs"}]},
    {"name": "sdb", "path": "/dev/sdb", "type": "disk", "size": 15376318464,
     "model": "Ultra", "serial": "4C530001", "vendor": "SanDisk", "rev": "1.00", "tran": "usb",
     "rota": False, "rm": True, "ro": False, "mountpoints": [None], "label": None,
     "children": [{"name": "sdb1", "path": "/dev/sdb1", "type": "part", "size": 4000000000,
                   "mountpoints": ["/run/live/medium"], "label": "EZWIPE", "fstype": "iso9660"},
                  {"name": "sdb2", "path": "/dev/sdb2", "type": "part", "size": 11000000000,
                   "mountpoints": ["/run/ezwipe-data"], "label": "EZWIPE-DATA", "fstype": "exfat"}]},
    {"name": "nvme0n1", "path": "/dev/nvme0n1", "type": "disk", "size": 500107862016,
     "model": "WD_BLACK SN770 500GB", "serial": "22195V800123", "vendor": None, "rev": "731030WD",
     "tran": "nvme", "rota": False, "rm": False, "ro": False, "mountpoints": [None], "label": None},
    {"name": "loop0", "path": "/dev/loop0", "type": "loop", "size": 1000, "mountpoints": ["/run/live/rootfs"]},
    {"name": "sr0", "path": "/dev/sr0", "type": "rom", "size": 0},
]})

HDPARM_I = """
/dev/sda:

ATA device, with non-removable media
	Model Number:       WDC WD20EFRX-68EUZN0
	Serial Number:      WD-WCC4M0FZ6LS5
	Firmware Revision:  82.00A82
	Transport:          Serial, SATA 1.0a, SATA II Extensions, SATA Rev 2.5, SATA Rev 2.6, SATA Rev 3.0
Standards:
	Supported: 9 8 7 6 5
Configuration:
	Logical		max	current
	LBA48  user addressable sectors:  3907029168
	Logical  Sector size:                   512 bytes
	Physical Sector size:                  4096 bytes
	Nominal Media Rotation Rate: 5400
Commands/features:
	Enabled	Supported:
	   *	SMART feature set
	    	Security Mode feature set
	   *	Host Protected Area feature set
	   *	SANITIZE feature set
	   *	OVERWRITE_EXT command
Security:
	Master password revision code = 65534
		supported
	not	enabled
	not	locked
		frozen
	not	expired: security count
		supported: enhanced erase
	214min for SECURITY ERASE UNIT. 214min for ENHANCED SECURITY ERASE UNIT.
Logical Unit WWN Device Identifier: 50014ee2b7a1
Checksum: correct
"""

HDPARM_I_SSD_UNFROZEN = HDPARM_I.replace("\t\tfrozen", "\tnot\tfrozen").replace(
    "Nominal Media Rotation Rate: 5400", "Nominal Media Rotation Rate: Solid State Device").replace(
    "214min for SECURITY ERASE UNIT. 214min", "2min for SECURITY ERASE UNIT. 8min")

HDPARM_I_ENABLED = HDPARM_I_SSD_UNFROZEN.replace("\tnot\tenabled", "\t\tenabled")

HDPARM_N_HPA = "\n/dev/sda:\n max sectors   = 3907027120/3907029168, HPA is enabled\n"
HDPARM_N_OPEN = "\n/dev/sda:\n max sectors   = 3907029168/3907029168, HPA is disabled\n"
HDPARM_DCO = "\n/dev/sda:\nDCO Revision: 0x0002\nThe following features can be selectively disabled via DCO:\n\tReal max sectors: 3907029168\n"

SANITIZE_RUNNING = """
/dev/sda:
Issuing SANITIZE_STATUS command
Sanitize status:
    State:    SD2 Sanitize operation In Process
    Progress: 0x5998 (35%)
"""
SANITIZE_DONE = """
/dev/sda:
Issuing SANITIZE_STATUS command
Sanitize status:
    State:    SD0 Sanitize Idle
    Last Sanitize Operation Completed Without Error
"""

NVME_ID_CTRL = json.dumps({"vid": 5559, "sn": "22195V800123        ", "mn": "WD_BLACK SN770 500GB",
                           "fr": "731030WD", "oacs": 23, "fna": 4, "sanicap": 3, "nn": 1})
NVME_ID_NS = json.dumps({"nsze": 976773168, "flbas": 0, "lbafs": [{"ms": 0, "ds": 9, "rp": 2}],
                         "eui64": "e8238fa6bf530001", "nguid": "00000000000000000000000000000000"})
NVME_SMART = json.dumps({"critical_warning": 0, "temperature": 318, "percent_used": 2,
                         "power_on_hours": 1531, "media_errors": 0})


def nvme_sanlog(sstat: int, sprog: int = 65535) -> str:
    return json.dumps({"nvme0": {"sprog": sprog, "sstat": sstat, "cdw10_info": 4}})


SMARTCTL = json.dumps({"power_on_time": {"hours": 751}, "temperature": {"current": 41},
                       "smart_status": {"passed": True}, "rotation_rate": 5400,
                       "ata_smart_attributes": {"table": [{"id": 5, "raw": {"value": 0}}]}})
