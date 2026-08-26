#!/bin/bash
set -euo pipefail

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
KIT_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
EXPECTED_BUNDLE_FORMAT=3
BUNDLE=""
RUNTIME=""
RUNTIME_EXPLICIT=0
# Optional defaults for a private/signed runtime download. Keep these empty in
# the public repository; users may fill them locally or use the CLI options.
DEFAULT_RUNTIME_URL=""
DEFAULT_RUNTIME_SHA256=""
RUNTIME_URL=${PE31625G24DIRA_RUNTIME_URL:-$DEFAULT_RUNTIME_URL}
RUNTIME_SHA256=${PE31625G24DIRA_RUNTIME_SHA256:-$DEFAULT_RUNTIME_SHA256}
AUDIT=0
REBOOT=0
ASSUME_YES=0
FORCE_PLATFORM_PROFILE=0
PLATFORM_PROFILE=auto
TEMP_DIR=""
PROFILE_TEMP_DIR=""
RUNTIME_DOWNLOAD_DIR=""
NEEDS_REBOOT=0
RUNTIME_MANIFEST_SOURCE=""

usage() {
    cat <<'EOF'
Usage: sudo bash deployment/deploy-debian13.sh [options]

Options:
  --audit                         Validate and inspect without changing anything
  --bundle FILE                   Use a private runtime/platform bundle
  --runtime FILE                  Use a compatible legacy SDK runtime package
  --runtime-url HTTPS_URL         Download the runtime package on this board
  --runtime-sha256 SHA256         Verify a downloaded runtime package (recommended)
  --platform-profile auto|sil001-hw4-b0|bundle
                                  Select an embedded profile automatically (default),
                                  a known profile, or the supplied bundle configuration
  --force-platform-profile       Allow the selected profile on unmatched hardware;
                                  never permits deployment without a real config/runtime
  --reboot --yes                  Reboot automatically after successful deployment
  --yes                           Confirm explicitly requested disruptive actions
EOF
}

log() { printf '[deploy] %s\n' "$*"; }
warn() { printf '[deploy] WARNING: %s\n' "$*" >&2; }
die() { printf '[deploy] ERROR: %s\n' "$*" >&2; exit 1; }

while [ "$#" -gt 0 ]; do
    case "$1" in
        --bundle) [ "$#" -ge 2 ] || die "--bundle requires a value"; BUNDLE=$2; shift 2 ;;
        --runtime) [ "$#" -ge 2 ] || die "--runtime requires a value"; RUNTIME=$2; RUNTIME_EXPLICIT=1; shift 2 ;;
        --runtime-url) [ "$#" -ge 2 ] || die "--runtime-url requires a value"; RUNTIME_URL=$2; RUNTIME_EXPLICIT=1; shift 2 ;;
        --runtime-sha256) [ "$#" -ge 2 ] || die "--runtime-sha256 requires a value"; RUNTIME_SHA256=$2; shift 2 ;;
        --audit) AUDIT=1; shift ;;
        --platform-profile) [ "$#" -ge 2 ] || die "--platform-profile requires a value"; PLATFORM_PROFILE=$2; shift 2 ;;
        --force-platform-profile) FORCE_PLATFORM_PROFILE=1; shift ;;
        --reboot) REBOOT=1; shift ;;
        --yes) ASSUME_YES=1; shift ;;
        -h|--help) usage; exit 0 ;;
        *) die "unknown argument: $1" ;;
    esac
done

