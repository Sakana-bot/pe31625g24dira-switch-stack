#!/bin/bash
# Shared legacy SDK runtime package validation and extraction helpers.

download_runtime_package() {
    local url=$1 target=$2 expected_sha256=${3:-}
    case "$url" in
        https://*) ;;
        *) printf '[runtime] ERROR: runtime URL must use HTTPS\n' >&2; return 1 ;;
    esac
    python3 - "$url" "$target" <<'PY'
import os
import pathlib
import sys
import urllib.request

url, target = sys.argv[1:]
headers = {"User-Agent": "PE31625G24DIRA-installer/1"}
token = os.environ.get("PE31625G24DIRA_RUNTIME_TOKEN", "").strip()
if token:
    headers["Authorization"] = "Bearer " + token
    headers["Accept"] = "application/octet-stream"
request = urllib.request.Request(url, headers=headers)
limit = 1024 * 1024 * 1024
written = 0
path = pathlib.Path(target)
with urllib.request.urlopen(request, timeout=60) as response, path.open("wb") as output:
    while True:
        chunk = response.read(1024 * 1024)
        if not chunk:
            break
        written += len(chunk)
        if written > limit:
            raise SystemExit("runtime download exceeds 1 GiB limit")
        output.write(chunk)
if written == 0:
    raise SystemExit("runtime download is empty")
PY
    if [ -n "$expected_sha256" ]; then
        case "$expected_sha256" in
            *[!0-9A-Fa-f]*|'') printf '[runtime] ERROR: invalid runtime SHA-256\n' >&2; return 1 ;;
        esac
        [ "${#expected_sha256}" -eq 64 ] || {
            printf '[runtime] ERROR: runtime SHA-256 must contain 64 hexadecimal characters\n' >&2
            return 1
        }
        printf '%s  %s\n' "$expected_sha256" "$target" | sha256sum -c --quiet - || return 1
    else
        printf '[runtime] WARNING: runtime SHA-256 was not supplied; package-internal hashes will still be checked\n' >&2
    fi
}
# The caller provides log(), warn(), die(), validate_bundle_archive(), and SUBSYSTEM.

validate_runtime_archive() {
    python3 - "$1" <<'PY'
import posixpath
import re
import sys
import tarfile

with tarfile.open(sys.argv[1], "r:gz") as archive:
    roots = set()
    names = set()
    for member in archive.getmembers():
        normalized = posixpath.normpath(member.name)
        if not member.name or member.name.startswith("/") or normalized == ".." or normalized.startswith("../"):
            raise SystemExit("unsafe runtime archive member: " + repr(member.name))
        roots.add(normalized.split("/", 1)[0])
        if member.isdev() or member.isfifo() or member.issym() or member.islnk():
            raise SystemExit("unsupported runtime archive member: " + repr(member.name))
        names.add(normalized)
    if len(roots) != 1:
        raise SystemExit("runtime archive must contain one root directory")
    root = next(iter(roots))
    if not re.fullmatch(r"pe31625g24dira-legacy-sdk-runtime-[A-Za-z0-9._+-]+", root):
        raise SystemExit("invalid runtime archive root: " + root)
    required = {
        root + "/RUNTIME-MANIFEST.json",
        root + "/RUNTIME-SHA256SUMS",
        root + "/runtime-rootfs.tar.gz",
    }
    missing = required - names
    if missing:
        raise SystemExit("runtime package is missing: " + ", ".join(sorted(missing)))
PY
}

validate_runtime_payload_archive() {
    python3 - "$1" <<'PY'
import posixpath
import sys
import tarfile

root = "pe31625g24dira-runtime-rootfs"
sdk = root + "/opt/silicom-legacy/usr/local/rrc"
with tarfile.open(sys.argv[1], "r:gz") as archive:
    names = set()
    for member in archive.getmembers():
        normalized = posixpath.normpath(member.name)
        if not member.name or member.name.startswith("/") or normalized == ".." or normalized.startswith("../"):
            raise SystemExit("unsafe runtime payload member: " + repr(member.name))
        if not (normalized == root or normalized.startswith(root + "/")):
            raise SystemExit("unexpected runtime payload root: " + repr(member.name))
        if member.isdev() or member.isfifo():
            raise SystemExit("special runtime payload member is not allowed: " + repr(member.name))
        if member.issym() or member.islnk():
            target = posixpath.normpath(posixpath.join(posixpath.dirname(normalized), member.linkname))
            if not target.startswith(root + "/"):
                raise SystemExit("unsafe runtime payload link: " + repr(member.name))
        names.add(normalized)
    if sdk not in names:
        raise SystemExit("runtime payload does not contain the legacy SDK")
PY
}

extract_runtime_package() {
    local archive=$1 work_dir=$2 platform_profile=$3
    local runtime_root runtime_payload runtime_version profile_bundle_root
    validate_runtime_archive "$archive" || return 1
    if [ -f "$archive.sha256" ]; then
        (cd "$(dirname "$archive")" && sha256sum -c --quiet "$(basename "$archive").sha256") || return 1
    elif [ "$archive" != "$KIT_ROOT"/runtime/* ]; then
        warn "runtime .sha256 sidecar is absent; internal file hashes will still be checked"
    fi
    mkdir -p "$work_dir"
    tar --no-same-owner --no-same-permissions -C "$work_dir" -xzf "$archive" || return 1
    runtime_root=$(find "$work_dir" -mindepth 1 -maxdepth 1 -type d \
        -name 'pe31625g24dira-legacy-sdk-runtime-*' -print -quit)
    [ -n "$runtime_root" ] || return 1
    (cd "$runtime_root" && sha256sum -c --quiet RUNTIME-SHA256SUMS) || return 1
    runtime_version=$(python3 - "$runtime_root/RUNTIME-MANIFEST.json" "$SUBSYSTEM" "$platform_profile" <<'PY'
import json
import re
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    data = json.load(handle)
if data.get("artifact_type") != "legacy-sdk-runtime" or data.get("format_version") != 2:
    raise SystemExit("unsupported runtime package format")
if data.get("product_model") != "PE31625G24DIRA":
    raise SystemExit("runtime package model mismatch")
version = str(data.get("package_version", ""))
if not re.fullmatch(r"2\.[0-9]+\.[0-9]+(?:[-+][A-Za-z0-9._-]+)?", version):
    raise SystemExit("unsupported runtime package version: " + version)
if sys.argv[3] not in data.get("compatible_platform_profiles", []):
    raise SystemExit("runtime package does not support platform profile " + sys.argv[3])
normalized = sys.argv[2].replace("0x", "")
if normalized not in data.get("compatible_pci_subsystems", []):
    raise SystemExit("runtime package does not support PCI subsystem " + normalized)
print(version)
PY
    ) || return 1
    runtime_payload="$runtime_root/runtime-rootfs.tar.gz"
    validate_runtime_payload_archive "$runtime_payload" || return 1
    mkdir -p "$work_dir/payload"
    tar --no-same-owner --no-same-permissions -C "$work_dir/payload" -xzf "$runtime_payload" || return 1
    profile_bundle_root="$work_dir/payload/pe31625g24dira-runtime-rootfs"
    RUNTIME_SDK_SOURCE="$profile_bundle_root/opt/silicom-legacy"
    [ -d "$RUNTIME_SDK_SOURCE/usr/local/rrc" ] || return 1
    RUNTIME_MANIFEST_SOURCE="$runtime_root/RUNTIME-MANIFEST.json"
    RUNTIME_PACKAGE_VERSION=$runtime_version
    RUNTIME_SOURCE_ID="legacy-sdk-runtime-$runtime_version"
}
