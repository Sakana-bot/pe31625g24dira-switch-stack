#!/usr/bin/env python3
"""Build the identity-free legacy runtime directly from an original disk image."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import stat
import tarfile
from pathlib import Path

from dissect.extfs import ExtFS
from dissect.volume.disk import Disk

PROFILE = "sil001-hw4-b0"
SKIP_RRC = {
    "/usr/local/rrc/fm_platform_attributes.cfg",
    "/usr/local/rrc/fm_platform_attributes-A0.cfg",
    "/usr/local/rrc/fm_platform_attributes_rev_1.cfg",
}


def walk(entry, source_path: str):
    yield source_path, entry
    if stat.S_ISDIR(entry.inode.i_mode):
        for child in entry.iterdir():
            if child.filename in (".", ".."):
                continue
            child_path = source_path.rstrip("/") + "/" + child.filename
            yield from walk(child, child_path)


def add_entry(tf: tarfile.TarFile, source_path: str, entry) -> None:
    if source_path in SKIP_RRC:
        return
    destination = "pe31625g24dira-runtime-rootfs/opt/silicom-legacy" + source_path
    mode = entry.inode.i_mode
    info = tarfile.TarInfo(destination)
    info.mode = stat.S_IMODE(mode)
    info.uid = int(entry.inode.i_uid)
    info.gid = int(entry.inode.i_gid)
    info.mtime = int(entry.mtime.timestamp())
    if stat.S_ISDIR(mode):
        info.type = tarfile.DIRTYPE
        tf.addfile(info)
    elif stat.S_ISLNK(mode):
        info.type = tarfile.SYMTYPE
        link = entry.link
        info.linkname = link.decode() if isinstance(link, bytes) else str(link)
        tf.addfile(info)
    elif stat.S_ISREG(mode):
        info.size = entry.size
        tf.addfile(info, entry.open())


def selected_paths(fs: ExtFS) -> list[str]:
    paths = [
        "/usr/local/rrc",
        "/etc/perl",
        "/usr/lib/x86_64-linux-gnu/perl",
        "/usr/lib/x86_64-linux-gnu/perl5",
        "/usr/lib/x86_64-linux-gnu/perl-base",
        "/usr/share/perl",
        "/usr/share/perl5",
        "/usr/bin/perl",
    ]
    for directory, prefix in (
        ("/usr/bin", "perl5.22"),
        ("/usr/lib/x86_64-linux-gnu", "libperl.so.5.22"),
    ):
        for child in fs.get(directory).iterdir():
            if child.filename.startswith(prefix):
                paths.append(directory + "/" + child.filename)
    return paths


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("image", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--version", required=True)
    parser.add_argument("--root-partition", type=int, default=2,
                        help="one-based GPT partition number (default: 2)")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)

    with args.image.open("rb") as image:
        disk = Disk(image)
        partition = next((p for p in disk.partitions if p.number == args.root_partition), None)
        if partition is None:
            raise SystemExit(f"partition {args.root_partition} not found")
        fs = ExtFS(partition.open())
        fs.get("/usr/local/rrc/perl/TestPoint")
        payload = io.BytesIO()
        with tarfile.open(fileobj=payload, mode="w:gz", compresslevel=9) as tf:
            seen: set[str] = set()
            for root in selected_paths(fs):
                try:
                    entry = fs.get(root)
                except Exception:
                    continue
                for source_path, child in walk(entry, root):
                    if source_path in seen:
                        continue
                    seen.add(source_path)
                    add_entry(tf, source_path, child)

    manifest = {
        "artifact_type": "legacy-sdk-runtime",
        "format_version": 2,
        "package_version": args.version,
        "product_model": "PE31625G24DIRA",
        "sdk_name": "Intel IES TestPoint",
        "sdk_version": "4.3",
        "architecture": "x86_64",
        "compatible_platform_profiles": [PROFILE],
        "compatible_pci_subsystems": ["1374:01d0"],
        "payload_layout": "identity-free-rootfs",
        "included_roots": ["/opt/silicom-legacy"],
        "contains_device_identity": False,
        "source_kind": "original-system-disk-image",
        "source_partition": args.root_partition,
    }
    manifest_bytes = (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode()
    payload_bytes = payload.getvalue()
    hashes = (
        f"{hashlib.sha256(manifest_bytes).hexdigest()}  RUNTIME-MANIFEST.json\n"
        f"{hashlib.sha256(payload_bytes).hexdigest()}  runtime-rootfs.tar.gz\n"
    ).encode()
    root = f"pe31625g24dira-legacy-sdk-runtime-{args.version}"
    archive = args.output / f"{root}.tar.gz"
    with tarfile.open(archive, "w:gz", compresslevel=9) as tf:
        for name, data in (
            ("RUNTIME-MANIFEST.json", manifest_bytes),
            ("runtime-rootfs.tar.gz", payload_bytes),
            ("RUNTIME-SHA256SUMS", hashes),
        ):
            info = tarfile.TarInfo(f"{root}/{name}")
            info.mode = 0o644
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    archive.with_suffix(archive.suffix + ".sha256").write_text(
        f"{digest}  {archive.name}\n", encoding="utf-8"
    )
    print(archive)


if __name__ == "__main__":
    main()