[ "$(id -u)" -eq 0 ] || die "run as root"
[ -z "$BUNDLE" ] || [ -f "$BUNDLE" ] || die "bundle not found: $BUNDLE"
[ -z "$RUNTIME" ] || [ -f "$RUNTIME" ] || die "runtime package not found: $RUNTIME"
[ -z "$RUNTIME" ] || [ -z "$RUNTIME_URL" ] || die "use only one of --runtime and --runtime-url"
[ -n "$RUNTIME_URL" ] || [ -z "$RUNTIME_SHA256" ] || die "runtime SHA-256 requires a runtime URL"
[ -z "$RUNTIME_URL" ] || RUNTIME_EXPLICIT=1
case "$PLATFORM_PROFILE" in auto|sil001-hw4-b0|bundle) ;; *) die "--platform-profile must be auto, sil001-hw4-b0, or bundle" ;; esac
[ "$PLATFORM_PROFILE" != bundle ] || [ -n "$BUNDLE" ] || die "the bundle platform profile requires --bundle FILE"
[ "$REBOOT" -eq 0 ] || [ "$ASSUME_YES" -eq 1 ] || die "--reboot also requires --yes"

for path in \
    "$KIT_ROOT/RELEASE-MANIFEST.json" \
    "$KIT_ROOT/VERSION" \
    "$KIT_ROOT/driver/fm10k-uio-6.12.101-ies1/dkms.conf" \
    "$KIT_ROOT/deployment/runtime-package.sh" \
    "$KIT_ROOT/platforms/sil001-hw4-b0/fm_platform_attributes.cfg" \
    "$KIT_ROOT/switch_service/pe31625g24dira-fan-init.service" \
    "$KIT_ROOT/switch_service/pe31625g24dira-switch.service" \
    "$KIT_ROOT/webui/app.py" \
    "$KIT_ROOT/webui/l2_features.py" \
    "$KIT_ROOT/webui/runtime_state.py" \
    "$KIT_ROOT/webui/pe31625g24dira-switch-manager.service"; do
    [ -f "$path" ] || die "incomplete deployment kit; missing $path"
done
[ -f "$KIT_ROOT/KIT-SHA256SUMS" ] || die "deployment kit hash manifest is missing"
(cd "$KIT_ROOT" && sha256sum -c --quiet KIT-SHA256SUMS) || die "deployment kit file verification failed"

cleanup() {
    [ -z "$TEMP_DIR" ] || rm -rf -- "$TEMP_DIR"
    [ -z "$PROFILE_TEMP_DIR" ] || rm -rf -- "$PROFILE_TEMP_DIR"
    [ -z "$RUNTIME_DOWNLOAD_DIR" ] || rm -rf -- "$RUNTIME_DOWNLOAD_DIR"
}
trap cleanup EXIT
trap 'exit 130' HUP INT TERM

validate_bundle_archive() {
    python3 - "$1" <<'PY'
import posixpath
import sys
import tarfile

archive = sys.argv[1]
with tarfile.open(archive, "r:gz") as tf:
    names = set()
    for member in tf.getmembers():
        name = member.name
        normalized = posixpath.normpath(name)
        if not name or name.startswith("/") or normalized == ".." or normalized.startswith("../"):
            raise SystemExit("unsafe archive member: " + repr(name))
        if not (normalized == "pe31625g24dira-board-bundle" or normalized.startswith("pe31625g24dira-board-bundle/")):
            raise SystemExit("unexpected archive root: " + repr(name))
        if member.isdev() or member.isfifo():
            raise SystemExit("special archive member is not allowed: " + repr(name))
        if member.issym() or member.islnk():
            base = posixpath.dirname(normalized)
            target = posixpath.normpath(posixpath.join(base, member.linkname))
            if target == ".." or target.startswith("../") or not target.startswith("pe31625g24dira-board-bundle/"):
                raise SystemExit("unsafe archive link: " + repr(name))
        names.add(normalized)
    required = {
        "pe31625g24dira-board-bundle/manifest.env",
        "pe31625g24dira-board-bundle/SHA256SUMS",
    }
    missing = required - names
    if missing:
        raise SystemExit("bundle is missing: " + ", ".join(sorted(missing)))
PY
}

. "$SCRIPT_DIR/runtime-package.sh"

