#!/usr/bin/env python3

import argparse
import binascii
import functools
import glob
import hashlib
import hmac
import json
import math
import mmap
import os
import re
import shutil
import stat
import struct
import subprocess
import tarfile
import tempfile
import threading
import time
import traceback
from contextlib import suppress
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path, PurePosixPath
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlparse
from urllib.request import Request, urlopen

try:
    from l2_features import LldpMonitor, default_config as default_l2_config
    from l2_features import normalize_config as normalize_l2_config
except ModuleNotFoundError:  # direct import used by the local test harness
    from webui.l2_features import LldpMonitor, default_config as default_l2_config
    from webui.l2_features import normalize_config as normalize_l2_config
try:
    from runtime_state import RuntimeState
except ModuleNotFoundError:  # direct import used by the local test harness
    from webui.runtime_state import RuntimeState
try:
    from optics import optics_diagnostic_script
except ModuleNotFoundError:  # direct import used by the local test harness
    from webui.optics import optics_diagnostic_script


def application_version():
    """Read the single project version in source and installed layouts."""
    for path in (Path(__file__).with_name("VERSION"), Path(__file__).parent.parent / "VERSION"):
        try:
            value = path.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if re.fullmatch(r"\d+\.\d+\.\d+(?:[-+][A-Za-z0-9._-]+)?", value):
            return value
    return "development"


APP_VERSION = application_version()
GROUPS = (
    {"key": "epl0", "epl": 0, "mpo": 1, "position": 1, "resource": 0},
    {"key": "epl1", "epl": 1, "mpo": 1, "position": 2, "resource": 1},
    {"key": "epl2", "epl": 2, "mpo": 1, "position": 3, "resource": 2},
    {"key": "epl5", "epl": 5, "mpo": 2, "position": 1, "resource": 3},
    {"key": "epl6", "epl": 6, "mpo": 2, "position": 2, "resource": 4},
    {"key": "epl7", "epl": 7, "mpo": 2, "position": 3, "resource": 5},
)
GROUP_BY_EPL = {item["epl"]: item for item in GROUPS}
GROUP_BY_KEY = {item["key"]: item for item in GROUPS}
SPLIT_MODES = {10000: "10GBase-SR", 25000: "25GBase-SR"}
BONDED_MODES = {40000: "40GBase-SR4", 100000: "100GBase-SR4"}
GUARANTEED_BUDGET = 600000
HARD_BUDGET = 647500
INTERNAL_BUDGET = 20000  # management PCIe logical port 0 + CPU PEP4
SERVICE = "pe31625g24dira-switch.service"
DEFAULT_CONFIG = "/etc/pe31625g24dira/webui/config.json"
FAN_CONFIG_PATH = "/etc/pe31625g24dira/webui/fan.json"
PORT_CONFIG_PATH = "/etc/pe31625g24dira/webui/ports.json"
L2_CONFIG_PATH = "/etc/pe31625g24dira/webui/l2.json"
FAN_INIT_SCRIPT = "/etc/pe31625g24dira/pe31625g24dira-fan-init.tp"
FAN_COMPLETE_MARKER = "PE31625G24DIRA_SWITCH_MANAGER_FAN_DONE"
SWITCH_READY_PATH = "/run/pe31625g24dira-testpoint/switch-ready"
FAN_READY_PATH = "/run/pe31625g24dira-testpoint/fan-ready"
SENSOR_COMPLETE_MARKER = "PE31625G24DIRA_SWITCH_MANAGER_SENSOR_DONE"
STATUS_COMPLETE_MARKER = "PE31625G24DIRA_SWITCH_MANAGER_STATUS_DONE"
PORT_ADMIN_COMPLETE_MARKER = "PE31625G24DIRA_SWITCH_MANAGER_PORT_ADMIN_DONE"
XCVR_VERIFY_FAILURE_MARKER = "PE31625G24DIRA_XCVR_VERIFY_FAILED"
FDB_COMPLETE_MARKER = "PE31625G24DIRA_SWITCH_MANAGER_FDB_DONE"
LANE_DIAGNOSTIC_COMPLETE_MARKER = "PE31625G24DIRA_SWITCH_MANAGER_LANE_DIAGNOSTIC_DONE"
OPTICS_DIAGNOSTIC_COMPLETE_MARKER = "PE31625G24DIRA_SWITCH_MANAGER_OPTICS_DIAGNOSTIC_DONE"
VLAN_READBACK_COMPLETE_MARKER = "PE31625G24DIRA_SWITCH_MANAGER_VLAN_READBACK_DONE"
VLAN_APPLY_COMPLETE_MARKER = "PE31625G24DIRA_SWITCH_MANAGER_VLAN_APPLY_DONE"
TOPOLOGY_APPLY_COMPLETE_MARKER = "PE31625G24DIRA_SWITCH_MANAGER_TOPOLOGY_APPLY_DONE"
MAC_REPAIR_AUDIT_MARKER = "PE31625G24DIRA_SWITCH_MANAGER_MAC_REPAIR_AUDIT_DONE"
MAC_REPAIR_APPLY_MARKER = "PE31625G24DIRA_SWITCH_MANAGER_MAC_REPAIR_APPLY_DONE"
L2_APPLY_COMPLETE_MARKER = "PE31625G24DIRA_SWITCH_MANAGER_L2_APPLY_DONE"
CONFIG_EXPORT_FORMAT = "pe31625g24dira-switch-config"
CONFIG_EXPORT_VERSION = 3
UPGRADE_ROOT = "/var/lib/pe31625g24dira/updates"
UPGRADE_MAX_BYTES = 64 * 1024 * 1024
RELEASE_API = "https://api.github.com/repos/Sakana-bot/pe31625g24dira-switch-stack/releases/latest"
RELEASES_API = "https://api.github.com/repos/Sakana-bot/pe31625g24dira-switch-stack/releases?per_page=30"
XCVR_WRITE_MAX_ATTEMPTS = 12
FAN_MAX_RPM = 2800
FAN_RESPONSE_TIMES = (5.45, 10.9, 21.6, 43.7)
SESSION_SECONDS = 12 * 60 * 60
# Use a distinct name from the former HTTPS-only cookie. Browsers intentionally
# prevent an insecure origin from replacing a matching Secure cookie.
SESSION_COOKIE = "pe31625g24dira_http_session"
FM10000_EPL_BASE = 0x0E0000
FM10000_EPL_STRIDE = 0x400
FM10000_PORT_STRIDE = 0x80
FM10000_RX_STATS_BASE = 0xE00000
FM10000_RX_STATS_BANK_STRIDE = 0x1000
FM10000_TX_STATS_BASE = 0xE80000
FM10000_TX_FRAME_OFFSET = 0x25000
FM10000_TX_BYTE_OFFSET = 0x26000
RX_LENGTH_LABELS = (
    "lt_64",
    "eq_64",
    "65_127",
    "128_255",
    "256_511",
    "512_1023",
    "1024_1522",
    "1523_2047",
    "2048_4095",
    "4096_8191",
    "8192_10239",
    "ge_10240",
)
TX_LENGTH_LABELS = RX_LENGTH_LABELS
RX_DROP_BANK4 = (
    "fid_forwarded",
    "flood_forwarded",
    "specially_handled",
    "parser_error",
    "ecc_error",
    "trapped",
    "pause",
    "stp",
    "security",
    "vlan_tag",
    "vlan_ingress",
    "vlan_egress",
    "glort_miss",
    "ffu",
    "trigger",
    "reserved",
)
RX_DROP_BANK5 = (
    "policer",
    "ttl",
    "cm_private",
    "cm_smp0",
    "cm_smp1",
    "cm_rx_hog0",
    "cm_rx_hog1",
    "cm_tx_hog0",
    "cm_tx_hog1",
    "reserved",
    "trigger_redirect",
    "flood_control",
    "glort_forwarded",
    "loopback_suppress",
    "other",
    "reserved_15",
)

PORT_PREFIX = "api.platform.config.switch.0.portIndex."
ACTIVE_PORT_RE = re.compile(
    r"^api\.platform\.config\.switch\.0\.portIndex\.(\d+)\.(.+?)\s+(text|int|bool)\s+(.+?)\s*$",
    re.MULTILINE,
)
MAPPING_RE = re.compile(
    r'^api\.platform\.config\.switch\.0\.portIndex\.(\d+)(?:\.lane\.(\d+))?\.portMapping\s+text\s+"LOG=\d+ EPL=(\d+) LANE=(\d+)"\s*$',
    re.MULTILINE,
)
SPEED_RE = re.compile(
    r"^api\.platform\.config\.switch\.0\.portIndex\.(\d+)\.speed\s+int\s+(\d+)\s*$",
    re.MULTILINE,
)
MODE_RE = re.compile(
    r"^api\.platform\.config\.switch\.0\.portIndex\.(\d+)\.ethernetMode\s+text\s+(\S+)\s*$",
    re.MULTILINE,
)
PORT_STATUS_RE = re.compile(r"^(\d+)\s+(10G|25G|40G|100G)\s+(\S+)\s+(\S+)\s+(\S+)", re.MULTILINE)
NUM_PORTS_RE = re.compile(
    r"^(api\.platform\.config\.switch\.0\.numPorts\s+int\s+)\d+\s*$", re.MULTILINE
)
CPU_PORT_RE = re.compile(
    r"^(api\.platform\.config\.switch\.0\.cpuPort\s+int\s+)\d+\s*$", re.MULTILINE
)
SENSOR_TEMP_RE = re.compile(
    r"^(MAIN TEMP SENSOR|REMOTE TEMP SENSOR\s+\d+)\s*:\s*([0-9.]+)\s+C", re.MULTILINE
)
SENSOR_VOLT_RE = re.compile(r"^(VOLTAGE SENSOR\s+[^:]+)\s*:\s*([0-9.]+)\s+V", re.MULTILINE)
FAN_TACH_LSB_RE = re.compile(r"Device=0x59:\s+<=\s+A3\s+=>\s+([0-9A-Fa-f]{2})")
FAN_TACH_MSB_RE = re.compile(r"Device=0x59:\s+<=\s+A4\s+=>\s+([0-9A-Fa-f]{2})")
FDB_ENTRY_RE = re.compile(
    r"^([0-9A-Fa-f:]{17})\s+(\S+)\s+(\d+)\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S+)\s*$",
    re.MULTILINE,
)
LANE_STATUS_RE = re.compile(
    r"\b([NRTL])\s*\|\s*([DNY0-3])\s*\|\s*([S1CiK])\s*\|\s*([WRCE])([WRCE])\s*\|\s*(\S{2})\s+(\S+/\S+)\s*$"
)
OPTICS_RECORD_RE = re.compile(
    r"^PE31625G24DIRA_OPTICS mpo=(\d+) mux=(\d+) select_status=(-?\d+) page_status=(-?\d+) read_status=(-?\d+) restore_page_status=(-?\d+) raw=([0-9A-Fa-f]{48})$",
    re.MULTILINE,
)
OPTICS_IDENTITY_RE = re.compile(
    r"^PE31625G24DIRA_OPTICS_IDENTITY mpo=(\d+) page_status=(-?\d+) read_status=(-?\d+) restore_page_status=(-?\d+) raw=([0-9A-Fa-f]{256})$",
    re.MULTILINE,
)
OPTICS_TEMPERATURE_RE = re.compile(
    r"^PE31625G24DIRA_OPTICS_TEMPERATURE mpo=(\d+) status=(-?\d+) raw=([0-9A-Fa-f]{4})$",
    re.MULTILINE,
)


class ApiError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status
        self.message = message


FM10840_TEMPERATURES = (
    {
        "label": "PCIe 主机接口 #0",
        "location": "PCI Host Interface #0",
        "category": "interface",
        "documented": True,
    },
    {
        "label": "交换核心测点 0",
        "location": "Switch temperature 0",
        "category": "switch",
        "documented": True,
    },
    {
        "label": "交换核心测点 1",
        "location": "Switch temperature 1",
        "category": "switch",
        "documented": True,
    },
    {
        "label": "交换核心测点 2",
        "location": "Switch temperature 2",
        "category": "switch",
        "documented": True,
    },
    {
        "label": "交换核心测点 3",
        "location": "Switch temperature 3",
        "category": "switch",
        "documented": True,
    },
    {
        "label": "交换核心测点 4",
        "location": "Switch temperature 4",
        "category": "switch",
        "documented": True,
    },
    {
        "label": "以太网端口逻辑 #8",
        "location": "Ethernet Port Logic #8",
        "category": "port",
        "documented": True,
    },
    {
        "label": "隧道引擎",
        "location": "Tunneling engine",
        "category": "engine",
        "documented": True,
    },
    {
        "label": "未公开测点 #8",
        "location": "TEMPERATURE[8] location not documented",
        "category": "unknown",
        "documented": False,
    },
)


class State(RuntimeState):
    def __init__(self, config, config_path=None):
        super().__init__(config, config_path, SESSION_SECONDS)


def read_json(path):
    with Path(path).open(encoding="utf-8") as handle:
        return json.load(handle)


def atomic_write(path, data, mode=0o600):
    directory = os.path.dirname(path)
    if directory and not os.path.isdir(directory):
        os.makedirs(directory)
    fd, temporary = tempfile.mkstemp(prefix=".pe31625g24dira-", dir=directory or ".")
    try:
        with os.fdopen(fd, "w") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    except Exception:
        with suppress(OSError):
            os.unlink(temporary)
        raise


def password_digest(password, salt_hex, rounds):
    return hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        binascii.unhexlify(salt_hex.encode("ascii")),
        rounds,
    ).hex()


def credentials_valid(config, username, password):
    try:
        candidate = password_digest(password, config["password_salt"], config["password_rounds"])
        return hmac.compare_digest(username, config["username"]) and hmac.compare_digest(
            candidate, config["password_hash"]
        )
    except Exception:
        return False


def config_initialized(config):
    if "initialized" in config:
        return config["initialized"] is True
    return all(config.get(key) for key in ("username", "password_salt", "password_hash"))


def validate_admin_values(username, password):
    if not isinstance(username, str):
        raise ApiError(400, "用户名无效")
    username = username.strip()
    if not re.match(r"^[A-Za-z0-9_.-]{3,64}$", username):
        raise ApiError(400, "用户名须为 3–64 位字母、数字、点、下划线或连字符")
    if not isinstance(password, str) or not 8 <= len(password) <= 256:
        raise ApiError(400, "密码须为 8–256 个字符")
    return username, password


def initialize_admin(state, username, password):
    if not state.config_path:
        raise ApiError(500, "未配置凭据文件路径")
    username, password = validate_admin_values(username, password)
    with state.config_lock:
        if config_initialized(state.config):
            raise ApiError(409, "WebUI 已完成初始化")
        updated = dict(state.config)
        salt = binascii.hexlify(os.urandom(24)).decode("ascii")
        rounds = 140000
        updated.update(
            {
                "initialized": True,
                "username": username,
                "password_salt": salt,
                "password_rounds": rounds,
                "password_hash": password_digest(password, salt, rounds),
            }
        )
        atomic_write(
            state.config_path,
            json.dumps(updated, indent=2, sort_keys=True) + "\n",
            0o600,
        )
        state.config = updated
    return username


def update_admin_credentials(state, current_username, current_password, new_username, new_password):
    if not state.config_path:
        raise ApiError(500, "未配置凭据文件路径")
    if not isinstance(current_password, str) or not credentials_valid(
        state.config, current_username, current_password
    ):
        raise ApiError(403, "当前密码错误")
    if not isinstance(new_username, str):
        raise ApiError(400, "用户名无效")
    new_username = new_username.strip()
    if not re.match(r"^[A-Za-z0-9_.-]{3,64}$", new_username):
        raise ApiError(400, "用户名须为 3–64 位字母、数字、点、下划线或连字符")
    if not isinstance(new_password, str):
        raise ApiError(400, "新密码无效")
    if new_password and not 8 <= len(new_password) <= 256:
        raise ApiError(400, "新密码须为 8–256 个字符")

    with state.config_lock:
        updated = dict(state.config)
        updated["username"] = new_username
        if new_password:
            salt = binascii.hexlify(os.urandom(24)).decode("ascii")
            rounds = int(updated.get("password_rounds", 140000))
            updated["password_salt"] = salt
            updated["password_rounds"] = rounds
            updated["password_hash"] = password_digest(new_password, salt, rounds)
        atomic_write(
            state.config_path,
            json.dumps(updated, indent=2, sort_keys=True) + "\n",
            0o600,
        )
        state.config = updated
    state.revoke_all_sessions()
    return new_username


def create_config(path, listen, port):
    if os.path.exists(path):
        raise RuntimeError(f"Configuration already exists: {path}")
    config = {
        "initialized": False,
        "listen": listen,
        "port": port,
        "platform_active": "/usr/share/netfab/fm_platform_attributes.cfg",
        "platform_persistent": "/usr/share/netfab/fm_platform_attributes_pe31625g24dira.cfg",
        "topology_base": "/opt/pe31625g24dira-switch-manager/reference_original_6x100.cfg",
        "startup_script": "/etc/pe31625g24dira/pe31625g24dira-switch.tp",
        "status_script": "/etc/pe31625g24dira/webui/status.tp",
        "sensor_script": "/etc/pe31625g24dira/webui/sensors.tp",
        "vlan_config": "/etc/pe31625g24dira/webui/vlans.json",
        "vlan_apply_script": "/etc/pe31625g24dira/webui/vlan-apply.tp",
        "fan_config": FAN_CONFIG_PATH,
        "port_config": PORT_CONFIG_PATH,
        "l2_config": L2_CONFIG_PATH,
        "fan_init_script": FAN_INIT_SCRIPT,
        "management_interface": "enp2s0",
        "backup_root": "/data/pe31625g24dira-switch-manager/backups",
        "static_root": "/opt/pe31625g24dira-switch-manager/static",
        "testpoint_root": "/usr/local/rrc/perl",
        "uio_device": "/dev/uio0",
        "cpu_interface": "enp1s0",
    }
    atomic_write(path, json.dumps(config, indent=2, sort_keys=True) + "\n", 0o600)
    print("WebUI configuration created; complete administrator setup in the browser.")


def read_text(path):
    with open(path) as handle:
        return handle.read()


def parse_port_values(text):
    result = {}
    for match in ACTIVE_PORT_RE.finditer(text):
        port = int(match.group(1))
        result.setdefault(port, {})[match.group(2)] = (match.group(3), match.group(4))
    return result


def semantic_port_speed(speed, ethernet_mode):
    """Translate SDK scheduler values to the front-panel line rate."""
    mode_speeds = {
        "10GBase-SR": 10000,
        "25GBase-SR": 25000,
        "40GBase-SR4": 40000,
        "100GBase-SR4": 100000,
    }
    return mode_speeds.get(ethernet_mode, speed)


def parse_platform_text(text):
    speeds = {int(m.group(1)): int(m.group(2)) for m in SPEED_RE.finditer(text)}
    modes = {int(m.group(1)): m.group(2) for m in MODE_RE.finditer(text)}
    mappings = {}
    for match in MAPPING_RE.finditer(text):
        logical = int(match.group(1))
        mappings.setdefault(int(match.group(3)), []).append(
            {
                "logical": logical,
                "mapping_lane": None if match.group(2) is None else int(match.group(2)),
                "lane": int(match.group(4)),
            }
        )
    groups = []
    external_ports = []
    for physical in GROUPS:
        entries = mappings.get(physical["epl"], [])
        logical_ids = sorted({item["logical"] for item in entries})
        if len(logical_ids) == 1 and len(entries) == 4:
            logical = logical_ids[0]
            scheduler_speed = speeds.get(logical)
            line_speed = semantic_port_speed(scheduler_speed, modes.get(logical))
            group = dict(physical)
            group.update(
                {
                    "layout": "bonded",
                    "speed": line_speed,
                    "scheduler_speed": scheduler_speed,
                    "ethernet_mode": modes.get(logical),
                    "logical_ports": [logical],
                }
            )
            external_ports.append(
                {
                    "logical": logical,
                    "group": physical["key"],
                    "lane": None,
                    "speed": line_speed,
                    "scheduler_speed": scheduler_speed,
                    "ethernet_mode": modes.get(logical),
                }
            )
        elif len(logical_ids) == 4 and len(entries) == 4:
            lane_ports = []
            by_lane = {}
            for item in entries:
                by_lane[item["lane"]] = item["logical"]
            if sorted(by_lane.keys()) != [0, 1, 2, 3]:
                raise RuntimeError("EPL{} lane mapping incomplete".format(physical["epl"]))
            bonded_logical = by_lane[0]
            bonded_mode = modes.get(bonded_logical)
            fixed_bonded = (
                bonded_mode in BONDED_MODES.values()
                and all(modes.get(by_lane[lane]) == "DISABLED" for lane in range(1, 4))
            )
            if fixed_bonded:
                scheduler_speed = speeds.get(bonded_logical)
                line_speed = semantic_port_speed(scheduler_speed, bonded_mode)
                group = dict(physical)
                group.update(
                    {
                        "layout": "bonded",
                        "speed": line_speed,
                        "scheduler_speed": scheduler_speed,
                        "ethernet_mode": bonded_mode,
                        "logical_ports": [bonded_logical],
                        "lane_logical_ports": [by_lane[lane] for lane in range(4)],
                    }
                )
                external_ports.append(
                    {
                        "logical": bonded_logical,
                        "group": physical["key"],
                        "lane": None,
                        "speed": line_speed,
                        "scheduler_speed": scheduler_speed,
                        "ethernet_mode": bonded_mode,
                    }
                )
                groups.append(group)
                continue
            for lane in range(4):
                logical = by_lane[lane]
                scheduler_speed = speeds.get(logical)
                line_speed = semantic_port_speed(scheduler_speed, modes.get(logical))
                lane_ports.append(
                    {
                        "lane": lane,
                        "logical": logical,
                        "speed": line_speed,
                        "scheduler_speed": scheduler_speed,
                        "ethernet_mode": modes.get(logical),
                    }
                )
                external_ports.append(
                    {
                        "logical": logical,
                        "group": physical["key"],
                        "lane": lane,
                        "speed": line_speed,
                        "scheduler_speed": scheduler_speed,
                        "ethernet_mode": modes.get(logical),
                    }
                )
            group = dict(physical)
            group.update(
                {
                    "layout": "split",
                    "lanes": lane_ports,
                    "logical_ports": logical_ids,
                    "lane_logical_ports": [by_lane[lane] for lane in range(4)],
                }
            )
        else:
            raise RuntimeError(
                "EPL{} topology cannot be identified ({} mappings, {} logical ports)".format(
                    physical["epl"], len(entries), len(logical_ids)
                )
            )
        groups.append(group)
    for port in external_ports:
        if port["speed"] is None or port["ethernet_mode"] is None:
            raise RuntimeError(
                "logical port {} has no speed or Ethernet mode".format(port["logical"])
            )
    external_ports.sort(key=lambda x: x["logical"])
    return {
        "groups": groups,
        "ports": external_ports,
        "external": sum(item["speed"] for item in external_ports),
        "total": sum(item["speed"] for item in external_ports)
        + sum(
            speed
            for logical, speed in speeds.items()
            if logical not in {
                item["logical"] for entries in mappings.values() for item in entries
            }
        ),
        "external_count": len(external_ports),
    }


def parse_platform(path):
    text = read_text(path)
    return text, parse_platform_text(text)


def endpoint_key(port):
    if port["lane"] is None:
        return "{}.bonded".format(port["group"])
    return "{}.lane{}".format(port["group"], port["lane"])


def topology_endpoints(parsed):
    endpoints = []
    physical_by_key = {item["key"]: item for item in GROUPS}
    for port in parsed["ports"]:
        physical = physical_by_key[port["group"]]
        key = endpoint_key(port)
        label = "MPO24-{} · EPL {} · {}".format(
            physical["mpo"],
            physical["epl"],
            "4×Lane 聚合" if port["lane"] is None else "Lane {}".format(port["lane"]),
        )
        endpoints.append(
            {
                "key": key,
                "logical": port["logical"],
                "label": label,
                "group": port["group"],
                "lane": port["lane"],
                "speed": port["speed"],
            }
        )
    return endpoints


