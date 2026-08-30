"""Slow optical-engine identity and RX-power sampling script.

Temperature sampling intentionally lives in ``sensors.tp`` so the regular
sensor refresh and the identity cache never perform the same I2C read twice.
"""


OPTICS_DIAGNOSTIC_COMPLETE_MARKER = (
    "PE31625G24DIRA_SWITCH_MANAGER_OPTICS_DIAGNOSTIC_DONE"
)


def optics_diagnostic_script():
    lines = [
        "# expert",
        "my $pe_chip = $self->{FT}->{CHIP};",
        "my $pe_mux_saved = [(0) x 1];",
        "my $pe_mux_read_status = $pe_chip->fmI2cWriteRead(0, 0x58, $pe_mux_saved, 0, 1);",
        "for my $pe_item ([1, 0x01], [2, 0x02]) {",
        "    my ($pe_mpo, $pe_mux) = @$pe_item;",
        "    my $pe_mux_data = [$pe_mux];",
        "    my $pe_select_status = $pe_chip->fmI2cWriteRead(0, 0x58, $pe_mux_data, 1, 0);",
        "    my $pe_page = [0x7f];",
        "    my $pe_page_status = $pe_chip->fmI2cWriteRead(0, 0x40, $pe_page, 1, 1);",
        "    my $pe_saved_page = $pe_page->[0];",
        "    my $pe_page_one = [0x7f, 0x01];",
        "    $pe_page_status = $pe_chip->fmI2cWriteRead(0, 0x40, $pe_page_one, 2, 0) if $pe_page_status == 0;",
        "    my $pe_data = [0xce, (0) x 24];",
        "    my $pe_read_status = $pe_page_status == 0 ? $pe_chip->fmI2cWriteRead(0, 0x40, $pe_data, 1, 24) : $pe_page_status;",
        "    my $pe_restore_page = [0x7f, $pe_saved_page];",
        "    my $pe_restore_page_status = $pe_chip->fmI2cWriteRead(0, 0x40, $pe_restore_page, 2, 0);",
        '    printf("PE31625G24DIRA_OPTICS mpo=%d mux=%d select_status=%d page_status=%d read_status=%d restore_page_status=%d raw=", $pe_mpo, $pe_mux, $pe_select_status, $pe_page_status, $pe_read_status, $pe_restore_page_status);',
        '    printf("%02X", $pe_data->[$_]) for (0 .. 23);',
        '    print "\\n";',
        "    my $pe_identity_page = [0x7f];",
        "    my $pe_identity_page_status = $pe_chip->fmI2cWriteRead(0, 0x50, $pe_identity_page, 1, 1);",
        "    my $pe_identity_saved_page = $pe_identity_page->[0];",
        "    my $pe_identity_page_zero = [0x7f, 0x00];",
        "    $pe_identity_page_status = $pe_chip->fmI2cWriteRead(0, 0x50, $pe_identity_page_zero, 2, 0) if $pe_identity_page_status == 0;",
        "    select(undef, undef, undef, 0.03);",
        "    my $pe_identity_hex = '';",
        "    my $pe_identity_read_status = 0;",
        "    for (my $pe_offset = 0x80; $pe_offset < 0x100; $pe_offset += 12) {",
        "        my $pe_count = 0x100 - $pe_offset;",
        "        $pe_count = 12 if $pe_count > 12;",
        "        my $pe_identity_data = [$pe_offset, (0) x ($pe_count - 1)];",
        "        my $pe_chunk_status = $pe_chip->fmI2cWriteRead(0, 0x50, $pe_identity_data, 1, $pe_count);",
        "        $pe_identity_read_status = $pe_chunk_status if $pe_chunk_status != 0;",
        "        $pe_identity_hex .= join('', map { sprintf('%02X', $_) } @{$pe_identity_data});",
        "    }",
        "    my $pe_identity_restore = [0x7f, $pe_identity_saved_page];",
        "    my $pe_identity_restore_status = $pe_chip->fmI2cWriteRead(0, 0x50, $pe_identity_restore, 2, 0);",
        '    printf("PE31625G24DIRA_OPTICS_IDENTITY mpo=%d page_status=%d read_status=%d restore_page_status=%d raw=%s\\n", $pe_mpo, $pe_identity_page_status, $pe_identity_read_status, $pe_identity_restore_status, $pe_identity_hex);',
        "}",
        "if ($pe_mux_read_status != 0) { $pe_mux_saved->[0] = 0x01; }",
        "$pe_chip->fmI2cWriteRead(0, 0x58, $pe_mux_saved, 1, 0);",
        f'print "{OPTICS_DIAGNOSTIC_COMPLETE_MARKER}\\n";',
    ]
    return "\n".join(lines) + "\n"