BUNDLE_ROOT=""
BUNDLE_ID=""
if [ -n "$BUNDLE" ]; then
    validate_bundle_archive "$BUNDLE"
    if [ -f "$BUNDLE.sha256" ]; then
        log "verifying external bundle checksum"
        (cd "$(dirname "$BUNDLE")" && sha256sum -c --quiet "$(basename "$BUNDLE").sha256")
    else
        warn "external .sha256 sidecar is absent; internal file hashes will still be checked"
    fi

    TEMP_DIR=$(mktemp -d "${TMPDIR:-/var/tmp}/pe31625g24dira-deploy.XXXXXX")
    tar --no-same-owner --no-same-permissions -C "$TEMP_DIR" -xzf "$BUNDLE"
    BUNDLE_ROOT="$TEMP_DIR/pe31625g24dira-board-bundle"
    (cd "$BUNDLE_ROOT" && sha256sum -c --quiet SHA256SUMS)

    manifest_value() {
        sed -n "s/^$1=//p" "$BUNDLE_ROOT/manifest.env" | head -n 1
    }
    [ "$(manifest_value FORMAT_VERSION)" = "$EXPECTED_BUNDLE_FORMAT" ] || die "unsupported bundle format"
    [ "$(manifest_value PRODUCT_MODEL)" = "PE31625G24DIRA" ] || die "bundle product model is not PE31625G24DIRA"
    BUNDLE_ID=$(manifest_value BUNDLE_ID)
    [ -n "$BUNDLE_ID" ] || die "bundle ID is missing"
    case "$BUNDLE_ID" in *[!A-Za-z0-9._-]*) die "unsafe bundle ID" ;; esac
fi

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

detect_platform_profile() {
    python3 - "$FM_DEV/vpd" <<'PY'
import pathlib
import re
import sys

try:
    data = pathlib.Path(sys.argv[1]).read_bytes()
except OSError:
    raise SystemExit(0)
strings = [value.decode("ascii").strip() for value in re.findall(rb"[\x20-\x7e]{4,}", data)]
model = next((value for value in strings if re.fullmatch(r"PE31625G24DIRA(?:-MPS)?", value, re.I)), None)
version = next((value for value in strings if re.fullmatch(r"\d{4}", value)), None)
if model and version:
    significant = version.lstrip("0")
    if significant and int(significant[0]) < 6:
        print("sil001-hw4-b0")
PY
}

DETECTED_PLATFORM_PROFILE=$(detect_platform_profile)
if [ "$PLATFORM_PROFILE" = auto ]; then
    if [ "$SUBSYSTEM" = "0x1374:0x01d0" ] && [ "$DETECTED_PLATFORM_PROFILE" = sil001-hw4-b0 ]; then
        PLATFORM_PROFILE=sil001-hw4-b0
    else
        die "no embedded platform profile matches subsystem $SUBSYSTEM and the board VPD; supply --bundle FILE --platform-profile bundle together with --runtime FILE or --runtime-url HTTPS_URL"
    fi
fi

# The embedded runtime is only selected for the verified sil001-hw4-b0 profile.
# Other profiles must opt in to a matching external runtime explicitly.
if [ "$PLATFORM_PROFILE" = sil001-hw4-b0 ] && [ "$RUNTIME_EXPLICIT" -eq 0 ] && \
   [ "$SUBSYSTEM" = "0x1374:0x01d0" ] && [ "$DETECTED_PLATFORM_PROFILE" = sil001-hw4-b0 ] && \
   [ -d "$KIT_ROOT/runtime" ]; then
    set -- "$KIT_ROOT"/runtime/pe31625g24dira-legacy-sdk-runtime-*.tar.gz
    if [ -f "$1" ] && [ "$#" -eq 1 ]; then
        RUNTIME=$1
        log "using the runtime embedded for the verified sil001-hw4-b0 profile"
    elif [ -f "$1" ]; then
        die "deployment kit contains multiple embedded runtime packages"
    fi
