#!/usr/bin/env python3
"""Read-only local preview server used by UI tests; never deployed to the board."""

import importlib.util
import json
import os
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
SPEC = importlib.util.spec_from_file_location("fmweb", os.path.join(HERE, "app.py"))
APP = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(APP)


def preview_statistics(seed):
    lengths = {label: seed * (index + 1) for index, label in enumerate(APP.RX_LENGTH_LABELS)}
    actions = dict.fromkeys(APP.RX_DROP_BANK4, 0)
    drops = dict.fromkeys(APP.RX_DROP_BANK5, 0)
    actions["stp"] = seed
    actions["vlan_tag"] = seed * 2
    return {
        "rx": {
            "frames": seed * 10000,
            "good_bytes": seed * 1250000,
            "bad_bytes": seed * 64,
            "unicast": seed * 9600,
            "multicast": seed * 300,
            "broadcast": seed * 100,
            "pause": 0,
            "pfc_pause": 0,
            "framing_errors": seed,
            "fcs_errors": seed * 3,
            "length": lengths,
            "actions": actions,
            "drops": drops,
            "mac": {
                "oversize": 0,
                "jabber": 0,
                "undersize": 0,
                "runt": seed,
                "overrun": 0,
                "underrun": 0,
                "code_errors": seed * 7,
                "tx_frame_errors": 0,
                "link_events": {"up": 1, "local_fault": seed, "remote_fault": 0},
            },
        },
        "tx": {
            "frames": seed * 9200,
            "good_bytes": seed * 1100000,
            "bad_bytes": 0,
            "unicast": seed * 9000,
            "multicast": seed * 150,
            "broadcast": seed * 50,
            "bad_fcs": 0,
            "timeout_drops": 0,
            "error_drops": 0,
            "ecc_drops": 0,
            "loopback_drops": 0,
            "ttl_drops": 0,
            "pause": 0,
            "pfc_pause": 0,
            "length": lengths,
        },
    }


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/api/state":
            _, parsed = APP.parse_platform(
                os.path.join(HERE, "..", "switch_service", "fm_platform_attributes_pe31625g24dira.cfg")
            )
            admin = APP.port_admin_payload(parsed, APP.default_port_config(parsed))
            l2 = APP.default_l2_config([item["key"] for item in admin["endpoints"]])
            l2["endpoints"] = [dict(item, name="") for item in admin["endpoints"]]
            l2["neighbors"] = {"state": "ok", "error": None, "neighbors": []}
            payload = {
                "version": APP.APP_VERSION,
                "service": "active",
                "service_health": {"status": "healthy", "service": "active", "uio_ready": True, "testpoint_ready": True},
                "system_information": {
                    "hostname": "pe31625-preview", "os": "Debian GNU/Linux 13 (trixie)", "kernel": "6.12.0-preview", "cpu_model": "Intel(R) Atom(TM) CPU E3826 @ 1.46GHz", "bios": "preview",
                    "storage": {"total": 32000000000, "used": 4000000000, "free": 28000000000, "usage_percent": 12.5},
                    "components": {"manager": APP.APP_VERSION, "ies_sdk": "4.3.3_0471_00339702_silicom", "testpoint": "4.3", "fm10k_uio": {"version": "6.12.101-ies2", "loaded": True}},
                },
                "groups": parsed["groups"],
                "ports": parsed["ports"],
                "endpoints": admin["endpoints"],
                "mpo_admin": admin["mpo"],
                "vlans": APP.default_vlan_config(parsed)["vlans"],
                "l2": l2,
                "fan_control": APP.fan_config_payload({}),
                "budget": {
                    "external": parsed["external"],
                    "total": parsed["external"] + APP.INTERNAL_BUDGET,
                    "internal": APP.INTERNAL_BUDGET,
                    "guaranteed": APP.GUARANTEED_BUDGET,
                    "hard": APP.HARD_BUDGET,
                },
                "csrf": "preview",
                "username": "admin",
                "system_settings": {
                    "hostname": "pe31625-preview",
                },
            }
            return self.send_bytes(
                json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                "application/json; charset=utf-8",
            )
        if self.path.startswith("/api/logs"):
            source = "kernel" if "source=kernel" in self.path else "switch" if "source=switch" in self.path else "system"
            content = "[    0.000000] Linux version 6.12.0-amd64\n[    0.000000] Command line: BOOT_IMAGE=/vmlinuz root=UUID=preview ro quiet\n[    0.041726] smpboot: CPU0: Intel Atom E3826" if source == "kernel" else "2026-08-24 20:31:10 systemd[1]: Started PE31625G24DIRA Switch Manager.\n2026-08-24 20:31:13 switch-manager[612]: WebUI ready"
            payload = {"source": source, "sampled": int(time.time()), "line_count": len(content.splitlines()), "content": content}
            return self.send_bytes(json.dumps(payload).encode("utf-8"), "application/json; charset=utf-8")
        if self.path == "/api/health":
            payload = {"version": APP.APP_VERSION, "status": "healthy", "service": "active", "uio_ready": True, "testpoint_ready": True}
            return self.send_bytes(json.dumps(payload).encode("utf-8"), "application/json; charset=utf-8")
        if self.path == "/api/telemetry":
            _, parsed = APP.parse_platform(
                os.path.join(HERE, "..", "switch_service", "fm_platform_attributes_pe31625g24dira.cfg")
            )
            ports = {}
            for index, port in enumerate(parsed["ports"]):
                physical = APP.GROUP_BY_KEY[port["group"]]
                ports[str(port["logical"])] = {
                    "oper": "UP" if index == 0 else "DOWN",
                    "fault": "none" if index == 0 else "local",
                    "high_ber": False,
                    "rx_link_up": index == 0,
                    "epl": physical["epl"],
                    "lane": port["lane"] or 0,
                    "pcs": 6,
                    "raw": "0x00000000",
                    "rx_bps": 12500000 if index == 0 else 0,
                    "tx_bps": 8400000 if index == 0 else 0,
                    "statistics": preview_statistics(index + 1),
                }
            payload = {
                "hostname": "pe31625-preview",
                "kernel": "4.13.0-preview",
                "hardware_identity": {
                    "vendor": "Silicom",
                    "model": "PE31625G24DIRA-MPS",
                    "display_model": "Silicom PE31625G24DIRA-MPS",
                    "vpd_version": "0490",
                    "serial": "S916260490015",
                    "hardware_family": "Silicom B0",
                    "hw_version": 4,
                    "platform": "sil001",
                    "pci_address": "0000:01:00.0",
                },
                "uptime_seconds": 93240,
                "cpu": {
                    "model": "Intel(R) Atom(TM) CPU E3826 @ 1.46GHz",
                    "cores": 2,
                    "usage_percent": 12.6,
                    "load": [0.12, 0.18, 0.21],
                },
                "memory": {
                    "total": 2147483648,
                    "used": 536870912,
                    "usage_percent": 25.0,
                },
                "temperatures": [
                    dict(
                        APP.describe_linux_temperature("coretemp", "Core 0"),
                        chip="coretemp",
                        label="Core 0",
                        celsius=33.0,
                    ),
                    dict(
                        APP.describe_linux_temperature("coretemp", "Core 2"),
                        chip="coretemp",
                        label="Core 2",
                        celsius=32.0,
                    ),
                    dict(
                        APP.describe_linux_temperature("soc_dts0", "temp1"),
                        chip="soc_dts0",
                        label="temp1",
                        celsius=34.0,
                    ),
                    dict(
                        APP.describe_linux_temperature("soc_dts1", "temp1"),
                        chip="soc_dts1",
                        label="temp1",
                        celsius=32.0,
                    ),
                    dict(
                        APP.describe_linux_temperature("acpitz", "temp1"),
                        chip="acpitz",
                        label="temp1",
                        celsius=33.0,
                    ),
                ],
                "fans": {
                    "state": "ready",
                    "source": "lm96163-tach",
                    "fans": [
                        {
                            "label": "System Fan",
                            "chip": "LM96163",
                            "rpm": 1402,
                            "tach_count": 3852,
                            "signal": True,
                        }
                    ],
                },
                "management": {
                    "connected": 1,
                    "total": 2,
                    "primary": "enp2s0",
                    "interfaces": [
                        {
                            "interface": "enp2s0",
                            "state": "up",
                            "carrier": True,
                            "speed_mbps": 1000,
                            "duplex": "full",
                            "mac": "00:e0:ed:7e:9e:da",
                            "mtu": 1500,
                            "ipv4": ["192.168.123.197/24"],
                            "ipv6": [],
                            "gateway": "192.168.123.1",
                            "rx_bps": 12000,
                            "tx_bps": 4000,
                            "statistics": {
                                "rx_bytes": 1200000,
                                "tx_bytes": 800000,
                                "rx_errors": 0,
                                "tx_errors": 0,
                            },
                        },
                        {
                            "interface": "enp3s0",
                            "state": "down",
                            "carrier": False,
                            "speed_mbps": 0,
                            "duplex": "unknown",
                            "mac": "00:e0:ed:7e:9e:db",
                            "mtu": 1500,
                            "ipv4": ["192.168.255.2/24"],
                            "ipv6": [],
                            "gateway": None,
                            "rx_bps": 0,
                            "tx_bps": 0,
                            "statistics": {
                                "rx_bytes": 0,
                                "tx_bytes": 0,
                                "rx_errors": 0,
                                "tx_errors": 0,
                            },
                        },
                    ],
                },
                "switch_sensors": APP.parse_switch_sensors(
                    "\n".join(
                        ["MAIN TEMP SENSOR : 35.5 C"]
                        + [
                            f"REMOTE TEMP SENSOR {index} : {35.0 + index / 10.0:.1f} C"
                            for index in range(8)
                        ]
                        + [
                            "VOLTAGE SENSOR VDD : 0.850 V",
                            "VOLTAGE SENSOR CORE_VDD_VIN : 0.850 V",
                        ]
                        + [
                            f"VOLTAGE SENSOR A2D_VIN[{index}] : {0.944 + index / 1000.0:.3f} V"
                            for index in range(6)
                        ]
                    )
                ),
                "port_status": {
                    "state": "ready",
                    "source": "uio-rmon",
                    "sampled": int(time.time()),
                    "ports": ports,
                    "traffic": {
                        "rx_bps": 12500000,
                        "tx_bps": 8400000,
                        "rx_bytes": 987654321,
                        "tx_bytes": 876543210,
                        "rx_frames": 1234567,
                        "tx_frames": 1200000,
                        "rx_errors": 12,
                        "tx_discards": 3,
                        "port_count": len(ports),
                    },
                },
            }
            return self.send_bytes(
                json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                "application/json; charset=utf-8",
            )
        page_routes = (
            "/",
            "/overview",
            "/sensors",
            "/system",
            "/cooling",
            "/ports",
            "/statistics",
            "/vlans",
            "/network",
            "/settings",
            "/backup",
            "/logs",
        )
        mapping = dict.fromkeys(page_routes, ("index.html", "text/html; charset=utf-8"))
        mapping.update(
            {
                "/login": ("login.html", "text/html; charset=utf-8"),
                "/setup": ("setup.html", "text/html; charset=utf-8"),
                "/app.js": ("app.js", "application/javascript; charset=utf-8"),
                "/api-client.js": ("api-client.js", "application/javascript; charset=utf-8"),
                "/controls.js": ("controls.js", "application/javascript; charset=utf-8"),
                "/login.js": ("login.js", "application/javascript; charset=utf-8"),
                "/setup.js": ("setup.js", "application/javascript; charset=utf-8"),
                "/theme.js": ("theme.js", "application/javascript; charset=utf-8"),
                "/style.css": ("style.css", "text/css; charset=utf-8"),
            }
        )
        if self.path not in mapping:
            self.send_error(404)
            return
        name, content_type = mapping[self.path]
        with open(os.path.join(HERE, "static", name), "rb") as handle:
            self.send_bytes(handle.read(), content_type)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(length).decode("utf-8")) if length else {}
        if self.path == "/api/account":
            username = body.get("username", "admin")
            payload = {
                "ok": True,
                "username": username,
                "reauthenticate": False,
                "message": "演示模式未保存账户设置",
            }
            return self.send_bytes(
                json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                "application/json; charset=utf-8",
            )
        if self.path == "/api/system/settings":
            payload = {"hostname": body.get("hostname", "pe31625-preview")}
            return self.send_bytes(json.dumps(payload).encode("utf-8"), "application/json; charset=utf-8")
        if self.path in ("/api/logout", "/api/login"):
            return self.send_bytes(b'{"ok":true}', "application/json; charset=utf-8")
        self.send_error(404)

    def send_bytes(self, data, content_type):
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt, *args):
        pass


if __name__ == "__main__":
    HTTPServer(("0.0.0.0", 18741), Handler).serve_forever()
