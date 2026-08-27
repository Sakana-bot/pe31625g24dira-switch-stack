import importlib.util
import hashlib
import io
import json
import os
import struct
import tempfile
import tarfile
import threading
import unittest
from http.client import HTTPConnection
from pathlib import Path
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
SPEC = importlib.util.spec_from_file_location("fmweb", os.path.join(HERE, "app.py"))
APP = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(APP)
BASE = Path(HERE, "reference_original_6x100.cfg").read_text(encoding="utf-8")


class SystemManagementTests(unittest.TestCase):
    def test_upgrade_version_comparison(self):
        with mock.patch.object(APP, "installed_package_version", return_value="0.9.0"):
            self.assertTrue(APP.upgrade_version_state("1.0.0")["update_available"])
            self.assertEqual(APP.upgrade_version_state("0.9.0")["version_relation"], "current")
            self.assertEqual(APP.upgrade_version_state("0.8.9")["version_relation"], "downgrade")

    def test_release_candidate_ordering(self):
        self.assertGreater(APP.version_key("1.3.0-rc.2"), APP.version_key("1.3.0-rc.1"))
        self.assertGreater(APP.version_key("1.3.0"), APP.version_key("1.3.0-rc.2"))
        self.assertGreater(APP.version_key("1.3.0-rc.1"), APP.version_key("1.3.0-dev"))

    def test_downgrade_requires_explicit_permission(self):
        metadata = {"version_relation": "downgrade"}
        with self.assertRaises(APP.ApiError):
            APP.upgrade_allowed(metadata)
        self.assertTrue(APP.upgrade_allowed(metadata, allow_downgrade=True))

    def test_log_sources_are_fixed_commands(self):
        self.assertEqual(APP.system_log_command("kernel"), ["/usr/bin/dmesg", "--color=never"])
        self.assertIn(APP.SERVICE, APP.system_log_command("switch"))
        with self.assertRaises(APP.ApiError):
            APP.system_log_command("../../etc/passwd")

    def test_system_settings_reject_invalid_hostname_before_writing(self):
        with self.assertRaises(APP.ApiError):
            APP.apply_system_settings({"hostname": "bad hostname"})

    def test_upgrade_archive_rejects_traversal(self):
        stream = io.BytesIO()
        with tarfile.open(fileobj=stream, mode="w:gz") as archive:
            member = tarfile.TarInfo("../outside")
            member.size = 1
            archive.addfile(member, io.BytesIO(b"x"))
        with tempfile.TemporaryDirectory() as directory:
            original = APP.UPGRADE_ROOT
            APP.UPGRADE_ROOT = directory
            try:
                with self.assertRaises(APP.ApiError):
                    APP.stage_upgrade_archive(stream.getvalue())
            finally:
                APP.UPGRADE_ROOT = original

    def test_latest_upgrade_verifies_release_sidecar(self):
        name = "pe31625g24dira-deploy-kit-1.0.0-webui-1.0.0.tar.gz"
        archive = b"release archive"
        digest = hashlib.sha256(archive).hexdigest()
        release = json.dumps({
            "tag_name": "v1.0.0",
            "assets": [
                {"name": name, "browser_download_url": "https://github.com/package"},
                {"name": name + ".sha256", "browser_download_url": "https://github.com/hash"},
            ],
        }).encode()

        def download(url, _limit):
            if url == APP.RELEASE_API:
                return release
            if url.endswith("/package"):
                return archive
            return f"{digest}  {name}\n".encode()

        with mock.patch.object(APP, "download_release_url", side_effect=download), mock.patch.object(
            APP, "stage_upgrade_archive", return_value={"filename": name, "version": "1.0.0"}
        ):
            value = APP.stage_latest_upgrade()
        self.assertEqual(value["release"], "v1.0.0")
        self.assertEqual(value["filename"], name)