fi
if [ -z "$RUNTIME" ] && [ -n "$RUNTIME_URL" ]; then
    RUNTIME_DOWNLOAD_DIR=$(mktemp -d "${TMPDIR:-/var/tmp}/pe31625g24dira-runtime-download.XXXXXX")
    RUNTIME="$RUNTIME_DOWNLOAD_DIR/legacy-sdk-runtime.tar.gz"
    log "downloading the user-supplied legacy SDK runtime"
    download_runtime_package "$RUNTIME_URL" "$RUNTIME" "$RUNTIME_SHA256" || die "runtime download or checksum verification failed"
fi

if [ "$PLATFORM_PROFILE" = sil001-hw4-b0 ]; then
    if { [ "$SUBSYSTEM" != "0x1374:0x01d0" ] || [ "$DETECTED_PLATFORM_PROFILE" != sil001-hw4-b0 ]; } && \
       [ "$FORCE_PLATFORM_PROFILE" -eq 0 ]; then
        die "sil001-hw4-b0 requires matching PCI subsystem and B0 VPD; use --force-platform-profile only after manual confirmation"
    fi
    if [ "$SUBSYSTEM" != "0x1374:0x01d0" ] || [ "$DETECTED_PLATFORM_PROFILE" != sil001-hw4-b0 ]; then
        warn "forcing sil001-hw4-b0 on unmatched hardware identity"
    fi
    source_platform="$KIT_ROOT/platforms/sil001-hw4-b0/fm_platform_attributes.cfg"
    base_platform=$source_platform
    [ -n "$RUNTIME" ] || die "sil001-hw4-b0 runtime is missing from this kit; supply --runtime FILE or --runtime-url HTTPS_URL"
    PROFILE_TEMP_DIR=$(mktemp -d "${TMPDIR:-/var/tmp}/pe31625g24dira-runtime.XXXXXX")
    extract_runtime_package "$RUNTIME" "$PROFILE_TEMP_DIR" sil001-hw4-b0 || die "runtime package validation failed"
    legacy_source=$RUNTIME_SDK_SOURCE
    BUNDLE_ID=$RUNTIME_SOURCE_ID
else
    [ -n "$BUNDLE_ROOT" ] || die "the bundle platform profile requires --bundle FILE"
    [ "$RUNTIME_EXPLICIT" -eq 1 ] && [ -n "$RUNTIME" ] || die "the bundle platform profile requires an explicit --runtime FILE or --runtime-url HTTPS_URL"
    source_platform=""
    for path in \
        "$BUNDLE_ROOT/rootfs/usr/share/netfab/fm_platform_attributes.cfg" \
        "$BUNDLE_ROOT/rootfs/usr/share/netfab/fm_platform_attributes_silicom.cfg"; do
        [ -f "$path" ] && source_platform=$path && break
    done
    [ -n "$source_platform" ] || die "bundle does not contain a platform configuration"
    base_platform="$BUNDLE_ROOT/rootfs/usr/share/netfab/fm_platform_attributes_silicom.cfg"
    [ -f "$base_platform" ] || base_platform=$source_platform
    PROFILE_TEMP_DIR=$(mktemp -d "${TMPDIR:-/var/tmp}/pe31625g24dira-runtime.XXXXXX")
    extract_runtime_package "$RUNTIME" "$PROFILE_TEMP_DIR" bundle || die "external runtime package does not declare compatibility with the bundle profile"
    legacy_source=$RUNTIME_SDK_SOURCE
fi

os_id=unknown
os_version=unknown
if [ -r /etc/os-release ]; then
    . /etc/os-release
    os_id=${ID:-unknown}
    os_version=${VERSION_ID:-unknown}
fi

[ -n "$BUNDLE_ID" ] || BUNDLE_ID="embedded-$PLATFORM_PROFILE"
log "runtime source: $BUNDLE_ID"
log "target: $os_id $os_version, kernel $(uname -r), subsystem $SUBSYSTEM"
log "detected platform profile: ${DETECTED_PLATFORM_PROFILE:-unknown}"
log "platform profile: $PLATFORM_PROFILE"
log "platform source: $source_platform"
log "factory base: $base_platform"

