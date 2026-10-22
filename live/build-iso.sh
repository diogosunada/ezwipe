#!/bin/bash
# Build the EZWipe boot ISO (Debian 13 "trixie", amd64).
#
# Run on Debian 13 (a VM is fine) as root:
#   sudo apt install live-build python3-pip python3-venv
#   sudo ./live/build-iso.sh
#
# Output: build/live/ezwipe-<version>-amd64.hybrid.iso
# Write it to a USB stick with live/make-usb.sh (adds the EZWIPE-DATA
# partition for certificates), or with Rufus in "DD image" mode.
set -euo pipefail

HERE=$(cd "$(dirname "$0")" && pwd)
ROOT=$(dirname "$HERE")
WORK=${WORK:-$ROOT/build/live}
VERSION=$(python3 -c "import re,sys; print(re.search(r'__version__ = \"(.+?)\"', open(sys.argv[1]).read()).group(1))" \
          "$ROOT/src/ezwipe/__init__.py")

if [ "$(id -u)" -ne 0 ]; then
    echo "Run as root (live-build needs it)." >&2
    exit 1
fi
command -v lb >/dev/null || { echo "Install live-build first: apt install live-build" >&2; exit 1; }

echo "== EZWipe $VERSION: building wheel"
rm -rf "$WORK"
mkdir -p "$WORK/dist"
python3 -m pip wheel --no-deps --no-build-isolation -w "$WORK/dist" "$ROOT" \
    || python3 -m pip wheel --no-deps -w "$WORK/dist" "$ROOT"

cd "$WORK"
echo "== live-build config"
lb config \
    --distribution trixie \
    --archive-areas "main contrib non-free non-free-firmware" \
    --architectures amd64 \
    --binary-images iso-hybrid \
    --debian-installer none \
    --apt-recommends false \
    --firmware-chroot true \
    --firmware-binary true \
    --memtest none \
    --iso-application "EZWipe" \
    --iso-volume "EZWIPE" \
    --iso-publisher "EZWipe" \
    --image-name "ezwipe-$VERSION" \
    --bootappend-live "boot=live components quiet splash noswap noautomount consoleblank=0 \
username=ezwipe hostname=ezwipe"

cp -a "$HERE/config/." config/
mkdir -p config/includes.chroot/opt/ezwipe-dist
cp dist/*.whl config/includes.chroot/opt/ezwipe-dist/
chmod +x config/hooks/live/*.hook.chroot config/includes.chroot/usr/local/bin/*

echo "== live-build build (takes a while)"
lb build

ls -lh ./*.iso
echo "Done. Next: sudo $HERE/make-usb.sh $(ls ./*.iso | head -1) /dev/sdX"