class TopologyTests(unittest.TestCase):
    def render(self, factory):
        request = {group["key"]: factory(group) for group in APP.GROUPS}
        text, parsed = APP.generate_platform(BASE, request)
        self.assertEqual(len(parsed["groups"]), 6)
        self.assertEqual(parsed, APP.parse_platform_text(text))
        return text, parsed

    def test_vpd_identity_decodes_live_board_fields(self):
        data = (
            b"\x82\x7d\x00"
            b"01v00       \x00"
            + b"\xff" * 32
            + b"PRDi\x01\x06\x04L\x04\x00"
            + b"PE31625G24DiRA-MPS\x00\x00"
            + b"0490"
            + b"\xff" * 12
            + b"S916260490015\x00"
        )
        identity = APP.parse_vpd_identity(data, "0x1374")
        self.assertEqual(identity["display_model"], "Silicom PE31625G24DIRA-MPS")
        self.assertEqual(identity["vpd_version"], "0490")
        self.assertEqual(identity["serial"], "S916260490015")
        self.assertEqual(identity["hardware_family"], "Silicom B0")
        self.assertEqual(identity["hw_version"], 4)

    def test_all_bonded_100(self):
        text, parsed = self.render(lambda group: {"layout": "bonded", "speed": 100000})
        self.assertEqual(parsed["external_count"], 6)
        self.assertEqual(parsed["external"], 600000)
        self.assertIn("switch.0.numPorts int 30", text)
        self.assertIn("switch.0.cpuPort int 27", text)

    def test_factory_100g_scheduler_value_is_exposed_as_100g(self):
        parsed = APP.parse_platform_text(BASE)
        self.assertEqual([group["speed"] for group in parsed["groups"]], [100000] * 6)
        self.assertEqual(
            [group["scheduler_speed"] for group in parsed["groups"]], [50000] * 6
        )
        self.assertEqual(parsed["external"], 600000)

    def test_all_split_25(self):
        text, parsed = self.render(lambda group: {"layout": "split", "speeds": [25000] * 4})
        self.assertEqual(parsed["external_count"], 24)
        self.assertEqual(parsed["external"], 600000)
        self.assertIn("switch.0.numPorts int 30", text)
        self.assertIn("switch.0.cpuPort int 27", text)
        self.assertIn("portIndex.24.hwResourceId int 0x305", text)

    def test_current_mixed_layout(self):
        _, parsed = self.render(
            lambda group: (
                {"layout": "split", "speeds": [10000] * 4}
                if group["mpo"] == 1
                else {"layout": "bonded", "speed": 100000}
            )
        )
        self.assertEqual(parsed["external_count"], 15)
        self.assertEqual(parsed["external"], 420000)
        self.assertEqual(
            [group["layout"] for group in parsed["groups"]],
            ["split"] * 3 + ["bonded"] * 3,
        )

    def test_runtime_sync_uses_restored_platform_topology(self):
        with tempfile.TemporaryDirectory() as directory:
            platform = os.path.join(directory, "platform.cfg")
            startup = os.path.join(directory, "startup.tp")
            status = os.path.join(directory, "status.tp")
            vlans = os.path.join(directory, "vlans.json")
            ports = os.path.join(directory, "ports.json")
            config_path = os.path.join(directory, "config.json")
            Path(platform).write_text(BASE, encoding="utf-8")
            Path(config_path).write_text(
                json.dumps(
                    {
                        "platform_persistent": platform,
                        "startup_script": startup,
                        "status_script": status,
                        "vlan_config": vlans,
                        "port_config": ports,
                    }
                ),
                encoding="utf-8",
            )
            APP.sync_runtime_config(config_path)
            self.assertIn("set port 1,2,3,4,5,6 up", Path(startup).read_text(encoding="utf-8"))
            self.assertNotIn("flushOnPortDown", Path(startup).read_text(encoding="utf-8"))
            status_text = Path(status).read_text(encoding="utf-8")
            self.assertIn("show port 1..6", status_text)
            self.assertNotIn("xcvr", status_text)
            self.assertIn(APP.STATUS_COMPLETE_MARKER, status_text)
            saved_vlans = json.loads(Path(vlans).read_text(encoding="utf-8"))
            self.assertEqual(len(saved_vlans["vlans"][0]["untagged"]), 6)

    def test_default_vlan_maps_every_endpoint_to_vlan_one(self):
        _, parsed = self.render(
            lambda group: (
                {"layout": "split", "speeds": [10000] * 4}
                if group["mpo"] == 1
                else {"layout": "bonded", "speed": 100000}
            )
        )
        value = APP.default_vlan_config(parsed)
        self.assertEqual(len(value["vlans"][0]["untagged"]), 15)
        commands = APP.vlan_commands(parsed, value, reset=True)
        self.assertIn("reset vlan table", commands)
        self.assertIn("create vlan 1", commands)
        self.assertNotIn("del vlan port 1 1..15", commands)
        self.assertIn("add vlan port 1 1..13,17,21", commands)
        self.assertIn("set port config 1..13,17,21 pvid 1", commands)
        self.assertIn("set port config 1..13,17,21 drop_bv on", commands)
        self.assertIn("set port config 1..13,17,21 drop_tagged on", commands)
        self.assertIn("set port config 1..13,17,21 drop_untagged off", commands)
        self.assertFalse(any("drop_mtu_violation" in command for command in commands))

    def test_vlan_validation_accepts_tagged_trunk_and_access_ports(self):
        _, parsed = self.render(lambda group: {"layout": "bonded", "speed": 100000})
        keys = [item["key"] for item in APP.topology_endpoints(parsed)]
        body = {
            "vlans": [
                {"id": 1, "name": "Default", "tagged": [], "untagged": keys[1:]},
                {
                    "id": 100,
                    "name": "Storage",
                    "tagged": keys[1:],
                    "untagged": [keys[0]],
                },
            ]
        }
        value = APP.validate_vlan_config(body, parsed)
        self.assertEqual([item["id"] for item in value["vlans"]], [1, 100])

    def test_vlan_commands_program_access_trunk_and_hybrid_admission(self):
        _, parsed = self.render(lambda group: {"layout": "bonded", "speed": 100000})
        keys = [item["key"] for item in APP.topology_endpoints(parsed)]
        body = {
            "vlans": [
                {"id": 1, "name": "Default", "tagged": [], "untagged": keys[2:]},
                {
                    "id": 100,
                    "name": "Storage",
                    "tagged": [keys[0], keys[1]],
                    "untagged": [],
                },
                {
                    "id": 200,
                    "name": "Native",
                    "tagged": [],
                    "untagged": [keys[1]],
                },
            ]
        }
        value = APP.validate_vlan_config(body, parsed)
        commands = APP.vlan_commands(parsed, value, reset=True)
        self.assertIn("set port config 1 drop_untagged on", commands)
        self.assertIn("set port config 1 drop_tagged off", commands)
        self.assertIn("set port config 5 drop_untagged off", commands)
        self.assertIn("set port config 5 drop_tagged off", commands)
        self.assertIn("set port config 9,13,17,21 drop_tagged on", commands)
        self.assertIn("set port config 1,5,9,13,17,21 drop_bv on", commands)

    def test_vlan_readback_verifies_membership_and_port_admission(self):
        _, parsed = self.render(lambda group: {"layout": "bonded", "speed": 100000})
        keys = [item["key"] for item in APP.topology_endpoints(parsed)]
        value = APP.validate_vlan_config(
            {
                "vlans": [
                    {"id": 1, "name": "Default", "tagged": [], "untagged": keys[1:]},
                    {"id": 100, "name": "Storage", "tagged": [keys[0]], "untagged": []},
                ]
            },
            parsed,
        )
        output = """
VLAN REF. MTU   MEMBERSHIP/TAGGING
------------------------------------------------------------
   1    1 1536  5  9 13 17 21  28 29
                U  U  U  U  U  U  U

VLAN REF. MTU   MEMBERSHIP/TAGGING
------------------------------------------------------------
 100    1 1536  1
                T

pvid                     1                 1                 1                 1                 1                 1
drop_bv                  on                on                on                on                on                on
drop_untagged            on                off               off               off               off               off
drop_tagged              off               on                on                on                on                on
"""
        APP.verify_vlan_readback_output(parsed, value, output)

    def test_vlan_mtu_profiles_program_vlan_and_port_limits(self):
        _, parsed = self.render(lambda group: {"layout": "bonded", "speed": 100000})
        body = APP.default_vlan_config(parsed)
        body["vlans"][0]["mtu"] = 9000
        commands = APP.vlan_commands(parsed, APP.validate_vlan_config(body, parsed))
        self.assertIn("set switch config mtu_list 0 9000", commands)
        self.assertIn("set vlan config mtu 1 0", commands)
        self.assertIn("set port config 1,5,9,13,17,21 max_frame_size 9000", commands)

    def test_topology_preview_lists_changed_epl(self):
        source = os.path.join(
            HERE, "..", "switch_service", "fm_platform_attributes_pe31625g24dira.cfg"
        )
        _, current = APP.parse_platform(source)
        requested = {}
        for group in current["groups"]:
            requested[group["key"]] = (
                {"layout": "bonded", "speed": group["speed"]}
                if group["layout"] == "bonded"
                else {"layout": "split", "speeds": [lane["speed"] for lane in group["lanes"]]}
            )
        requested["epl0"] = {"layout": "split", "speeds": [25000] * 4}
        preview = APP.topology_preview(
            {
                "platform_persistent": source,
                "topology_base": os.path.join(HERE, "reference_original_6x100.cfg"),
            },
            {"groups": requested},
        )
        self.assertEqual([change["epl"] for change in preview["changes"]], [0])
        self.assertFalse(preview["scheduler_proof"])

    def test_topology_reconcile_resets_changed_group_to_vlan_one(self):
        _, old = self.render(lambda group: {"layout": "bonded", "speed": 100000})
        value = APP.default_vlan_config(old)
        value["vlans"].append({"id": 20, "name": "Test", "tagged": ["epl5.bonded"], "untagged": []})
        _, new = self.render(
            lambda group: (
                {"layout": "split", "speeds": [10000] * 4}
                if group["key"] == "epl5"
                else {"layout": "bonded", "speed": 100000}
            )
        )
        reconciled = APP.reconcile_vlan_config(value, old, new)
        vlan1 = [item for item in reconciled["vlans"] if item["id"] == 1][0]
        self.assertTrue(all(f"epl5.lane{lane}" in vlan1["untagged"] for lane in range(4)))
        vlan20 = [item for item in reconciled["vlans"] if item["id"] == 20][0]
        self.assertNotIn("epl5.bonded", vlan20["tagged"])

    def test_topology_apply_reads_back_reconciled_vlan_without_second_restart(self):
        state = mock.Mock()
        state.config = {}
        parsed = {"external_count": 6}
        normalized = {"version": 3, "vlans": [{"id": 1}]}
        prepared = {
            "old_parsed": {},
            "old_vlans": normalized,
            "old_ports": {},
            "parsed": parsed,
            "vlans": normalized,
            "ports": {},
        }
        with (
            mock.patch.object(APP, "make_backup", return_value="/backup"),
            mock.patch.object(APP, "prepare_requested", return_value=prepared),
            mock.patch.object(APP, "persist_requested"),
            mock.patch.object(APP, "uses_fixed_logical_model", return_value=False),
            mock.patch.object(APP, "run_systemctl", return_value=0) as restart,
            mock.patch.object(APP, "wait_for_switch", return_value=(True, "ok", "")),
            mock.patch.object(APP, "verify_vlan_readback") as verify,
            mock.patch.object(APP, "platform_payload", return_value={}),
        ):
            APP.apply_worker(state, "job", {}, 600000, None)
        restart.assert_called_once_with("restart")
        verify.assert_called_once_with(state.config, parsed, normalized)
        self.assertEqual(state.update_job.call_args_list[-1].kwargs["state"], "done")

    def test_configuration_import_validates_portable_logical_settings(self):
        source = os.path.join(
            HERE, "..", "switch_service", "fm_platform_attributes_pe31625g24dira.cfg"
        )
        _, parsed = APP.parse_platform(source)
        body = {
            "format": APP.CONFIG_EXPORT_FORMAT,
            "format_version": APP.CONFIG_EXPORT_VERSION,
            "topology": {"groups": APP.topology_choices(parsed)},
            "vlans": APP.default_vlan_config(parsed),
            "ports": APP.default_port_config(parsed),
            "fan": APP.default_fan_config(),
        }
        value = APP.validate_configuration_import(
            {
                "platform_persistent": source,
                "topology_base": os.path.join(HERE, "reference_original_6x100.cfg"),
            },
            body,
        )
        self.assertEqual(value["parsed"]["external_count"], parsed["external_count"])