audit_report() {
    local state
    printf '\nAudit summary\n'
    if [ -n "$BUNDLE_ROOT" ]; then
        printf '  bundle integrity: OK\n'
    else
        printf '  embedded profile: OK\n'
    fi
    printf '  product model:    PE31625G24DIRA\n'
    printf '  FM10840 ASIC:     OK (%s)\n' "$SUBSYSTEM"
    printf '  legacy SDK:       OK\n'
    printf '  deployment kit:   OK\n'
    printf '  target OS:        %s %s\n' "$os_id" "$os_version"
    if command -v dkms >/dev/null 2>&1; then
        state=$(dkms status 2>/dev/null | grep 'fm10k-uio/6.12.101-ies1' || true)
        printf '  DKMS:             %s\n' "${state:-not installed}"
    else
        printf '  DKMS:             command unavailable\n'
    fi
    printf '  driver source:     local DKMS build\n'
    if [ -e /dev/uio0 ]; then
        printf '  UIO:              /dev/uio0 present\n'
    else
        printf '  UIO:              absent (normal before deployment/reboot)\n'
    fi
    if grep -qw 'pci=realloc=off' /proc/cmdline 2>/dev/null; then
        printf '  kernel argument:  active\n'
    else
        printf '  kernel argument:  not active; deployment will require reboot\n'
    fi
}

if [ "$AUDIT" -eq 1 ]; then
    audit_report
    exit 0
fi

[ "$os_id" = debian ] && [ "$os_version" = 13 ] || die "write mode requires Debian 13"
[ "$(uname -m)" = x86_64 ] || die "write mode requires x86_64"

BACKUP_ROOT="/var/backups/pe31625g24dira/deploy-$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "$BACKUP_ROOT/files"
chmod 700 "$BACKUP_ROOT"

backup_path() {
    local path=$1
    [ -e "$path" ] || [ -L "$path" ] || return 0
    cp -a --parents -- "$path" "$BACKUP_ROOT/files"
}

for path in \
    /opt/silicom-legacy \
    /usr/local/rrc \
    /usr/src/fm10k-uio-1.1.0 \
    /usr/src/fm10k-uio-6.12.101-ies1 \
    /usr/share/netfab/fm_platform_attributes.cfg \
    /usr/share/netfab/fm_platform_attributes_silicom.cfg \
    /usr/share/netfab/fm_platform_attributes_pe31625g24dira.cfg \
    /etc/modules-load.d/fm10840.conf \
    /etc/modules-load.d/fm10k-uio.conf \
    /etc/network/interfaces \
    /etc/default/grub.d/99-fm10840.cfg \
    /etc/default/grub.d/99-pe31625g24dira.cfg; do
    backup_path "$path"
done

log "installing Debian dependencies"
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y --no-install-recommends \
    build-essential ca-certificates dkms ethtool i2c-tools ifupdown iproute2 kmod libcrypt1 \
    "linux-headers-$(uname -r)" pciutils procps python3 rsync util-linux
dpkg --purge ifupdown2 >/dev/null 2>&1 || true

log "restoring original management interface layout"
install -m 644 "$KIT_ROOT/deployment/pe31625g24dira-interfaces" /etc/network/interfaces

log "removing all superseded development installations and local configuration"
for svc in \
    rrcd.service netfabagent.service hmonagent.service \
    fm10840-board-init.service fm10840-dumb-switch.service fm10840-webui.service \
    pe31625g24dira-board-init.service pe31625g24dira-switch.service \
    pe31625g24dira-fan-init.service pe31625g24dira-switch-manager.service; do
    systemctl disable --now "$svc" 2>/dev/null || true
