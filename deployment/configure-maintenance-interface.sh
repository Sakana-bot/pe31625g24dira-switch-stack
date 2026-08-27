#!/bin/bash
set -euo pipefail

INTERFACE=${PE31625G24DIRA_MAINTENANCE_INTERFACE:-enp3s0}
ADDRESS=${PE31625G24DIRA_MAINTENANCE_ADDRESS:-192.168.255.2/24}
CONNECTION=pe31625g24dira-maintenance

log() { printf '[network] %s\n' "$*"; }
die() { printf '[network] ERROR: %s\n' "$*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || die "run as root"
[ -d "/sys/class/net/$INTERFACE" ] || die "maintenance interface not found: $INTERFACE"

if command -v nmcli >/dev/null 2>&1 && systemctl is-active --quiet NetworkManager; then
    log "using the installed NetworkManager backend; existing network configuration is preserved"
    if nmcli -t -f NAME connection show | grep -Fxq "$CONNECTION"; then
        nmcli connection modify "$CONNECTION" \
            connection.interface-name "$INTERFACE" \
            ipv4.method manual ipv4.addresses "$ADDRESS" ipv4.never-default yes \
            ipv6.method disabled
    else
        nmcli connection add type ethernet ifname "$INTERFACE" con-name "$CONNECTION" \
            ipv4.method manual ipv4.addresses "$ADDRESS" ipv4.never-default yes \
            ipv6.method disabled
    fi
    nmcli connection up "$CONNECTION"
elif command -v netplan >/dev/null 2>&1 && [ -d /etc/netplan ]; then
    log "using the installed Netplan backend; existing network configuration is preserved"
    install -d -m 755 /etc/netplan
    cat > /etc/netplan/99-pe31625g24dira-maintenance.yaml <<EOF
network:
  version: 2
  ethernets:
    $INTERFACE:
      addresses:
        - $ADDRESS
      dhcp4: false
      dhcp6: false
      optional: true
      ignore-carrier: true
EOF
    chmod 600 /etc/netplan/99-pe31625g24dira-maintenance.yaml
    netplan generate
    netplan apply
elif [ -r /etc/network/interfaces ] && grep -Eq '^[[:space:]]*(source|source-directory)[[:space:]]+/etc/network/interfaces\.d(/\*|[[:space:]]|$)' /etc/network/interfaces; then
    log "using the installed ifupdown backend; existing network configuration is preserved"
    install -d -m 755 /etc/network/interfaces.d
    cat > /etc/network/interfaces.d/pe31625g24dira-maintenance <<EOF
allow-hotplug $INTERFACE
iface $INTERFACE inet static
    address ${ADDRESS%/*}
    netmask 255.255.255.0
EOF
    if command -v ifdown >/dev/null 2>&1; then
        ifdown "$INTERFACE" >/dev/null 2>&1 || true
    fi
    ifup "$INTERFACE"
elif command -v networkctl >/dev/null 2>&1 && systemctl is-active --quiet systemd-networkd; then
    log "using the installed systemd-networkd backend; existing network configuration is preserved"
    install -d -m 755 /etc/systemd/network
    cat > /etc/systemd/network/80-pe31625g24dira-maintenance.network <<EOF
[Match]
Name=$INTERFACE

[Network]
Address=$ADDRESS
DHCP=no
IPv6AcceptRA=no
EOF
    networkctl reload
    networkctl reconfigure "$INTERFACE"
else
    die "unsupported active network backend; configure $INTERFACE as $ADDRESS manually"
fi

ip -4 address show dev "$INTERFACE" | grep -Fq "inet ${ADDRESS%/*}/" || \
    die "$INTERFACE did not acquire $ADDRESS"
log "$INTERFACE configured as $ADDRESS"
