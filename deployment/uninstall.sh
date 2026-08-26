#!/bin/bash
set -euo pipefail

KEEP_RUNTIME=0
KEEP_DATA=0
ASSUME_YES=0

usage() {
    cat <<'EOF'
Usage: sudo bash deployment/uninstall.sh --yes [options]

Removes the Switch Stack services, WebUI, configuration, platform files and
fm10k-uio DKMS increment. The management network configuration and packages
installed through apt are preserved.

Options:
  --keep-runtime   Preserve /opt/silicom-legacy and /usr/local/rrc
  --keep-data      Preserve configuration, update state and project backups
  --yes            Confirm removal
EOF
}

log() { printf '[uninstall] %s\n' "$*"; }
die() { printf '[uninstall] ERROR: %s\n' "$*" >&2; exit 1; }

while [ "$#" -gt 0 ]; do
    case "$1" in
        --keep-runtime) KEEP_RUNTIME=1; shift ;;
        --keep-data) KEEP_DATA=1; shift ;;
        --yes) ASSUME_YES=1; shift ;;
        -h|--help) usage; exit 0 ;;
        *) die "unknown argument: $1" ;;
    esac
done

[ "$(id -u)" -eq 0 ] || die "run as root"
[ "$ASSUME_YES" -eq 1 ] || die "destructive removal requires --yes"

log "stopping and disabling project services"
for service in pe31625g24dira-switch-manager.service pe31625g24dira-fan-init.service pe31625g24dira-switch.service pe31625g24dira-board-init.service; do
    systemctl disable --now "$service" >/dev/null 2>&1 || true
done

rm -f -- \
    /etc/systemd/system/pe31625g24dira-switch-manager.service \
    /etc/systemd/system/pe31625g24dira-fan-init.service \
    /etc/systemd/system/pe31625g24dira-switch.service \
    /etc/systemd/system/pe31625g24dira-board-init.service \
    /usr/local/sbin/pe31625g24dira-board-init \
    /usr/local/sbin/pe31625g24dira-queue-fan-init \
    /usr/local/sbin/pe31625g24dira-testpoint-wrapper \
    /etc/modules-load.d/fm10k-uio.conf \
    /etc/default/grub.d/99-pe31625g24dira.cfg \
    /etc/default/grub.d/99-pe31625g24dira-display.cfg \
    /usr/share/netfab/fm_platform_attributes.cfg \
    /usr/share/netfab/fm_platform_attributes_pe31625g24dira.cfg

rm -rf -- /opt/pe31625g24dira-switch-manager /run/pe31625g24dira-testpoint

modprobe -r fm10k >/dev/null 2>&1 || true

if command -v dkms >/dev/null 2>&1; then
    dkms remove fm10k-uio/1.1.0 --all >/dev/null 2>&1 || true
fi
rm -rf -- /usr/src/fm10k-uio-1.1.0
depmod -a
update-initramfs -u -k "$(uname -r)" >/dev/null 2>&1 || true

if [ "$KEEP_RUNTIME" -eq 0 ]; then
    [ ! -L /usr/local/rrc ] || rm -f -- /usr/local/rrc
    rm -rf -- /opt/silicom-legacy
fi

if [ "$KEEP_DATA" -eq 0 ]; then
    rm -rf -- /etc/pe31625g24dira /var/lib/pe31625g24dira \
        /var/backups/pe31625g24dira /data/pe31625g24dira-switch-manager
fi

systemctl daemon-reload
update-grub >/dev/null 2>&1 || true
log "removed project files; management networking and distribution packages were preserved"
log "reboot before reinstalling if /dev/uio0 or the old fm10k module remains active"