done
rm -f -- \
    /etc/systemd/system/fm10840-board-init.service \
    /etc/systemd/system/fm10840-dumb-switch.service \
    /etc/systemd/system/fm10840-webui.service \
    /etc/systemd/system/pe31625g24dira-board-init.service \
    /etc/systemd/system/pe31625g24dira-switch.service \
    /etc/systemd/system/pe31625g24dira-fan-init.service \
    /etc/systemd/system/pe31625g24dira-switch-manager.service \
    /usr/local/sbin/fm10840-board-init \
    /usr/local/sbin/fm10840-queue-fan-init \
    /usr/local/sbin/fm10840-testpoint-wrapper \
    /usr/local/sbin/pe31625g24dira-board-init \
    /usr/local/sbin/pe31625g24dira-queue-fan-init \
    /usr/local/sbin/pe31625g24dira-testpoint-wrapper \
    /etc/modules-load.d/fm10840.conf \
    /etc/default/grub.d/99-fm10840.cfg \
    /usr/share/netfab/fm_platform_attributes_silicom.cfg
rm -rf -- \
    /opt/fm10840-webui /etc/fm10840 /etc/fm10840-webui \
    /opt/pe31625g24dira-switch-manager /etc/pe31625g24dira
systemctl daemon-reload

log "preserving the selected platform/runtime provenance"
original_dir="/var/lib/pe31625g24dira/original-board/$BUNDLE_ID"
rm -rf -- "$original_dir"
mkdir -p "$original_dir"
mkdir -p "$original_dir/metadata" "$original_dir/factory-rootfs"
if [ -n "$BUNDLE_ROOT" ]; then
    cp -a "$BUNDLE_ROOT/manifest.env" "$BUNDLE_ROOT/SHA256SUMS" "$original_dir/"
    [ ! -d "$BUNDLE_ROOT/metadata" ] || cp -a "$BUNDLE_ROOT/metadata/." "$original_dir/metadata/"
fi
[ -z "$RUNTIME_MANIFEST_SOURCE" ] || install -m 644 "$RUNTIME_MANIFEST_SOURCE" "$original_dir/RUNTIME-MANIFEST.json"
install -d -m 755 "$original_dir/factory-rootfs/usr/share/netfab"
install -m 644 "$source_platform" \
    "$original_dir/factory-rootfs/usr/share/netfab/fm_platform_attributes.cfg"
install -m 644 "$KIT_ROOT/RELEASE-MANIFEST.json" "$original_dir/RELEASE-MANIFEST.json"
install -m 644 "$KIT_ROOT/VERSION" "$original_dir/VERSION"

log "restoring controlled Perl 5.22 / legacy SDK runtime"
mkdir -p /opt/silicom-legacy
rsync -a --delete "$legacy_source/" /opt/silicom-legacy/
if [ -n "$RUNTIME_MANIFEST_SOURCE" ]; then
    install -d -m 755 /var/lib/pe31625g24dira
    install -m 644 "$RUNTIME_MANIFEST_SOURCE" /var/lib/pe31625g24dira/runtime-manifest.json
fi
if [ -L /usr/local/rrc ]; then
    rm -f -- /usr/local/rrc
elif [ -e /usr/local/rrc ]; then
    mv -- /usr/local/rrc "$BACKUP_ROOT/rrc-before-link"
fi
mkdir -p /usr/local
ln -s /opt/silicom-legacy/usr/local/rrc /usr/local/rrc