def default_port_config(parsed):
    return {
        "version": 1,
        "enabled": {item["key"]: True for item in topology_endpoints(parsed)},
    }


def normalize_port_config(value, parsed):
    endpoints = topology_endpoints(parsed)
    keys = {item["key"] for item in endpoints}
    raw_enabled = value.get("enabled", {}) if isinstance(value, dict) else {}
    enabled = {key: bool(raw_enabled.get(key, True)) for key in sorted(keys)}
    return {"version": 1, "enabled": enabled}


def load_port_config(config, parsed, create=True):
    path = config.get("port_config", PORT_CONFIG_PATH)
    raw = None
    if os.path.exists(path):
        raw = read_json(path)
        value = normalize_port_config(raw, parsed)
    else:
        value = default_port_config(parsed)
    if create and value != raw:
        atomic_write(
            path,
            json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            0o600,
        )
    return value


def reconcile_port_config(value, old_parsed, new_parsed):
    old_value = normalize_port_config(value, old_parsed)
    old_by_group = {}
    for endpoint in topology_endpoints(old_parsed):
        old_by_group.setdefault(endpoint["group"], []).append(endpoint["key"])
    new_value = default_port_config(new_parsed)
    for endpoint in topology_endpoints(new_parsed):
        key = endpoint["key"]
        if key in old_value["enabled"]:
            new_value["enabled"][key] = old_value["enabled"][key]
            continue
        previous = old_by_group.get(endpoint["group"], [])
        if previous:
            new_value["enabled"][key] = any(old_value["enabled"].get(item, True) for item in previous)
    return new_value


def load_l2_config(config, parsed, create=True):
    path = config.get("l2_config", L2_CONFIG_PATH)
    keys = [item["key"] for item in topology_endpoints(parsed)]
    raw = read_json(path) if os.path.exists(path) else None
    value = normalize_l2_config(raw, keys)
    if create and value != raw:
        atomic_write(
            path,
            json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            0o600,
        )
    return value


def validate_l2_config(body, parsed):
    if not isinstance(body, dict):
        raise ApiError(400, "网络功能配置无效")
    keys = {item["key"] for item in topology_endpoints(parsed)}
    labels = body.get("labels", {})
    if not isinstance(labels, dict) or any(key not in keys for key in labels):
        raise ApiError(400, "端口名称与当前拓扑不匹配")
    for label in labels.values():
        if not isinstance(label, str) or len(label.strip()) > 32 or any(ord(ch) < 32 for ch in label):
            raise ApiError(400, "端口名称不能超过 32 个可见字符")
    try:
        value = normalize_l2_config(body, keys)
    except (TypeError, ValueError):
        raise ApiError(400, "保护参数必须是有效数字") from None
    mirror = value["mirror"]
    if mirror["enabled"] and (
        not mirror["source"]
        or not mirror["destination"]
        or mirror["source"] == mirror["destination"]
    ):
        raise ApiError(400, "镜像源端口和监控端口必须不同")
    return value


def reconcile_l2_config(value, new_parsed):
    return normalize_l2_config(value, [item["key"] for item in topology_endpoints(new_parsed)])


def l2_payload(config, parsed, state=None):
    value = load_l2_config(config, parsed)
    labels = value["labels"]
    endpoints = []
    for endpoint in topology_endpoints(parsed):
        item = dict(endpoint)
        item["name"] = labels.get(item["key"], "")
        endpoints.append(item)
    neighbors = {"state": "unavailable", "error": None, "neighbors": []}
    if state is not None and state.lldp_monitor is not None:
        with state.l2_lock:
            mapping = dict(state.lldp_mac_to_endpoint)
        neighbors = state.lldp_monitor.snapshot(mapping)
    return {**value, "endpoints": endpoints, "neighbors": neighbors}


def port_admin_payload(parsed, value):
    normalized = normalize_port_config(value, parsed)
    endpoints = []
    mpo_summary = {}
    for endpoint in topology_endpoints(parsed):
        item = dict(endpoint)
        item["enabled"] = normalized["enabled"][item["key"]]
        endpoints.append(item)
        mpo = str(GROUP_BY_KEY[item["group"]]["mpo"])
        summary = mpo_summary.setdefault(mpo, {"enabled_count": 0, "total": 0})
        summary["total"] += 1
        summary["enabled_count"] += int(item["enabled"])
    for summary in mpo_summary.values():
        summary["enabled"] = summary["total"] > 0 and summary["enabled_count"] == summary["total"]
    return {"endpoints": endpoints, "mpo": mpo_summary}


def default_vlan_config(parsed):
    keys = [item["key"] for item in topology_endpoints(parsed)]
    return {
        "version": 3,
        "vlans": [
            {"id": 1, "name": "Default", "mtu": 1536, "tagged": [], "untagged": keys}
        ],
    }


def load_vlan_config(config, parsed, create=True):
    path = config.get("vlan_config", "/etc/pe31625g24dira/webui/vlans.json")
    if not os.path.exists(path):
        value = default_vlan_config(parsed)
        if create:
            atomic_write(
                path,
                json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                0o600,
            )
        return value
    value = read_json(path)
    for vlan in value.get("vlans", []):
        vlan.setdefault("mtu", 1536)
    value["version"] = 3
    return value


def validate_vlan_config(body, parsed):
    raw = body.get("vlans") if isinstance(body, dict) else None
    if not isinstance(raw, list) or not raw:
        raise ApiError(400, "VLAN 列表不能为空")
    endpoints = topology_endpoints(parsed)
    endpoint_keys = {item["key"] for item in endpoints}
    seen_ids = set()
    untagged_owner = {}
    normalized = []
    for item in raw:
        if not isinstance(item, dict):
            raise ApiError(400, "VLAN 项格式无效")
        try:
            vid = int(item.get("id"))
        except (TypeError, ValueError):
            raise ApiError(400, "VLAN ID 必须是整数") from None
        if vid < 1 or vid > 4094 or vid in seen_ids:
            raise ApiError(400, "VLAN ID 必须唯一且位于 1–4094")
        seen_ids.add(vid)
        name = str(item.get("name", f"VLAN {vid}")).strip()
        if not name or len(name) > 32 or any(ord(ch) < 32 for ch in name):
            raise ApiError(400, "VLAN 名称必须为 1–32 个可见字符")
        try:
            mtu = int(item.get("mtu", 1536))
        except (TypeError, ValueError):
            raise ApiError(400, f"VLAN {vid} 的最大帧必须是整数") from None
        if mtu < 64 or mtu > 16383:
            raise ApiError(400, f"VLAN {vid} 的最大帧必须位于 64–16383 字节")
        tagged = {str(key) for key in item.get("tagged", [])}
        untagged = {str(key) for key in item.get("untagged", [])}
        if (tagged | untagged) - endpoint_keys:
            raise ApiError(400, f"VLAN {vid} 包含当前拓扑不存在的端口")
        if tagged & untagged:
            raise ApiError(400, f"同一端口不能同时是 VLAN {vid} 的 tagged 和 untagged 成员")
        for key in untagged:
            if key in untagged_owner:
                raise ApiError(400, f"端口 {key} 只能属于一个 untagged VLAN")
            untagged_owner[key] = vid
        normalized.append(
            {
                "id": vid,
                "name": name,
                "mtu": mtu,
                "tagged": sorted(tagged),
                "untagged": sorted(untagged),
            }
        )
    if 1 not in seen_ids:
        raise ApiError(400, "VLAN 1 不能删除")
    member_keys = set(untagged_owner.keys())
    for item in normalized:
        member_keys.update(item["tagged"])
    missing = endpoint_keys - member_keys
    if missing:
        raise ApiError(
            400,
            "每个端口必须至少加入一个 VLAN；缺少：{}".format(", ".join(sorted(missing))),
        )
    normalized.sort(key=lambda item: item["id"])
    if len({item["mtu"] for item in normalized}) > 8:
        raise ApiError(400, "FM10840 最多只能同时使用 8 个不同的最大帧值")
    return {"version": 3, "vlans": normalized}


def reconcile_vlan_config(value, old_parsed, new_parsed):
    old_keys = {item["key"] for item in topology_endpoints(old_parsed)}
    new_keys = {item["key"] for item in topology_endpoints(new_parsed)}
    retained = old_keys & new_keys
    result = {"version": 3, "vlans": []}
    has_vlan1 = False
    for vlan in value.get("vlans", []):
        item = {
            "id": int(vlan["id"]),
            "name": vlan.get("name", "VLAN {}".format(vlan["id"])),
            "mtu": int(vlan.get("mtu", 1536)),
            "tagged": sorted(set(vlan.get("tagged", [])) & retained),
            "untagged": sorted(set(vlan.get("untagged", [])) & retained),
        }
        if item["id"] == 1:
            has_vlan1 = True
            item["untagged"] = sorted(set(item["untagged"]) | (new_keys - retained))
        result["vlans"].append(item)
    if not has_vlan1:
        result["vlans"].append(
            {
                "id": 1,
                "name": "Default",
                "mtu": 1536,
                "tagged": [],
                "untagged": sorted(new_keys - retained),
            }
        )
    result["vlans"].sort(key=lambda item: item["id"])
    return result


def vlan_pvid_map(value):
    result = {}
    for vlan in value["vlans"]:
        for key in vlan["untagged"]:
            result[key] = vlan["id"]
    return result


def vlan_port_profiles(parsed, value):
    profiles = {
        item["key"]: {"mode": "access", "native_vlan": None, "tagged_vlans": []}
        for item in topology_endpoints(parsed)
    }
    for vlan in value["vlans"]:
        for key in vlan["untagged"]:
            profiles[key]["native_vlan"] = vlan["id"]
        for key in vlan["tagged"]:
            profiles[key]["tagged_vlans"].append(vlan["id"])
    for profile in profiles.values():
        profile["tagged_vlans"].sort()
        if profile["native_vlan"] is None:
            profile["mode"] = "trunk"
        elif profile["tagged_vlans"]:
            profile["mode"] = "hybrid"
        else:
            profile["mode"] = "access"
    return profiles


def group_logical_ports(keys, endpoint_map):
    values = sorted(endpoint_map[key] for key in keys)
    ranges = []
    start = previous = values[0]
    for value in values[1:]:
        if value == previous + 1:
            previous = value
            continue
        ranges.append(str(start) if start == previous else f"{start}..{previous}")
        start = previous = value
    ranges.append(str(start) if start == previous else f"{start}..{previous}")
    return ",".join(ranges)


def active_logical_spec(parsed):
    endpoints = topology_endpoints(parsed)
    return group_logical_ports(
        {item["key"] for item in endpoints},
        {item["key"]: item["logical"] for item in endpoints},
    )


def vlan_commands(parsed, value, reset=False):
    endpoints = topology_endpoints(parsed)
    endpoint_map = {item["key"]: item["logical"] for item in endpoints}
    commands = []
    if reset:
        commands.append("reset vlan table")
    mtu_values = sorted({int(vlan.get("mtu", 1536)) for vlan in value["vlans"]})
    mtu_indices = {mtu: index for index, mtu in enumerate(mtu_values)}
    for mtu, index in mtu_indices.items():
        commands.append(f"set switch config mtu_list {index} {mtu}")
    for index in range(len(mtu_values), 8):
        commands.append(f"set switch config mtu_list {index} 16383")
    for vlan in value["vlans"]:
        vid = vlan["id"]
        # TestPoint's reset removes VLAN 1 as well. Recreating VLAN 1 also
        # restores the SDK-owned tunnelling-engine members (ports 19/20).
        if reset or vid != 1:
            commands.append(f"create vlan {vid}")
        commands.append(f"set vlan config mtu {vid} {mtu_indices[int(vlan.get('mtu', 1536))]}")
        members = set(vlan["tagged"]) | set(vlan["untagged"])
        if members:
            ports = group_logical_ports(members, endpoint_map)
            commands.append(f"add vlan port {vid} {ports}")
        if vlan["tagged"]:
            commands.append(
                "set vlan tagging {} {} tag".format(
                    vid, group_logical_ports(vlan["tagged"], endpoint_map)
                )
            )
        if vlan["untagged"]:
            commands.append(
                "set vlan tagging {} {} untag".format(
                    vid, group_logical_ports(vlan["untagged"], endpoint_map)
                )
            )
    commands.extend(vlan_port_profile_commands(parsed, value))
    return commands


def vlan_members_by_logical(parsed, value):
    """Return VLAN membership keyed by the stable logical port numbers."""
    endpoint_map = {
        item["key"]: item["logical"] for item in topology_endpoints(parsed)
    }
    result = {}
    for vlan in value["vlans"]:
        result[vlan["id"]] = {
            "tagged": {endpoint_map[key] for key in vlan["tagged"]},
            "untagged": {endpoint_map[key] for key in vlan["untagged"]},
        }
    return result


def logical_port_spec(values):
    values = sorted(set(values))
    if not values:
        return ""
    return group_logical_ports(
        {str(value) for value in values},
        {str(value): value for value in values},
    )


def vlan_port_profile_commands(parsed, value):
    """Render the per-port portion shared by full and incremental applies."""
    endpoints = topology_endpoints(parsed)
    endpoint_map = {item["key"]: item["logical"] for item in endpoints}
    profiles = vlan_port_profiles(parsed, value)
    commands = []
    pvid_groups = {}
    for key, profile in profiles.items():
        vid = profile["native_vlan"] if profile["native_vlan"] is not None else 1
        pvid_groups.setdefault(vid, set()).add(key)
    for vid in sorted(pvid_groups):
        commands.append(
            f"set port config {group_logical_ports(pvid_groups[vid], endpoint_map)} pvid {vid}"
        )
    port_mtu = {item["key"]: 64 for item in endpoints}
    for vlan in value["vlans"]:
        for key in set(vlan["tagged"]) | set(vlan["untagged"]):
            port_mtu[key] = max(port_mtu[key], int(vlan.get("mtu", 1536)))
    mtu_ports = {}
    for key, mtu in port_mtu.items():
        mtu_ports.setdefault(mtu, set()).add(key)
    for mtu in sorted(mtu_ports):
        commands.append(
            f"set port config {group_logical_ports(mtu_ports[mtu], endpoint_map)} max_frame_size {mtu}"
        )
    commands.append(f"set port config {active_logical_spec(parsed)} drop_bv on")
    mode_ports = {}
    for key, profile in profiles.items():
        mode_ports.setdefault(profile["mode"], set()).add(key)
    for mode, drop_untagged, drop_tagged in (
        ("access", "off", "on"),
        ("trunk", "on", "off"),
        ("hybrid", "off", "off"),
    ):
        if mode_ports.get(mode):
            ports = group_logical_ports(mode_ports[mode], endpoint_map)
            commands.append(f"set port config {ports} drop_untagged {drop_untagged}")
            commands.append(f"set port config {ports} drop_tagged {drop_tagged}")
    return commands


def vlan_delta_commands(old_parsed, old_value, new_parsed, new_value):
    """Apply only the VLAN differences; no switch reset or service restart."""
    old_by_id = {item["id"]: item for item in old_value["vlans"]}
    new_by_id = {item["id"]: item for item in new_value["vlans"]}
    old_members = vlan_members_by_logical(old_parsed, old_value)
    new_members = vlan_members_by_logical(new_parsed, new_value)
    commands = []

    mtu_values = sorted({int(vlan.get("mtu", 1536)) for vlan in new_value["vlans"]})
    mtu_indices = {mtu: index for index, mtu in enumerate(mtu_values)}
    for mtu, index in mtu_indices.items():
        commands.append(f"set switch config mtu_list {index} {mtu}")
    for index in range(len(mtu_values), 8):
        commands.append(f"set switch config mtu_list {index} 16383")

    for vid in sorted(set(new_by_id) - set(old_by_id)):
        commands.append(f"create vlan {vid}")
    for vid in sorted(new_by_id):
        commands.append(
            f"set vlan config mtu {vid} {mtu_indices[int(new_by_id[vid].get('mtu', 1536))]}"
        )
        old_all = set().union(*old_members.get(vid, {"tagged": set(), "untagged": set()}).values())
        new_all = set().union(*new_members[vid].values())
        added = new_all - old_all
        if added:
            commands.append(f"add vlan port {vid} {logical_port_spec(added)}")
        for tagging in ("tagged", "untagged"):
            ports = new_members[vid][tagging]
            if ports:
                tag = "tag" if tagging == "tagged" else "untag"
                commands.append(
                    f"set vlan tagging {vid} {logical_port_spec(ports)} {tag}"
                )

    # Set PVID and ingress rules before removing obsolete memberships so an
    # active access port is never left without a valid native VLAN.
    commands.extend(vlan_port_profile_commands(new_parsed, new_value))
    for vid in sorted(set(old_by_id) | set(new_by_id)):
        old_all = set().union(*old_members.get(vid, {"tagged": set(), "untagged": set()}).values())
        new_all = set().union(*new_members.get(vid, {"tagged": set(), "untagged": set()}).values())
        removed = old_all - new_all
        if removed:
            commands.append(f"del vlan port {vid} {logical_port_spec(removed)}")
    for vid in sorted(set(old_by_id) - set(new_by_id), reverse=True):
        if vid != 1:
            commands.append(f"del vlan {vid}")
    return commands


def tp_script(commands):
    lines = ["# expert"]
    for command in commands:
        lines.append('tp("{}");'.format(command.replace('"', '\\"')))
    return "\n".join(lines) + "\n"


def default_fan_config():
    return {
        "sensor": "fm10840_core",
        "idle_temperature_c": 35,
        "load_temperature_c": 70,
        "critical_temperature_c": 80,
        "idle_speed_percent": 50,
        "load_speed_percent": 80,
        "response_time_s": 10.9,
        "hysteresis_c": 4,
    }


def validate_fan_config(body):
    if not isinstance(body, dict):
        raise ApiError(400, "风扇曲线参数无效")
    if body.get("sensor", "fm10840_core") != "fm10840_core":
        raise ApiError(400, "温度源固定为 FM10840 核心热二极管")
    try:
        idle_temperature = int(body.get("idle_temperature_c"))
        load_temperature = int(body.get("load_temperature_c"))
        critical_temperature = int(body.get("critical_temperature_c"))
        idle_speed = int(body.get("idle_speed_percent"))
        load_speed = int(body.get("load_speed_percent"))
        response_time = float(body.get("response_time_s"))
        hysteresis = int(body.get("hysteresis_c", 4))
    except (TypeError, ValueError):
        raise ApiError(400, "风扇温度、转速或响应时间格式无效") from None
    if not 10 <= idle_temperature <= 80:
        raise ApiError(400, "闲置温度必须在 10–80°C 之间")
    if not idle_temperature + 10 <= load_temperature <= 100:
        raise ApiError(400, "负载温度须至少比闲置温度高 10°C，且不超过 100°C")
    if not load_temperature < critical_temperature <= 120:
        raise ApiError(400, "临界温度必须高于负载温度且不超过 120°C")
    if not 0 <= idle_speed <= 100:
        raise ApiError(400, "闲置转速必须在 0–100% 之间")
    if not idle_speed <= load_speed <= 100:
        raise ApiError(400, "负载转速不能低于闲置转速，且不能超过 100%")
    response_time = min(FAN_RESPONSE_TIMES, key=lambda value: abs(value - response_time))
    if abs(response_time - float(body.get("response_time_s"))) > 0.01:
        raise ApiError(400, "不支持的硬件响应时间")
    if not 0 <= hysteresis <= 15:
        raise ApiError(400, "降档回差必须在 0–15°C 之间")
    return {
        "sensor": "fm10840_core",
        "idle_temperature_c": idle_temperature,
        "load_temperature_c": load_temperature,
        "critical_temperature_c": critical_temperature,
        "idle_speed_percent": idle_speed,
        "load_speed_percent": load_speed,
        "response_time_s": response_time,
        "hysteresis_c": hysteresis,
    }


def load_fan_config(config):
    path = config.get("fan_config", FAN_CONFIG_PATH)
    if not os.path.exists(path):
        return default_fan_config()
    return validate_fan_config(read_json(path))


def fan_config_payload(config):
    value = load_fan_config(config)
    return {
        **value,
        "sensor_label": "FM10840 核心热二极管",
        "controller": "LM96163 硬件查找表",
        "response_time_options_s": list(FAN_RESPONSE_TIMES),
        "nominal_max_rpm": FAN_MAX_RPM,
        "rpm_is_approximate": True,
    }


def percent_to_pwm(percent):
    return round(percent * 255 / 100)


def fan_lut_points(value):
    points = [{"temperature_c": 0, "speed_percent": value["idle_speed_percent"]}]
    for index in range(9):
        ratio = index / 8
        points.append(
            {
                "temperature_c": round(
                    value["idle_temperature_c"]
                    + (value["load_temperature_c"] - value["idle_temperature_c"]) * ratio
                ),
                "speed_percent": round(
                    value["idle_speed_percent"]
                    + (value["load_speed_percent"] - value["idle_speed_percent"]) * ratio
                ),
            }
        )
    points.append(
        {"temperature_c": value["critical_temperature_c"], "speed_percent": 100}
    )
    points.append({"temperature_c": 127, "speed_percent": 100})
    return points


def fan_enhanced_config(value):
    response_bits = {5.45: 0, 10.9: 1, 21.6: 2, 43.7: 3}[value["response_time_s"]]
    return 0x10 | (response_bits << 1) | 0x01


def render_fan_init(value):
    value = validate_fan_config(value)
    lines = [
        "# expert",
        "# LM96163 autonomous curve using the FM10840 remote thermal diode.",
        "# Select the fan controller I2C branch and restore the default branch later.",
        'tp("i2c swr 0x58 08");',
        "",
        "# Preserve TruTherm and enter direct mode at fail-safe full speed.",
        'tp("i2c wr-rd 0 0x4c 0x33 1 1");',
        'tp("i2c write 0 0x4c 0x3002 2");',
        # PWPGM=1 while editing the LUT. PWOP=1 is required because the board
        # inverts the LM96163 open-drain PWM before it reaches the fan header.
        'tp("i2c write 0 0x4c 0x4A30 2");',
        'tp("i2c write 0 0x4c 0x4B3F 2");',
        'tp("i2c write 0 0x4c 0x4D08 2");',
        'tp("i2c write 0 0x4c 0x4CFF 2");',
        "",
        "# 22.5 kHz high-resolution PWM with configurable transition smoothing.",
        f'tp("i2c write 0 0x4c 0x45{fan_enhanced_config(value):02X} 2");',
        'tp("i2c write 0 0x4c 0x4E00 2");',
        f'tp("i2c write 0 0x4c 0x4F{value["hysteresis_c"]:02X} 2");',
        "",
        "# Temperature limit and PWM percentage entries generated from four endpoints.",
    ]
    for index, point in enumerate(fan_lut_points(value)):
        temperature_register = 0x50 + index * 2
        pwm_register = temperature_register + 1
        pwm = percent_to_pwm(point["speed_percent"])
        lines.append(
            f'tp("i2c write 0 0x4c 0x{temperature_register:02X}{point["temperature_c"]:02X} 2"); '
            f'tp("i2c write 0 0x4c 0x{pwm_register:02X}{pwm:02X} 2");'
        )
    lines.extend(
        [
            "",
            "# Enable TACH and transfer control from direct PWM to the hardware LUT.",
            'tp("i2c write 0 0x4c 0x0306 2");',
            f'tp("i2c write 0 0x4c 0x19{value["critical_temperature_c"]:02X} 2");',
            f'tp("i2c write 0 0x4c 0x21{value["hysteresis_c"]:02X} 2");',
            'tp("i2c write 0 0x4c 0x4A10 2");',
            "select(undef, undef, undef, 6.0);",
            "",
            "# Read back temperature, active PWM and coherent TACH count.",
            'tp("i2c wr-rd 0 0x4c 0x01 1 1");',
            'tp("i2c wr-rd 0 0x4c 0x10 1 1");',
            'tp("i2c wr-rd 0 0x4c 0x4A 1 1");',
            'tp("i2c wr-rd 0 0x4c 0x4C 1 1");',
            'tp("i2c wr-rd 0 0x4c 0x46 1 1");',
            'tp("i2c wr-rd 0 0x4c 0x47 1 1");',
            'tp("i2c swr 0x58 01");',
            f'print "{FAN_COMPLETE_MARKER}\\n";',
            f'if (open(my $fan_ready, ">", "{FAN_READY_PATH}")) {{ print $fan_ready "ready\\n"; close($fan_ready); }}',
        ]
    )
    return "\n".join(lines) + "\n"


