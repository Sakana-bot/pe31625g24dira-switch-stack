#!/bin/bash
set -euo pipefail

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
KIT_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
MODE=audit
BACKUP_ROOT=""
DRIVER_CHANGED=0
SWITCH_CHANGED=0
MANAGER_CHANGED=0
BOOT_CHANGED=0
STALE_METADATA=0
NEEDS_REBOOT=0

usage() {
    cat <<'EOF'
Usage: sudo bash deployment/upgrade.sh [--audit|--apply]

  --audit        Compare the kit with the installed system (default)
  --apply        Back up changed files, synchronize them, and restart only required services
The upgrade preserves platform configuration, topology, VLANs, port state, fan curve,
administrator credentials, management networking, the installed legacy SDK runtime,
and the board's Flash contents.
EOF
}

log() { printf '[upgrade] %s\n' "$*"; }
warn() { printf '[upgrade] WARNING: %s\n' "$*" >&2; }
die() { printf '[upgrade] ERROR: %s\n' "$*" >&2; exit 1; }

while [ "$#" -gt 0 ]; do
    case "$1" in
        --audit) MODE=audit; shift ;;
        --apply) MODE=apply; shift ;;
        -h|--help) usage; exit 0 ;;
        *) die "unknown argument: $1" ;;
    esac
done

[ "$(id -u)" -eq 0 ] || die "run as root"
for path in \
    "$KIT_ROOT/KIT-SHA256SUMS" \
    "$KIT_ROOT/RELEASE-MANIFEST.json" \
    "$KIT_ROOT/VERSION" \
    "$KIT_ROOT/driver/fm10k-uio-6.12.101-ies2/dkms.conf" \
    "$KIT_ROOT/webui/app.py" \
    "$KIT_ROOT/webui/l2_features.py" \
    "$KIT_ROOT/webui/runtime_state.py" \
    "$KIT_ROOT/webui/pe31625g24dira-switch-manager.service" \
    "$KIT_ROOT/deployment/99-pe31625g24dira-display.cfg" \
    "$KIT_ROOT/switch_service/pe31625g24dira-switch.service"; do
    [ -f "$path" ] || die "incomplete deployment kit; missing $path"
done
(cd "$KIT_ROOT" && sha256sum -c --quiet KIT-SHA256SUMS) || die "deployment kit verification failed"
[ -f /etc/pe31625g24dira/webui/config.json ] || die "Switch Manager is not installed"
[ -d /opt/pe31625g24dira-switch-manager ] || die "Switch Manager program directory is missing"
trap 'exit 130' HUP INT TERM

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
[ "$SUBSYSTEM" = "0x1374:0x01d0" ] || die "unsupported FM10840 subsystem: $SUBSYSTEM"

show_file_difference() {
    local category=$1 source=$2 target=$3
    if [ ! -e "$target" ]; then
        printf '  ADD     %-8s %s\n' "$category" "$target"
        return 0
    fi
    if ! cmp -s -- "$source" "$target"; then
        printf '  UPDATE  %-8s %s\n' "$category" "$target"
        return 0
    fi
    return 1
}

tree_difference() {
    local category=$1 source=$2 target=$3 output
    if [ ! -d "$target" ]; then
        printf '  ADD     %-8s %s/\n' "$category" "$target"
        return 0
    fi
    # Kit archives are built on Windows. Compare content and file names, not
    # archive timestamps or Windows mode metadata.
    output=$(rsync -rnic --delete --no-times --omit-dir-times --no-perms "$source/" "$target/")
    if [ -n "$output" ]; then
        printf '  UPDATE  %-8s %s/ (%s entries)\n' "$category" "$target" "$(printf '%s\n' "$output" | wc -l)"
        printf '%s\n' "$output" | sed -n '1,12s/^/           /p'
        [ "$(printf '%s\n' "$output" | wc -l)" -le 12 ] || printf '           ...\n'
        return 0
    fi
    return 1
}

check_file() {
    local category=$1 source=$2 target=$3
    if show_file_difference "$category" "$source" "$target"; then
        case "$category" in
            manager) MANAGER_CHANGED=1 ;;
            switch) SWITCH_CHANGED=1 ;;
            boot) BOOT_CHANGED=1 ;;
        esac
    fi
}

log "auditing installed files against kit $(cat "$KIT_ROOT/VERSION")"
check_file manager "$KIT_ROOT/RELEASE-MANIFEST.json" /opt/pe31625g24dira-switch-manager/RELEASE-MANIFEST.json
check_file manager "$KIT_ROOT/VERSION" /opt/pe31625g24dira-switch-manager/VERSION
for name in app.py l2_features.py runtime_state.py uio_probe.py uio_watch.py; do
    check_file manager "$KIT_ROOT/webui/$name" "/opt/pe31625g24dira-switch-manager/$name"
