"""Configuration and passive LLDP support for home-oriented L2 features."""

import socket
import struct
import threading
import time


DEFAULT_STORM_RATE_KBPS = 100000
DEFAULT_STORM_BURST_BYTES = 65536
DEFAULT_LOOP_PPS = 20000


def default_config(endpoint_keys):
    return {
        "version": 1,
        "labels": {key: "" for key in sorted(endpoint_keys)},
        "loop_protection": {
            "enabled": False,
            "broadcast_pps": DEFAULT_LOOP_PPS,
            "blocked": {},
        },
        "storm_control": {
            "enabled": False,
            "rate_kbps": DEFAULT_STORM_RATE_KBPS,
            "burst_bytes": DEFAULT_STORM_BURST_BYTES,
        },
        "mirror": {
            "enabled": False,
            "source": None,
            "destination": None,
            "direction": "both",
        },
    }


def normalize_config(value, endpoint_keys):
    keys = set(endpoint_keys)
    defaults = default_config(keys)
    value = value if isinstance(value, dict) else {}

    raw_labels = value.get("labels", {})
    labels = {}
    for key in sorted(keys):
        label = str(raw_labels.get(key, "")).strip() if isinstance(raw_labels, dict) else ""
        labels[key] = label[:32]

    raw_loop = value.get("loop_protection", {})
    raw_loop = raw_loop if isinstance(raw_loop, dict) else {}
    loop = {
        "enabled": bool(raw_loop.get("enabled", defaults["loop_protection"]["enabled"])),
        "broadcast_pps": max(1000, min(1000000, int(raw_loop.get("broadcast_pps", DEFAULT_LOOP_PPS)))),
        "blocked": {},
    }
    raw_blocked = raw_loop.get("blocked", {})
    if isinstance(raw_blocked, dict):
        loop["blocked"] = {
            key: int(raw_blocked[key])
            for key in sorted(keys & set(raw_blocked))
            if isinstance(raw_blocked[key], (int, float))
        }

    raw_storm = value.get("storm_control", {})
    raw_storm = raw_storm if isinstance(raw_storm, dict) else {}
    storm = {
        "enabled": bool(raw_storm.get("enabled", defaults["storm_control"]["enabled"])),
        "rate_kbps": max(10000, min(122500, int(raw_storm.get("rate_kbps", DEFAULT_STORM_RATE_KBPS)))),
        "burst_bytes": max(1024, min(1048576, int(raw_storm.get("burst_bytes", DEFAULT_STORM_BURST_BYTES)))),
    }

    raw_mirror = value.get("mirror", {})
    source = raw_mirror.get("source") if isinstance(raw_mirror, dict) else None
    destination = raw_mirror.get("destination") if isinstance(raw_mirror, dict) else None
    direction = raw_mirror.get("direction", "both") if isinstance(raw_mirror, dict) else "both"
    mirror = {
        "enabled": bool(raw_mirror.get("enabled", False)) if isinstance(raw_mirror, dict) else False,
        "source": source if source in keys else None,
        "destination": destination if destination in keys else None,
        "direction": direction if direction in {"rx", "tx", "both"} else "both",
    }
    if mirror["source"] == mirror["destination"]:
        mirror["enabled"] = False

    return {
        "version": 1,
        "labels": labels,
        "loop_protection": loop,
        "storm_control": storm,
        "mirror": mirror,
    }


def parse_lldp_frame(frame):
    if len(frame) < 14:
        return None
    destination = frame[:6]
    source = frame[6:12]
    ether_type = struct.unpack("!H", frame[12:14])[0]
    offset = 14
    if ether_type == 0x8100 and len(frame) >= 18:
        ether_type = struct.unpack("!H", frame[16:18])[0]
        offset = 18
    if ether_type != 0x88CC or destination != b"\x01\x80\xc2\x00\x00\x0e":
        return None
    value = {
        "source_mac": ":".join(f"{byte:02x}" for byte in source),
        "chassis": None,
        "port": None,
        "system_name": None,
        "system_description": None,
        "management_address": None,
        "ttl": 120,
    }
    while offset + 2 <= len(frame):
        header = struct.unpack("!H", frame[offset : offset + 2])[0]
        offset += 2
        tlv_type = header >> 9
        length = header & 0x1FF
        if offset + length > len(frame):
            break
        payload = frame[offset : offset + length]
        offset += length
        if tlv_type == 0:
            break
        if tlv_type in {1, 2} and payload:
            text = payload[1:].decode("utf-8", "replace").strip()
            value["chassis" if tlv_type == 1 else "port"] = text
        elif tlv_type == 3 and len(payload) == 2:
            value["ttl"] = struct.unpack("!H", payload)[0]
        elif tlv_type == 5:
            value["system_name"] = payload.decode("utf-8", "replace").strip()
        elif tlv_type == 6:
            value["system_description"] = payload.decode("utf-8", "replace").strip()
        elif tlv_type == 8 and len(payload) >= 2:
            size = payload[0]
            if size >= 2 and len(payload) >= size + 1:
                family = payload[1]
                address = payload[2 : 1 + size]
                if family == 1 and len(address) == 4:
                    value["management_address"] = socket.inet_ntop(socket.AF_INET, address)
                elif family == 2 and len(address) == 16:
                    value["management_address"] = socket.inet_ntop(socket.AF_INET6, address)
    return value


class LldpMonitor:
    """Passively collect LLDP frames trapped by FM10840 to the CPU port."""

    def __init__(self, interface="enp1s0"):
        self.interface = interface
        self.lock = threading.Lock()
        self.neighbors = {}
        self.error = None

    def run(self):
        try:
            sock = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.htons(0x88CC))
            sock.bind((self.interface, 0))
            sock.settimeout(1.0)
        except Exception as exc:
            self.error = str(exc)
            return
        while True:
            try:
                frame = sock.recv(65535)
            except socket.timeout:
                self._expire()
                continue
            except Exception as exc:
                self.error = str(exc)
                time.sleep(1)
                continue
            value = parse_lldp_frame(frame)
            if not value:
                continue
            now = int(time.time())
            value["last_seen"] = now
            value["expires"] = now + max(30, value.get("ttl") or 120)
            with self.lock:
                self.neighbors[value["source_mac"]] = value
                self.error = None

    def _expire(self):
        now = int(time.time())
        with self.lock:
            self.neighbors = {
                key: value for key, value in self.neighbors.items() if value["expires"] > now
            }

    def snapshot(self, mac_to_endpoint=None):
        self._expire()
        mapping = mac_to_endpoint or {}
        with self.lock:
            values = []
            for neighbor in self.neighbors.values():
                item = dict(neighbor)
                item["endpoint"] = mapping.get(item["source_mac"])
                values.append(item)
            values.sort(key=lambda item: (item.get("endpoint") or "~", item["source_mac"]))
            return {"state": "ok" if self.error is None else "error", "error": self.error, "neighbors": values}