def extract_lane_profiles(base_text):
    values = parse_port_values(base_text)
    profiles = {}
    for physical, original_port in zip(GROUPS, range(1, 7)):
        source = values.get(original_port, {})
        lane_profiles = []
        for lane in range(4):
            profile = {}
            polarity = source.get(f"lane.{lane}.lanePolarity")
            if not polarity:
                raise RuntimeError(
                    "base config missing EPL{} lane {} polarity".format(physical["epl"], lane)
                )
            profile["lanePolarity"] = polarity
            for name in (
                "rxTermination",
                "preCursor25GOptical",
                "cursor25GOptical",
                "postCursor25GOptical",
            ):
                value = source.get(f"lane.{lane}.{name}")
                if value:
                    profile[name] = value
            lane_profiles.append(profile)
        profiles[physical["epl"]] = lane_profiles
    return profiles


def cfg_line(port, suffix, value_type, value):
    return f"{PORT_PREFIX}{port}.{suffix} {value_type} {value}"


def append_qsfp_lane_profiles(lines, logical, epl, profiles):
    # Liberty Trail requires QSFP SerDes attributes to use the per-lane form
    # and to be anchored on the QSFP_LANE0 representative port.  The lane
    # profiles belong to the physical EPL even when it is exposed as four
    # independent logical ports.
    for lane in range(4):
        for name, typed in sorted(profiles[epl][lane].items()):
            lines.append(cfg_line(logical, f"lane.{lane}.{name}", typed[0], typed[1]))


def append_fixed_lane(lines, logical, physical, lane, speed, ethernet_mode):
    epl = physical["epl"]
    lines.append(cfg_line(logical, "portMapping", "text", f'"LOG={logical} EPL={epl} LANE={lane}"'))
    lines.append(cfg_line(logical, "interfaceType", "text", f"QSFP_LANE{lane}"))
    lines.append(cfg_line(logical, "dfeMode", "text", "ONE_SHOT"))
    lines.append(cfg_line(logical, "speed", "int", speed))
    lines.append(cfg_line(logical, "ethernetMode", "text", ethernet_mode))
    lines.append(cfg_line(logical, "capability", "text", "LAG,ROUTE,10G,25G,40G,100G"))
    resource = physical["resource"] if lane == 0 else "0x{}{:02x}".format(lane, physical["resource"])
    lines.append(cfg_line(logical, "hwResourceId", "int", resource))


def append_split(lines, logical, physical, lane, speed):
    epl = physical["epl"]
    lines.append(cfg_line(logical, "portMapping", "text", f'"LOG={logical} EPL={epl} LANE={lane}"'))
    lines.append(cfg_line(logical, "interfaceType", "text", f"QSFP_LANE{lane}"))
    lines.append(cfg_line(logical, "dfeMode", "text", "ONE_SHOT"))
    lines.append(cfg_line(logical, "speed", "int", speed))
    lines.append(cfg_line(logical, "ethernetMode", "text", SPLIT_MODES[speed]))
    lines.append(cfg_line(logical, "capability", "text", "LAG,ROUTE,10G,25G"))
    resource = "0x{}{:02x}".format(lane, physical["resource"])
    lines.append(cfg_line(logical, "hwResourceId", "int", resource))


def generate_platform(base_text, requested):
    profiles = extract_lane_profiles(base_text)
    dynamic = []
    logical = 1
    external_count = 0
    for physical in GROUPS:
        choice = requested[physical["key"]]
        dynamic.append("")
        dynamic.append(
            "# WebUI generated: MPO24-{} group {}, EPL{}, {}".format(
                physical["mpo"], physical["position"], physical["epl"], choice["layout"]
            )
        )
        representative = logical
        for lane in range(4):
            if choice["layout"] == "bonded":
                mode = BONDED_MODES[choice["speed"]] if lane == 0 else "DISABLED"
                maximum_speed = choice["speed"] if lane == 0 else 25000
            else:
                mode = SPLIT_MODES[choice["speeds"][lane]]
                # Keep the lane-zero slot capable of a future live 100G
                # aggregation; disabled peer slots retain 25G capability.
                maximum_speed = 100000 if lane == 0 else 25000
            append_fixed_lane(
                dynamic, logical, physical, lane, maximum_speed, mode
            )
            logical += 1
        append_qsfp_lane_profiles(dynamic, representative, physical["epl"], profiles)
        if choice["layout"] == "bonded":
            external_count += 1
        else:
            external_count += 4

    allocated_external = len(GROUPS) * 4
    cpu = allocated_external + 3
    internal = (
        (allocated_external + 1, "PCIE=0", 0),
        (allocated_external + 2, "PCIE=2", 0),
        (cpu, "PCIE=4", 10000),
        (allocated_external + 4, "TE=0", 0),
        (allocated_external + 5, "TE=1", 0),
    )
    dynamic.append("")
    dynamic.append("# WebUI generated: internal ports")
    for port, mapping, speed in internal:
        dynamic.append(cfg_line(port, "portMapping", "text", f'"LOG={port} {mapping}"'))
        dynamic.append(cfg_line(port, "speed", "int", speed))

    kept = []
    for line in base_text.splitlines():
        match = re.match(r"^api\.platform\.config\.switch\.0\.portIndex\.(\d+)\.", line)
        if match and int(match.group(1)) >= 1:
            continue
        kept.append(line)
    rendered = (
        "\n".join(kept).rstrip()
        + "\n\n# ===== PE31625G24DIRA Switch Manager dynamic topology =====\n"
        + "\n".join(dynamic).lstrip()
        + "\n"
    )
    rendered, count = NUM_PORTS_RE.subn(lambda m: m.group(1) + str(allocated_external + 6), rendered)
    if count != 1:
        raise RuntimeError("base config has an invalid numPorts entry")
    rendered, count = CPU_PORT_RE.subn(lambda m: m.group(1) + str(cpu), rendered)
    if count != 1:
        raise RuntimeError("base config has an invalid cpuPort entry")
    parsed = parse_platform_text(rendered)
    if parsed["external_count"] != external_count:
        raise RuntimeError("generated external port count mismatch")
    return rendered, parsed


def validate_requested(config, body):
    raw_groups = body.get("groups") if isinstance(body, dict) else None
    if not isinstance(raw_groups, dict):
        raise ApiError(400, "请求必须包含六个物理组")
    if set(raw_groups.keys()) != {item["key"] for item in GROUPS}:
        raise ApiError(400, "必须完整提交 EPL0/1/2/5/6/7 六个物理组")
    requested = {}
    external = 0
    for physical in GROUPS:
        raw = raw_groups[physical["key"]]
        if not isinstance(raw, dict) or raw.get("layout") not in ("split", "bonded"):
            raise ApiError(400, "{} 的布局必须为 split 或 bonded".format(physical["key"]))
        if raw["layout"] == "split":
            values = raw.get("speeds")
            if not isinstance(values, list) or len(values) != 4:
                raise ApiError(400, "{} 拆分模式必须包含四条 Lane".format(physical["key"]))
            try:
                values = [int(value) for value in values]
            except (TypeError, ValueError):
                raise ApiError(400, "Lane 速率必须是整数") from None
            if any(value not in SPLIT_MODES for value in values):
                raise ApiError(400, "拆分 Lane 只允许 10G 或 25G")
            requested[physical["key"]] = {"layout": "split", "speeds": values}
            external += sum(values)
        else:
            try:
                speed = int(raw.get("speed"))
            except (TypeError, ValueError):
                raise ApiError(400, "聚合口速率必须是整数") from None
            if speed not in BONDED_MODES:
                raise ApiError(400, "聚合口只允许 40G 或 100G")
            requested[physical["key"]] = {"layout": "bonded", "speed": speed}
            external += speed
    total = external + INTERNAL_BUDGET
    if total > HARD_BUDGET:
        raise ApiError(409, f"调度总额 {total // 1000}G 超过安全硬上限 {HARD_BUDGET // 1000}G")
    warning = None
    if total > GUARANTEED_BUDGET:
        warning = f"配置可启动，但 {total // 1000}G 超过 600G SKU 保证预算"
        if not body.get("accept_over_guaranteed"):
            raise ApiError(409, warning + "；请明确确认超配")
    return requested, total, warning


