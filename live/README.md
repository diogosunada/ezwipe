# EZWipe boot USB

EZWipe boots its own Linux (Debian 13) from a USB stick and opens the
EZWipe interface full screen. Because nothing runs from the computer's own
drives, every internal drive can be erased, including the one that held
Windows, and firmware commands (ATA Secure Erase, ATA and NVMe Sanitize)
reach the drives without Windows blocking them.

## What is on the stick

| Part | What it does |
| --- | --- |
| Debian 13 live system | Kernel, firmware, drive tools (`hdparm`, `nvme-cli`, `smartmontools`) |
| `/opt/ezwipe` | EZWipe and PySide6 |
| `ezwipe-kiosk.service` | Starts EZWipe on screen 1 at boot, restarts it if it closes |
| `ezwipe-session` | Runs EZWipe in `cage` (full screen); falls back to the plain framebuffer when the graphics driver fails |
| `EZWIPE-DATA` partition | Audit database, PDF certificates, logs and `ezwipe.toml`. Mounted at `/run/ezwipe-data` |

The stick holding EZWipe, and any drive with an `EZWIPE-DATA` partition, is
always protected: it never shows as ready and cannot be selected.

## Build the ISO

On Debian 13 (a virtual machine is fine):

```bash
sudo apt install live-build python3-pip python3-venv
sudo bash live/build-iso.sh
```

Or on GitHub: **Actions > CI > Run workflow**. The ISO is attached to the
run as `ezwipe-iso`.

## Write the USB stick

Linux (adds the EZWIPE-DATA partition and a sample `ezwipe.toml`):

```bash
sudo bash live/make-usb.sh build/live/ezwipe-0.2.0-amd64.hybrid.iso /dev/sdX
```

Windows: write the ISO with Rufus in **DD image** mode. Then either add a
partition labelled `EZWIPE-DATA` in the free space, or plug in a second USB
stick whose partition is labelled `EZWIPE-DATA` (exFAT or FAT32). Without
one, EZWipe still works but shows a red banner: certificates are kept in
memory and lost at shutdown.

## Settings (`ezwipe.toml` on EZWIPE-DATA)

```toml
operator = "Jane Doe"
organization = "Sunada Recycling"
location = "Bench 2"
report_logo = "logo.png"
custom_fields = ["Asset tag", "PO number"]
dark_mode = true

# Auto mode: after a countdown, erase every eligible drive with this profile.
auto_profile = "auto-purge"     # auto-purge, nist-clear, zero-fill, dod-3pass
auto_delay_s = 60
```

Auto mode can also be started for one boot with the kernel option
`ezwipe.auto=auto-purge` (press `e` in the boot menu). Every operation
started this way records which file or option confirmed it.

## Boot options

| Option | Effect |
| --- | --- |
| `ezwipe.auto=PROFILE` | Auto mode with this profile |
| `ezwipe.simulate` | Simulated drives, for training and demos |

## Frozen drives

Most BIOSes "freeze" SATA security at boot, which blocks Secure Erase and
Sanitize. EZWipe shows a banner with an **Unfreeze** button: the computer
sleeps for about 6 seconds and wakes up by itself (`rtcwake -m mem -s 6`),
which lifts the freeze on most machines. If a drive is still frozen, the
banner says so; use NIST Clear (overwrite) or try hot-plugging the drive.

## Status

Built and unit-tested from Windows with simulated drives and file-backed
"drives". **Not yet booted on real hardware.** Before using it on customer
drives, test on spare drives of each type (NVMe, SATA SSD, HDD, USB stick)
and compare the certificates with what you expect.