class RuntimeTests(unittest.TestCase):
    def test_mac_repair_only_selects_confirmed_cache_hardware_mismatch(self):
        output = """
MA_TABLE[12]:
                 CACHE ENTRY        DMAC ENTRY         SMAC ENTRY
State          : Young              Valid              Valid
MAC Address    : d85ed365a9d2       d85ed365a9d2       d85ed365a9d2
FID            : 1                  1                  1
Address Type   : DYNAMIC            --                 --
Port           : 3                  1                  1
MA_TABLE[13]:
                 CACHE ENTRY        DMAC ENTRY         SMAC ENTRY
State          : Young              Valid              Valid
MAC Address    : 001122334455       001122334455       001122334455
FID            : 20                 20                 20
Address Type   : DYNAMIC            --                 --
Port           : 4                  4                  4
2 entries listed
"""
        self.assertEqual(
            APP.mismatched_dynamic_macs(output),
            [
                {
                    "mac": "d8:5e:d3:65:a9:d2",
                    "fid": 1,
                    "cache_port": "3",
                    "dmac_port": "1",
                    "smac_port": "1",
                }
            ],
        )

    def test_first_stage_diagnostic_parsers(self):
        fdb = "aa:bb:cc:dd:ee:ff Dynamic 1 NA Local 1 1 3:0x5 - -"
        self.assertEqual(APP.parse_fdb(fdb)["entries"][0]["destination"], "1")
        self.assertEqual(APP.parse_fdb(fdb)["entries"][0]["fid"], 1)
        lane = "1 10G SR UP UP L | Y | 1 | CC | -- 24/NA"
        result = APP.parse_lane_diagnostic(lane, {"logical": 1, "lane": 0})
        self.assertEqual(result["lanes"][0]["signal"], "有信号")
        self.assertEqual(result["lanes"][0]["eye_height"], 24)
        self.assertIsNone(result["lanes"][0]["eye_width"])
        records = "\n".join(
            "PE31625G24DIRA_OPTICS mpo={} mux={} select_status=0 page_status=0 "
            "read_status=0 restore_page_status=0 raw={}".format(mpo, mpo, "00" * 24)
            for mpo in (1, 2)
        )
        identity = bytes.fromhex(
            "00d83380460cff426807d0ff04702a08aa2801004f3e0000"
            "464349204d657267654f707469637320000a0d313031323435"
            "38382d3231312020202045534f4d313634372d303030313120"
            "20202032303136313131342020202020202020202040000000"
            "00000000000000000000000000000000000000000000000000"
            "00000000"
        )
        records += "\n" + "\n".join(
            "PE31625G24DIRA_OPTICS_IDENTITY mpo={} page_status=0 read_status=0 "
            "restore_page_status=0 raw={}".format(mpo, identity.hex())
            for mpo in (1, 2)
        )
        modules = APP.parse_optics_diagnostic(records)["modules"]
        self.assertTrue(
            all(item["state"] == "unavailable" for item in modules)
        )
        self.assertEqual(modules[0]["identity"]["vendor"], "FCI / Amphenol")
        self.assertEqual(modules[0]["identity"]["part_number"], "10124588-211")
        self.assertEqual(modules[0]["identity"]["serial"], "ESOM1647-00011")
        self.assertEqual(modules[0]["identity"]["date_code"], "20161114")

    def test_default_fan_curve_renders_complete_hardware_lut(self):
        value = APP.validate_fan_config(APP.default_fan_config())
        entries = APP.fan_lut_points(value)
        script = APP.render_fan_init(value)
        self.assertEqual(len(entries), 12)
        self.assertEqual(entries[0], {"temperature_c": 0, "speed_percent": 50})
        self.assertEqual(entries[1], {"temperature_c": 35, "speed_percent": 50})
        self.assertEqual(entries[-2], {"temperature_c": 80, "speed_percent": 100})
        self.assertEqual(entries[-1], {"temperature_c": 127, "speed_percent": 100})
        self.assertEqual([item["temperature_c"] for item in entries], sorted(item["temperature_c"] for item in entries))
        self.assertIn('0x4F04', script)
        self.assertIn('0x4513', script)
        self.assertIn('0x0306', script)
        self.assertIn('0x1950', script)
        self.assertIn('0x2104', script)
        self.assertIn('0x50' + format(entries[0]["temperature_c"], "02X"), script)
        self.assertIn('0x67FF', script)
        self.assertIn('0x4A10', script)
        self.assertIn('0x4A30', script)
        self.assertNotIn("'", script)

    def test_fan_curve_rejects_unsafe_endpoint_order(self):
        invalid = APP.default_fan_config()
        invalid["load_temperature_c"] = invalid["idle_temperature_c"] + 5
        with self.assertRaises(APP.ApiError):
            APP.validate_fan_config(invalid)

    def test_fan_curve_accepts_zero_percent_idle_speed(self):
        value = APP.default_fan_config()
        value["idle_speed_percent"] = 0
        value["load_speed_percent"] = 1
        self.assertEqual(APP.validate_fan_config(value)["idle_speed_percent"], 0)
        value["idle_speed_percent"] = -1
        with self.assertRaises(APP.ApiError):
            APP.validate_fan_config(value)
        invalid = APP.default_fan_config()
        invalid["load_speed_percent"] = invalid["idle_speed_percent"] - 1
        with self.assertRaises(APP.ApiError):
            APP.validate_fan_config(invalid)

    def test_fan_response_time_maps_to_enhanced_register(self):
        expected = {5.45: 0x11, 10.9: 0x13, 21.6: 0x15, 43.7: 0x17}
        for response_time, register in expected.items():
            value = APP.default_fan_config()
            value["response_time_s"] = response_time
            self.assertEqual(APP.fan_enhanced_config(APP.validate_fan_config(value)), register)
        self.assertEqual(APP.percent_to_pwm(50), 128)
        self.assertEqual(APP.percent_to_pwm(100), 255)

    def test_backup_includes_fan_files_for_switch_manager_config(self):
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory, "fan.json")
            script_path = Path(directory, "fan.tp")
            config_path.write_text("original config", encoding="utf-8")
            script_path.write_text("original script", encoding="utf-8")
            old_config_path, old_script_path = APP.FAN_CONFIG_PATH, APP.FAN_INIT_SCRIPT
            try:
                APP.FAN_CONFIG_PATH = str(config_path)
                APP.FAN_INIT_SCRIPT = str(script_path)
                backup = APP.make_backup({"backup_root": str(Path(directory, "backups"))})
                self.assertEqual(Path(backup, "fan.json").read_text(), "original config")
                self.assertEqual(Path(backup, "pe31625g24dira-fan-init.tp").read_text(), "original script")
            finally:
                APP.FAN_CONFIG_PATH = old_config_path
                APP.FAN_INIT_SCRIPT = old_script_path

    def test_factory_configuration_restores_original_topology_and_defaults(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = {
                "topology_base": str(root / "base.cfg"),
                "platform_active": str(root / "active.cfg"),
                "platform_persistent": str(root / "persistent.cfg"),
                "startup_script": str(root / "startup.tp"),
                "status_script": str(root / "status.tp"),
                "vlan_config": str(root / "vlans.json"),
                "port_config": str(root / "ports.json"),
                "fan_config": str(root / "fan.json"),
                "fan_init_script": str(root / "fan.tp"),
            }
            Path(paths["topology_base"]).write_text(BASE, encoding="utf-8")
            parsed = APP.write_factory_configuration(paths)
            self.assertEqual(parsed["external_count"], 6)
            self.assertEqual(parsed["external"], 600000)
            self.assertEqual(
                json.loads(Path(paths["fan_config"]).read_text(encoding="utf-8")),
                APP.default_fan_config(),
            )
            self.assertEqual(
                len(json.loads(Path(paths["port_config"]).read_text())["enabled"]), 6
            )

    def test_testpoint_fifo_completion_requires_matching_load_and_marker(self):
        path = "/etc/pe31625g24dira/pe31625g24dira-fan-init.tp"
        marker = APP.FAN_COMPLETE_MARKER
        self.assertIsNone(APP.testpoint_script_result("unrelated output", path, marker))
        output = f"Loading {path}\nBus=0, Device=0x4c: Write=0x4A10\n"
        self.assertIsNone(APP.testpoint_script_result(output, path, marker))
        completed = output + marker + "\n"
        self.assertIn("0x4A10", APP.testpoint_script_result(completed, path, marker))
        with self.assertRaises(RuntimeError):
            APP.testpoint_script_result(f"Loading {path}\nsyntax error at line 1\n", path, marker)

    def test_generated_sdk_scripts_include_completion_markers(self):
        self.assertIn(APP.FAN_COMPLETE_MARKER, APP.render_fan_init(APP.default_fan_config()))
        _, parsed = TopologyTests().render(lambda group: {"layout": "bonded", "speed": 100000})
        self.assertIn(APP.STATUS_COMPLETE_MARKER, APP.status_text(parsed))

    def test_linux_temperature_labels_preserve_sensor_meaning(self):
        core = APP.describe_linux_temperature("coretemp", "Core 2")
        self.assertEqual(core["category"], "cpu-core")
        self.assertEqual(core["display_label"], "CPU Core 2")
        self.assertEqual(APP.describe_linux_temperature("soc_dts1", "temp1")["category"], "soc")
        self.assertEqual(APP.describe_linux_temperature("acpitz", "temp1")["category"], "acpi")

    def test_switch_temperature_metadata_matches_datasheet(self):
        output = "\n".join(
            [
                "MAIN TEMP SENSOR : 35.5 C",
                "REMOTE TEMP SENSOR 0 : 35.1 C",
                "REMOTE TEMP SENSOR 5 : 35.2 C",
                "REMOTE TEMP SENSOR 6 : 35.3 C",
                "REMOTE TEMP SENSOR 7 : 35.4 C",
                "VOLTAGE SENSOR VDD : 0.850 V",
                "Device=0x59:  <= A3 => 20",
                "Device=0x59:  <= A4 => 03",
            ]
        )
        parsed = APP.parse_switch_sensors(output)
        by_index = {item["sensor_index"]: item for item in parsed["temperatures"]}
        self.assertEqual(by_index[0]["location"], "PCI Host Interface #0")
        self.assertEqual(by_index[6]["location"], "Ethernet Port Logic #8")
        self.assertEqual(by_index[7]["location"], "Tunneling engine")
        self.assertFalse(by_index[8]["documented"])
        self.assertEqual(by_index[8]["accuracy_c"], 5)
        self.assertEqual(parsed["fans"][0]["tach_count"], 0x0320)
        self.assertEqual(parsed["fans"][0]["rpm"], 6750)
        self.assertTrue(parsed["fans"][0]["signal"])

    def test_zero_tach_is_reported_as_no_signal_not_fake_rpm(self):
        output = "\n".join(
            [
                "MAIN TEMP SENSOR : 35.5 C",
                "Device=0x59:  <= A3 => 00",
                "Device=0x59:  <= A4 => 00",
            ]
        )
        parsed = APP.parse_switch_sensors(output)
        self.assertEqual(parsed["fans"][0]["rpm"], 0)
        self.assertFalse(parsed["fans"][0]["signal"])

    def test_poweroff_requires_explicit_boolean_confirmation(self):
        self.assertTrue(APP.validate_poweroff_request({"confirm": True}))
        with self.assertRaises(APP.ApiError):
            APP.validate_poweroff_request({"confirm": False})

    def test_service_log_cleanup_removes_testpoint_spinner(self):
        raw = ">> Loading TestPoint Module (\\)\b\b\b(|)\b\b\b(/)\nFATAL: example\n"
        cleaned = APP.clean_service_log(raw)
        self.assertEqual(cleaned, ">> Loading TestPoint Module\nFATAL: example\n")

    def test_port_admin_uses_private_squelch_and_powerdown(self):
        _, parsed = TopologyTests().render(
            lambda group: (
                {"layout": "split", "speeds": [10000] * 4}
                if group["mpo"] == 1
                else {"layout": "bonded", "speed": 100000}
            )
        )
        value = APP.default_port_config(parsed)
        for endpoint in APP.topology_endpoints(parsed):
            if APP.GROUP_BY_KEY[endpoint["group"]]["mpo"] == 1:
                value["enabled"][endpoint["key"]] = endpoint["logical"] == 7
        commands = APP.port_admin_commands(parsed, value)
        verification = APP.xcvr_verification_script(parsed, value)
        self.assertIn("set port 7,13,17,21 up", commands)
        self.assertIn("set port 1,2,3,4,5,6,8,9,10,11,12 powerdown", commands)
        self.assertIn("$pe_xcvr_write_verified->(1, 1, 56, 0)", verification)
        self.assertIn("fmPlatformXcvrMemRead", verification)
        self.assertIn("fmPlatformXcvrMemWrite", verification)
        self.assertIn(APP.XCVR_VERIFY_FAILURE_MARKER, verification)

    def test_port_admin_all_enabled_returns_vendor_masks(self):
        _, parsed = TopologyTests().render(lambda group: {"layout": "bonded", "speed": 100000})
        verification = APP.xcvr_verification_script(
            parsed, APP.default_port_config(parsed)
        )
        self.assertIn("$pe_xcvr_write_verified->(2, 13, 56, 15)", verification)
        self.assertIn("$pe_xcvr_write_verified->(2, 13, 57, 255)", verification)

    def test_mpo_summary_is_on_only_when_every_port_is_enabled(self):
        _, parsed = TopologyTests().render(
            lambda group: {"layout": "split", "speeds": [10000] * 4}
        )
        value = APP.default_port_config(parsed)
        value["enabled"]["epl0.lane0"] = False
        summary = APP.port_admin_payload(parsed, value)["mpo"]["1"]
        self.assertFalse(summary["enabled"])
        self.assertEqual(summary["enabled_count"], summary["total"] - 1)

    def test_enabling_mpo_turns_on_every_port(self):
        _, parsed = TopologyTests().render(
            lambda group: {"layout": "split", "speeds": [10000] * 4}
        )
        value = APP.default_port_config(parsed)
        for key in value["enabled"]:
            value["enabled"][key] = key == "epl2.lane3"
        enabled = APP.update_port_admin(parsed, value, "mpo", 1, True)
        mpo1 = [
            item["key"]
            for item in APP.topology_endpoints(parsed)
            if APP.GROUP_BY_KEY[item["group"]]["mpo"] == 1
        ]
        self.assertTrue(all(enabled["enabled"][key] for key in mpo1))

    def test_direct_port_status_decoder(self):
        up = APP.decode_port_status((1 << 9) | (3 << 18))
        self.assertEqual(up["oper"], "UP")
        self.assertEqual(up["fault"], "none")
        self.assertEqual(up["pcs"], 3)
        down = APP.decode_port_status((1 << 9) | 2 | (1 << 11))
        self.assertEqual(down["oper"], "DOWN")
        self.assertEqual(down["fault"], "remote")
        self.assertTrue(down["high_ber"])

    def test_hardware_counter_decoding(self):
        mapped = bytearray(16)
        mapped[4:8] = struct.pack("<I", 0x89ABCDEF)
        mapped[8:12] = struct.pack("<I", 0xFF123456)
        self.assertEqual(APP.read_counter(mapped, 1, 0xFFFFFF), 0x12345689ABCDEF)
        self.assertEqual(APP.read_counter(mapped, 1, 0xFFFF), 0x345689ABCDEF)

    def test_switch_rate_sample_and_aggregate(self):
        def ports(rx, tx):
            return {
                "1": {
                    "statistics": {
                        "rx": {
                            "good_bytes": rx,
                            "frames": 10,
                            "framing_errors": 1,
                            "fcs_errors": 2,
                        },
                        "tx": {
                            "good_bytes": tx,
                            "frames": 9,
                            "timeout_drops": 1,
                            "error_drops": 2,
                            "ecc_drops": 0,
                            "loopback_drops": 0,
                            "ttl_drops": 1,
                        },
                    }
                }
            }

        state = APP.State({})
        first = ports(100, 200)
        APP.switch_rate_sample(state, 10.0, first)
        second = ports(350, 500)
        APP.switch_rate_sample(state, 12.0, second)
        self.assertEqual(second["1"]["rx_bps"], 1000)
        self.assertEqual(second["1"]["tx_bps"], 1200)
        aggregate = APP.aggregate_switch_statistics(second)
        self.assertEqual(aggregate["rx_errors"], 3)
        self.assertEqual(aggregate["tx_discards"], 4)

    def test_session_creation_lookup_and_revoke(self):
        state = APP.State({})
        token, created = state.new_session("admin")
        self.assertEqual(state.get_session(token)["csrf"], created["csrf"])
        state.revoke_session(token)
        self.assertIsNone(state.get_session(token))

    def test_credentials_use_existing_pbkdf2_config(self):
        salt = "11" * 24
        config = {"username": "admin", "password_salt": salt, "password_rounds": 1000}
        config["password_hash"] = APP.password_digest("correct horse", salt, 1000)
        self.assertTrue(APP.credentials_valid(config, "admin", "correct horse"))
        self.assertFalse(APP.credentials_valid(config, "admin", "wrong"))

    def test_new_config_requires_one_time_browser_initialization(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "config.json")
            APP.create_config(path, "127.0.0.1", 8080)
            config = APP.read_json(path)
            self.assertFalse(APP.config_initialized(config))
            self.assertNotIn("username", config)
            self.assertNotIn("password_hash", config)

            state = APP.State(config, path)
            APP.initialize_admin(state, "switch-admin", "correct horse")
            saved = APP.read_json(path)
            self.assertTrue(APP.config_initialized(saved))
            self.assertTrue(APP.credentials_valid(saved, "switch-admin", "correct horse"))
            with self.assertRaises(APP.ApiError):
                APP.initialize_admin(state, "other-admin", "another password")

    def test_admin_credentials_are_rehashed_persisted_and_sessions_revoked(self):
        salt = "22" * 24
        config = {"username": "admin", "password_salt": salt, "password_rounds": 1000}
        config["password_hash"] = APP.password_digest("old password", salt, 1000)
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "config.json")
            APP.atomic_write(path, json.dumps(config))
            state = APP.State(config, path)
            token, _ = state.new_session("admin")
            APP.update_admin_credentials(
                state, "admin", "old password", "switch-admin", "new password"
            )
            saved = APP.read_json(path)
            self.assertEqual(saved["username"], "switch-admin")
            self.assertTrue(APP.credentials_valid(saved, "switch-admin", "new password"))
            self.assertFalse(APP.credentials_valid(saved, "admin", "old password"))
            self.assertIsNone(state.get_session(token))


class FirstRunHttpTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.config_path = os.path.join(self.temporary.name, "config.json")
        APP.create_config(self.config_path, "127.0.0.1", 0)
        config = APP.read_json(self.config_path)
        config["static_root"] = os.path.join(HERE, "static")
        APP.atomic_write(self.config_path, json.dumps(config))
        self.server = APP.ReusableThreadingHTTPServer(("127.0.0.1", 0), APP.Handler)
        self.server.app_state = APP.State(config, self.config_path)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.temporary.cleanup()

    def request(self, method, path, body=None, headers=None):
        connection = HTTPConnection("127.0.0.1", self.server.server_port, timeout=3)
        connection.request(method, path, body=body, headers=headers or {})
        response = connection.getresponse()
        payload = response.read()
        result = response.status, dict(response.getheaders()), payload
        connection.close()
        return result

    def test_setup_route_closes_after_admin_creation(self):
        status, headers, _ = self.request("GET", "/")
        self.assertEqual(status, 303)
        self.assertEqual(headers["Location"], "/setup")
        self.assertEqual(self.request("GET", "/setup")[0], 200)

        body = json.dumps({"username": "switch-admin", "password": "correct horse"})
        status, _, _ = self.request(
            "POST", "/api/setup", body, {"Content-Type": "application/json"}
        )
        self.assertEqual(status, 201)
        status, headers, _ = self.request("GET", "/setup")
        self.assertEqual(status, 303)
        self.assertEqual(headers["Location"], "/login")

        status, headers, _ = self.request(
            "POST", "/api/login", body, {"Content-Type": "application/json"}
        )
        self.assertEqual(status, 200)
        self.assertIn(APP.SESSION_COOKIE, headers["Set-Cookie"])
        cookie = headers["Set-Cookie"].split(";", 1)[0]
        status, headers, payload = self.request(
            "GET", "/controls.js", headers={"Cookie": cookie}
        )
        self.assertEqual(status, 200)
        self.assertIn("application/javascript", headers["Content-Type"])
        self.assertIn(b"enhanceSelects", payload)
        status, headers, payload = self.request(
            "GET", "/backup", headers={"Cookie": cookie}
        )
        self.assertEqual(status, 200)
        self.assertIn("text/html", headers["Content-Type"])
        self.assertIn(b'id="page-maintenance"', payload)


if __name__ == "__main__":
    unittest.main()
