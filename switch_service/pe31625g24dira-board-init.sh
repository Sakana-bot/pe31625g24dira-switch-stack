#!/bin/bash
set -euo pipefail

modprobe i2c_i801
modprobe i2c-dev

i801_bus=""
for name_file in /sys/class/i2c-dev/i2c-*/name; do
    [[ -e "$name_file" ]] || continue
    if grep -q '^SMBus I801 adapter' "$name_file"; then
        i801_bus=${name_file%/name}
        i801_bus=${i801_bus##*/i2c-}
        break
    fi
done

if [[ -z "$i801_bus" ]]; then
    echo "PE31625G24DIRA board power controller: I801 adapter not found" >&2
    exit 1
fi

i2cset -y "$i801_bus" 0x36 0x00 0x3c
