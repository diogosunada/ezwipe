#!/bin/bash
# Write the EZWipe ISO to a USB stick and add an EZWIPE-DATA partition
# (exFAT, readable on Windows and Mac) for certificates and settings.
#
#   sudo ./live/make-usb.sh build/live/ezwipe-0.2.0-amd64.hybrid.iso /dev/sdX
#
# THIS ERASES THE WHOLE STICK. The script shows the device and asks you to
# type its name before writing.
set -euo pipefail

ISO=${1:-}
DEV=${2:-}
if [ -z "$ISO" ] || [ -z "$DEV" ]; then
    echo "Usage: sudo $0 IMAGE.iso /dev/sdX" >&2
    exit 1
fi
[ "$(id -u)" -eq 0 ] || { echo "Run as root." >&2; exit 1; }
[ -f "$ISO" ] || { echo "No such file: $ISO" >&2; exit 1; }
[ -b "$DEV" ] || { echo "Not a block device: $DEV" >&2; exit 1; }
[ "$(lsblk -dno TYPE "$DEV")" = "disk" ] || { echo "$DEV is not a whole disk." >&2; exit 1; }

root_disk=$(lsblk -no PKNAME "$(findmnt -no SOURCE /)" 2>/dev/null || true)
if [ "/dev/$root_disk" = "$DEV" ]; then
    echo "$DEV holds the running system. Refusing." >&2
    exit 1
fi
if lsblk -no MOUNTPOINTS "$DEV" | grep -q .; then
    echo "$DEV has mounted partitions. Unmount them first:" >&2
    lsblk -o NAME,SIZE,MOUNTPOINTS "$DEV" >&2
    exit 1
fi

echo
lsblk -d -o NAME,SIZE,MODEL,SERIAL,TRAN "$DEV"
echo
echo "Everything on $DEV will be destroyed."
read -r -p "Type the device name ($(basename "$DEV")) to continue: " answer
[ "$answer" = "$(basename "$DEV")" ] || { echo "Cancelled."; exit 1; }

echo "== Writing image"
dd if="$ISO" of="$DEV" bs=4M conv=fsync status=progress
sync
partprobe "$DEV" || true
sleep 2

echo "== Adding EZWIPE-DATA partition in the free space"
echo ',,7' | sfdisk --append --no-reread "$DEV"
partprobe "$DEV" || true
udevadm settle || true
sleep 2
part=$(lsblk -lnpo NAME,TYPE "$DEV" | awk '$2=="part"{p=$1} END{print p}')
mkfs.exfat -n EZWIPE-DATA "$part"

mnt=$(mktemp -d)
mount "$part" "$mnt"
mkdir -p "$mnt/reports" "$mnt/logs"
cat >"$mnt/ezwipe.toml" <<'EOF'
# EZWipe settings. Every line is optional.
# operator = "Your name"
# organization = "Your company"
# location = "Bench 1"
# report_logo = "logo.png"        # put the image next to this file
# custom_fields = ["Asset tag", "PO number"]
# dark_mode = true

# Auto mode: erase every eligible drive after a countdown, no typing needed.
# Only drives that are not protected and that the profile can erase are touched.
# auto_profile = "auto-purge"     # or "nist-clear", "zero-fill", "dod-3pass"
# auto_delay_s = 60
EOF
sync
umount "$mnt"
rmdir "$mnt"
echo
echo "Done. $DEV boots EZWipe; certificates go to the EZWIPE-DATA partition ($part)."
