# EZWipe

Data sanitization for e-recycling and ITAD benches, with an auditable record
and a PDF certificate for every drive.

Two editions share one engine:

| Edition | How it runs | Can erase |
| --- | --- | --- |
| **Boot USB** (main) | Boots its own Debian 13 Linux, EZWipe full screen | NVMe, SATA SSD/HDD (firmware erase or overwrite), USB sticks and bridged drives (overwrite), including the drive that held Windows |
| Windows 11 | Desktop app | Read-only for now (Phase 2 tests the NVMe routes) |

**Status: 0.2.0. Tested with simulated drives and file-backed drives only.
Not yet run on real hardware.** See [live/README.md](live/README.md).

## What it does

- **Finds every drive** with its model, serial, size, temperature, power-on hours
  and wear, and protects the EZWipe stick and its data partition.
- **Picks the strongest method each drive supports** for the chosen profile:
  - NVMe Sanitize (crypto, block, overwrite) and Format, via `nvme-cli`
  - ATA Sanitize and Enhanced Secure Erase, via `hdparm`
  - host overwrite (zeros, random, DoD 3-pass) with full or sampled read-back
- **Opens hidden areas (HPA)** before erasing when the profile allows it, and
  reports DCO.
- **Unfreezes** SATA drives the BIOS froze, with one sleep/wake cycle.
- **Proves the result**: the drive's own status, read-back checks and sample
  reads before and after. Completion alone is never "erased"; without
  evidence the result is "not verified".
- **Auto mode**: erase every eligible drive after a countdown, set in
  `ezwipe.toml` or at boot.
- **Certificates**: a PDF per drive (device, method, NIST category, checks,
  host system, operator, signature line) tied to a JSON record by SHA-256.
- **Audit trail**: append-only, hash-chained SQLite log; crash recovery marks
  open operations as not verified.

## Profiles

| Profile | Category | Uses |
| --- | --- | --- |
| Best Purge (default) | Purge | NVMe/ATA Sanitize, then ATA Enhanced Secure Erase. Drives without one are not touched |
| NIST Clear | Clear | Firmware method if available, else 1 random pass + full read-back. Works on every writable drive |
| Quick zero fill | Clear | 1 zero pass + 10% read-back |
| DoD 5220.22-M (3 passes) | Clear | Legacy, for contracts that name it |
| NVMe Purge, NVMe (any), SATA Purge | | Under "More profiles" |

## Run

```powershell
python -m venv .venv
.venv\Scripts\pip install -e .[dev]
.venv\Scripts\ezwipe --simulate          # interface with simulated drives (safe anywhere)
.venv\Scripts\ezwipe-diag --simulate     # read-only diagnostic
.venv\Scripts\pytest -q
```

On Linux as root, `ezwipe` uses real drives. Build the boot USB with
[live/build-iso.sh](live/build-iso.sh).

## Layout

| Module | Role |
| --- | --- |
| `discovery/` | Read-only drive discovery: `windows_provider`, `linux_provider` |
| `platform/linux/` | Command runner and parsers for lsblk, hdparm, nvme-cli, smartctl |
| `platform/windows/` | ctypes IOCTL wrappers and parsers |
| `backends/` | `linux_nvme`, `linux_ata`, `host_overwrite`, `windows_nvme`, `simulated`; `sampling` for before/after reads |
| `capability/` | Declared vs reachable methods, recommendation |
| `engine/` | Plan, confirm, execute, follow, classify; auto mode; unfreeze |
| `safety/` | Fail-closed target checks and identity re-check |
| `verification/` | Evidence to result state |
| `audit/` | SQLite, hash chain |
| `reports/` | JSON/CSV records and the PDF certificate |
| `policy/` | Profiles and acceptance criteria |
| `ui/` | PySide6 interface; never sends drive commands |
| `editions.py`, `config.py` | Edition wiring; data folder and `ezwipe.toml` |
| `live/` | Debian live-build config, kiosk session, USB writer |

## Rules the code enforces

- Nothing destructive runs during discovery.
- The operator types the last 4 characters of each serial; auto mode records
  which settings file confirmed instead.
- The drive's identity is re-read right before the command; a change stops it.
- A finished process is never "success": the device's own status plus
  method-specific checks are required, otherwise the result is not verified.
- NIST categories are marked provisional until reviewed against IEEE 2883.
- Results follow NIST SP 800-88 Rev. 2 guidance. EZWipe is not NIST certified.
