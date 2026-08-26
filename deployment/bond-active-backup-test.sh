#!/bin/bash
set -euo pipefail

ACTION=status
BOND=bond0
STATE_ROOT=/var/lib/pe31625g24dira-bond-test

usage() {
    cat <<'EOF'
Usage: sudo bash bond-active-backup-test.sh [status|apply|rollback] [--bond NAME]

Applies a temporary, reversible Linux active-backup bond failover profile.
It does not edit /etc/network/interfaces or any other persistent network file.

  status    Show the current runtime values (default)
  apply     Save current values, then reduce immediate failback/MAC churn
  rollback  Restore the values saved by the last apply
EOF
}

log() { printf '[bond-test] %s\n' "$*"; }
die() { printf '[bond-test] ERROR: %s\n' "$*" >&2; exit 1; }

if [ "$#" -gt 0 ]; then
    case "$1" in
        status|apply|rollback) ACTION=$1; shift ;;
        -h|--help) usage; exit 0 ;;
    esac
fi
while [ "$#" -gt 0 ]; do
    case "$1" in
        --bond) [ "$#" -ge 2 ] || die "--bond requires a value"; BOND=$2; shift 2 ;;
        -h|--help) usage; exit 0 ;;
        *) die "unknown argument: $1" ;;
    esac
done

case "$BOND" in
    *[!A-Za-z0-9_.:-]*|'') die "invalid bond interface name" ;;
esac
BONDING=/sys/class/net/$BOND/bonding
[ -d "$BONDING" ] || die "$BOND is not a Linux bonding interface"

numeric_value() {
    awk '{print $NF}' "$BONDING/$1"
}

show_status() {
    local name
    printf 'interface=%s\n' "$BOND"
    for name in mode active_slave primary primary_reselect miimon updelay num_grat_arp peer_notif_delay; do
        [ -r "$BONDING/$name" ] || continue
        printf '%s=%s\n' "$name" "$(tr -d '\n' < "$BONDING/$name")"
    done
}

[ "$(numeric_value mode)" = 1 ] || die "$BOND is not in active-backup mode"

STATE_DIR=$STATE_ROOT/$BOND
STATE_FILE=$STATE_DIR/runtime-values

saved_value() {
    local key=$1 value
    value=$(sed -n "s/^${key}=//p" "$STATE_FILE")
    [ -n "$value" ] || die "backup is missing $key"
    printf '%s\n' "$value"
}

write_value() {
    local name=$1 value=$2 actual
    if ! printf '%s\n' "$value" > "$BONDING/$name"; then
        log "could not write $name=$value"
        return 1
    fi
    actual=$(numeric_value "$name")
    if [ "$actual" != "$value" ]; then
        log "$name verification failed: requested $value, got $actual"
        return 1
    fi
}

restore_values() {
    write_value primary_reselect "$(saved_value primary_reselect)"
    write_value updelay "$(saved_value updelay)"
    write_value peer_notif_delay "$(saved_value peer_notif_delay)"
    write_value num_grat_arp "$(saved_value num_grat_arp)"
}

case "$ACTION" in
    status)
        show_status
        [ ! -f "$STATE_FILE" ] || printf 'rollback_available=yes\n'
        ;;
    apply)
        [ "$(id -u)" -eq 0 ] || die "apply must run as root"
        [ ! -e "$STATE_FILE" ] || die "a test profile is already active; rollback it first"
        MIIMON=$(numeric_value miimon)
        [ "$MIIMON" -gt 0 ] || die "miimon must be enabled before using this test profile"
        UPDELAY=$(( ((1000 + MIIMON - 1) / MIIMON) * MIIMON ))
        PEER_DELAY=$((2 * MIIMON))
        umask 077
        install -d -m 700 "$STATE_DIR"
        {
            printf 'primary_reselect=%s\n' "$(numeric_value primary_reselect)"
            printf 'updelay=%s\n' "$(numeric_value updelay)"
            printf 'num_grat_arp=%s\n' "$(numeric_value num_grat_arp)"
            printf 'peer_notif_delay=%s\n' "$(numeric_value peer_notif_delay)"
        } > "$STATE_FILE"
        if ! {
            # Keep the backup path active after a failover instead of moving the
            # bond MAC back as soon as the preferred 10G link reports carrier.
            write_value primary_reselect 2
            # Debounce a recovered carrier and send repeated peer notifications.
            write_value updelay "$UPDELAY"
            write_value peer_notif_delay "$PEER_DELAY"
            write_value num_grat_arp 5
        }; then
            restore_values || true
            die "profile apply failed; rollback was attempted"
        fi
        log "temporary profile applied to $BOND"
        log "10G will not automatically take over again until the active backup fails or the bond is changed manually"
        show_status
        ;;
    rollback)
        [ "$(id -u)" -eq 0 ] || die "rollback must run as root"
        [ -f "$STATE_FILE" ] || die "no saved runtime profile for $BOND"
        restore_values
        ARCHIVE=$STATE_DIR/runtime-values.rolled-back-$(date +%Y%m%d-%H%M%S)
        mv -- "$STATE_FILE" "$ARCHIVE"
        log "original runtime values restored; audit copy: $ARCHIVE"
        show_status
        ;;
esac
