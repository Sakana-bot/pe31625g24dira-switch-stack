#!/bin/bash
set -euo pipefail

FORMAT_VERSION=3
PRODUCT_MODEL=PE31625G24DIRA
OUTPUT_DIR=/root
WORK_DIR=""

usage() {
    cat <<'EOF'
Usage: sudo bash capture-legacy.sh [--output-dir DIR]

Creates a per-board, root-only migration bundle. The filesystem whitelist is
based on the factory F214704700308 and F214704700934 B0/sil001 images. Only the
legacy SDK runtime and the selected B0 platform files are migration payloads;
the remaining collected files are read-only hardware/OS audit metadata. The
script does not read or write FM10840/optical-engine Flash because reinstalling
the system disk does not alter those board-resident devices.
EOF
}

log() { printf '[capture] %s\n' "$*"; }
die() { printf '[capture] ERROR: %s\n' "$*" >&2; exit 1; }

while [ "$#" -gt 0 ]; do
    case "$1" in
        --output-dir) [ "$#" -ge 2 ] || die "--output-dir requires a value"; OUTPUT_DIR=$2; shift 2 ;;
        -h|--help) usage; exit 0 ;;
        *) die "unknown argument: $1" ;;
    esac
done

[ "$(id -u)" -eq 0 ] || die "run as root"
command -v tar >/dev/null || die "tar is required"
command -v sha256sum >/dev/null || die "sha256sum is required"