done
check_file manager "$KIT_ROOT/webui/sensors.tp" /etc/pe31625g24dira/webui/sensors.tp
check_file manager "$KIT_ROOT/webui/pe31625g24dira-switch-manager.service" /etc/systemd/system/pe31625g24dira-switch-manager.service
if tree_difference manager "$KIT_ROOT/webui/static" /opt/pe31625g24dira-switch-manager/static; then
    MANAGER_CHANGED=1
fi
if [ -e /var/lib/pe31625g24dira/runtime-manifest.json ] || \
   [ -d /var/lib/pe31625g24dira/original-board ]; then
    printf '  REMOVE  %-8s %s\n' metadata /var/lib/pe31625g24dira/runtime-manifest.json
    printf '  REMOVE  %-8s %s\n' metadata /var/lib/pe31625g24dira/original-board
    STALE_METADATA=1
fi
check_file boot "$KIT_ROOT/deployment/99-pe31625g24dira-display.cfg" \
    /etc/default/grub.d/99-pe31625g24dira-display.cfg

check_file switch "$KIT_ROOT/switch_service/pe31625g24dira-board-init.sh" /usr/local/sbin/pe31625g24dira-board-init
check_file switch "$KIT_ROOT/switch_service/pe31625g24dira-queue-fan-init.sh" /usr/local/sbin/pe31625g24dira-queue-fan-init
check_file switch "$KIT_ROOT/switch_service/pe31625g24dira-testpoint-wrapper.sh" /usr/local/sbin/pe31625g24dira-testpoint-wrapper
for name in pe31625g24dira-board-init.service pe31625g24dira-switch.service pe31625g24dira-fan-init.service; do
    check_file switch "$KIT_ROOT/switch_service/$name" "/etc/systemd/system/$name"
done
for name in pe31625g24dira-fan-dump.tp pe31625g24dira-fan-pwm-test.tp pe31625g24dira-verify.tp; do
    check_file switch "$KIT_ROOT/switch_service/$name" "/etc/pe31625g24dira/$name"
done

if tree_difference driver "$KIT_ROOT/driver/fm10k-uio-6.12.101-ies2" /usr/src/fm10k-uio-6.12.101-ies2 || \
   dkms status 2>/dev/null | grep -Eq 'fm10k-uio/(1\.1\.0|6\.12\.101-ies1)'; then
    DRIVER_CHANGED=1
else
    log "fm10k-uio driver source matches"
fi
log "installed legacy SDK runtime is preserved"

if [ "$MANAGER_CHANGED" -eq 0 ] && [ "$SWITCH_CHANGED" -eq 0 ] && \
   [ "$BOOT_CHANGED" -eq 0 ] && \
   [ "$DRIVER_CHANGED" -eq 0 ] && [ "$STALE_METADATA" -eq 0 ]; then
    log "installed system already matches the supplied sources"
    exit 0
fi
[ "$MODE" = apply ] || { log "audit complete; run again with --apply to synchronize"; exit 0; }

BACKUP_ROOT="/var/backups/pe31625g24dira/upgrade-$(date +%Y%m%d-%H%M%S)"
install -d -m 700 "$BACKUP_ROOT/files"

backup_path() {
    local target=$1
    [ -e "$target" ] || [ -L "$target" ] || return 0
    (cd / && cp -a --parents -- "${target#/}" "$BACKUP_ROOT/files")
}

backup_path /etc/pe31625g24dira
backup_path /opt/pe31625g24dira-switch-manager
backup_path /etc/systemd/system/pe31625g24dira-switch-manager.service
backup_path /etc/systemd/system/pe31625g24dira-board-init.service
backup_path /etc/systemd/system/pe31625g24dira-switch.service
backup_path /etc/systemd/system/pe31625g24dira-fan-init.service
backup_path /usr/local/sbin/pe31625g24dira-board-init
backup_path /usr/local/sbin/pe31625g24dira-queue-fan-init
backup_path /usr/local/sbin/pe31625g24dira-testpoint-wrapper
[ "$BOOT_CHANGED" -eq 0 ] || backup_path /etc/default/grub.d/99-pe31625g24dira-display.cfg
[ "$DRIVER_CHANGED" -eq 0 ] || backup_path /usr/src/fm10k-uio-1.1.0
[ "$DRIVER_CHANGED" -eq 0 ] || backup_path /usr/src/fm10k-uio-6.12.101-ies1
[ "$DRIVER_CHANGED" -eq 0 ] || backup_path /usr/src/fm10k-uio-6.12.101-ies2