def topology_choice_text(choice):
    if choice["layout"] == "bonded":
        return "4× 聚合 · {}G".format(choice["speed"] // 1000)
    return "4× 拆分 · " + "/".join(f"{speed // 1000}G" for speed in choice["speeds"])


def topology_preview(config, body):
    preview_body = dict(body) if isinstance(body, dict) else body
    if isinstance(preview_body, dict):
        preview_body["accept_over_guaranteed"] = True
    requested, total, warning = validate_requested(config, preview_body)
    _, current = parse_platform(config["platform_persistent"])
    base_text = read_text(config.get("topology_base", config["platform_persistent"]))
    _, generated = generate_platform(base_text, requested)
    current_groups = {group["key"]: group for group in current["groups"]}
    changes = []
    affected_ports = 0
    for physical in GROUPS:
        old_group = current_groups[physical["key"]]
        old_choice = (
            {"layout": "bonded", "speed": old_group["speed"]}
            if old_group["layout"] == "bonded"
            else {"layout": "split", "speeds": [lane["speed"] for lane in old_group["lanes"]]}
        )
        new_choice = requested[physical["key"]]
        if old_choice == new_choice:
            continue
        old_ports = len(old_group["logical_ports"])
        new_ports = 1 if new_choice["layout"] == "bonded" else 4
        affected_ports += old_ports
        changes.append(
            {
                "key": physical["key"],
                "epl": physical["epl"],
                "mpo": physical["mpo"],
                "position": physical["position"],
                "before": topology_choice_text(old_choice),
                "after": topology_choice_text(new_choice),
                "old_ports": old_ports,
                "new_ports": new_ports,
            }
        )
    return {
        "changes": changes,
        "affected_ports": affected_ports,
        "external_count": generated["external_count"],
        "external": generated["external"],
        "total": total,
        "warning": warning,
        "requires_restart": bool(changes) and not uses_fixed_logical_model(current),
        "scheduler_proof": False,
    }


def topology_choices(parsed):
    choices = {}
    for group in parsed["groups"]:
        if group["layout"] == "bonded":
            choices[group["key"]] = {
                "layout": "bonded",
                "speed": group["speed"],
            }
        else:
            choices[group["key"]] = {
                "layout": "split",
                "speeds": [lane["speed"] for lane in group["lanes"]],
            }
    return choices


def uses_fixed_logical_model(parsed):
    return all(len(group.get("lane_logical_ports", [])) == 4 for group in parsed["groups"])


def changed_topology_groups(old_parsed, new_parsed):
    old_groups = {group["key"]: group for group in old_parsed["groups"]}
    result = []
    for new_group in new_parsed["groups"]:
        old_group = old_groups[new_group["key"]]
        old_choice = (
            (old_group["layout"], old_group.get("speed"))
            if old_group["layout"] == "bonded"
            else (old_group["layout"], tuple(lane["speed"] for lane in old_group["lanes"]))
        )
        new_choice = (
            (new_group["layout"], new_group.get("speed"))
            if new_group["layout"] == "bonded"
            else (new_group["layout"], tuple(lane["speed"] for lane in new_group["lanes"]))
        )
        if old_choice != new_choice:
            result.append(new_group)
    return result


def topology_live_commands(old_parsed, new_parsed, new_ports):
    """Change only affected EPLs; logical port allocation remains constant."""
    changed = changed_topology_groups(old_parsed, new_parsed)
    commands = []
    for group in changed:
        logicals = group["lane_logical_ports"]
        ports = logical_port_spec(logicals)
        commands.append(f"set port {ports} powerdown")
        commands.append(f"set port config {ports} eth_mode disabled")
        if group["layout"] == "bonded":
            commands.append(
                f"set port config {logicals[0]} eth_mode {BONDED_MODES[group['speed']]}"
            )
        else:
            for lane, logical in zip(group["lanes"], logicals):
                commands.append(
                    f"set port config {logical} eth_mode {SPLIT_MODES[lane['speed']]}"
                )
    commands.extend(port_admin_commands(new_parsed, new_ports))
    return commands


def configuration_export_payload(config):
    _, parsed = parse_platform(config["platform_persistent"])
    return {
        "format": CONFIG_EXPORT_FORMAT,
        "format_version": CONFIG_EXPORT_VERSION,
        "product": "Silicom PE31625G24DIRA",
        "exported_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "manager_version": APP_VERSION,
        "topology": {"groups": topology_choices(parsed)},
        "vlans": load_vlan_config(config, parsed),
        "ports": load_port_config(config, parsed),
        "l2": load_l2_config(config, parsed),
        "fan": load_fan_config(config),
    }


def validate_imported_port_config(body, parsed):
    if not isinstance(body, dict) or not isinstance(body.get("enabled"), dict):
        raise ApiError(400, "备份中的端口开关配置无效")
    expected = {item["key"] for item in topology_endpoints(parsed)}
    raw = body["enabled"]
    if set(raw) != expected or any(not isinstance(value, bool) for value in raw.values()):
        raise ApiError(400, "备份中的端口开关与端口拓扑不匹配")
    return {"version": 1, "enabled": {key: raw[key] for key in sorted(expected)}}


def validate_configuration_import(config, body):
    if not isinstance(body, dict):
        raise ApiError(400, "配置备份格式无效")
    if body.get("format") != CONFIG_EXPORT_FORMAT:
        raise ApiError(400, "不是 PE31625G24DIRA 配置备份")
    if body.get("format_version") != CONFIG_EXPORT_VERSION:
        raise ApiError(400, "不支持的配置备份版本")
    topology = body.get("topology")
    if not isinstance(topology, dict):
        raise ApiError(400, "备份中缺少端口拓扑")
    requested, total, warning = validate_requested(
        config,
        {"groups": topology.get("groups"), "accept_over_guaranteed": True},
    )
    rendered, parsed = generate_platform(
        read_text(config.get("topology_base", config["platform_persistent"])), requested
    )
    vlans = validate_vlan_config(body.get("vlans"), parsed)
    ports = validate_imported_port_config(body.get("ports"), parsed)
    raw_l2 = body.get("l2")
    l2 = (
        validate_l2_config(raw_l2, parsed)
        if raw_l2 is not None
        else default_l2_config([item["key"] for item in topology_endpoints(parsed)])
    )
    fan = validate_fan_config(body.get("fan"))
    return {
        "requested": requested,
        "rendered": rendered,
        "parsed": parsed,
        "vlans": vlans,
        "ports": ports,
        "l2": l2,
        "fan": fan,
        "total": total,
        "warning": warning,
    }


def write_imported_configuration(config, value):
    parsed = value["parsed"]
    for path in (config["platform_persistent"], config["platform_active"]):
        atomic_write(path, value["rendered"], 0o644)
    atomic_write(
        config["vlan_config"],
        json.dumps(value["vlans"], ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        0o600,
    )
    atomic_write(
        config.get("port_config", PORT_CONFIG_PATH),
        json.dumps(value["ports"], ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        0o600,
    )
    atomic_write(
        config.get("l2_config", L2_CONFIG_PATH),
        json.dumps(value["l2"], ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        0o600,
    )
    atomic_write(
        config.get("fan_config", FAN_CONFIG_PATH),
        json.dumps(value["fan"], ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        0o600,
    )
    atomic_write(config["status_script"], status_text(parsed), 0o644)
    atomic_write(
        config.get("fan_init_script", FAN_INIT_SCRIPT),
        render_fan_init(value["fan"]),
        0o644,
    )
    atomic_write(
        config["startup_script"],
        startup_text(parsed, value["vlans"], value["ports"], value["l2"]),
        0o644,
    )


def port_admin_plan(parsed, value):
    normalized = normalize_port_config(value, parsed)
    endpoints = topology_endpoints(parsed)
    enabled_ports = [
        item["logical"]
        for item in endpoints
        if normalized["enabled"][item["key"]]
    ]
    disabled_ports = [
        item["logical"]
        for item in endpoints
        if not normalized["enabled"][item["key"]]
    ]
    optical_targets = []
    for mpo in (1, 2):
        groups = [group for group in parsed["groups"] if group["mpo"] == mpo]
        representative = groups[0]["logical_ports"][0]
        mask = 0
        for group in groups:
            physical = GROUP_BY_KEY[group["key"]]
            for endpoint in endpoints:
                if (
                    endpoint["group"] != group["key"]
                    or not normalized["enabled"][endpoint["key"]]
                ):
                    continue
                lanes = range(4) if endpoint["lane"] is None else (endpoint["lane"],)
                for lane in lanes:
                    mask |= 1 << ((physical["position"] - 1) * 4 + lane)
        optical_targets.append(
            {
                "mpo": mpo,
                "port": representative,
                "mask": mask,
            }
        )
    return enabled_ports, disabled_ports, optical_targets


def port_admin_commands(parsed, value):
    enabled_ports, disabled_ports, _ = port_admin_plan(parsed, value)
    commands = []
    if enabled_ports:
        commands.append(
            "set port {} up".format(",".join(str(port) for port in enabled_ports))
        )
    if disabled_ports:
        commands.append(
            "set port {} powerdown".format(
                ",".join(str(port) for port in disabled_ports)
            )
        )
    return commands


def xcvr_verification_script(parsed, value):
    _, _, optical_targets = port_admin_plan(parsed, value)
    lines = [
        "my $pe_xcvr_chip = $self->{FT}->{CHIP};",
        "my $pe_xcvr_write_verified = sub {",
        "    my ($mpo, $port, $offset, $expected) = @_;",
        "    my $last_status = 0;",
        "    my $last_actual = -1;",
        "    my $initial = [(0) x 1];",
        (
            "    $last_status = $pe_xcvr_chip->fmPlatformXcvrMemRead"
            "(0, $port, 0, $offset, $initial, 1);"
        ),
        "    if ($last_status == 0) { $last_actual = $initial->[0]; }",
        "    if ($last_status == 0 && $last_actual == $expected) {",
        (
            '        printf("PE31625G24DIRA_XCVR_VERIFIED mpo=%d port=%d '
            'offset=%d value=%d attempts=0\\n", $mpo, $port, $offset, $expected);'
        ),
        "        return 1;",
        "    }",
        f"    for my $attempt (1 .. {XCVR_WRITE_MAX_ATTEMPTS}) {{",
        "        my $write_data = [$expected];",
        (
            "        $last_status = $pe_xcvr_chip->fmPlatformXcvrMemWrite"
            "(0, $port, 0, $offset, $write_data, 1);"
        ),
        "        next if $last_status != 0;",
        "        my $read_data = [(0) x 1];",
        (
            "        $last_status = $pe_xcvr_chip->fmPlatformXcvrMemRead"
            "(0, $port, 0, $offset, $read_data, 1);"
        ),
        "        $last_actual = $read_data->[0] if $last_status == 0;",
        "        if ($last_status == 0 && $last_actual == $expected) {",
        (
            '            printf("PE31625G24DIRA_XCVR_VERIFIED mpo=%d port=%d '
            'offset=%d value=%d attempts=%d\\n", '
            "$mpo, $port, $offset, $expected, $attempt);"
        ),
        "            return 1;",
        "        }",
        "    }",
        (
            f'    printf("{XCVR_VERIFY_FAILURE_MARKER} mpo=%d port=%d '
            'offset=%d expected=%d actual=%d status=%d\\n", '
            "$mpo, $port, $offset, $expected, $last_actual, $last_status);"
        ),
        "    return 0;",
        "};",
        "my $pe_xcvr_ok = 1;",
    ]
    for target in optical_targets:
        high = (target["mask"] >> 8) & 0x0F
        low = target["mask"] & 0xFF
        for offset, expected in ((56, high), (57, low)):
            lines.append(
                "$pe_xcvr_ok = $pe_xcvr_write_verified->({}, {}, {}, {}) && $pe_xcvr_ok;".format(
                    target["mpo"], target["port"], offset, expected
                )
            )
    return "\n".join(lines) + "\n"


def port_admin_script(parsed, value):
    return tp_script(port_admin_commands(parsed, value)) + xcvr_verification_script(parsed, value)


def l2_sdk_commands(parsed, value, remove=False):
    """Render commands for WebUI-owned SDK objects (portset 0, storm 200, mirror 15)."""
    value = normalize_l2_config(
        value, [item["key"] for item in topology_endpoints(parsed)]
    )
    commands = []
    if remove:
        if value["mirror"]["enabled"]:
            commands.append("del mirror group 15")
        if value["storm_control"]["enabled"]:
            commands.extend(("del storm-ctrl 200", "del portset 0"))
        return commands
    if value["storm_control"]["enabled"]:
        storm = value["storm_control"]
        commands.extend(
            (
                "create portset",
                f"add portset port 0 {active_logical_spec(parsed)}",
                "create storm-ctrl 200",
                f"set storm-ctrl 200 capacity {storm['burst_bytes']}",
                f"set storm-ctrl 200 rate {storm['rate_kbps']}",
                "add storm-ctrl action 200 filter_portset 0",
                "add storm-ctrl condition 200 broadcast",
                "add storm-ctrl condition 200 multicast",
                "add storm-ctrl condition 200 flood",
            )
        )
    mirror = value["mirror"]
    if mirror["enabled"]:
        by_key = {item["key"]: item for item in topology_endpoints(parsed)}
        source = by_key[mirror["source"]]["logical"]
        destination = by_key[mirror["destination"]]["logical"]
        direction = {"rx": "ingress", "tx": "egress", "both": "bi-directional"}[
            mirror["direction"]
        ]
        commands.extend(
            (
                f"create mirror 15 {destination} {direction}",
                f"add mirror port 15 {source} {direction}",
            )
        )
    return commands


def startup_text(parsed, vlans, port_config=None, l2_config=None):
    ports = active_logical_spec(parsed)
    port_config = port_config or default_port_config(parsed)
    commands = vlan_commands(parsed, vlans, reset=True)
    commands.extend(
        [
            f"set port config {ports} learning on",
            f"set port config {ports} ucast_flooding forward",
            "set mac config mac_age_time 300",
            "set switch config spanning-tree multiple",
            f"set spanning-tree port-state 0 {ports} forwarding",
        ]
    )
    commands.extend(l2_sdk_commands(parsed, l2_config or default_l2_config([])))
    return (
        port_admin_script(parsed, port_config)
        + tp_script(commands).removeprefix("# expert\n")
        + 'if ($pe_xcvr_ok) {\n'
        + f'    if (open(my $switch_ready, ">", "{SWITCH_READY_PATH}")) '
        + '{ print $switch_ready "ready\\n"; close($switch_ready); }\n'
        + '    print "PE31625G24DIRA_SWITCH_READY\\n";\n'
        + '} else {\n'
        + f'    print "ERROR: {XCVR_VERIFY_FAILURE_MARKER}\\n";\n'
        + '}\n'
    )


def status_text(parsed):
    return (
        "# expert\n"
        'tp("show port {0}");\n'
        'tp("show sensors switch");\n'
        'tp("i2c swr 0x59 A3 1");\n'
        'tp("i2c swr 0x59 A4 1");\n'
        f'print "{STATUS_COMPLETE_MARKER}\\n";\n'
    ).format(active_logical_spec(parsed))


def service_state():
    try:
        process = subprocess.run(
            ["/bin/systemctl", "is-active", SERVICE],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return "unknown"
    return process.stdout.strip() or "inactive"


def service_health_payload():
    service = service_state()
    uio_ready = Path("/dev/uio0").exists()
    testpoint_ready = Path(SWITCH_READY_PATH).is_file()
    if service == "active" and uio_ready and testpoint_ready:
        status = "healthy"
    elif service in {"activating", "reloading"} or (
        service == "active" and uio_ready and not testpoint_ready
    ):
        status = "initializing"
    else:
        status = "error"
    return {
        "status": status,
        "service": service,
        "uio_ready": uio_ready,
        "testpoint_ready": testpoint_ready,
    }


def _os_release_name():
    try:
        values = {}
        for line in Path("/etc/os-release").read_text(encoding="utf-8").splitlines():
            if "=" in line:
                key, value = line.split("=", 1)
                values[key] = value.strip().strip('"')
        return values.get("PRETTY_NAME") or values.get("NAME") or "未知"
    except OSError:
        return "未知"


def _ies_sdk_version():
    candidates = []
    for path in Path("/usr/local/rrc/lib").glob("libFocalpointSDK.so.*"):
        match = re.fullmatch(
            r"libFocalpointSDK\.so\.(\d+\.\d+\.\d+(?:_[A-Za-z0-9_]+)?)",
            path.name,
        )
        if match:
            candidates.append(match.group(1))
    return max(candidates, key=len, default="未知")


def _testpoint_version():
    path = Path("/usr/local/rrc/perl/Applications/TestPoint.pm")
    try:
        match = re.search(
            r"\$TestPoint_VERSION\s*=\s*[\"']([^\"']+)",
            path.read_text(encoding="utf-8", errors="replace"),
        )
    except OSError:
        return "未知"
    return match.group(1) if match else "未知"


def _driver_version():
    try:
        output = subprocess.run(
            ["dkms", "status"], capture_output=True, text=True, timeout=5, check=False
        ).stdout
    except (OSError, subprocess.TimeoutExpired):
        output = ""
    match = re.search(r"(?:^|\n)fm10k-uio/([^,\s]+)", output)
    dkms_version = match.group(1) if match else "未知"
    loaded = Path("/sys/module/fm10k").exists() and Path("/dev/uio0").exists()
    module_version = ""
    if loaded:
        try:
            module_version = read_text("/sys/module/fm10k/version").strip()
        except OSError:
            pass
    if not module_version:
        try:
            module_version = subprocess.run(
                ["modinfo", "-F", "version", "fm10k"],
                capture_output=True,
                text=True,
                timeout=3,
                check=False,
            ).stdout.strip()
        except (OSError, subprocess.TimeoutExpired):
            module_version = ""
    return {
        "version": module_version or dkms_version,
        "loaded": loaded,
    }


def system_information_payload():
    usage = shutil.disk_usage("/")
    driver = _driver_version()
    try:
        bios = read_text("/sys/class/dmi/id/bios_version").strip()
    except OSError:
        bios = "未知"
    return {
        "hostname": os.uname()[1],
        "os": _os_release_name(),
        "kernel": os.uname()[2],
        "cpu_model": cpu_model(),
        "bios": bios or "未知",
        "storage": {
            "total": usage.total,
            "used": usage.used,
            "free": usage.free,
            "usage_percent": round(usage.used * 100 / usage.total, 1) if usage.total else 0,
        },
        "components": {
            "manager": APP_VERSION,
            "ies_sdk": _ies_sdk_version(),
            "testpoint": _testpoint_version(),
            "fm10k_uio": driver,
        },
    }


def platform_payload(config):
    _, parsed = parse_platform(config["platform_persistent"])
    vlans = load_vlan_config(config, parsed)
    port_config = load_port_config(config, parsed)
    l2 = load_l2_config(config, parsed)
    admin = port_admin_payload(parsed, port_config)
    health = service_health_payload()
    return {
        "version": APP_VERSION,
        "service": health["service"],
        "service_health": health,
        "system_information": system_information_payload(),
        "groups": parsed["groups"],
        "ports": parsed["ports"],
        "budget": {
            "external": parsed["external"],
            "total": parsed["external"] + INTERNAL_BUDGET,
            "internal": INTERNAL_BUDGET,
            "guaranteed": GUARANTEED_BUDGET,
            "hard": HARD_BUDGET,
        },
        "endpoints": admin["endpoints"],
        "mpo_admin": admin["mpo"],
        "vlans": vlans["vlans"],
        "l2": l2_payload(config, parsed),
        "fan_control": fan_config_payload(config),
        "capabilities": {
            "l3_hardware": True,
            "l3_control_plane": False,
            "roce_forwarding": True,
            "roce_lossless_profile": False,
        },
    }


def decode_port_status(value):
    """Decode the read-only link fields in FM10000 PORT_STATUS."""
    fault = value & 0x7
    rx_link_up = bool(value & (1 << 9))
    return {
        "oper": "UP" if fault == 0 and rx_link_up else "DOWN",
        "fault": ("none", "local", "remote", "reserved")[fault] if fault < 4 else "invalid",
        "rx_link_up": rx_link_up,
        "high_ber": bool(value & (1 << 11)),
        "pcs": (value >> 18) & 0xF,
        "raw": f"0x{value:08x}",
    }


def read_u32(mapped, word_address):
    offset = word_address * 4
    return struct.unpack("<I", mapped[offset : offset + 4])[0]


def read_counter(mapped, word_address, high_mask):
    """Read a rollover-safe little-endian hardware counter split over two words."""
    while True:
        high_before = read_u32(mapped, word_address + 1) & high_mask
        low = read_u32(mapped, word_address)
        high_after = read_u32(mapped, word_address + 1) & high_mask
        if high_before == high_after:
            return (high_after << 32) | low


def rx_counter(mapped, bank, port, counter_type):
    word = (
        FM10000_RX_STATS_BASE + FM10000_RX_STATS_BANK_STRIDE * bank + 4 * (port * 16 + counter_type)
    )
    return {
        "frames": read_counter(mapped, word, 0xFFFF),
        "bytes": read_counter(mapped, word + 2, 0xFFFFFF),
    }


def tx_counter(mapped, bank, port, counter_type):
    index = port * 16 + counter_type
    frame_word = FM10000_TX_STATS_BASE + 0x800 * bank + FM10000_TX_FRAME_OFFSET + 2 * index
    byte_word = FM10000_TX_STATS_BASE + 0x800 * bank + FM10000_TX_BYTE_OFFSET + 2 * index
    return {
        "frames": read_counter(mapped, frame_word, 0xFFFF),
        "bytes": read_counter(mapped, byte_word, 0xFFFFFF),
    }


def counter_frames(values, indexes):
    return sum(values[index]["frames"] for index in indexes)


def counter_bytes(values, indexes):
    return sum(values[index]["bytes"] for index in indexes)


def port_statistics(mapped, logical, physical, lane):
    rx_type = [rx_counter(mapped, 0, logical, index) for index in range(13)]
    rx_length = [rx_counter(mapped, 1, logical, index) for index in range(12)]
    rx_action4 = [rx_counter(mapped, 3, logical, index) for index in range(16)]
    rx_action5 = [rx_counter(mapped, 4, logical, index) for index in range(16)]
    tx_type = [tx_counter(mapped, 0, logical, index) for index in range(11)]
    tx_length = [tx_counter(mapped, 1, logical, index) for index in range(12)]
    mac_base = FM10000_EPL_BASE + FM10000_EPL_STRIDE * physical["epl"] + FM10000_PORT_STRIDE * lane
    mac_names = (
        "oversize",
        "jabber",
        "undersize",
        "runt",
        "overrun",
        "underrun",
        "code_errors",
        "tx_frame_errors",
    )
    mac = {name: read_u32(mapped, mac_base + 0x21 + index) for index, name in enumerate(mac_names)}
    link_counter = read_u32(mapped, mac_base + 0x29)
    mac["link_events"] = {
        "up": link_counter & 0xFFF,
        "local_fault": (link_counter >> 12) & 0x3FF,
        "remote_fault": (link_counter >> 22) & 0x3FF,
    }
    good_rx = tuple(range(9))
    good_tx = (0, 1, 2)
    return {
        "rx": {
            "frames": counter_frames(rx_type, good_rx),
            "good_bytes": counter_bytes(rx_type, good_rx),
            "bad_bytes": counter_bytes(rx_type, (11, 12)),
            "unicast": counter_frames(rx_type, (0, 3, 6)),
            "multicast": counter_frames(rx_type, (1, 4, 7)),
            "broadcast": counter_frames(rx_type, (2, 5, 8)),
            "pause": counter_frames(rx_type, (9,)),
            "pfc_pause": counter_frames(rx_type, (10,)),
            "framing_errors": rx_type[11]["frames"],
            "fcs_errors": rx_type[12]["frames"],
            "length": {
                label: rx_length[index]["frames"] for index, label in enumerate(RX_LENGTH_LABELS)
            },
            "actions": {
                label: rx_action4[index]["frames"] for index, label in enumerate(RX_DROP_BANK4)
            },
            "drops": {
                label: rx_action5[index]["frames"] for index, label in enumerate(RX_DROP_BANK5)
            },
            "mac": mac,
        },
        "tx": {
            "frames": counter_frames(tx_type, good_tx),
            "good_bytes": counter_bytes(tx_type, good_tx),
            "bad_bytes": tx_type[3]["bytes"],
            "unicast": tx_type[0]["frames"],
            "multicast": tx_type[1]["frames"],
            "broadcast": tx_type[2]["frames"],
            "bad_fcs": tx_type[3]["frames"],
            "timeout_drops": tx_type[4]["frames"],
            "error_drops": tx_type[5]["frames"],
            "ecc_drops": tx_type[6]["frames"],
            "loopback_drops": tx_type[7]["frames"],
            "ttl_drops": tx_type[8]["frames"],
            "pause": tx_type[9]["frames"],
            "pfc_pause": tx_type[10]["frames"],
            "length": {
                label: tx_length[index]["frames"] for index, label in enumerate(TX_LENGTH_LABELS)
            },
        },
    }


def switch_rate_sample(state, now, ports):
    current = {
        key: (
            value["statistics"]["rx"]["good_bytes"],
            value["statistics"]["tx"]["good_bytes"],
        )
        for key, value in ports.items()
    }
    with state.telemetry_lock:
        previous = state.switch_sample
        state.switch_sample = (now, current)
    for value in ports.values():
        value["rx_bps"] = None
        value["tx_bps"] = None
    if not previous or now <= previous[0]:
        return
    elapsed = now - previous[0]
    for key, value in ports.items():
        before = previous[1].get(key)
        current_pair = current[key]
        if before and current_pair[0] >= before[0] and current_pair[1] >= before[1]:
            value["rx_bps"] = int((current_pair[0] - before[0]) * 8 / elapsed)
            value["tx_bps"] = int((current_pair[1] - before[1]) * 8 / elapsed)


def aggregate_switch_statistics(ports):
    return {
        "rx_bps": sum(value.get("rx_bps") or 0 for value in ports.values())
        if any(value.get("rx_bps") is not None for value in ports.values())
        else None,
        "tx_bps": sum(value.get("tx_bps") or 0 for value in ports.values())
        if any(value.get("tx_bps") is not None for value in ports.values())
        else None,
        "rx_bytes": sum(value["statistics"]["rx"]["good_bytes"] for value in ports.values()),
        "tx_bytes": sum(value["statistics"]["tx"]["good_bytes"] for value in ports.values()),
        "rx_frames": sum(value["statistics"]["rx"]["frames"] for value in ports.values()),
        "tx_frames": sum(value["statistics"]["tx"]["frames"] for value in ports.values()),
        "rx_errors": sum(
            value["statistics"]["rx"]["framing_errors"] + value["statistics"]["rx"]["fcs_errors"]
            for value in ports.values()
        ),
        "tx_discards": sum(
            value["statistics"]["tx"][name]
            for value in ports.values()
            for name in (
                "timeout_drops",
                "error_drops",
                "ecc_drops",
                "loopback_drops",
                "ttl_drops",
            )
        ),
        "port_count": len(ports),
    }


def direct_port_payload(config, state=None):
    """Read link state and RMON statistics directly from FM10840 BAR4 via UIO."""
    _, parsed = parse_platform(config["platform_persistent"])
    port_config = load_port_config(config, parsed)
    enabled_by_key = port_config["enabled"]
    device = config.get("uio_device", "/dev/uio0")
    addresses = []
    for port in parsed["ports"]:
        physical = GROUP_BY_KEY[port["group"]]
        lane = port["lane"] if port["lane"] is not None else 0
        register = (
            FM10000_EPL_BASE + FM10000_EPL_STRIDE * physical["epl"] + FM10000_PORT_STRIDE * lane
        )
        addresses.append((port, physical, lane, register * 4))
    max_port = max(port["logical"] for port in parsed["ports"])
    last_tx_word = (
        FM10000_TX_STATS_BASE + 0x800 + FM10000_TX_BYTE_OFFSET + 2 * (max_port * 16 + 11) + 1
    )
    required = max(max(item[3] for item in addresses) + 4, (last_tx_word + 1) * 4)
    page = getattr(mmap, "PAGESIZE", 4096)
    length = ((required + page - 1) // page) * page
    fd = os.open(device, os.O_RDWR)
    mapped = None
    try:
        mapped = mmap.mmap(fd, length, flags=mmap.MAP_SHARED, prot=mmap.PROT_READ, offset=0)
        ports = {}
        for port, physical, lane, offset in addresses:
            value = struct.unpack("<I", mapped[offset : offset + 4])[0]
            decoded = decode_port_status(value)
            decoded.update(
                {
                    "speed": "{}G".format(port["speed"] // 1000),
                    "type": port["ethernet_mode"],
                    "admin": "UP" if enabled_by_key.get(endpoint_key(port), True) else "OFF",
                    "epl": physical["epl"],
                    "lane": lane,
                    "statistics": port_statistics(mapped, port["logical"], physical, lane),
                }
            )
            ports[str(port["logical"])] = decoded
        now = time.time()
        if state is not None:
            switch_rate_sample(state, now, ports)
        else:
            for value in ports.values():
                value["rx_bps"] = value["tx_bps"] = None
        return {
            "state": "ready",
            "source": "uio-rmon",
            "sampled": int(now),
            "ports": ports,
            "traffic": aggregate_switch_statistics(ports),
        }
    finally:
        if mapped is not None:
            mapped.close()
        os.close(fd)


def direct_link_states(config):
    """Read only external-port link state without walking the RMON banks."""
    _, parsed = parse_platform(config["platform_persistent"])
    device = config.get("uio_device", "/dev/uio0")
    addresses = []
    for port in parsed["ports"]:
        physical = GROUP_BY_KEY[port["group"]]
        lane = port["lane"] if port["lane"] is not None else 0
        register = (
            FM10000_EPL_BASE
            + FM10000_EPL_STRIDE * physical["epl"]
            + FM10000_PORT_STRIDE * lane
        )
        addresses.append((port["logical"], register * 4))
    required = max(offset for _, offset in addresses) + 4
    page = getattr(mmap, "PAGESIZE", 4096)
    length = ((required + page - 1) // page) * page
    fd = os.open(device, os.O_RDWR)
    mapped = None
    try:
        mapped = mmap.mmap(fd, length, flags=mmap.MAP_SHARED, prot=mmap.PROT_READ, offset=0)
        return {
            logical: decode_port_status(struct.unpack("<I", mapped[offset : offset + 4])[0])[
                "oper"
            ]
            == "UP"
            for logical, offset in addresses
        }
    finally:
        if mapped is not None:
            mapped.close()
        os.close(fd)


def file_value(path, default=None):
    try:
        return read_text(path).strip()
    except OSError:
        return default


def parse_vpd_identity(data, subsystem_vendor=None):
    """Decode the stable identity strings stored in the FM10840 PCI VPD."""
    strings = [
        value.decode("ascii").strip()
        for value in re.findall(rb"[\x20-\x7e]{4,}", data)
        if value.strip()
    ]
    model = next(
        (value for value in strings if re.fullmatch(r"PE31625G24DIRA(?:-MPS)?", value, re.I)),
        None,
    )
    model = model.upper() if model else None
    vpd_version = next((value for value in strings if re.fullmatch(r"\d{4}", value)), None)
    serial = next((value for value in strings if re.fullmatch(r"S\d{10,}", value)), None)
    vendor = "Silicom" if (subsystem_vendor or "").lower() == "0x1374" else None

    hardware_family = None
    hw_version = None
    if vendor and vpd_version:
        significant = vpd_version.lstrip("0")
        if significant and significant[0].isdigit():
            if int(significant[0]) >= 6:
                hardware_family = "Silicom A11"
                hw_version = 5
            else:
                hardware_family = "Silicom B0"
                hw_version = 4

    return {
        "vendor": vendor,
        "model": model,
        "display_model": " ".join(value for value in (vendor, model) if value) or None,
        "vpd_version": vpd_version,
        "serial": serial,
        "hardware_family": hardware_family,
        "hw_version": hw_version,
    }


def fm10840_pci_device():
    candidates = []
    for path in sorted(Path("/sys/bus/pci/devices").glob("*")):
        if file_value(path / "vendor", "").lower() != "0x8086":
            continue
        if file_value(path / "device", "").lower() != "0x15a4":
            continue
        candidates.append(path)
    return next(
        (
            path
            for path in candidates
            if file_value(path / "subsystem_vendor", "").lower() == "0x1374"
            and file_value(path / "subsystem_device", "").lower() == "0x01d0"
        ),
        candidates[0] if candidates else None,
    )


def configured_platform_name(path):
    try:
        match = re.search(
            r"^api\.platform\.config\.platformName\s+text\s+(\S+)\s*$",
            read_text(path),
            re.MULTILINE,
        )
    except (OSError, TypeError):
        return None
    return match.group(1) if match else None


@functools.lru_cache(maxsize=4)
def hardware_identity_payload(platform_path):
    device = fm10840_pci_device()
    identity = parse_vpd_identity(b"")
    if device is not None:
        try:
            identity = parse_vpd_identity(
                (device / "vpd").read_bytes(),
                file_value(device / "subsystem_vendor"),
            )
        except OSError:
            identity["vendor"] = (
                "Silicom"
                if file_value(device / "subsystem_vendor", "").lower() == "0x1374"
                else None
            )
        identity["pci_address"] = device.name
    else:
        identity["pci_address"] = None
    identity["platform"] = configured_platform_name(platform_path)
    return identity


def cpu_model():
    for line in read_text("/proc/cpuinfo").splitlines():
        if line.lower().startswith("model name"):
            return line.split(":", 1)[1].strip()
    return "Unknown CPU"


def cpu_totals():
    fields = read_text("/proc/stat").splitlines()[0].split()[1:]
    values = [int(value) for value in fields]
    idle = values[3] + (values[4] if len(values) > 4 else 0)
    return sum(values), idle


def sample_cpu(state):
    now = cpu_totals()
    with state.telemetry_lock:
        previous = state.cpu_sample
        state.cpu_sample = now
    usage = None
    if previous:
        total_delta = now[0] - previous[0]
        idle_delta = now[1] - previous[1]
        if total_delta > 0:
            usage = round(
                max(0.0, min(100.0, (total_delta - idle_delta) * 100.0 / total_delta)),
                1,
            )
    load = os.getloadavg()
    return {
        "model": cpu_model(),
        "cores": os.cpu_count() or 1,
        "usage_percent": usage,
        "load": [round(value, 2) for value in load],
    }


def memory_payload():
    values = {}
    for line in read_text("/proc/meminfo").splitlines():
        name, raw = line.split(":", 1)
        values[name] = int(raw.strip().split()[0]) * 1024
    total = values.get("MemTotal", 0)
    available = values.get(
        "MemAvailable",
        values.get("MemFree", 0) + values.get("Buffers", 0) + values.get("Cached", 0),
    )
    used = max(0, total - available)
    return {
        "total": total,
        "used": used,
        "available": available,
        "usage_percent": round(used * 100.0 / total, 1) if total else None,
    }


def describe_linux_temperature(chip, label):
    """Return stable, user-facing meaning without inventing board locations."""
    lowered = label.lower()
    if chip == "coretemp" or "core" in lowered:
        core = re.search(r"core\s+(\d+)", label, re.IGNORECASE)
        suffix = core.group(1) if core else label
        return {
            "category": "cpu-core",
            "display_label": f"CPU Core {suffix}",
        }
    if chip in ("soc_dts0", "soc_dts1"):
        index = chip[-1]
        return {
            "category": "soc",
            "display_label": f"SoC DTS {index}",
        }
    if chip == "acpitz":
        return {
            "category": "acpi",
            "display_label": "ACPI TZ01",
        }
    return {
        "category": "board",
        "display_label": label or chip,
    }


def temperature_payload():
    sensors = []
    for root in sorted(glob.glob("/sys/class/hwmon/hwmon*")):
        chip = file_value(os.path.join(root, "name"), "hwmon")
        for path in sorted(glob.glob(os.path.join(root, "temp*_input"))):
            raw = file_value(path)
            if raw is None:
                continue
            stem = path[:-6]
            label = file_value(stem + "_label", os.path.basename(stem))
            try:
                value = round(float(raw) / 1000.0, 1)
            except ValueError:
                continue
            meaning = describe_linux_temperature(chip, label)
            sensors.append(
                {
                    "chip": chip,
                    "label": label,
                    "display_label": meaning["display_label"],
                    "celsius": value,
                    "category": meaning["category"],
                }
            )
    return sensors


def fan_payload(switch_sensors=None):
    fans = []
    for root in sorted(glob.glob("/sys/class/hwmon/hwmon*")):
        chip = file_value(os.path.join(root, "name"), "hwmon")
        for path in sorted(glob.glob(os.path.join(root, "fan*_input"))):
            match = re.search(r"fan(\d+)_input$", path)
            raw = file_value(path)
            if not match or raw is None:
                continue
            index = match.group(1)
            try:
                rpm = int(float(raw))
            except ValueError:
                continue
            label = file_value(os.path.join(root, f"fan{index}_label"), f"Fan {index}")
            pwm_raw = file_value(os.path.join(root, f"pwm{index}"))
            try:
                pwm = int(pwm_raw) if pwm_raw is not None else None
            except ValueError:
                pwm = None
            fans.append({"chip": chip, "label": label, "rpm": rpm, "pwm": pwm})
    if fans:
        return {"state": "ready", "source": "hwmon", "fans": fans}
    if switch_sensors and switch_sensors.get("fans"):
        return {
            "state": "ready",
            "source": "cpld-lm96163",
            "fans": switch_sensors["fans"],
        }
    return {"state": "not-detected", "source": "hwmon", "fans": []}


def ip_lines(family, interface):
    process = subprocess.Popen(
        ["/sbin/ip", "-o", family, "addr", "show", "dev", interface],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    stdout, _ = process.communicate()
    values = []
    token = "inet" if family == "-4" else "inet6"
    for line in stdout.decode("utf-8", "replace").splitlines():
        parts = line.split()
        if token in parts:
            values.append(parts[parts.index(token) + 1])
    return values


def management_interfaces(config):
    explicit = config.get("management_interfaces")
    if isinstance(explicit, list) and explicit:
        return [str(name) for name in explicit if os.path.isdir(f"/sys/class/net/{name}")]
    preferred = config.get("management_interface", "enp2s0")
    candidates = []
    for root in sorted(glob.glob("/sys/class/net/*")):
        interface = os.path.basename(root)
        driver_link = os.path.join(root, "device", "driver")
        driver = (
            os.path.basename(os.path.realpath(driver_link)) if os.path.exists(driver_link) else ""
        )
        pci_device = (file_value(os.path.join(root, "device", "device"), "") or "").lower()
        if interface == preferred or (driver == "igb" and pci_device == "0x1539"):
            candidates.append(interface)
    if preferred in candidates:
        candidates.remove(preferred)
        candidates.insert(0, preferred)
    return candidates


def interface_network_payload(interface, previous, now):
    root = f"/sys/class/net/{interface}"
    stats = {}
    for name in (
        "rx_bytes",
        "tx_bytes",
        "rx_packets",
        "tx_packets",
        "rx_errors",
        "tx_errors",
        "rx_dropped",
        "tx_dropped",
    ):
        try:
            stats[name] = int(file_value(os.path.join(root, "statistics", name), "0"))
        except ValueError:
            stats[name] = 0
    rx_bps = tx_bps = None
    if previous and now > previous[0]:
        elapsed = now - previous[0]
        if stats["rx_bytes"] >= previous[1]:
            rx_bps = int((stats["rx_bytes"] - previous[1]) * 8 / elapsed)
        if stats["tx_bytes"] >= previous[2]:
            tx_bps = int((stats["tx_bytes"] - previous[2]) * 8 / elapsed)
    route = subprocess.Popen(
        ["/sbin/ip", "route", "show", "default", "dev", interface],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    route_out, _ = route.communicate()
    gateway = None
    match = re.search(r"\bvia\s+(\S+)", route_out.decode("utf-8", "replace"))
    if match:
        gateway = match.group(1)
    try:
        speed = int(file_value(os.path.join(root, "speed"), "0") or 0)
    except ValueError:
        speed = 0
    return {
        "interface": interface,
        "state": file_value(os.path.join(root, "operstate"), "unknown"),
        "carrier": file_value(os.path.join(root, "carrier"), "0") == "1",
        "speed_mbps": max(0, speed),
        "duplex": (file_value(os.path.join(root, "duplex"), "unknown") or "unknown").lower(),
        "mac": file_value(os.path.join(root, "address")),
        "mtu": int(file_value(os.path.join(root, "mtu"), "0") or 0),
        "ipv4": ip_lines("-4", interface),
        "ipv6": ip_lines("-6", interface),
        "gateway": gateway,
        "rx_bps": rx_bps,
        "tx_bps": tx_bps,
        "statistics": stats,
    }


def network_payload(state):
    names = management_interfaces(state.config)
    now = time.time()
    with state.telemetry_lock:
        previous = dict(state.net_sample)
    interfaces = [interface_network_payload(name, previous.get(name), now) for name in names]
    with state.telemetry_lock:
        state.net_sample = {
            item["interface"]: (
                now,
                item["statistics"]["rx_bytes"],
                item["statistics"]["tx_bytes"],
            )
            for item in interfaces
        }
    connected = sum(1 for item in interfaces if item["state"] == "up" and item["carrier"])
    preferred = state.config.get("management_interface", "enp2s0")
    primary = next(
        (item["interface"] for item in interfaces if item["interface"] == preferred),
        interfaces[0]["interface"] if interfaces else None,
    )
    return {
        "interfaces": interfaces,
        "connected": connected,
        "total": len(interfaces),
        "primary": primary,
    }


def telemetry_payload(state):
    try:
        uptime = int(float(read_text("/proc/uptime").split()[0]))
    except Exception:
        uptime = None
    with state.telemetry_lock:
        switch_sensors = dict(state.sensor_cache)
        optics_diagnostic = dict(state.optics_cache)
    try:
        port_status = direct_port_payload(state.config, state)
    except Exception as exc:
        port_status = {
            "state": "error",
            "source": "uio-port-status",
            "sampled": int(time.time()),
            "ports": {},
            "error": str(exc),
        }
    return {
        "sampled": int(time.time()),
        "hostname": os.uname()[1],
        "kernel": os.uname()[2],
        "hardware_identity": hardware_identity_payload(state.config.get("platform_active")),
        "uptime_seconds": uptime,
        "cpu": sample_cpu(state),
        "memory": memory_payload(),
        "temperatures": temperature_payload(),
        "fans": fan_payload(switch_sensors),
        "management": network_payload(state),
        "switch_sensors": switch_sensors,
        "optics_diagnostic": optics_diagnostic,
        "port_status": port_status,
    }


def parse_switch_sensors(output):
    temperatures = []
    voltages = []
    for match in SENSOR_TEMP_RE.finditer(output):
        name = match.group(1)
        if name == "MAIN TEMP SENSOR":
            sensor_index = 0
        else:
            sensor_index = int(re.search(r"(\d+)$", name).group(1)) + 1
        metadata = (
            FM10840_TEMPERATURES[sensor_index]
            if sensor_index < len(FM10840_TEMPERATURES)
            else {
                "label": f"未知测点 #{sensor_index}",
                "location": "Not documented",
                "category": "unknown",
                "documented": False,
            }
        )
        temperatures.append(
            {
                "name": name,
                "sensor_index": sensor_index,
                "label": metadata["label"],
                "location": metadata["location"],
                "category": metadata["category"],
                "documented": metadata["documented"],
                "accuracy_c": 5,
                "celsius": float(match.group(2)),
            }
        )
    for match in SENSOR_VOLT_RE.finditer(output):
        voltages.append({"name": match.group(1), "volts": float(match.group(2))})
    if len(temperatures) < 1:
        raise RuntimeError("没有解析到 FM10840 温度")
    fans = []
    lsb_matches = FAN_TACH_LSB_RE.findall(output)
    msb_matches = FAN_TACH_MSB_RE.findall(output)
    if lsb_matches and msb_matches:
        tach_count = (int(msb_matches[-1], 16) << 8) | int(lsb_matches[-1], 16)
        signal = tach_count not in (0, 0xFFFF)
        rpm = 0x5265C0 // tach_count if signal else 0
        fans.append(
            {
                "chip": "lm96163",
                "label": "System Fan",
                "rpm": rpm,
                "pwm": None,
                "tach_count": tach_count,
                "signal": signal,
            }
        )
    return {
        "state": "ready",
        "sampled": int(time.time()),
        "temperatures": temperatures,
        "voltages": voltages,
        "fans": fans,
    }


def optical_dbm(microwatts):
    if microwatts is None or microwatts <= 0:
        return None
    return round(10.0 * math.log10(microwatts / 1000.0), 2)


def parse_optics_temperatures(output):
    records = {}
    for match in OPTICS_TEMPERATURE_RE.finditer(output):
        raw_value = int(match.group(3), 16)
        signed_value = raw_value - 0x10000 if raw_value & 0x8000 else raw_value
        temperature_c = signed_value / 256.0
        status = int(match.group(2))
        valid = status == 0 and -40.0 <= temperature_c <= 125.0
        records[int(match.group(1))] = {
            "mpo": int(match.group(1)),
            "temperature_c": round(temperature_c, 2) if valid else None,
            "temperature_raw": raw_value,
            "temperature_status": status,
        }
    modules = [
        records.get(
            mpo,
            {
                "mpo": mpo,
                "temperature_c": None,
                "temperature_raw": None,
                "temperature_status": None,
            },
        )
        for mpo in (1, 2)
    ]
    readable = sum(module["temperature_c"] is not None for module in modules)
    return {
        "state": "ready" if readable == 2 else "partial" if readable else "error",
        "modules": modules,
    }


def parse_hardware_sensors(output):
    sensors = parse_switch_sensors(output)
    optics = parse_optics_temperatures(output)
    optics["sampled"] = sensors["sampled"]
    sensors["optics"] = optics
    return sensors


def preserve_optics_temperatures(current, *fallbacks):
    """Keep the newest valid value for each module without exposing stale state."""
    result = dict(current or {})
    modules = {
        int(module["mpo"]): dict(module)
        for module in result.get("modules", [])
    }
    for fallback in fallbacks:
        for module in (fallback or {}).get("modules", []):
            mpo = int(module["mpo"])
            existing = modules.get(mpo)
            if (
                (not existing or existing.get("temperature_c") is None)
                and module.get("temperature_c") is not None
            ):
                modules[mpo] = dict(module)
    result["modules"] = [
        modules.get(
            mpo,
            {
                "mpo": mpo,
                "temperature_c": None,
                "temperature_raw": None,
                "temperature_status": None,
            },
        )
        for mpo in (1, 2)
    ]
    readable = sum(module.get("temperature_c") is not None for module in result["modules"])
    result["state"] = "ready" if readable == 2 else "partial" if readable else "error"
    return result


def refresh_sensor_cache(state):
    script = state.config.get("sensor_script", "/etc/pe31625g24dira/webui/sensors.tp")
    with state.telemetry_lock:
        previous_optics = state.sensor_cache.get("optics", {})

    def read_sensors():
        return parse_hardware_sensors(
            queue_testpoint_script(
                state.config, script, SENSOR_COMPLETE_MARKER, timeout=20
            )
        )

    sensors = read_sensors()
    first_optics = sensors["optics"]
    if any(module.get("temperature_c") is None for module in first_optics["modules"]):
        try:
            retry = read_sensors()
        except Exception:
            sensors["optics"] = preserve_optics_temperatures(
                first_optics, previous_optics
            )
        else:
            retry["optics"] = preserve_optics_temperatures(retry["optics"], first_optics)
            sensors = retry
    sensors["optics"] = preserve_optics_temperatures(
        sensors["optics"], previous_optics
    )
    with state.telemetry_lock:
        state.sensor_cache = sensors
    return sensors


def store_sensor_error(state, exc):
    with state.telemetry_lock:
        state.sensor_cache = {
            "state": "error",
            "sampled": int(time.time()),
            "temperatures": [],
            "voltages": [],
            "optics": {"state": "error", "modules": []},
            "error": str(exc),
        }


def sensor_refresh_job_worker(state, job_id):
    try:
        state.update_job(job_id, state="running", message="刷新板卡传感器")
        refresh_sensor_cache(state)
        state.update_job(job_id, state="done", message="板卡传感器已刷新")
    except Exception as exc:
        store_sensor_error(state, exc)
        state.update_job(job_id, state="failed", message="板卡传感器刷新失败", error=str(exc))


def maybe_refresh_sensors(state, max_age=30):
    with state.telemetry_lock:
        sampled = state.sensor_cache.get("sampled")
        current_state = state.sensor_cache.get("state")
    if current_state == "pending" or not sampled or time.time() - sampled > max_age:
        state.start_operation(
            "sensor-refresh",
            sensor_refresh_job_worker,
            priority=20,
            coalesce_key="sensor-refresh",
        )


def backup_targets():
    return (
        ("platform_active", "fm_platform_attributes.cfg", None),
        ("platform_persistent", "fm_platform_attributes_pe31625g24dira.cfg", None),
        ("startup_script", "pe31625g24dira-switch.tp", None),
        ("status_script", "status.tp", None),
        ("vlan_config", "vlans.json", "/etc/pe31625g24dira/webui/vlans.json"),
        ("fan_config", "fan.json", FAN_CONFIG_PATH),
        ("port_config", "ports.json", PORT_CONFIG_PATH),
        ("l2_config", "l2.json", L2_CONFIG_PATH),
        ("fan_init_script", "pe31625g24dira-fan-init.tp", FAN_INIT_SCRIPT),
    )


def make_backup(config):
    root = config["backup_root"]
    if not os.path.isdir(root):
        os.makedirs(root)
    destination = os.path.join(root, time.strftime("%Y%m%d-%H%M%S"))
    suffix = 0
    while os.path.exists(destination):
        suffix += 1
        destination = os.path.join(root, time.strftime("%Y%m%d-%H%M%S") + f"-{suffix}")
    os.makedirs(destination)
    os.chmod(destination, 0o700)
    for key, name, default in backup_targets():
        path = config.get(key, default)
        if path and os.path.exists(path):
            shutil.copy2(path, os.path.join(destination, name))
    return destination


def restore_backup(config, backup):
    for key, name, default in backup_targets():
        source = os.path.join(backup, name)
        target = config.get(key, default)
        if target and os.path.exists(source):
            shutil.copy2(source, target)


def prepare_requested(config, requested):
    _, old_parsed = parse_platform(config["platform_persistent"])
    current_vlans = load_vlan_config(config, old_parsed)
    current_ports = load_port_config(config, old_parsed)
    current_l2 = load_l2_config(config, old_parsed)
    rendered, parsed = generate_platform(read_text(config["topology_base"]), requested)
    next_vlans = reconcile_vlan_config(current_vlans, old_parsed, parsed)
    next_ports = reconcile_port_config(current_ports, old_parsed, parsed)
    next_l2 = reconcile_l2_config(current_l2, parsed)
    return {
        "old_parsed": old_parsed,
        "old_vlans": current_vlans,
        "old_ports": current_ports,
        "old_l2": current_l2,
        "rendered": rendered,
        "parsed": parsed,
        "vlans": next_vlans,
        "ports": next_ports,
        "l2": next_l2,
    }


def persist_requested(config, prepared):
    parsed = prepared["parsed"]
    next_vlans = prepared["vlans"]
    next_ports = prepared["ports"]
    next_l2 = prepared["l2"]
    for path in (config["platform_persistent"], config["platform_active"]):
        atomic_write(path, prepared["rendered"], 0o644)
    atomic_write(
        config["startup_script"],
        startup_text(parsed, next_vlans, next_ports, next_l2),
        0o644,
    )
    atomic_write(config["status_script"], status_text(parsed), 0o644)
    atomic_write(
        config.get("vlan_config", "/etc/pe31625g24dira/webui/vlans.json"),
        json.dumps(next_vlans, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        0o600,
    )
    atomic_write(
        config.get("port_config", PORT_CONFIG_PATH),
        json.dumps(next_ports, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        0o600,
    )
    atomic_write(
        config.get("l2_config", L2_CONFIG_PATH),
        json.dumps(next_l2, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        0o600,
    )
    return parsed


def write_requested(config, requested):
    prepared = prepare_requested(config, requested)
    persist_requested(config, prepared)
    return prepared["parsed"]


def write_factory_configuration(config):
    """Restore product defaults without touching the host OS or management networking."""
    base_platform = read_text(config["topology_base"])
    factory_topology = {
        group["key"]: {"layout": "bonded", "speed": 100000}
        for group in GROUPS
    }
    platform, parsed = generate_platform(base_platform, factory_topology)
    vlans = default_vlan_config(parsed)
    ports = default_port_config(parsed)
    l2 = default_l2_config([item["key"] for item in topology_endpoints(parsed)])
    fan = default_fan_config()
    for path in (config["platform_persistent"], config["platform_active"]):
        atomic_write(path, platform, 0o644)
    for path, value in (
        (config.get("vlan_config", "/etc/pe31625g24dira/webui/vlans.json"), vlans),
        (config.get("port_config", PORT_CONFIG_PATH), ports),
        (config.get("l2_config", L2_CONFIG_PATH), l2),
        (config.get("fan_config", FAN_CONFIG_PATH), fan),
    ):
        atomic_write(
            path,
            json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            0o600,
        )
    atomic_write(
        config["startup_script"], startup_text(parsed, vlans, ports, l2), 0o600
    )
    atomic_write(config["status_script"], status_text(parsed), 0o600)
    atomic_write(
        config.get("fan_init_script", FAN_INIT_SCRIPT), render_fan_init(fan), 0o600
    )
    return parsed


def clear_admin_credentials(state):
    with state.config_lock:
        updated = dict(state.config)
        updated["initialized"] = False
        for key in ("username", "password_salt", "password_rounds", "password_hash"):
            updated.pop(key, None)
        atomic_write(
            state.config_path,
            json.dumps(updated, indent=2, sort_keys=True) + "\n",
            0o600,
        )
        state.config = updated


def run_systemctl(action):
    return subprocess.call(["/bin/systemctl", action, SERVICE])


def validate_poweroff_request(body):
    if not isinstance(body, dict) or body.get("confirm") is not True:
        raise ApiError(400, "请确认关闭系统")
    return True


def system_log_command(source):
    commands = {
        "system": ["/bin/journalctl", "-b", "--no-pager", "-n", "500", "-o", "short-iso"],
        # Expose the raw kernel ring buffer, including the boot-relative
        # timestamps also shown on the local console.
        "kernel": ["/usr/bin/dmesg", "--color=never"],
        "switch": ["/bin/journalctl", "-b", "-u", SERVICE, "--no-pager", "-n", "500", "-o", "short-iso"],
    }
    if source not in commands:
        raise ApiError(400, "日志来源无效")
    return commands[source]


def system_log_payload(source):
    command = system_log_command(source)
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=5, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ApiError(503, f"日志读取失败: {exc}") from None
    content = clean_service_log(result.stdout)
    if result.returncode and not content:
        content = clean_service_log(result.stderr) or "没有可显示的日志。"
    return {
        "source": source,
        "content": content[-524288:],
        "line_count": len(content.splitlines()),
        "sampled": int(time.time()),
    }


TIMEZONE_ROOT = Path("/usr/share/zoneinfo")


def current_timezone():
    try:
        target = Path("/etc/localtime").resolve()
        return target.relative_to(TIMEZONE_ROOT.resolve()).as_posix()
    except (OSError, ValueError):
        pass
    try:
        value = Path("/etc/timezone").read_text(encoding="utf-8").strip()
        if value:
            return value
    except OSError:
        pass
    return "UTC"


def valid_timezone(value):
    if not value or len(value) > 128:
        return False
    relative = PurePosixPath(value)
    if relative.is_absolute() or any(part in {"", ".", ".."} for part in relative.parts):
        return False
    try:
        target = (TIMEZONE_ROOT / Path(*relative.parts)).resolve()
        target.relative_to(TIMEZONE_ROOT.resolve())
        return target.is_file()
    except (OSError, ValueError):
        return False


def set_system_timezone(value):
    target = (TIMEZONE_ROOT / Path(*PurePosixPath(value).parts)).resolve()
    temporary = Path("/etc") / f".pe31625g24dira-localtime-{os.getpid()}"
    with suppress(OSError):
        temporary.unlink()
    try:
        os.symlink(target, temporary)
        os.replace(temporary, "/etc/localtime")
        atomic_write("/etc/timezone", f"{value}\n", 0o644)
    except Exception:
        with suppress(OSError):
            temporary.unlink()
        raise


@functools.lru_cache(maxsize=1)
def available_timezones():
    zones = {"UTC"}
    for table_name in ("zone.tab", "zone1970.tab"):
        try:
            lines = (TIMEZONE_ROOT / table_name).read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in lines:
            if not line or line.startswith("#"):
                continue
            fields = line.split("\t")
            if len(fields) >= 3 and valid_timezone(fields[2]):
                zones.add(fields[2])
    current = current_timezone()
    if valid_timezone(current):
        zones.add(current)
    return sorted(zones, key=lambda item: (item.split("/", 1)[0], item.casefold()))


def system_settings_payload():
    return {
        "hostname": os.uname().nodename,
        "timezone": current_timezone(),
        "timezones": available_timezones(),
    }


def apply_system_settings(body):
    if not isinstance(body, dict):
        raise ApiError(400, "系统设置无效")
    hostname = str(body.get("hostname", "")).strip()
    if not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9.-]{0,61}[A-Za-z0-9])?", hostname):
        raise ApiError(400, "主机名格式无效")
    timezone = str(body["timezone"] if "timezone" in body else current_timezone()).strip()
    if not valid_timezone(timezone):
        raise ApiError(400, "时区无效，请使用 Asia/Shanghai 等 IANA 时区名称")
    if timezone != current_timezone():
        try:
            set_system_timezone(timezone)
        except OSError as exc:
            raise ApiError(500, f"时区设置失败: {exc}") from None
    commands = []
    if hostname != os.uname().nodename:
        commands.append(["/usr/bin/hostnamectl", "set-hostname", hostname])
    for command in commands:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        if result.returncode:
            raise ApiError(500, clean_service_log(result.stderr) or "系统设置保存失败")
    return system_settings_payload()


def verify_kit_manifest(kit_root):
    manifest_path = kit_root / "KIT-SHA256SUMS"
    expected = {}
    try:
        lines = manifest_path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ApiError(400, f"无法读取部署包清单: {exc}") from None
    for line in lines:
        match = re.fullmatch(r"([0-9a-f]{64})  (.+)", line)
        if not match:
            raise ApiError(400, "部署包清单格式无效")
        relative = PurePosixPath(match.group(2))
        if relative.is_absolute() or ".." in relative.parts or str(relative) in expected:
            raise ApiError(400, "部署包清单路径无效或重复")
        expected[str(relative)] = match.group(1)
    actual = {
        path.relative_to(kit_root).as_posix()
        for path in kit_root.rglob("*")
        if path.is_file() and path != manifest_path
    }
    if actual != set(expected):
        raise ApiError(400, "部署包清单与文件集合不一致")
    for relative, digest in expected.items():
        value = hashlib.sha256((kit_root / relative).read_bytes()).hexdigest()
        if not hmac.compare_digest(value, digest):
            raise ApiError(400, f"部署包文件校验失败: {relative}")
    return True


def installed_package_version():
    return APP_VERSION


def version_key(value):
    match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)(?:[-+]([A-Za-z0-9._-]+))?", str(value))
    if not match:
        return None
    suffix = (match.group(4) or "").lower()
    if not suffix:
        phase, sequence = 2, 0
    elif suffix.startswith("rc"):
        phase = 1
        number = re.search(r"(?:^|[._-])(\d+)(?:$|[._-])", suffix[2:])
        sequence = int(number.group(1)) if number else 0
    else:
        phase, sequence = 0, 0
    return tuple(map(int, match.groups()[:3])) + (phase, sequence, suffix)


def upgrade_version_state(candidate):
    current = installed_package_version()
    current_key = version_key(current)
    candidate_key = version_key(candidate)
    if candidate_key is None:
        relation = "unknown"
    elif current_key is None or candidate_key > current_key:
        relation = "upgrade"
    elif candidate_key < current_key:
        relation = "downgrade"
    else:
        relation = "current"
    return {
        "current_version": current or "未知",
        "version_relation": relation,
        "update_available": relation == "upgrade",
    }


def stage_upgrade_archive(data, filename="update.tar.gz"):
    if not isinstance(data, bytes) or not data.startswith(b"\x1f\x8b"):
        raise ApiError(400, "更新包不是 gzip 压缩包")
    base = Path(UPGRADE_ROOT)
    base.mkdir(parents=True, exist_ok=True, mode=0o700)
    staging = Path(tempfile.mkdtemp(prefix="upload-", dir=base))
    archive_path = staging / "update.tar.gz"
    try:
        archive_path.write_bytes(data)
        with tarfile.open(archive_path, "r:gz") as archive:
            members = archive.getmembers()
            if not members or len(members) > 2500:
                raise ApiError(400, "更新包内容数量无效")
            roots = set()
            expanded = 0
            for member in members:
                path = PurePosixPath(member.name)
                if path.is_absolute() or ".." in path.parts or not path.parts:
                    raise ApiError(400, "更新包包含不安全路径")
                if not (member.isdir() or member.isfile()):
                    raise ApiError(400, "更新包包含不支持的链接或设备文件")
                roots.add(path.parts[0])
                expanded += max(0, member.size)
            if len(roots) != 1 or expanded > 256 * 1024 * 1024:
                raise ApiError(400, "更新包目录结构或解压大小无效")
            root_name = next(iter(roots))
            if not re.fullmatch(
                r"pe31625g24dira-deploy-kit-[A-Za-z0-9._+-]+",
                root_name,
            ):
                raise ApiError(400, "不是 PE31625G24DIRA 部署包")
            archive.extractall(staging, filter="data")
        kit_root = staging / root_name
        required = (
            kit_root / "KIT-SHA256SUMS",
            kit_root / "RELEASE-MANIFEST.json",
            kit_root / "VERSION",
            kit_root / "deployment" / "upgrade.sh",
            kit_root / "webui" / "app.py",
        )
        missing = [str(path.relative_to(kit_root)) for path in required if not path.is_file()]
        if missing:
            raise ApiError(400, "部署包不完整：缺少 " + "、".join(missing))
        verify_kit_manifest(kit_root)
        version = (kit_root / "VERSION").read_text(encoding="utf-8").strip()
        if not re.fullmatch(r"[0-9]+(?:\.[0-9]+){2}(?:[-+][A-Za-z0-9._-]+)?", version):
            raise ApiError(400, "部署包版本无效")
        try:
            release = json.loads((kit_root / "RELEASE-MANIFEST.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ApiError(400, f"部署包版本清单无效: {exc}") from None
        artifact_type = release.get("artifact_type")
        if artifact_type != "deploy-kit":
            raise ApiError(400, "部署包类型无效")
        if release.get("version") != version:
            raise ApiError(400, "部署包版本清单不一致")
        metadata = {
            "version": version,
            "artifact_type": artifact_type,
            "root": root_name,
            "filename": Path(filename).name,
            "sha256": hashlib.sha256(data).hexdigest(),
            "size": len(data),
            "uploaded": int(time.time()),
        }
        metadata.update(upgrade_version_state(version))
        (staging / "metadata.json").write_text(json.dumps(metadata, ensure_ascii=False), encoding="utf-8")
        pending = base / "pending"
        if pending.exists():
            shutil.rmtree(pending)
        os.replace(staging, pending)
        return metadata
    except ApiError:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    except (OSError, tarfile.TarError, subprocess.TimeoutExpired) as exc:
        shutil.rmtree(staging, ignore_errors=True)
        raise ApiError(400, f"更新包处理失败: {exc}") from None


def download_release_url(url, max_bytes):
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in {
        "api.github.com",
        "github.com",
        "objects.githubusercontent.com",
        "release-assets.githubusercontent.com",
    }:
        raise ApiError(502, "Release 返回了不受信任的下载地址")
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": f"PE31625G24DIRA-Switch-Manager/{APP_VERSION}",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    token = os.environ.get("PE31625G24DIRA_GITHUB_TOKEN", "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        with urlopen(Request(url, headers=headers), timeout=30) as response:
            length = response.headers.get("Content-Length")
            if length and int(length) > max_bytes:
                raise ApiError(413, "Release 文件超过允许大小")
            data = response.read(max_bytes + 1)
    except ApiError:
        raise
    except HTTPError as exc:
        if exc.code == 404:
            raise ApiError(404, "尚未发布可用的正式版本") from None
        raise ApiError(502, f"GitHub Release 请求失败: HTTP {exc.code}") from None
    except (URLError, TimeoutError, OSError, ValueError) as exc:
        raise ApiError(502, f"无法连接 GitHub Release: {exc}") from None
    if len(data) > max_bytes:
        raise ApiError(413, "Release 文件超过允许大小")
    return data


def release_package(release):
    assets = release.get("assets")
    if not isinstance(assets, list):
        return None
    packages = [
        asset for asset in assets if isinstance(asset, dict) and re.fullmatch(
            r"pe31625g24dira-deploy-kit-[A-Za-z0-9._+-]+\.tar\.gz", str(asset.get("name", ""))
        )
    ]
    if len(packages) != 1:
        return None
    version_match = re.fullmatch(
        r"pe31625g24dira-deploy-kit-([A-Za-z0-9._+-]+)\.tar\.gz", packages[0]["name"]
    )
    key = version_key(version_match.group(1)) if version_match else None
    return (key, packages[0]) if key is not None else None


def stage_latest_upgrade(include_prerelease=False, allow_downgrade=False):
    try:
        value = json.loads(download_release_url(
            RELEASES_API if include_prerelease else RELEASE_API, 2 * 1024 * 1024
        ))
    except json.JSONDecodeError:
        raise ApiError(502, "GitHub Release 元数据无效") from None
    if include_prerelease:
        choices = [
            (release_package(item), item) for item in value
            if isinstance(item, dict) and not item.get("draft")
        ] if isinstance(value, list) else []
        choices = [(package, item) for package, item in choices if package]
        if not choices:
            raise ApiError(404, "尚未发布可用版本")
        _, release = max(choices, key=lambda choice: choice[0][0])
    else:
        release = value
    assets = release.get("assets")
    if not isinstance(assets, list):
        raise ApiError(502, "GitHub Release 没有有效资产列表")
    packages = [
        asset
        for asset in assets
        if isinstance(asset, dict)
        and re.fullmatch(
            r"pe31625g24dira-deploy-kit-[A-Za-z0-9._+-]+\.tar\.gz",
            str(asset.get("name", "")),
        )
    ]
    if len(packages) != 1:
        raise ApiError(502, "Release 中必须且只能包含一个通用部署包")
    package = packages[0]
    package_name = package["name"]
    version_match = re.fullmatch(
        r"pe31625g24dira-deploy-kit-([A-Za-z0-9._+-]+)\.tar\.gz", package_name
    )
    candidate_version = version_match.group(1) if version_match else ""
    state = upgrade_version_state(candidate_version)
    if state["version_relation"] == "current" or (
        state["version_relation"] == "downgrade" and not allow_downgrade
    ):
        return {
            "version": candidate_version,
            "filename": package_name,
            "sha256": "",
            "size": 0,
            "release": str(release.get("tag_name", "")),
            "staged": False,
            **state,
        }
    sidecars = [
        asset
        for asset in assets
        if isinstance(asset, dict) and asset.get("name") == package_name + ".sha256"
    ]
    if len(sidecars) != 1:
        raise ApiError(502, "Release 缺少部署包 SHA-256 文件")
    archive = download_release_url(
        str(package.get("browser_download_url", "")), UPGRADE_MAX_BYTES
    )
    try:
        sidecar = download_release_url(
            str(sidecars[0].get("browser_download_url", "")), 4096
        ).decode("ascii", "strict").strip()
    except UnicodeDecodeError:
        raise ApiError(502, "Release SHA-256 文件格式无效") from None
    match = re.fullmatch(r"([0-9a-fA-F]{64})  (.+)", sidecar)
    if not match or match.group(2) != package_name:
        raise ApiError(502, "Release SHA-256 文件格式无效")
    digest = hashlib.sha256(archive).hexdigest()
    if not hmac.compare_digest(digest, match.group(1).lower()):
        raise ApiError(502, "Release 部署包 SHA-256 校验失败")
    metadata = stage_upgrade_archive(archive, package_name)
    return {**metadata, "release": str(release.get("tag_name", "")), "staged": True}


def pending_upgrade():
    pending = Path(UPGRADE_ROOT) / "pending"
    try:
        metadata = json.loads((pending / "metadata.json").read_text(encoding="utf-8"))
        kit_root = pending / metadata["root"]
    except (OSError, KeyError, json.JSONDecodeError):
        raise ApiError(409, "尚未上传有效更新包") from None
    if not (kit_root / "deployment" / "upgrade.sh").is_file():
        raise ApiError(409, "暂存的更新包不完整")
    return metadata, kit_root


def upgrade_allowed(metadata, allow_downgrade=False):
    relation = metadata.get("version_relation") or upgrade_version_state(metadata.get("version"))["version_relation"]
    if relation == "upgrade":
        return True
    if relation == "downgrade" and allow_downgrade:
        return True
    if relation == "downgrade":
        raise ApiError(409, "这是较旧版本；如需降级，请先启用“允许降级”")
    if relation == "current":
        raise ApiError(409, "已是当前版本，无需更新")
    raise ApiError(409, "无法比较更新包版本")


def audit_pending_upgrade(allow_downgrade=False):
    metadata, kit_root = pending_upgrade()
    upgrade_allowed(metadata, allow_downgrade)
    script = kit_root / "deployment" / "upgrade.sh"
    try:
        result = subprocess.run(
            ["/bin/bash", str(script), "--audit"],
            cwd=kit_root,
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
        )
    except subprocess.TimeoutExpired:
        raise ApiError(504, "更新审计超时") from None
    output = clean_service_log(result.stdout + ("\n" + result.stderr if result.stderr else ""))[-131072:]
    if result.returncode:
        raise ApiError(409, output or "更新审计失败")
    return {**metadata, "ok": True, "output": output}


def start_pending_upgrade(allow_downgrade=False):
    metadata, kit_root = pending_upgrade()
    upgrade_allowed(metadata, allow_downgrade)
    script = kit_root / "deployment" / "upgrade.sh"
    unit = f"pe31625g24dira-web-upgrade-{int(time.time())}"
    command = [
        "/bin/systemd-run", "--unit", unit, "--property=Type=oneshot", "--no-block",
        f"--working-directory={kit_root}", "/bin/bash",
        str(script), "--apply",
    ]
    result = subprocess.run(command, capture_output=True, text=True, timeout=15, check=False)
    if result.returncode:
        raise ApiError(500, clean_service_log(result.stderr) or "无法启动更新作业")
    job = {"unit": unit, "target_version": metadata["version"], "started": int(time.time())}
    job_path = Path(UPGRADE_ROOT) / "job.json"
    temporary = job_path.with_suffix(".tmp")
    temporary.write_text(json.dumps(job, ensure_ascii=False), encoding="utf-8")
    os.replace(temporary, job_path)
    return {**metadata, "ok": True, "unit": unit, "message": "更新作业已启动"}


def upgrade_job_status():
    try:
        job = json.loads((Path(UPGRADE_ROOT) / "job.json").read_text(encoding="utf-8"))
        unit = job["unit"]
    except (OSError, KeyError, json.JSONDecodeError):
        return {"state": "idle", "message": "没有正在执行的更新"}
    if not re.fullmatch(r"pe31625g24dira-web-upgrade-\d+", str(unit)):
        raise ApiError(500, "更新作业记录无效")
    result = subprocess.run(
        ["/bin/systemctl", "show", unit, "--property=ActiveState", "--value"],
        capture_output=True, text=True, timeout=10, check=False,
    )
    active = result.stdout.strip() or "unknown"
    state = "running" if active in {"active", "activating"} else "failed" if active == "failed" else "done"
    current = installed_package_version()
    if state == "done" and current != job.get("target_version"):
        state = "failed"
    return {
        **job,
        "state": state,
        "current_version": current or "未知",
        "message": {"running": "正在应用更新，管理页面和相关服务可能短暂中断", "done": "更新完成", "failed": "更新失败，请查看系统日志"}[state],
    }


def poweroff_worker(delay=2.0):
    """Let the HTTP response reach the browser before asking PID 1 to power off."""
    time.sleep(delay)
    subprocess.call(["/bin/systemctl", "poweroff"])


def schedule_poweroff(delay=2.0):
    thread = threading.Thread(target=poweroff_worker, args=(delay,))
    thread.daemon = True
    thread.start()
    return thread


def reboot_worker(delay=2.0):
    """Let the HTTP response reach the browser before asking PID 1 to reboot."""
    time.sleep(delay)
    subprocess.call(["/bin/systemctl", "reboot"])


def schedule_reboot(delay=2.0):
    thread = threading.Thread(target=reboot_worker, args=(delay,))
    thread.daemon = True
    thread.start()
    return thread


def factory_reset_worker(state, job_id):
    backup = None
    try:
        state.update_job(job_id, state="running", message="恢复默认配置")
        backup = make_backup(state.config)
        if state.config_path and os.path.exists(state.config_path):
            shutil.copy2(state.config_path, os.path.join(backup, "config.json"))
        write_factory_configuration(state.config)
        state.update_job(job_id, message="重启交换服务")
        started_at = time.time()
        if run_systemctl("restart") != 0:
            raise RuntimeError("systemctl restart failed")
        ok, message, log = wait_for_switch(started_at)
        if not ok:
            raise RuntimeError(message + "\n" + log[-1200:])
        if subprocess.call(
            ["/bin/systemctl", "restart", "pe31625g24dira-fan-init.service"]
        ) != 0:
            raise RuntimeError("fan service restart failed")
        state.update_job(
            job_id,
            state="done",
            message="已恢复默认配置",
            backup=backup,
            setup_required=True,
        )
        # Give the browser enough time to receive the terminal job state before
        # the first-run gate starts rejecting authenticated API requests.
        time.sleep(3)
        clear_admin_credentials(state)
        state.revoke_all_sessions()
    except Exception as exc:
        if backup:
            with suppress(Exception):
                restore_backup(state.config, backup)
                config_backup = os.path.join(backup, "config.json")
                if state.config_path and os.path.exists(config_backup):
                    shutil.copy2(config_backup, state.config_path)
                    state.config = read_json(state.config_path)
                run_systemctl("restart")
        state.update_job(
            job_id,
            state="failed",
            message="恢复默认配置失败",
            error=str(exc),
            backup=backup,
        )


def wait_for_switch(started_at, timeout=120):
    fatal = re.compile(
        r"FATAL:|ERROR:|Oversubscribed|Failed to bring switch up|no scheduler solution",
        re.IGNORECASE,
    )
    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(5)
        stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(started_at - 1))
        process = subprocess.Popen(
            [
                "/bin/journalctl",
                "-u",
                SERVICE,
                "--since",
                stamp,
                "--no-pager",
                "-o",
                "cat",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        stdout, _ = process.communicate()
        log = stdout.decode("utf-8", "replace")
        if fatal.search(log):
            return False, "SDK 初始化失败", clean_service_log(log)[-4000:]
        if "TestPoint loaded in" in log and service_state() == "active":
            return True, "配置已生效", clean_service_log(log)[-4000:]
        if service_state() in ("failed", "inactive") and time.time() - started_at > 20:
            return False, "交换服务已退出", log[-4000:]
    return False, "等待交换芯片初始化超时", ""


def clean_service_log(log):
    """Remove TestPoint's terminal spinner/control bytes from API errors."""
    value = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", log)
    value = re.sub(
        r"(?m)^>> Loading TestPoint Module.*$", ">> Loading TestPoint Module", value
    )
    return "".join(
        character
        for character in value
        if character in "\n\t" or ord(character) >= 32
    )


def apply_worker(state, job_id, requested, total, warning):
    backup = None
    prepared = None
    live_apply = False
    try:
        state.update_job(job_id, state="running", message="生成端口配置")
        backup = make_backup(state.config)
        prepared = prepare_requested(state.config, requested)
        parsed = prepared["parsed"]
        if uses_fixed_logical_model(prepared["old_parsed"]):
            live_apply = True
            state.update_job(job_id, message="在线应用受影响的 EPL")
            run_live_configuration(
                state.config,
                prepared["old_parsed"],
                prepared["old_vlans"],
                parsed,
                prepared["vlans"],
                prepared["ports"],
                include_topology=True,
            )
            run_l2_configuration(
                state.config, parsed, prepared["old_l2"], prepared["l2"]
            )
            persist_requested(state.config, prepared)
        else:
            # One-time migration from variable logical-port allocation to the
            # fixed 24-slot model. Future changes use the live path above.
            persist_requested(state.config, prepared)
            state.update_job(job_id, message="迁移固定端口模型（仅本次重启）")
            started = time.time()
            if run_systemctl("restart") != 0:
                raise RuntimeError("systemctl restart failed")
            ok, message, log = wait_for_switch(started)
            if not ok:
                raise RuntimeError(message + "\n" + log[-1200:])
            verify_vlan_readback(state.config, parsed, prepared["vlans"])
        state.update_job(
            job_id,
            state="done",
            message="端口配置已应用",
            backup=backup,
            total=total,
            warning=warning,
            external_count=parsed["external_count"],
            payload=platform_payload(state.config),
        )
    except Exception as exc:
        rollback_ok = False
        rollback_error = None
        if backup:
            try:
                if live_apply and prepared is not None:
                    state.update_job(job_id, message="恢复应用前配置")
                    run_live_configuration(
                        state.config,
                        prepared["parsed"],
                        prepared["vlans"],
                        prepared["old_parsed"],
                        prepared["old_vlans"],
                        prepared["old_ports"],
                        include_topology=True,
                    )
                    run_l2_configuration(
                        state.config,
                        prepared["old_parsed"],
                        prepared["l2"],
                        prepared["old_l2"],
                    )
                    restore_backup(state.config, backup)
                    rollback_ok = True
                else:
                    restore_backup(state.config, backup)
                    started = time.time()
                    run_systemctl("restart")
                    rollback_ok, _, _ = wait_for_switch(started)
            except Exception as rollback_exc:
                rollback_error = str(rollback_exc)
                try:
                    restore_backup(state.config, backup)
                    started = time.time()
                    run_systemctl("restart")
                    rollback_ok, _, _ = wait_for_switch(started)
                except Exception as restart_exc:
                    rollback_error += "; " + str(restart_exc)
        state.update_job(
            job_id,
            state="failed",
            message="应用失败，已回滚" if rollback_ok else "应用与回滚均失败",
            error=str(exc),
            rollback_ok=rollback_ok,
            rollback_error=rollback_error,
            backup=backup,
        )


def configuration_import_worker(state, job_id, value):
    backup = None
    try:
        state.update_job(job_id, state="running", message="导入配置")
        backup = make_backup(state.config)
        write_imported_configuration(state.config, value)
        state.update_job(job_id, message="重启交换服务")
        started = time.time()
        if run_systemctl("restart") != 0:
            raise RuntimeError("systemctl restart failed")
        ok, message, log = wait_for_switch(started)
        if not ok:
            raise RuntimeError(message + "\n" + log[-1200:])
        fan_script = state.config.get("fan_init_script", FAN_INIT_SCRIPT)
        output = queue_testpoint_script(
            state.config, fan_script, FAN_COMPLETE_MARKER, timeout=20
        )
        errors = meaningful_sdk_errors(output)
        if errors:
            raise RuntimeError("; ".join(errors[-4:]))
        state.update_job(
            job_id,
            state="done",
            message="配置已恢复",
            backup=backup,
            payload=platform_payload(state.config),
        )
    except Exception as exc:
        rollback_ok = False
        rollback_error = None
        if backup:
            try:
                restore_backup(state.config, backup)
                started = time.time()
                run_systemctl("restart")
                rollback_ok, _, _ = wait_for_switch(started)
                previous_fan = state.config.get("fan_init_script", FAN_INIT_SCRIPT)
                if rollback_ok and os.path.exists(previous_fan):
                    queue_testpoint_script(
                        state.config, previous_fan, FAN_COMPLETE_MARKER, timeout=20
                    )
            except Exception as rollback_exc:
                rollback_error = str(rollback_exc)
        state.update_job(
            job_id,
            state="failed",
            message="配置恢复失败，已回滚" if rollback_ok else "配置恢复与回滚均失败",
            error=str(exc),
            rollback_ok=rollback_ok,
            rollback_error=rollback_error,
            backup=backup,
        )


def live_status_worker(state, job_id):
    try:
        state.update_job(job_id, state="running", message="读取 FM10840 硬件状态")
        _, parsed = parse_platform(state.config["platform_persistent"])
        expected = {item["logical"] for item in parsed["ports"]}
        output = queue_testpoint_script(
            state.config, state.config["status_script"], STATUS_COMPLETE_MARKER, timeout=55
        )
        ports = {}
        for match in PORT_STATUS_RE.finditer(output):
            port = int(match.group(1))
            if port in expected:
                ports[str(port)] = {
                    "speed": match.group(2),
                    "type": match.group(3),
                    "oper": match.group(4),
                    "admin": match.group(5),
                }
        if {int(key) for key in ports} != expected:
            raise RuntimeError(f"只解析到 {len(ports)}/{len(expected)} 个外部端口")
        try:
            sensors = parse_switch_sensors(output)
            with state.telemetry_lock:
                sensors["optics"] = state.sensor_cache.get(
                    "optics", {"state": "pending", "modules": []}
                )
                state.sensor_cache = sensors
        except Exception:
            pass
        state.update_job(
            job_id,
            state="done",
            message="硬件数据已刷新",
            ports=ports,
            sampled=int(time.time()),
        )
    except Exception as exc:
        state.update_job(job_id, state="failed", message="端口状态读取失败", error=str(exc))


def meaningful_sdk_errors(output):
    result = []
    for line in output.splitlines():
        if "ERROR:" in line and "fmTerminate" not in line:
            result.append(line.strip())
    return result


def journal_cursor():
    output = subprocess.check_output(
        ["/bin/journalctl", "-u", SERVICE, "-n", "0", "--show-cursor", "--no-pager"],
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    for line in reversed(output.splitlines()):
        if line.startswith("-- cursor: "):
            return line.partition(": ")[2].strip()
    raise RuntimeError("无法获取交换服务日志游标")


def journal_after(cursor):
    return subprocess.check_output(
        [
            "/bin/journalctl",
            "-u",
            SERVICE,
            "--after-cursor",
            cursor,
            "--no-pager",
            "-o",
            "cat",
        ],
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def testpoint_script_result(output, script_path, completion_marker="Device=0x58:  <= 01"):
    loading = f"Loading {script_path}"
    if loading not in output:
        return None
    relevant = output[output.rfind(loading) :]
    failures = (
        "syntax error",
        "No such file",
        "Cannot open",
        "ERROR:",
        "has too many errors",
    )
    for marker in failures:
        if marker in relevant:
            raise RuntimeError("TestPoint 加载失败: " + relevant[-1200:])
    if completion_marker in relevant:
        return relevant
    return None


def queue_testpoint_script(config, script_path, completion_marker, timeout=20):
    if "\n" in script_path or "\r" in script_path:
        raise RuntimeError("TestPoint 脚本路径无效")
    fifo = config.get("testpoint_control_fifo", "/run/pe31625g24dira-testpoint/control")
    if not stat.S_ISFIFO(os.stat(fifo).st_mode):
        raise RuntimeError("TestPoint 控制 FIFO 不存在")
    cursor = journal_cursor()
    descriptor = os.open(fifo, os.O_WRONLY | os.O_NONBLOCK)
    try:
        os.write(descriptor, f"load {script_path}\n".encode())
    finally:
        os.close(descriptor)
    deadline = time.monotonic() + timeout
    latest = ""
    while time.monotonic() < deadline:
        latest = journal_after(cursor)
        result = testpoint_script_result(latest, script_path, completion_marker)
        if result is not None:
            return result
        time.sleep(0.25)
    raise RuntimeError(f"常驻 TestPoint 未在期限内确认操作完成: {completion_marker}")


def run_temporary_testpoint(config, prefix, script, marker, timeout=25):
    path = None
    try:
        fd, path = tempfile.mkstemp(
            prefix=prefix + "-", suffix=".tp", dir="/run/pe31625g24dira-testpoint"
        )
        try:
            os.write(fd, script.encode("utf-8"))
        finally:
            os.close(fd)
        return queue_testpoint_script(config, path, marker, timeout=timeout)
    finally:
        if path:
            with suppress(OSError):
                os.unlink(path)


def live_configuration_script(
    old_parsed,
    old_vlans,
    new_parsed,
    new_vlans,
    new_ports,
    include_topology=False,
):
    commands = []
    if include_topology:
        commands.extend(topology_live_commands(old_parsed, new_parsed, new_ports))
    commands.extend(vlan_delta_commands(old_parsed, old_vlans, new_parsed, new_vlans))
    script = tp_script(commands)
    if include_topology:
        script += xcvr_verification_script(new_parsed, new_ports)
        script += "if ($pe_xcvr_ok) {\n"
        script += f'    print "{TOPOLOGY_APPLY_COMPLETE_MARKER}\\n";\n'
        script += "} else {\n"
        script += f'    print "ERROR: {XCVR_VERIFY_FAILURE_MARKER}\\n";\n'
        script += "}\n"
        marker = TOPOLOGY_APPLY_COMPLETE_MARKER
    else:
        script += f'print "{VLAN_APPLY_COMPLETE_MARKER}\\n";\n'
        marker = VLAN_APPLY_COMPLETE_MARKER
    return script, marker


def run_live_configuration(
    config,
    old_parsed,
    old_vlans,
    new_parsed,
    new_vlans,
    new_ports,
    include_topology=False,
):
    script, marker = live_configuration_script(
        old_parsed,
        old_vlans,
        new_parsed,
        new_vlans,
        new_ports,
        include_topology=include_topology,
    )
    output = run_temporary_testpoint(
        config,
        "topology-live" if include_topology else "vlan-live",
        script,
        marker,
        timeout=80 if include_topology else 40,
    )
    errors = meaningful_sdk_errors(output)
    if errors:
        raise RuntimeError("; ".join(errors[-6:]))
    verify_vlan_readback(config, new_parsed, new_vlans)
    return output


def diagnostic_tp(command, marker):
    return tp_script([command]) + f'print "{marker}\\n";\n'


def vlan_readback_script(parsed, value):
    ports = active_logical_spec(parsed)
    commands = [f"show vlan {vlan['id']}" for vlan in value["vlans"]]
    commands.extend(
        f"show port config {ports} {attribute}"
        for attribute in ("pvid", "drop_bv", "drop_untagged", "drop_tagged")
    )
    return tp_script(commands) + f'print "{VLAN_READBACK_COMPLETE_MARKER}\\n";\n'


def parse_vlan_memberships(output):
    result = {}
    for block in output.split("VLAN REF. MTU   MEMBERSHIP/TAGGING")[1:]:
        lines = block.splitlines()
        for index, line in enumerate(lines):
            columns = line.split()
            if len(columns) < 3 or not all(value.isdigit() for value in columns[:3]):
                continue
            vid = int(columns[0])
            ports = [int(value) for value in columns[3:] if value.isdigit()]
            tags = []
            for candidate in lines[index + 1 :]:
                values = candidate.split()
                if values and all(value in ("U", "T") for value in values):
                    tags = values
                    break
                if values and set(values) != {"-"}:
                    break
            result[vid] = dict(zip(ports, tags))
            break
    return result


def parse_port_attribute(output, attribute, count):
    matches = re.findall(rf"(?m)^\s*{re.escape(attribute)}\s+([^\r\n]+)$", output)
    if not matches:
        raise RuntimeError(f"VLAN 回读缺少端口属性 {attribute}")
    values = matches[-1].split()
    if len(values) != count:
        raise RuntimeError(
            f"VLAN 回读属性 {attribute} 数量异常：期望 {count}，得到 {len(values)}"
        )
    return values


def verify_vlan_readback_output(parsed, value, output):
    endpoints = topology_endpoints(parsed)
    end = len(endpoints)
    active_ports = {item["logical"] for item in endpoints}
    endpoint_map = {item["key"]: item["logical"] for item in endpoints}
    observed_vlans = parse_vlan_memberships(output)
    failures = []
    for vlan in value["vlans"]:
        expected = {
            endpoint_map[key]: "T" for key in vlan["tagged"]
        } | {
            endpoint_map[key]: "U" for key in vlan["untagged"]
        }
        observed = {
            port: tag
            for port, tag in observed_vlans.get(vlan["id"], {}).items()
            if port in active_ports
        }
        if observed != expected:
            failures.append(f"VLAN {vlan['id']} 成员不匹配")
    profiles = vlan_port_profiles(parsed, value)
    ordered_profiles = [
        profiles[item["key"]] for item in sorted(endpoints, key=lambda item: item["logical"])
    ]
    expected_rows = {
        "pvid": [
            str(profile["native_vlan"] if profile["native_vlan"] is not None else 1)
            for profile in ordered_profiles
        ],
        "drop_bv": ["on"] * end,
        "drop_untagged": [
            "on" if profile["mode"] == "trunk" else "off"
            for profile in ordered_profiles
        ],
        "drop_tagged": [
            "on" if profile["mode"] == "access" else "off"
            for profile in ordered_profiles
        ],
    }
    for attribute, expected in expected_rows.items():
        if parse_port_attribute(output, attribute, end) != expected:
            failures.append(f"端口属性 {attribute} 不匹配")
    if failures:
        raise RuntimeError("VLAN 硬件回读失败：" + "；".join(failures))


def verify_vlan_readback(config, parsed, value):
    output = run_temporary_testpoint(
        config,
        "vlan-readback",
        vlan_readback_script(parsed, value),
        VLAN_READBACK_COMPLETE_MARKER,
        timeout=35,
    )
    verify_vlan_readback_output(parsed, value, output)
    return output


def parse_debug_mac_entries(output):
    """Parse TestPoint's cache/DMAC/SMAC comparison table."""
    entries = []
    for block_match in re.finditer(
        r"MA_TABLE\[(\d+)\]:\s*(.*?)(?=\nMA_TABLE\[|\n\d+ entries listed|\Z)",
        output,
        re.DOTALL,
    ):
        columns = {"index": int(block_match.group(1))}
        for row in block_match.group(2).splitlines():
            match = re.match(
                r"^\s*(State|MAC Address|FID|Address Type|Port)\s*:\s+(\S+)\s+(\S+)\s+(\S+)\s*$",
                row,
            )
            if match:
                key = match.group(1).lower().replace(" ", "_")
                columns[key] = tuple(match.group(index) for index in range(2, 5))
        required = {"state", "mac_address", "fid", "address_type", "port"}
        if required.issubset(columns):
            entries.append(columns)
    return entries


def mismatched_dynamic_macs(output):
    """Return stable cache/hardware disagreements that are safe to relearn."""
    mismatches = []
    for entry in parse_debug_mac_entries(output):
        macs = entry["mac_address"]
        fids = entry["fid"]
        ports = entry["port"]
        states = entry["state"]
        if entry["address_type"][0].upper() != "DYNAMIC":
            continue
        if not (macs[0] == macs[1] == macs[2] and fids[0] == fids[1] == fids[2]):
            continue
        if states[1].lower() != "valid" or states[2].lower() != "valid":
            continue
        if ports[0] == ports[1] == ports[2]:
            continue
        raw_mac = macs[0].lower()
        if not re.fullmatch(r"[0-9a-f]{12}", raw_mac) or not fids[0].isdigit():
            continue
        mismatches.append(
            {
                "mac": ":".join(raw_mac[index : index + 2] for index in range(0, 12, 2)),
                "fid": int(fids[0]),
                "cache_port": ports[0],
                "dmac_port": ports[1],
                "smac_port": ports[2],
            }
        )
    return mismatches


def mac_repair_audit(config):
    output = run_temporary_testpoint(
        config,
        "mac-repair-audit",
        diagnostic_tp("show dbg mac all", MAC_REPAIR_AUDIT_MARKER),
        MAC_REPAIR_AUDIT_MARKER,
        timeout=30,
    )
    return mismatched_dynamic_macs(output)


def repair_mismatched_dynamic_macs(config):
    """Confirm persistent mismatches, then delete only those MAC/FID keys."""
    first = {(item["mac"], item["fid"]): item for item in mac_repair_audit(config)}
    if not first:
        return []
    time.sleep(1)
    second = {(item["mac"], item["fid"]): item for item in mac_repair_audit(config)}
    stable = [second[key] for key in sorted(first.keys() & second.keys())]
    if not stable:
        return []
    script = tp_script([f'del mac {item["mac"]} {item["fid"]}' for item in stable])
    script += f'print "{MAC_REPAIR_APPLY_MARKER}\\n";\n'
    run_temporary_testpoint(
        config,
        "mac-repair-apply",
        script,
        MAC_REPAIR_APPLY_MARKER,
        timeout=30,
    )
    return stable


def mac_repair_watchdog(state, poll_seconds=1, settle_seconds=3, startup_delay=10):
    """Audit after startup or link-up and repair SDK cache/hardware divergence."""
    previous = None
    audit_after = time.monotonic() + startup_delay
    while True:
        try:
            if not os.path.exists(SWITCH_READY_PATH):
                previous = None
                audit_after = time.monotonic() + startup_delay
                time.sleep(poll_seconds)
                continue
            current = direct_link_states(state.config)
            now = time.monotonic()
            if previous is not None and any(
                current.get(port, False) and not previous.get(port, False) for port in current
            ):
                audit_after = now + settle_seconds
            previous = current
            if audit_after is not None and now >= audit_after:
                state.start_operation(
                    "mac-repair",
                    mac_repair_worker,
                    priority=15,
                    coalesce_key="mac-repair",
                )
                audit_after = None
        except Exception as exc:
            print(f"MAC repair audit skipped: {exc}", flush=True)
            audit_after = time.monotonic() + 5
        time.sleep(poll_seconds)


def mac_repair_worker(state, job_id):
    try:
        state.update_job(job_id, state="running", message="核对动态 MAC 表")
        repaired = repair_mismatched_dynamic_macs(state.config)
        if repaired:
            summary = ", ".join(
                f'{item["mac"]}/FID {item["fid"]} '
                f'{item["dmac_port"]}->{item["cache_port"]}'
                for item in repaired
            )
            print(f"MAC repair: relearned {summary}", flush=True)
        state.update_job(job_id, state="done", message="动态 MAC 表核对完成")
    except Exception as exc:
        state.update_job(job_id, state="failed", message="动态 MAC 表核对失败", error=str(exc))


def parse_fdb(output):
    entries = []
    for match in FDB_ENTRY_RE.finditer(output):
        entries.append(
            {
                "mac": match.group(1).lower(),
                "mode": match.group(2),
                "fid": int(match.group(3)),
                "destination_type": match.group(5),
                "destination": match.group(6),
            }
        )
    return {"sampled": int(time.time()), "count": len(entries), "entries": entries[:1024]}


def fdb_worker(state, job_id):
    try:
        state.update_job(job_id, state="running", message="读取 MAC 地址表")
        output = run_temporary_testpoint(
            state.config,
            "fdb-read",
            diagnostic_tp("show mac table all", FDB_COMPLETE_MARKER),
            FDB_COMPLETE_MARKER,
            timeout=30,
        )
        result = parse_fdb(output)
        state.update_job(
            job_id,
            state="done",
            message=f"读取到 {result['count']} 条 MAC 记录",
            fdb=result,
        )
    except Exception as exc:
        state.update_job(job_id, state="failed", message="MAC 地址表读取失败", error=str(exc))


def parse_lane_diagnostic(output, endpoint):
    rows = []
    current_port = None
    for line in output.splitlines():
        port_match = re.match(
            r"^\s*(\d+)\s+(10G|25G|40G|100G)\s+(\S+)\s+(\S+)\s+(\S+)\s+(.+?)\s+(\S+/\S+)\s*$",
            line,
        )
        if port_match:
            current_port = {
                "logical": int(port_match.group(1)),
                "speed": port_match.group(2),
                "type": port_match.group(3),
                "state": port_match.group(4),
                "mode": port_match.group(5),
            }
            status_text_value = port_match.group(6) + " " + port_match.group(7)
            lane_index = endpoint["lane"] if endpoint["lane"] is not None else 0
        else:
            lane_match = re.match(r"^\s*Lane\s+(\d+)\s+(.+?)\s+(\S+/\S+)\s*$", line)
            if not lane_match or current_port is None:
                continue
            lane_index = int(lane_match.group(1))
            status_text_value = lane_match.group(2) + " " + lane_match.group(3)
        status = LANE_STATUS_RE.search(status_text_value)
        if not status:
            continue
        pll_code, signal_code, dfe_code, coarse, fine, eee, eye = status.groups()
        eye_height_text, eye_width_text = eye.split("/", 1)
        eye_height = int(eye_height_text) if eye_height_text.isdigit() else None
        eye_width = int(eye_width_text) if eye_width_text.isdigit() else None

        rows.append(
            {
                "lane": lane_index,
                "pll": {"N": "未锁定", "R": "接收锁定", "T": "发送锁定", "L": "双向锁定"}[pll_code],
                "signal": {"D": "检测关闭", "N": "无信号", "Y": "有信号"}.get(signal_code, signal_code),
                "dfe_mode": {"S": "Static", "1": "One-shot", "C": "Continuous", "i": "ICAL", "K": "KR"}[dfe_code],
                "coarse": {"W": "未开始", "R": "进行中", "C": "完成", "E": "错误"}[coarse],
                "fine": {"W": "未开始", "R": "进行中", "C": "完成", "E": "错误"}[fine],
                "eee": eee,
                "eye_height": eye_height if eye_height is not None and 0 <= eye_height <= 64 else None,
                "eye_width": eye_width if eye_width is not None and 0 <= eye_width <= 64 else None,
            }
        )
    if not rows:
        raise RuntimeError("没有解析到 Lane 状态")
    return {"sampled": int(time.time()), "endpoint": endpoint, "port": current_port, "lanes": rows}


def lane_diagnostic_worker(state, job_id, logical):
    try:
        state.update_job(job_id, state="running", message=f"读取端口 {logical} Lane 状态")
        _, parsed = parse_platform(state.config["platform_persistent"])
        endpoint = next(item for item in topology_endpoints(parsed) if item["logical"] == logical)
        output = run_temporary_testpoint(
            state.config,
            "lane-diagnostic",
            diagnostic_tp(f"show port {logical} verbose", LANE_DIAGNOSTIC_COMPLETE_MARKER),
            LANE_DIAGNOSTIC_COMPLETE_MARKER,
            timeout=25,
        )
        result = parse_lane_diagnostic(output, endpoint)
        state.update_job(
            job_id,
            state="done",
            message=f"端口 {logical} Lane 状态已读取",
            lane_diagnostic=result,
        )
    except Exception as exc:
        state.update_job(job_id, state="failed", message="Lane 诊断失败", error=str(exc))


def decode_optics_identity(raw):
    vendor = "FCI / Amphenol" if b"FCI MergeOptics" in raw else None
    part_match = re.search(rb"10124588-[0-9A-Z]{3}", raw)
    part_number = part_match.group().decode("ascii") if part_match else None
    serial = None
    date_code = None
    if part_match:
        tail = "".join(
            chr(value) if 32 <= value < 127 else " " for value in raw[part_match.end() :]
        )
        fields = [field.strip() for field in re.split(r"\s{2,}", tail) if field.strip()]
        date_code = next((field for field in fields if re.fullmatch(r"20\d{6}", field)), None)
        serial = next(
            (
                field
                for field in fields
                if field != date_code
                and len(field) >= 6
                and re.fullmatch(r"[A-Z0-9-]+", field)
            ),
            None,
        )
    return {
        "vendor": vendor,
        "part_number": part_number,
        "serial": serial,
        "date_code": date_code,
        "readable": bool(vendor or part_number or serial),
    }


def parse_optics_diagnostic(output):
    identities = {}
    for match in OPTICS_IDENTITY_RE.finditer(output):
        statuses = [int(match.group(index)) for index in range(2, 5)]
        raw = bytes.fromhex(match.group(5))
        identity = decode_optics_identity(raw) if all(status == 0 for status in statuses) else {}
        identity.update(
            {
                "raw": match.group(5).upper(),
                "statuses": {
                    "page": statuses[0],
                    "read": statuses[1],
                    "restore_page": statuses[2],
                },
            }
        )
        identities[int(match.group(1))] = identity
    modules = []
    for match in OPTICS_RECORD_RE.finditer(output):
        raw = bytes.fromhex(match.group(7))
        statuses = [int(match.group(index)) for index in range(3, 7)]
        valid = all(status == 0 for status in statuses) and any(raw)
        channels = []
        if valid:
            for index in range(12):
                raw_value = (raw[index * 2] << 8) | raw[index * 2 + 1]
                microwatts = raw_value / 10.0
                channels.append(
                    {
                        "channel": index,
                        "raw": raw_value,
                        "microwatts": microwatts,
                        "dbm": optical_dbm(microwatts),
                    }
                )
        module = {
            "mpo": int(match.group(1)),
            "mux": int(match.group(2)),
            "state": "ready" if valid else "unavailable",
            "raw": match.group(7).upper(),
            "channels": channels,
            "statuses": {
                "select": statuses[0],
                "page": statuses[1],
                "read": statuses[2],
                "restore_page": statuses[3],
            },
        }
        module["identity"] = identities.get(module["mpo"], {"readable": False})
        modules.append(module)
    if len(modules) != 2:
        raise RuntimeError("光功率读取没有返回两个 MUX 分支")
    return {"sampled": int(time.time()), "modules": modules}


def preserve_optics_identities(current, previous=None):
    """Retain the last readable identity while recording a partial fresh sample."""
    result = dict(current or {})
    previous_by_mpo = {
        int(module["mpo"]): module for module in (previous or {}).get("modules", [])
    }
    modules = []
    fresh = 0
    for current_module in result.get("modules", []):
        module = dict(current_module)
        identity = dict(module.get("identity") or {})
        identity_fresh = bool(identity.get("readable"))
        if identity_fresh:
            fresh += 1
        else:
            previous_identity = dict(
                previous_by_mpo.get(int(module["mpo"]), {}).get("identity") or {}
            )
            if previous_identity.get("readable"):
                identity = previous_identity
                module["identity_stale"] = True
        module["identity"] = identity
        module["identity_fresh"] = identity_fresh
        modules.append(module)
    result["modules"] = modules
    result["state"] = "ready" if modules and fresh == len(modules) else "partial"
    return result


def refresh_optics_cache(state):
    output = run_temporary_testpoint(
        state.config,
        "optics-diagnostic",
        optics_diagnostic_script(),
        OPTICS_DIAGNOSTIC_COMPLETE_MARKER,
        timeout=30,
    )
    with state.telemetry_lock:
        previous = state.optics_cache
    result = preserve_optics_identities(parse_optics_diagnostic(output), previous)
    with state.telemetry_lock:
        state.optics_cache = result
    return result


def schedule_optics_cache(state, delay=0, retry_seconds=30):
    def enqueue():
        state.start_operation(
            "optics-cache",
            optics_cache_job_worker,
            retry_seconds,
            priority=20,
            coalesce_key="optics-cache",
        )

    if delay <= 0:
        enqueue()
        return
    timer = threading.Timer(delay, enqueue)
    timer.daemon = True
    timer.start()


def optics_cache_job_worker(state, job_id, retry_seconds=30):
    retry_seconds = max(30, min(int(retry_seconds), 300))
    next_retry = min(retry_seconds * 2, 300)
    try:
        state.update_job(job_id, state="running", message="读取光引擎信息")
        result = refresh_optics_cache(state)
        if result["state"] == "partial":
            state.update_job(job_id, state="done", message="部分光引擎信息已缓存，后台将重试")
            schedule_optics_cache(
                state, delay=retry_seconds, retry_seconds=next_retry
            )
        else:
            state.update_job(job_id, state="done", message="光引擎信息已缓存")
    except Exception as exc:
        with state.telemetry_lock:
            previous = state.optics_cache
            retained = list(previous.get("modules", []))
            state.optics_cache = {
                "state": "partial" if retained else "error",
                "sampled": int(time.time()),
                "modules": retained,
                "error": str(exc),
            }
        state.update_job(job_id, state="failed", message="光引擎信息读取失败", error=str(exc))
        schedule_optics_cache(state, delay=retry_seconds, retry_seconds=next_retry)


def fan_apply_worker(state, job_id, value):
    backup = None
    try:
        state.update_job(job_id, state="running", message="备份风扇配置")
        backup = make_backup(state.config)
        config_path = state.config.get("fan_config", FAN_CONFIG_PATH)
        script_path = state.config.get("fan_init_script", FAN_INIT_SCRIPT)
        atomic_write(
            config_path,
            json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            0o600,
        )
        atomic_write(script_path, render_fan_init(value), 0o644)
        state.update_job(job_id, message="写入 LM96163 风扇曲线")
        output = queue_testpoint_script(
            state.config, script_path, FAN_COMPLETE_MARKER, timeout=20
        )
        errors = meaningful_sdk_errors(output)
        if errors:
            raise RuntimeError("; ".join(errors[-4:]))
        state.update_job(
            job_id,
            state="done",
            message="风扇曲线已应用",
            backup=backup,
            fan_control=fan_config_payload(state.config),
        )
    except Exception as exc:
        rollback_ok = False
        rollback_error = None
        if backup:
            try:
                restore_backup(state.config, backup)
                previous_script = state.config.get("fan_init_script", FAN_INIT_SCRIPT)
                if os.path.exists(previous_script):
                    queue_testpoint_script(
                        state.config, previous_script, FAN_COMPLETE_MARKER, timeout=20
                    )
                rollback_ok = True
            except Exception as rollback_exc:
                rollback_error = str(rollback_exc)
        state.update_job(
            job_id,
            state="failed",
            message="风扇曲线应用失败，已回滚" if rollback_ok else "风扇曲线应用失败",
            error=str(exc),
            rollback_ok=rollback_ok,
            rollback_error=rollback_error,
            backup=backup,
        )


def validate_port_admin(body, parsed):
    if not isinstance(body, dict) or not isinstance(body.get("enabled"), bool):
        raise ApiError(400, "端口开关参数无效")
    endpoints = topology_endpoints(parsed)
    if "key" in body:
        key = str(body["key"])
        if key not in {item["key"] for item in endpoints}:
            raise ApiError(400, "端口不属于当前拓扑")
        return "port", key, body["enabled"]
    try:
        mpo = int(body.get("mpo"))
    except (TypeError, ValueError):
        raise ApiError(400, "MPO 编号必须是 1 或 2") from None
    if mpo not in (1, 2):
        raise ApiError(400, "MPO 编号必须是 1 或 2")
    return "mpo", mpo, body["enabled"]


def update_port_admin(parsed, current, scope, target, enabled):
    value = normalize_port_config(current, parsed)
    endpoints = topology_endpoints(parsed)
    if scope == "port":
        value["enabled"][target] = enabled
        return value
    keys = [
        item["key"]
        for item in endpoints
        if GROUP_BY_KEY[item["group"]]["mpo"] == target
    ]
    for key in keys:
        value["enabled"][key] = enabled
    return value


def run_port_admin_script(state, parsed, value):
    path = None
    try:
        fd, path = tempfile.mkstemp(
            prefix="port-admin-", suffix=".tp", dir="/run/pe31625g24dira-testpoint"
        )
        try:
            os.write(
                fd,
                (
                    port_admin_script(parsed, value)
                    + 'if ($pe_xcvr_ok) {\n'
                    + f'    print "{PORT_ADMIN_COMPLETE_MARKER}\\n";\n'
                    + '} else {\n'
                    + f'    print "ERROR: {XCVR_VERIFY_FAILURE_MARKER}\\n";\n'
                    + '}\n'
                ).encode("utf-8"),
            )
        finally:
            os.close(fd)
        output = queue_testpoint_script(
            state.config, path, PORT_ADMIN_COMPLETE_MARKER, timeout=70
        )
        errors = meaningful_sdk_errors(output)
        if errors:
            raise RuntimeError("; ".join(errors[-4:]))
    finally:
        if path:
            with suppress(OSError):
                os.unlink(path)


def port_admin_worker(state, job_id, scope, target, enabled):
    previous = None
    try:
        state.update_job(job_id, state="running", message="更新端口状态")
        _, parsed = parse_platform(state.config["platform_persistent"])
        previous = load_port_config(state.config, parsed)
        value = update_port_admin(parsed, previous, scope, target, enabled)
        l2 = load_l2_config(state.config, parsed)
        if enabled:
            blocked = l2["loop_protection"]["blocked"]
            for key, is_enabled in value["enabled"].items():
                if is_enabled:
                    blocked.pop(key, None)
        path = state.config.get("port_config", PORT_CONFIG_PATH)
        atomic_write(path, json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", 0o600)
        vlans = load_vlan_config(state.config, parsed)
        atomic_write(
            state.config["startup_script"],
            startup_text(parsed, vlans, value, l2),
            0o644,
        )
        atomic_write(
            state.config.get("l2_config", L2_CONFIG_PATH),
            json.dumps(l2, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            0o600,
        )
        run_port_admin_script(state, parsed, value)
        state.update_job(
            job_id,
            state="done",
            message="端口已开启" if enabled else "端口已关闭",
            port_admin=port_admin_payload(parsed, value),
        )
    except Exception as exc:
        if previous is not None:
            try:
                path = state.config.get("port_config", PORT_CONFIG_PATH)
                atomic_write(path, json.dumps(previous, ensure_ascii=False, indent=2, sort_keys=True) + "\n", 0o600)
                vlans = load_vlan_config(state.config, parsed)
                atomic_write(
                    state.config["startup_script"],
                    startup_text(parsed, vlans, previous, load_l2_config(state.config, parsed)),
                    0o644,
                )
                run_port_admin_script(state, parsed, previous)
            except Exception:
                pass
        state.update_job(job_id, state="failed", message="端口开关应用失败", error=str(exc))


def vlan_apply_worker(state, job_id, value):
    backup = None
    previous = None
    parsed = None
    try:
        state.update_job(job_id, state="running", message="在线应用 VLAN")
        _, parsed = parse_platform(state.config["platform_persistent"])
        previous = validate_vlan_config(load_vlan_config(state.config, parsed), parsed)
        backup = make_backup(state.config)
        port_config = load_port_config(state.config, parsed)
        run_live_configuration(
            state.config,
            parsed,
            previous,
            parsed,
            value,
            port_config,
            include_topology=False,
        )
        vlan_path = state.config.get("vlan_config", "/etc/pe31625g24dira/webui/vlans.json")
        atomic_write(
            vlan_path,
            json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            0o600,
        )
        atomic_write(
            state.config["startup_script"],
            startup_text(parsed, value, port_config, load_l2_config(state.config, parsed)),
            0o644,
        )
        state.update_job(
            job_id,
            state="done",
            message="VLAN 已应用",
            backup=backup,
            vlans=value["vlans"],
        )
    except Exception as exc:
        rollback_ok = False
        rollback_error = None
        if backup and previous is not None and parsed is not None:
            try:
                port_config = load_port_config(state.config, parsed)
                run_live_configuration(
                    state.config,
                    parsed,
                    value,
                    parsed,
                    previous,
                    port_config,
                    include_topology=False,
                )
                restore_backup(state.config, backup)
                rollback_ok = True
            except Exception as rollback_exc:
                rollback_error = str(rollback_exc)
                try:
                    restore_backup(state.config, backup)
                    started = time.time()
                    run_systemctl("restart")
                    rollback_ok, _, _ = wait_for_switch(started)
                except Exception as restart_exc:
                    rollback_error += "; " + str(restart_exc)
        state.update_job(
            job_id,
            state="failed",
            message="VLAN 应用失败，已回滚"
            if rollback_ok
            else "VLAN 应用与回滚均失败",
            error=str(exc),
            rollback_ok=rollback_ok,
            rollback_error=rollback_error,
            backup=backup,
        )


def run_l2_configuration(config, parsed, previous, value):
    commands = l2_sdk_commands(parsed, previous, remove=True)
    commands.extend(l2_sdk_commands(parsed, value))
    script = tp_script(commands) + f'print "{L2_APPLY_COMPLETE_MARKER}\\n";\n'
    output = run_temporary_testpoint(
        config, "l2-apply", script, L2_APPLY_COMPLETE_MARKER, timeout=35
    )
    errors = meaningful_sdk_errors(output)
    if errors:
        raise RuntimeError("; ".join(errors[-6:]))


def l2_apply_worker(state, job_id, value):
    previous = None
    parsed = None
    try:
        state.update_job(job_id, state="running", message="应用网络功能")
        _, parsed = parse_platform(state.config["platform_persistent"])
        previous = load_l2_config(state.config, parsed)
        run_l2_configuration(state.config, parsed, previous, value)
        atomic_write(
            state.config.get("l2_config", L2_CONFIG_PATH),
            json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            0o600,
        )
        vlans = load_vlan_config(state.config, parsed)
        ports = load_port_config(state.config, parsed)
        atomic_write(
            state.config["startup_script"],
            startup_text(parsed, vlans, ports, value),
            0o644,
        )
        state.update_job(
            job_id,
            state="done",
            message="网络功能已应用",
            l2=l2_payload(state.config, parsed, state),
        )
    except Exception as exc:
        rollback_ok = False
        if previous is not None and parsed is not None:
            with suppress(Exception):
                run_l2_configuration(state.config, parsed, value, previous)
                rollback_ok = True
        state.update_job(
            job_id,
            state="failed",
            message="网络功能应用失败，已回滚" if rollback_ok else "网络功能应用失败",
            error=str(exc),
            rollback_ok=rollback_ok,
        )


def lldp_refresh_worker(state, job_id):
    try:
        state.update_job(job_id, state="running", message="识别邻居端口")
        _, parsed = parse_platform(state.config["platform_persistent"])
        output = run_temporary_testpoint(
            state.config,
            "lldp-fdb",
            diagnostic_tp("show mac table all", FDB_COMPLETE_MARKER),
            FDB_COMPLETE_MARKER,
            timeout=30,
        )
        logical_to_key = {
            str(item["logical"]): item["key"] for item in topology_endpoints(parsed)
        }
        mapping = {}
        for entry in parse_fdb(output)["entries"]:
            destination = str(entry.get("destination", ""))
            match = re.search(r"\d+", destination)
            if match and match.group() in logical_to_key:
                mapping[entry["mac"]] = logical_to_key[match.group()]
        with state.l2_lock:
            state.lldp_mac_to_endpoint = mapping
        result = l2_payload(state.config, parsed, state)["neighbors"]
        state.update_job(
            job_id,
            state="done",
            message=f"发现 {len(result['neighbors'])} 个 LLDP 邻居",
            neighbors=result,
        )
    except Exception as exc:
        state.update_job(job_id, state="failed", message="邻居识别失败", error=str(exc))


def loop_block_worker(state, job_id, endpoint_key_value):
    try:
        state.update_job(job_id, state="running", message="隔离疑似环路端口")
        _, parsed = parse_platform(state.config["platform_persistent"])
        ports = load_port_config(state.config, parsed)
        if endpoint_key_value not in ports["enabled"]:
            raise RuntimeError("端口已不在当前拓扑中")
        ports["enabled"][endpoint_key_value] = False
        run_port_admin_script(state, parsed, ports)
        atomic_write(
            state.config.get("port_config", PORT_CONFIG_PATH),
            json.dumps(ports, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            0o600,
        )
        l2 = load_l2_config(state.config, parsed)
        l2["loop_protection"]["blocked"][endpoint_key_value] = int(time.time())
        atomic_write(
            state.config.get("l2_config", L2_CONFIG_PATH),
            json.dumps(l2, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            0o600,
        )
        atomic_write(
            state.config["startup_script"],
            startup_text(parsed, load_vlan_config(state.config, parsed), ports, l2),
            0o644,
        )
        state.update_job(job_id, state="done", message="疑似环路端口已关闭")
    except Exception as exc:
        state.update_job(job_id, state="failed", message="环路端口隔离失败", error=str(exc))


def loop_protection_watchdog(state, poll_seconds=2):
    previous = {}
    strikes = {}
    while True:
        try:
            _, parsed = parse_platform(state.config["platform_persistent"])
            l2 = load_l2_config(state.config, parsed)
            if not l2["loop_protection"]["enabled"]:
                previous.clear()
                strikes.clear()
                time.sleep(poll_seconds)
                continue
            status = direct_port_payload(state.config)
            logical_to_key = {
                str(item["logical"]): item["key"] for item in topology_endpoints(parsed)
            }
            threshold = l2["loop_protection"]["broadcast_pps"]
            now = time.monotonic()
            for logical, port in status["ports"].items():
                key = logical_to_key.get(logical)
                if not key or key in l2["loop_protection"]["blocked"]:
                    continue
                count = port["statistics"]["rx"]["broadcast"]
                old = previous.get(key)
                previous[key] = (now, count)
                if not old or count < old[1] or now <= old[0]:
                    continue
                rate = (count - old[1]) / (now - old[0])
                strikes[key] = strikes.get(key, 0) + 1 if rate >= threshold else 0
                if strikes[key] >= 3:
                    if state.start_operation(
                        "loop-protection",
                        loop_block_worker,
                        key,
                        priority=10,
                        coalesce_key=f"loop-protection:{key}",
                    ):
                        strikes[key] = 0
        except Exception as exc:
            print(f"Loop protection sample skipped: {exc}", flush=True)
        time.sleep(poll_seconds)


class ReusableThreadingHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True


class Handler(BaseHTTPRequestHandler):
    server_version = "PE31625G24DIRA-Switch-Manager/" + APP_VERSION

    def log_message(self, fmt, *args):
        print(f"{self.client_address[0]} - {fmt % args}")

    @property
    def app_state(self):
        return self.server.app_state

    def security_headers(self):
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; style-src 'self'; script-src 'self'; connect-src 'self'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'",
        )

    def json_response(self, status, payload, headers=None):
        data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.security_headers()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        for name, value in headers or []:
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(data)

    def redirect(self, location):
        self.send_response(303)
        self.security_headers()
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def session_token(self):
        for item in self.headers.get("Cookie", "").split(";"):
            name, separator, value = item.strip().partition("=")
            if separator and name == SESSION_COOKIE:
                return value
        return None

    def auth_ok(self, api=False):
        self.session = self.app_state.get_session(self.session_token())
        if self.session:
            return True
        if api:
            self.json_response(401, {"error": "登录已失效，请重新登录"})
        else:
            self.redirect("/login")
        return False

    def login(self):
        if not config_initialized(self.app_state.config):
            self.json_response(409, {"error": "WebUI 尚未初始化"})
            return
        address = self.client_address[0]
        now = time.time()
        if not self.app_state.login_allowed(address, now):
            self.json_response(429, {"error": "登录失败次数过多，请一分钟后再试"})
            return
        try:
            body = self.body_json()
            username = body.get("username", "")
            password = body.get("password", "")
            valid = (
                isinstance(username, str)
                and isinstance(password, str)
                and credentials_valid(self.app_state.config, username, password)
            )
        except Exception:
            valid = False
        if not valid:
            self.app_state.record_login_failure(address, now)
            self.json_response(401, {"error": "用户名或密码错误"})
            return
        self.app_state.clear_login_failures(address)
        token, session = self.app_state.new_session(username)
        cookie = f"{SESSION_COOKIE}={token}; Path=/; Max-Age={SESSION_SECONDS}; HttpOnly; SameSite=Strict"
        self.json_response(
            200, {"ok": True, "expires": session["expires"]}, [("Set-Cookie", cookie)]
        )

    def csrf_ok(self):
        if not self.session or not hmac.compare_digest(
            self.headers.get("X-PE31625G24DIRA-CSRF", ""), self.session["csrf"]
        ):
            raise ApiError(403, "CSRF 校验失败")

    def body_json(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            raise ApiError(400, "Content-Length 无效") from None
        if length <= 0 or length > 262144:
            raise ApiError(400, "请求正文为空或过大")
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except Exception:
            raise ApiError(400, "JSON 格式无效") from None

    def body_bytes(self, maximum):
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            raise ApiError(400, "Content-Length 无效") from None
        if length <= 0 or length > maximum:
            raise ApiError(400, "上传文件为空或过大")
        data = self.rfile.read(length)
        if len(data) != length:
            raise ApiError(400, "上传文件不完整")
        return data

    def serve_static(self, relative, content_type):
        root = Path(self.app_state.config["static_root"]).resolve()
        path = (root / relative).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise ApiError(404, "Not found")
        data = path.read_bytes()
        self.send_response(200)
        self.security_headers()
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def start_operation(self, kind, target, *args):
        job = self.app_state.start_operation(kind, target, *args)
        if job is None:
            raise ApiError(429, "SDK 操作队列已满，请稍后重试")
        return self.json_response(202, job)

    def do_GET(self):
        try:
            path = urlparse(self.path).path
            initialized = config_initialized(self.app_state.config)
            if path == "/setup":
                if initialized:
                    return self.redirect("/login")
                return self.serve_static("setup.html", "text/html; charset=utf-8")
            if path == "/setup.js":
                if initialized:
                    raise ApiError(404, "Not found")
                return self.serve_static("setup.js", "application/javascript; charset=utf-8")
            if path == "/login":
                if not initialized:
                    return self.redirect("/setup")
                if self.app_state.get_session(self.session_token()):
                    return self.redirect("/")
                return self.serve_static("login.html", "text/html; charset=utf-8")
            if path == "/login.js":
                return self.serve_static("login.js", "application/javascript; charset=utf-8")
            if path == "/theme.js":
                return self.serve_static("theme.js", "application/javascript; charset=utf-8")
            if path == "/style.css":
                return self.serve_static("style.css", "text/css; charset=utf-8")
            if path == "/api/identity":
                identity = hardware_identity_payload(
                    self.app_state.config.get("platform_active")
                )
                return self.json_response(
                    200,
                    {
                        "model": identity.get("display_model")
                        or identity.get("model")
                        or "PE31625G24DIRA"
                    },
                )
            if not initialized:
                if path.startswith("/api/"):
                    raise ApiError(503, "WebUI 尚未初始化")
                return self.redirect("/setup")
            if not self.auth_ok(path.startswith("/api/")):
                return
            if path in (
                "/",
                "/overview",
                "/sensors",
                "/system",
                "/cooling",
                "/ports",
                "/statistics",
                "/vlans",
                "/network",
                "/backup",
                "/settings",
                "/logs",
            ):
                return self.serve_static("index.html", "text/html; charset=utf-8")
            if path == "/app.js":
                return self.serve_static("app.js", "application/javascript; charset=utf-8")
            if path == "/api-client.js":
                return self.serve_static("api-client.js", "application/javascript; charset=utf-8")
            if path == "/controls.js":
                return self.serve_static("controls.js", "application/javascript; charset=utf-8")
            if path == "/dashboard.js":
                return self.serve_static("dashboard.js", "application/javascript; charset=utf-8")
            if path == "/diagnostics.js":
                return self.serve_static("diagnostics.js", "application/javascript; charset=utf-8")
            if path == "/maintenance.js":
                return self.serve_static("maintenance.js", "application/javascript; charset=utf-8")
            if path == "/api/state":
                payload = platform_payload(self.app_state.config)
                _, parsed = parse_platform(self.app_state.config["platform_persistent"])
                payload["l2"] = l2_payload(self.app_state.config, parsed, self.app_state)
                payload["csrf"] = self.session["csrf"]
                payload["username"] = self.session["username"]
                payload["system_settings"] = system_settings_payload()
                return self.json_response(200, payload)
            if path == "/api/health":
                return self.json_response(
                    200, {"version": APP_VERSION, **service_health_payload()}
                )
            if path == "/api/config/export":
                return self.json_response(
                    200, configuration_export_payload(self.app_state.config)
                )
            if path == "/api/logs":
                query = parse_qs(urlparse(self.path).query)
                return self.json_response(
                    200, system_log_payload(query.get("source", ["system"])[0])
                )
            if path == "/api/telemetry":
                maybe_refresh_sensors(self.app_state)
                return self.json_response(200, telemetry_payload(self.app_state))
            if path == "/api/system/upgrade/status":
                return self.json_response(200, upgrade_job_status())
            if path.startswith("/api/jobs/"):
                job = self.app_state.get_job(path.rsplit("/", 1)[-1])
                if not job:
                    raise ApiError(404, "任务不存在")
                return self.json_response(200, job)
            raise ApiError(404, "Not found")
        except ApiError as exc:
            self.json_response(exc.status, {"error": exc.message})
        except Exception as exc:
            traceback.print_exc()
            self.json_response(500, {"error": str(exc)})

    def do_POST(self):
        try:
            path = urlparse(self.path).path
            if path == "/api/setup":
                body = self.body_json()
                username = initialize_admin(
                    self.app_state, body.get("username", ""), body.get("password", "")
                )
                return self.json_response(
                    201, {"ok": True, "username": username, "message": "管理员账户已创建"}
                )
            if path == "/api/login":
                return self.login()
            if not self.auth_ok(True):
                return
            self.csrf_ok()
            if path == "/api/logout":
                self.body_json()
                self.app_state.revoke_session(self.session_token())
                cookie = f"{SESSION_COOKIE}=; Path=/; Max-Age=0; HttpOnly; SameSite=Strict"
                return self.json_response(200, {"ok": True}, [("Set-Cookie", cookie)])
            if path == "/api/account":
                body = self.body_json()
                username = update_admin_credentials(
                    self.app_state,
                    self.session["username"],
                    body.get("current_password", ""),
                    body.get("username", ""),
                    body.get("new_password", ""),
                )
                cookie = f"{SESSION_COOKIE}=; Path=/; Max-Age=0; HttpOnly; SameSite=Strict"
                return self.json_response(
                    200,
                    {"ok": True, "username": username, "reauthenticate": True},
                    [("Set-Cookie", cookie)],
                )
            if path == "/api/system/settings":
                return self.json_response(200, apply_system_settings(self.body_json()))
            if path == "/api/system/upgrade/upload":
                if self.app_state.operation_busy():
                    raise ApiError(409, "硬件配置或 SDK 读取正在进行")
                value = stage_upgrade_archive(
                    self.body_bytes(UPGRADE_MAX_BYTES),
                    self.headers.get("X-PE31625G24DIRA-Filename", "update.tar.gz"),
                )
                return self.json_response(201, value)
            if path == "/api/system/upgrade/latest":
                body = self.body_json()
                if self.app_state.operation_busy():
                    raise ApiError(409, "硬件配置或 SDK 读取正在进行")
                return self.json_response(201, stage_latest_upgrade(
                    bool(body.get("include_prerelease")), bool(body.get("allow_downgrade"))
                ))
            if path == "/api/system/upgrade/audit":
                body = self.body_json()
                return self.json_response(200, audit_pending_upgrade(bool(body.get("allow_downgrade"))))
            if path == "/api/system/upgrade/apply":
                body = self.body_json()
                if not isinstance(body, dict) or body.get("confirm") is not True:
                    raise ApiError(400, "请确认执行更新")
                if self.app_state.operation_busy():
                    raise ApiError(409, "硬件配置或 SDK 读取正在进行")
                allow_downgrade = bool(body.get("allow_downgrade"))
                audit_pending_upgrade(allow_downgrade)
                return self.json_response(202, start_pending_upgrade(allow_downgrade))
            if path == "/api/config/import":
                value = validate_configuration_import(
                    self.app_state.config, self.body_json()
                )
                return self.start_operation(
                    "config-import", configuration_import_worker, value
                )
            if path == "/api/system/poweroff":
                validate_poweroff_request(self.body_json())
                if self.app_state.operation_busy():
                    raise ApiError(409, "硬件配置或 SDK 读取正在进行，请完成后再关机")
                self.json_response(202, {"ok": True, "message": "系统将在 2 秒后开始安全关机"})
                schedule_poweroff()
                return
            if path == "/api/system/reboot":
                validate_poweroff_request(self.body_json())
                if self.app_state.operation_busy():
                    raise ApiError(409, "硬件配置或 SDK 读取正在进行，请完成后再重启")
                self.json_response(202, {"ok": True, "message": "系统将在 2 秒后重启"})
                schedule_reboot()
                return
            if path == "/api/system/factory-reset":
                body = self.body_json()
                if not isinstance(body, dict) or body.get("confirm") is not True:
                    raise ApiError(400, "请确认恢复默认配置")
                return self.start_operation("factory-reset", factory_reset_worker)
            if path == "/api/apply":
                requested, total, warning = validate_requested(
                    self.app_state.config, self.body_json()
                )
                return self.start_operation("apply", apply_worker, requested, total, warning)
            if path == "/api/topology/preview":
                return self.json_response(
                    200, topology_preview(self.app_state.config, self.body_json())
                )
            if path == "/api/refresh":
                self.body_json()
                return self.start_operation("status", live_status_worker)
            if path == "/api/sensors/refresh":
                self.body_json()
                return self.start_operation("sensors", sensor_refresh_job_worker)
            if path == "/api/vlans/apply":
                _, parsed = parse_platform(self.app_state.config["platform_persistent"])
                value = validate_vlan_config(self.body_json(), parsed)
                return self.start_operation("vlan", vlan_apply_worker, value)
            if path == "/api/l2/apply":
                _, parsed = parse_platform(self.app_state.config["platform_persistent"])
                value = validate_l2_config(self.body_json(), parsed)
                return self.start_operation("l2", l2_apply_worker, value)
            if path == "/api/l2/neighbors/refresh":
                self.body_json()
                return self.start_operation("lldp", lldp_refresh_worker)
            if path == "/api/fan/apply":
                value = validate_fan_config(self.body_json())
                return self.start_operation("fan", fan_apply_worker, value)
            if path == "/api/ports/admin":
                _, parsed = parse_platform(self.app_state.config["platform_persistent"])
                scope, target, enabled = validate_port_admin(self.body_json(), parsed)
                return self.start_operation("port-admin", port_admin_worker, scope, target, enabled)
            if path == "/api/fdb/refresh":
                self.body_json()
                return self.start_operation("fdb", fdb_worker)
            if path == "/api/ports/diagnostics":
                body = self.body_json()
                try:
                    logical = int(body.get("logical"))
                except (TypeError, ValueError):
                    raise ApiError(400, "逻辑端口必须是整数") from None
                _, parsed = parse_platform(self.app_state.config["platform_persistent"])
                if logical not in {item["logical"] for item in topology_endpoints(parsed)}:
                    raise ApiError(400, "逻辑端口不属于当前外部拓扑")
                return self.start_operation(
                    "lane-diagnostic", lane_diagnostic_worker, logical
                )
            raise ApiError(404, "Not found")
        except ApiError as exc:
            self.json_response(exc.status, {"error": exc.message})
        except Exception as exc:
            traceback.print_exc()
            self.json_response(500, {"error": str(exc)})


def check_platform(path):
    _, parsed = parse_platform(path)
    print(json.dumps(parsed, indent=2, sort_keys=True))


def sync_runtime_config(config_path):
    config = read_json(config_path)
    _, parsed = parse_platform(config["platform_persistent"])
    vlans = validate_vlan_config(load_vlan_config(config, parsed), parsed)
    port_config = load_port_config(config, parsed)
    l2 = load_l2_config(config, parsed)
    atomic_write(
        config["vlan_config"],
        json.dumps(vlans, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        0o600,
    )
    atomic_write(
        config["startup_script"],
        startup_text(parsed, vlans, port_config, l2),
        0o600,
    )
    atomic_write(config["status_script"], status_text(parsed), 0o600)
    print(
        "runtime configuration synchronized: "
        f"{parsed['external_count']} external ports, {len(vlans['vlans'])} VLANs"
    )


def main():
    parser = argparse.ArgumentParser(description="PE31625G24DIRA Switch Manager")
    parser.add_argument("--config", default=DEFAULT_CONFIG)
    parser.add_argument("--init-config", metavar="PATH")
    parser.add_argument("--listen", default="0.0.0.0")
    parser.add_argument("--port", type=int, help="override HTTP(S) port")
    parser.add_argument("--check-platform", metavar="PATH")
    parser.add_argument("--sync-runtime", metavar="CONFIG")
    args = parser.parse_args()
    if args.init_config:
        create_config(args.init_config, args.listen, args.port or 80)
        return
    if args.check_platform:
        check_platform(args.check_platform)
        return
    if args.sync_runtime:
        sync_runtime_config(args.sync_runtime)
        return
    config = read_json(args.config)
    state = State(config, args.config)
    state.lldp_monitor = LldpMonitor(config.get("cpu_interface", "enp1s0"))
    listen = config.get("listen", args.listen)
    port = args.port or int(config.get("port", 80))
    server = ReusableThreadingHTTPServer((listen, port), Handler)
    server.app_state = state
    threading.Thread(target=mac_repair_watchdog, args=(state,), daemon=True).start()
    threading.Thread(target=state.lldp_monitor.run, daemon=True).start()
    threading.Thread(target=loop_protection_watchdog, args=(state,), daemon=True).start()
    schedule_optics_cache(state)
    print(f"PE31625G24DIRA Switch Manager {APP_VERSION} listening on http://{listen}:{port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
