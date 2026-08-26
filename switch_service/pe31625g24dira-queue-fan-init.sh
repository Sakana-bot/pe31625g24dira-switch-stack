#!/bin/bash
set -euo pipefail

control_fifo=/run/pe31625g24dira-testpoint/control
switch_ready=/run/pe31625g24dira-testpoint/switch-ready
fan_ready=/run/pe31625g24dira-testpoint/fan-ready

for _ in $(seq 1 90); do
    if [[ -p "$control_fifo" && -f "$switch_ready" ]]; then
        break
    fi
    sleep 1
done

if [[ ! -p "$control_fifo" || ! -f "$switch_ready" ]]; then
    echo "TestPoint did not report switch-ready within 90 seconds" >&2
    exit 1
fi

rm -f "$fan_ready"
printf 'load /etc/pe31625g24dira/pe31625g24dira-fan-init.tp\n' >"$control_fifo"

for _ in $(seq 1 30); do
    [[ -f "$fan_ready" ]] && exit 0
    sleep 1
done

echo "LM96163 fan initialization did not report fan-ready within 30 seconds" >&2
exit 1