rollback() {
    local status=$?
    trap - ERR
    set +e
    warn "upgrade failed; restoring $BACKUP_ROOT"
    if [ "$BOOT_CHANGED" -eq 1 ] && \
       [ ! -e "$BACKUP_ROOT/files/etc/default/grub.d/99-pe31625g24dira-display.cfg" ]; then
        rm -f -- /etc/default/grub.d/99-pe31625g24dira-display.cfg
    fi
    cp -a "$BACKUP_ROOT/files/." /
    [ "$BOOT_CHANGED" -eq 0 ] || update-grub
    systemctl daemon-reload
    if [ "$DRIVER_CHANGED" -eq 1 ]; then
        dkms remove fm10k-uio/6.12.101-ies2 --all >/dev/null 2>&1
    fi
    if [ "$DRIVER_CHANGED" -eq 1 ] && [ -d "$BACKUP_ROOT/files/usr/src/fm10k-uio-1.1.0" ]; then
        rsync -a --delete "$BACKUP_ROOT/files/usr/src/fm10k-uio-1.1.0/" /usr/src/fm10k-uio-1.1.0/
        dkms remove fm10k-uio/1.1.0 --all >/dev/null 2>&1
        dkms add fm10k-uio/1.1.0 >/dev/null 2>&1
        dkms build fm10k-uio/1.1.0 -k "$(uname -r)" >/dev/null 2>&1
        dkms install fm10k-uio/1.1.0 -k "$(uname -r)" >/dev/null 2>&1
        depmod -a
    elif [ "$DRIVER_CHANGED" -eq 1 ] && [ -d "$BACKUP_ROOT/files/usr/src/fm10k-uio-6.12.101-ies1" ]; then
        rsync -a --delete "$BACKUP_ROOT/files/usr/src/fm10k-uio-6.12.101-ies1/" /usr/src/fm10k-uio-6.12.101-ies1/
        dkms add fm10k-uio/6.12.101-ies1 >/dev/null 2>&1
        dkms build fm10k-uio/6.12.101-ies1 -k "$(uname -r)" >/dev/null 2>&1
        dkms install fm10k-uio/6.12.101-ies1 -k "$(uname -r)" >/dev/null 2>&1
        depmod -a
    elif [ "$DRIVER_CHANGED" -eq 1 ] && [ -d "$BACKUP_ROOT/files/usr/src/fm10k-uio-6.12.101-ies2" ]; then
        rsync -a --delete "$BACKUP_ROOT/files/usr/src/fm10k-uio-6.12.101-ies2/" /usr/src/fm10k-uio-6.12.101-ies2/
        dkms add fm10k-uio/6.12.101-ies2 >/dev/null 2>&1
        dkms build fm10k-uio/6.12.101-ies2 -k "$(uname -r)" >/dev/null 2>&1
        dkms install fm10k-uio/6.12.101-ies2 -k "$(uname -r)" >/dev/null 2>&1
        depmod -a
    fi
    modprobe uio
    modprobe fm10k
    systemctl restart pe31625g24dira-switch.service
    systemctl restart pe31625g24dira-fan-init.service
    systemctl restart pe31625g24dira-switch-manager.service
    warn "rollback attempted; inspect services before retrying"
    exit "$status"
}
trap rollback ERR

if [ "$DRIVER_CHANGED" -eq 1 ] || [ "$SWITCH_CHANGED" -eq 1 ]; then
    systemctl stop pe31625g24dira-switch-manager.service
    systemctl stop pe31625g24dira-fan-init.service >/dev/null 2>&1 || true
    systemctl stop pe31625g24dira-switch.service
fi

if [ "$MANAGER_CHANGED" -eq 1 ]; then
    log "synchronizing Switch Manager"
    install -d -m 755 /opt/pe31625g24dira-switch-manager/static /etc/pe31625g24dira/webui
    install -m 644 "$KIT_ROOT/RELEASE-MANIFEST.json" /opt/pe31625g24dira-switch-manager/RELEASE-MANIFEST.json
    install -m 644 "$KIT_ROOT/VERSION" /opt/pe31625g24dira-switch-manager/VERSION
    for name in app.py l2_features.py runtime_state.py uio_probe.py uio_watch.py; do
        install -m 644 "$KIT_ROOT/webui/$name" "/opt/pe31625g24dira-switch-manager/$name"
    done
    rsync -r --delete --no-times --omit-dir-times --no-perms \
        "$KIT_ROOT/webui/static/" /opt/pe31625g24dira-switch-manager/static/
    find /opt/pe31625g24dira-switch-manager/static -type d -exec chmod 755 {} +
    find /opt/pe31625g24dira-switch-manager/static -type f -exec chmod 644 {} +
    install -m 600 "$KIT_ROOT/webui/sensors.tp" /etc/pe31625g24dira/webui/sensors.tp
    install -m 644 "$KIT_ROOT/webui/pe31625g24dira-switch-manager.service" /etc/systemd/system/pe31625g24dira-switch-manager.service