log "installing platform configuration and service scripts"
install -d -m 755 /usr/share/netfab /etc/pe31625g24dira /usr/local/sbin
install -m 644 "$source_platform" /usr/share/netfab/fm_platform_attributes.cfg
install -m 644 "$source_platform" /usr/share/netfab/fm_platform_attributes_pe31625g24dira.cfg
install -m 755 "$KIT_ROOT/switch_service/pe31625g24dira-board-init.sh" /usr/local/sbin/pe31625g24dira-board-init
install -m 755 "$KIT_ROOT/switch_service/pe31625g24dira-queue-fan-init.sh" /usr/local/sbin/pe31625g24dira-queue-fan-init
install -m 755 "$KIT_ROOT/switch_service/pe31625g24dira-testpoint-wrapper.sh" /usr/local/sbin/pe31625g24dira-testpoint-wrapper
install -m 644 "$KIT_ROOT/switch_service/pe31625g24dira-board-init.service" /etc/systemd/system/pe31625g24dira-board-init.service
install -m 644 "$KIT_ROOT/switch_service/pe31625g24dira-switch.service" /etc/systemd/system/pe31625g24dira-switch.service
install -m 644 "$KIT_ROOT/switch_service/pe31625g24dira-fan-init.service" /etc/systemd/system/pe31625g24dira-fan-init.service
for path in "$KIT_ROOT"/switch_service/*.tp; do
    install -m 600 "$path" "/etc/pe31625g24dira/$(basename "$path")"
done

log "building and installing fm10k 6.12.101-ies1 for $(uname -r)"
install -d -m 755 /usr/src/fm10k-uio-6.12.101-ies1
rsync -a --delete "$KIT_ROOT/driver/fm10k-uio-6.12.101-ies1/" /usr/src/fm10k-uio-6.12.101-ies1/
dkms remove fm10k-uio/1.1.0 --all >/dev/null 2>&1 || true
dkms remove fm10k-uio/6.12.101-ies1 --all >/dev/null 2>&1 || true
dkms add fm10k-uio/6.12.101-ies1
dkms build fm10k-uio/6.12.101-ies1 -k "$(uname -r)"
dkms install fm10k-uio/6.12.101-ies1 -k "$(uname -r)"
rm -rf -- /usr/src/fm10k-uio-1.1.0
log "installed locally compiled DKMS driver"
depmod -a
update-initramfs -u -k "$(uname -r)"
cat > /etc/modules-load.d/fm10k-uio.conf <<'EOF'
uio
fm10k
EOF

install -d -m 755 /etc/default/grub.d
install -m 644 "$KIT_ROOT/deployment/99-pe31625g24dira-display.cfg" \
    /etc/default/grub.d/99-pe31625g24dira-display.cfg
if ! grep -Rqs -- 'pci=realloc=off' /etc/default/grub /etc/default/grub.d 2>/dev/null; then
    cat > /etc/default/grub.d/99-pe31625g24dira.cfg <<'EOF'
GRUB_CMDLINE_LINUX_DEFAULT="${GRUB_CMDLINE_LINUX_DEFAULT} pci=realloc=off"
EOF
fi
update-grub
[ ! -w /sys/module/drm_kms_helper/parameters/poll ] || \
    printf 'N\n' > /sys/module/drm_kms_helper/parameters/poll

log "installing WebUI"
install -d -m 755 /opt/pe31625g24dira-switch-manager/static /etc/pe31625g24dira/webui
install -m 644 "$KIT_ROOT/RELEASE-MANIFEST.json" /opt/pe31625g24dira-switch-manager/RELEASE-MANIFEST.json
install -m 644 "$KIT_ROOT/VERSION" /opt/pe31625g24dira-switch-manager/VERSION
for path in app.py l2_features.py runtime_state.py uio_probe.py uio_watch.py; do
    install -m 644 "$KIT_ROOT/webui/$path" "/opt/pe31625g24dira-switch-manager/$path"
done
install -m 644 "$base_platform" \
    /opt/pe31625g24dira-switch-manager/reference_original_6x100.cfg
rsync -a --delete "$KIT_ROOT/webui/static/" /opt/pe31625g24dira-switch-manager/static/
for path in status.tp sensors.tp; do
    install -m 600 "$KIT_ROOT/webui/$path" "/etc/pe31625g24dira/webui/$path"
done
install -m 644 "$KIT_ROOT/webui/pe31625g24dira-switch-manager.service" /etc/systemd/system/pe31625g24dira-switch-manager.service
python3 /opt/pe31625g24dira-switch-manager/app.py --init-config /etc/pe31625g24dira/webui/config.json
install -m 600 "$KIT_ROOT/webui/fan-default.json" /etc/pe31625g24dira/webui/fan.json
log "fresh WebUI configuration created; administrator setup is required on first access"

management_if=$(ip -o route show default 2>/dev/null | awk '{print $5; exit}' || true)
python3 - /etc/pe31625g24dira/webui/config.json "$management_if" <<'PY'
import json
import os
import sys
import tempfile

path, interface = sys.argv[1:]
with open(path, encoding="utf-8") as handle:
    config = json.load(handle)
if interface:
    config["management_interface"] = interface
config.update({
    "platform_persistent": "/usr/share/netfab/fm_platform_attributes_pe31625g24dira.cfg",
    "topology_base": "/opt/pe31625g24dira-switch-manager/reference_original_6x100.cfg",
    "startup_script": "/etc/pe31625g24dira/pe31625g24dira-switch.tp",
    "status_script": "/etc/pe31625g24dira/webui/status.tp",
    "sensor_script": "/etc/pe31625g24dira/webui/sensors.tp",
    "vlan_config": "/etc/pe31625g24dira/webui/vlans.json",
    "vlan_apply_script": "/etc/pe31625g24dira/webui/vlan-apply.tp",
    "static_root": "/opt/pe31625g24dira-switch-manager/static",
    "backup_root": "/data/pe31625g24dira-switch-manager/backups",
})
directory = os.path.dirname(path)
fd, temporary = tempfile.mkstemp(prefix=".config.", dir=directory, text=True)
try:
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(config, handle, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)
finally:
    if os.path.exists(temporary):
        os.unlink(temporary)
PY

log "generating topology-specific switch startup and monitoring scripts"
python3 /opt/pe31625g24dira-switch-manager/app.py --sync-runtime /etc/pe31625g24dira/webui/config.json

systemctl daemon-reload
systemctl enable pe31625g24dira-board-init.service pe31625g24dira-switch.service pe31625g24dira-fan-init.service pe31625g24dira-switch-manager.service
systemctl enable systemd-modules-load.service >/dev/null 2>&1 || true
systemctl start pe31625g24dira-board-init.service

modprobe uio
if lsmod | awk '$1 == "fm10k" {found=1} END {exit !found}'; then
    if ! modprobe -r fm10k; then
        warn "fm10k is busy; the new module will load after reboot"
        NEEDS_REBOOT=1
    fi
fi
if [ "$NEEDS_REBOOT" -eq 0 ]; then
    modprobe fm10k || NEEDS_REBOOT=1
fi

if ! grep -qw 'pci=realloc=off' /proc/cmdline 2>/dev/null; then
    NEEDS_REBOOT=1
fi
if [ ! -e /dev/uio0 ]; then
    NEEDS_REBOOT=1
fi

if [ "$NEEDS_REBOOT" -eq 0 ]; then
    systemctl restart pe31625g24dira-switch.service
    systemctl restart pe31625g24dira-switch-manager.service
else
    warn "services are enabled but ASIC/WebUI startup is deferred until reboot"
fi

log "deployment completed; rollback copy: $BACKUP_ROOT"
dkms status | grep 'fm10k-uio/6.12.101-ies1' || true
if [ -e /dev/uio0 ]; then
    log "UIO ready: $(cat /sys/class/uio/uio0/name 2>/dev/null || printf unknown)"
fi

if [ "$REBOOT" -eq 1 ]; then
    log "rebooting by explicit request"
    systemctl reboot
elif [ "$NEEDS_REBOOT" -eq 1 ]; then
    printf '\nReboot required. Run: sudo reboot\n'
else
    printf '\nDeployment is active. Open the management IP in a browser.\n'
fi