find_fm_device() {
    local dev
    for dev in /sys/bus/pci/devices/*; do
        [ -r "$dev/vendor" ] || continue
        [ "$(cat "$dev/vendor")" = "0x8086" ] || continue
        [ "$(cat "$dev/device")" = "0x15a4" ] || continue
        printf '%s\n' "$dev"
        return 0
    done
    return 1
}

FM_DEV=$(find_fm_device) || die "Intel FM10840 PCI device 8086:15a4 not found"
SUBSYSTEM="$(cat "$FM_DEV/subsystem_vendor" 2>/dev/null || true):$(cat "$FM_DEV/subsystem_device" 2>/dev/null || true)"
if [ "$SUBSYSTEM" != "0x1374:0x01d0" ]; then
    log "WARNING: subsystem is $SUBSYSTEM, expected 0x1374:0x01d0"
fi

serial=$(cat /sys/class/dmi/id/product_serial 2>/dev/null || true)
if [ -z "$serial" ] || [ "$serial" = "None" ] || [ "$serial" = "To Be Filled By O.E.M." ]; then
    serial=$(basename "$FM_DEV")
fi
safe_id=$(printf '%s' "$serial" | tr -c 'A-Za-z0-9._-' '_' | cut -c1-48)
timestamp=$(date -u +%Y%m%dT%H%M%SZ)
bundle_id="${safe_id}-${timestamp}"

umask 077
mkdir -p "$OUTPUT_DIR"
WORK_DIR=$(mktemp -d "${TMPDIR:-/tmp}/pe31625g24dira-capture.XXXXXX")
STAGE="$WORK_DIR/pe31625g24dira-board-bundle"
ROOTFS="$STAGE/rootfs"
META="$STAGE/metadata"
mkdir -p "$ROOTFS" "$META"

cleanup() {
    [ -z "$WORK_DIR" ] || rm -rf -- "$WORK_DIR"
}
trap cleanup EXIT
trap 'exit 130' HUP INT TERM

copy_path() {
    local src=$1 dst
    [ -e "$src" ] || [ -L "$src" ] || return 0
    dst="$ROOTFS$src"
    mkdir -p "$(dirname "$dst")"
    cp -a -- "$src" "$dst"
}

copy_contents() {
    local src=$1 dst=$2
    [ -d "$src" ] || return 0
    mkdir -p "$dst"
    cp -a "$src"/. "$dst"/
}

log "capturing board metadata"
{
    printf 'FORMAT_VERSION=%s\n' "$FORMAT_VERSION"
    printf 'PRODUCT_MODEL=%s\n' "$PRODUCT_MODEL"
    printf 'BUNDLE_ID=%s\n' "$bundle_id"
    printf 'CREATED_UTC=%s\n' "$timestamp"
    printf 'PCI_VENDOR_DEVICE=8086:15a4\n'
    printf 'PCI_SUBSYSTEM=%s\n' "$SUBSYSTEM"
    printf 'SOURCE_OS_ID=%s\n' "$(. /etc/os-release 2>/dev/null; printf '%s' "${ID:-unknown}")"
    printf 'SOURCE_OS_VERSION=%s\n' "$(. /etc/os-release 2>/dev/null; printf '%s' "${VERSION_ID:-unknown}")"
    printf 'FLASH_ACCESSED=0\n'
} > "$STAGE/manifest.env"

uname -a > "$META/uname.txt"
cp -a /etc/os-release "$META/os-release" 2>/dev/null || true
command -v lspci >/dev/null && lspci -Dnnvv > "$META/lspci-Dnnvv.txt" 2>&1 || true
command -v lsmod >/dev/null && lsmod > "$META/lsmod.txt" 2>&1 || true
command -v modinfo >/dev/null && modinfo fm10k > "$META/modinfo-fm10k.txt" 2>&1 || true
command -v dkms >/dev/null && dkms status > "$META/dkms-status.txt" 2>&1 || true
command -v ip >/dev/null && ip -brief link > "$META/ip-link.txt" 2>&1 || true
command -v systemctl >/dev/null && systemctl list-unit-files --no-pager > "$META/systemd-unit-files.txt" 2>&1 || true
command -v dpkg-query >/dev/null && dpkg-query -W -f='${binary:Package}\t${Version}\n' > "$META/packages.tsv" 2>&1 || true
command -v efibootmgr >/dev/null && efibootmgr -v > "$META/efibootmgr-v.txt" 2>&1 || true
command -v findmnt >/dev/null && findmnt --all --bytes > "$META/findmnt.txt" 2>&1 || true
command -v lsblk >/dev/null && lsblk -O -b > "$META/lsblk.txt" 2>&1 || true
for path in /sys/class/dmi/id/sys_vendor /sys/class/dmi/id/product_name /sys/class/dmi/id/product_version /sys/class/dmi/id/product_serial /sys/class/dmi/id/board_vendor /sys/class/dmi/id/board_name /sys/class/dmi/id/board_version /sys/class/dmi/id/board_serial /opt/image_ver.txt; do
    [ -r "$path" ] || continue
    destination="$META/$(printf '%s' "$path" | sed 's#^/##; s#/#-#g')"
    cp -a "$path" "$destination"
done
if [ -r "$FM_DEV/vpd" ]; then
    cp -a "$FM_DEV/vpd" "$META/fm10840-pci-vpd.bin"
fi

log "capturing the controlled legacy SDK runtime"
if [ -d /opt/silicom-legacy ]; then
    copy_contents /opt/silicom-legacy "$ROOTFS/opt/silicom-legacy"
else
    mkdir -p "$ROOTFS/opt/silicom-legacy/usr/local/rrc"
    [ -e /usr/local/rrc ] || die "neither /opt/silicom-legacy nor /usr/local/rrc exists"
    cp -aL /usr/local/rrc/. "$ROOTFS/opt/silicom-legacy/usr/local/rrc/"
    for path in \
        /etc/perl \
        /usr/lib/x86_64-linux-gnu/perl \
        /usr/lib/x86_64-linux-gnu/perl5 \
        /usr/lib/x86_64-linux-gnu/perl-base \
        /usr/share/perl \
        /usr/share/perl5; do
        [ -e "$path" ] || continue
        mkdir -p "$ROOTFS/opt/silicom-legacy$(dirname "$path")"
        cp -a "$path" "$ROOTFS/opt/silicom-legacy$(dirname "$path")/"
    done
    mkdir -p "$ROOTFS/opt/silicom-legacy/usr/bin" "$ROOTFS/opt/silicom-legacy/usr/lib/x86_64-linux-gnu"
    for path in /usr/bin/perl /usr/bin/perl5.22* /usr/lib/x86_64-linux-gnu/libperl.so.5.22*; do
        [ -e "$path" ] || [ -L "$path" ] || continue
        cp -a "$path" "$ROOTFS/opt/silicom-legacy${path%/*}/"
    done
fi

log "capturing the selected B0 platform files (explicit whitelist)"
for path in \
    /usr/share/netfab/fm_platform_attributes.cfg \
    /usr/share/netfab/fm_platform_attributes_silicom.cfg; do
    copy_path "$path"
done

[ -f "$ROOTFS/usr/share/netfab/fm_platform_attributes.cfg" ] || \
    [ -f "$ROOTFS/usr/share/netfab/fm_platform_attributes_silicom.cfg" ] || \
    die "no B0 platform configuration was found"

log "hashing and packaging"
(
    cd "$STAGE"
    : > SHA256SUMS
    find . -type f ! -name SHA256SUMS -print | LC_ALL=C sort | while IFS= read -r file; do
        sha256sum "$file" >> SHA256SUMS
    done
)

archive="$OUTPUT_DIR/pe31625g24dira-board-bundle-${bundle_id}.tar.gz"
tar -C "$WORK_DIR" -czf "$archive" pe31625g24dira-board-bundle
chmod 600 "$archive"
(cd "$OUTPUT_DIR" && sha256sum "$(basename "$archive")" > "$(basename "$archive").sha256")
chmod 600 "$archive.sha256"
log "created: $archive"
log "checksum: $archive.sha256"
log "copy both files off the machine before reinstalling"