fi

if [ "$STALE_METADATA" -eq 1 ]; then
    rm -f -- /var/lib/pe31625g24dira/runtime-manifest.json
    rm -rf -- /var/lib/pe31625g24dira/original-board
fi

if [ "$BOOT_CHANGED" -eq 1 ]; then
    log "installing display polling policy"
    install -d -m 755 /etc/default/grub.d
    install -m 644 "$KIT_ROOT/deployment/99-pe31625g24dira-display.cfg" \
        /etc/default/grub.d/99-pe31625g24dira-display.cfg
    update-grub
fi
[ ! -w /sys/module/drm_kms_helper/parameters/poll ] || \
    printf 'N\n' > /sys/module/drm_kms_helper/parameters/poll

if [ "$SWITCH_CHANGED" -eq 1 ]; then
    log "synchronizing board and switch service files"
    install -m 755 "$KIT_ROOT/switch_service/pe31625g24dira-board-init.sh" /usr/local/sbin/pe31625g24dira-board-init
    install -m 755 "$KIT_ROOT/switch_service/pe31625g24dira-queue-fan-init.sh" /usr/local/sbin/pe31625g24dira-queue-fan-init
    install -m 755 "$KIT_ROOT/switch_service/pe31625g24dira-testpoint-wrapper.sh" /usr/local/sbin/pe31625g24dira-testpoint-wrapper
    for name in pe31625g24dira-board-init.service pe31625g24dira-switch.service pe31625g24dira-fan-init.service; do
        install -m 644 "$KIT_ROOT/switch_service/$name" "/etc/systemd/system/$name"
    done
    for name in pe31625g24dira-fan-dump.tp pe31625g24dira-fan-pwm-test.tp pe31625g24dira-verify.tp; do
        install -m 600 "$KIT_ROOT/switch_service/$name" "/etc/pe31625g24dira/$name"
    done
fi

if [ "$DRIVER_CHANGED" -eq 1 ]; then
    log "installing fm10k 6.12.101-ies2"
    rsync -r --delete --no-times --omit-dir-times --no-perms \
        "$KIT_ROOT/driver/fm10k-uio-6.12.101-ies2/" /usr/src/fm10k-uio-6.12.101-ies2/
    find /usr/src/fm10k-uio-6.12.101-ies2 -type d -exec chmod 755 {} +
    find /usr/src/fm10k-uio-6.12.101-ies2 -type f -exec chmod 644 {} +
    dkms remove fm10k-uio/1.1.0 --all >/dev/null 2>&1 || true
    dkms remove fm10k-uio/6.12.101-ies1 --all >/dev/null 2>&1 || true
    dkms remove fm10k-uio/6.12.101-ies2 --all >/dev/null 2>&1 || true
    dkms add fm10k-uio/6.12.101-ies2
    dkms build fm10k-uio/6.12.101-ies2 -k "$(uname -r)"
    dkms install fm10k-uio/6.12.101-ies2 -k "$(uname -r)"
    rm -rf -- /usr/src/fm10k-uio-1.1.0
    rm -rf -- /usr/src/fm10k-uio-6.12.101-ies1
    depmod -a
    update-initramfs -u -k "$(uname -r)"
    if ! modprobe -r fm10k; then
        NEEDS_REBOOT=1
        warn "running fm10k module is busy; the upgraded module will load after reboot"
    else
        modprobe uio
        modprobe fm10k
    fi
fi

systemctl daemon-reload
python3 /opt/pe31625g24dira-switch-manager/app.py \
    --check-platform /usr/share/netfab/fm_platform_attributes_pe31625g24dira.cfg >/dev/null
python3 /opt/pe31625g24dira-switch-manager/app.py \
    --sync-runtime /etc/pe31625g24dira/webui/config.json >/dev/null

if [ "$DRIVER_CHANGED" -eq 1 ] || [ "$SWITCH_CHANGED" -eq 1 ]; then
    systemctl start pe31625g24dira-switch.service
    systemctl restart pe31625g24dira-fan-init.service
fi
systemctl restart pe31625g24dira-switch-manager.service
systemctl is-active --quiet pe31625g24dira-switch.service
systemctl is-active --quiet pe31625g24dira-switch-manager.service

trap - ERR
log "upgrade complete; backup: $BACKUP_ROOT"
[ "$NEEDS_REBOOT" -eq 0 ] || warn "reboot is required to activate the new fm10k module"
