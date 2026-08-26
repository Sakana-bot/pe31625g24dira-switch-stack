#!/bin/bash
set -euo pipefail

legacy_root=/opt/silicom-legacy
export PATH="$legacy_root/usr/bin:$PATH"
export PERL5LIB="$legacy_root/usr/lib/x86_64-linux-gnu/perl/5.22.1:$legacy_root/usr/lib/x86_64-linux-gnu/perl/5.22:$legacy_root/usr/lib/x86_64-linux-gnu/perl5/5.22:$legacy_root/usr/lib/x86_64-linux-gnu/perl-base:$legacy_root/usr/share/perl/5.22.1:$legacy_root/usr/share/perl5${PERL5LIB:+:$PERL5LIB}"
export LD_LIBRARY_PATH="$legacy_root/usr/lib/x86_64-linux-gnu:/usr/local/rrc/lib:/usr/local/rrc/perl-lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"

control_fifo=/run/pe31625g24dira-testpoint/control
rm -f "$control_fifo" \
    /run/pe31625g24dira-testpoint/switch-ready \
    /run/pe31625g24dira-testpoint/fan-ready
mkfifo -m 600 "$control_fifo"

# Keep a writer open so TestPoint's readline loop does not receive EOF.
exec 3<>"$control_fifo"

# TestPoint's readline layer requires a real terminal even when commands are
# supplied through --load. util-linux script(1) provides a private PTY while
# the FIFO keeps stdin open for the lifetime of the ASIC owner process.
exec /usr/bin/script -q -e -c \
    "/usr/local/rrc/perl/TestPoint --skip-startup --load=/etc/pe31625g24dira/pe31625g24dira-switch.tp -i" \
    /dev/null <"$control_fifo"
