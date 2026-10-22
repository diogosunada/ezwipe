# Technical study (summary)

Full study with sources: https://claude.ai/artifact/TofZLsfim6hvFKd2mEBfYt

## NVMe destructive routes on Windows 11, to test in Phase 2 (in order)

1. `IOCTL_STORAGE_REINITIALIZE_MEDIA` with `STORAGE_REINITIALIZE_MEDIA`
   (`StorageSanitizeMethodCryptoErase` / `BlockErase`). Documented; data disks only;
   Microsoft gives no guarantee of the result, so our own verification is required.
   Call `FSCTL_LOCK_VOLUME` on every volume of the disk first.
2. `IOCTL_SCSI_PASS_THROUGH` with SCSI SANITIZE, translated by StorNVMe to NVMe
   Sanitize. Reported to work for Crypto and Block Erase.
3. `IOCTL_STORAGE_PROTOCOL_COMMAND` with NVMe Sanitize (84h). Documented, but
   reported to fail on full Windows 11 with error 0x13D ("WinPE only").

Fallback if all fail: a WinPE boot edition using the same engine.

## TerraMaster D4 SSD

ASM2464PDX USB4 controller; on USB4/Thunderbolt the SSDs appear as NVMe on
StorNVMe (bus type NVMe). On USB 3.x they appear behind a USB bridge and
sanitize commands are expected to be blocked. EZWipe reports this and tells
the operator to move the cable.

## Phase 1 known gaps

- USB4/Thunderbolt vs internal M.2 is not yet told apart (shown as "NVMe over PCIe").
- Page file and EFI boot partition on a second disk are not yet detected;
  disks with any mounted volume are blocked, which covers these cases for now.
- ATA/SATA capability reading is Phase 3.
