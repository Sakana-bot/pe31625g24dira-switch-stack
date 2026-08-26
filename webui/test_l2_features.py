import socket
import struct
import unittest

from webui.l2_features import normalize_config, parse_lldp_frame


def tlv(kind, value):
    return struct.pack("!H", (kind << 9) | len(value)) + value


class L2FeatureTests(unittest.TestCase):
    def test_configuration_is_bounded_and_topology_aware(self):
        value = normalize_config(
            {
                "labels": {"epl0.lane0": " uplink ", "removed": "x"},
                "storm_control": {"enabled": True, "rate_kbps": 999999},
                "mirror": {
                    "enabled": True,
                    "source": "epl0.lane0",
                    "destination": "epl0.lane1",
                    "direction": "rx",
                },
            },
            ["epl0.lane0", "epl0.lane1"],
        )
        self.assertEqual(value["labels"], {"epl0.lane0": "uplink", "epl0.lane1": ""})
        self.assertEqual(value["storm_control"]["rate_kbps"], 122500)
        self.assertTrue(value["mirror"]["enabled"])

    def test_lldp_parser_reads_identity_and_management_address(self):
        payload = b"".join(
            (
                tlv(1, b"\x07switch-a"),
                tlv(2, b"\x05Ethernet1"),
                tlv(3, struct.pack("!H", 120)),
                tlv(5, b"core-switch"),
                tlv(8, b"\x05\x01" + socket.inet_aton("192.0.2.1") + b"\x02\x00\x00\x00\x01\x00"),
                tlv(0, b""),
            )
        )
        frame = (
            b"\x01\x80\xc2\x00\x00\x0e"
            + b"\x00\x11\x22\x33\x44\x55"
            + struct.pack("!H", 0x88CC)
            + payload
        )
        value = parse_lldp_frame(frame)
        self.assertEqual(value["source_mac"], "00:11:22:33:44:55")
        self.assertEqual(value["system_name"], "core-switch")
        self.assertEqual(value["management_address"], "192.0.2.1")


if __name__ == "__main__":
    unittest.main()
