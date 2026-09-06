"""
Integration test for full calibration cycle in ToolCalibrator.
"""

import unittest
import os
import re
import glob
import tempfile
import shutil
from unittest.mock import MagicMock, patch


from klippy.extras.tool_calibrator import ToolCalibrator
from klippy.extras.safe_navigator import SafeNavigatorException


class DummyGCodeCommand:
    def __init__(self, params=None):
        self.params = params or {}
        self.info_messages = []
        self.error_messages = []

    def get_int(self, key, default=0):
        val = self.params.get(key, default)
        return int(val)

    def get_float(self, key, default=0.0):
        val = self.params.get(key, default)
        return float(val)

    def get(self, key, default=None):
        return self.params.get(key, default)

    def respond_info(self, msg):
        self.info_messages.append(msg)

    def respond_error(self, msg):
        self.error_messages.append(msg)

    def error(self, msg):
        self.error_messages.append(msg)
        return Exception(msg)


class DummyToolchanger:
    def __init__(self, tools=(0, 1)):
        self.tool_numbers = list(tools)


class DummyToolhead:
    def __init__(self):
        self.pos = [150.0, 150.0, 35.0, 0.0]

    def get_position(self):
        return list(self.pos)

    def manual_move(self, new_pos, speed):
        for i, val in enumerate(new_pos):
            if val is not None:
                self.pos[i] = val

    def wait_moves(self):
        pass

    def get_status(self, eventtime):
        return {
            "homed_axes": "xyz",
            "axis_minimum": [0.0, 0.0, 0.0, 0.0],
            "axis_maximum": [300.0, 300.0, 250.0, 0.0]
        }


class DummyReactor:
    def monotonic(self):
        return 0.0

    def pause(self, until):
        pass


class DummyTemplateWrapper:
    def __init__(self, script=""):
        self.script = script
        self.run_count = 0

    def run_gcode_from_command(self, context=None):
        self.run_count += 1


class DummyGCodeMacro:
    def load_template(self, config, name, default=None):
        raw = config.get(name, default)
        if raw is None:
            return None
        return DummyTemplateWrapper(raw)

    def run_script(self, name, script, context):
        pass


class DummyGCode:
    def __init__(self):
        self.commands = {}
        self.executed_scripts = []

    def register_command(self, name, func, desc=None):
        if name in self.commands:
            raise Exception(f"Command '{name}' already registered")
        self.commands[name] = func


    def run_script_from_command(self, script):
        self.executed_scripts.append(script)


class DummyPrinter:
    def __init__(self, toolhead, gcode, toolchanger=None):
        self.reactor = DummyReactor()
        self.toolhead = toolhead
        self.gcode = gcode
        self.toolchanger = toolchanger or DummyToolchanger()
        self.gcode_move = MagicMock()
        self.gcode_macro = DummyGCodeMacro()
        self.objects = {
            "toolhead": self.toolhead,
            "gcode": self.gcode,
            "toolchanger": self.toolchanger,
            "gcode_move": self.gcode_move,
            "gcode_macro": self.gcode_macro,
        }

    def get_reactor(self):
        return self.reactor

    def lookup_object(self, name, default=None):
        return self.objects.get(name, default)

    def lookup_objects(self):
        return self.objects

    def load_object(self, config, name):
        if name == "gcode_macro":
            return self.gcode_macro
        return MagicMock()


class DummyConfig:
    def __init__(self, printer, config_dict=None):
        self._printer = printer
        self.data = config_dict or {}

    def get_printer(self):
        return self._printer

    def get(self, key, default=None):
        return self.data.get(key, default)

    def getint(self, key, default=None, minval=None, maxval=None, above=None, below=None, **kwargs):
        val = self.data.get(key, default)
        return int(val) if val is not None else default

    def getfloat(self, key, default=None, minval=None, maxval=None, above=None, below=None, **kwargs):
        val = self.data.get(key, default)
        return float(val) if val is not None else default

    def getboolean(self, key, default=None, **kwargs):
        val = self.data.get(key, default)
        return bool(val) if val is not None else default

    def getsection(self, section):
        return self

    def error(self, msg):
        return Exception(msg)


class TestCalibrationCycle(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.config_path = os.path.join(self.test_dir, "tool_offsets.cfg")

        self.toolhead = DummyToolhead()
        self.gcode = DummyGCode()
        self.toolchanger = DummyToolchanger(tools=[0, 1])
        self.printer = DummyPrinter(self.toolhead, self.gcode, self.toolchanger)

        self.config_data = {
            "server_url": "http://localhost:8090",
            "reference_tool": 0,
            "z_backend": "cartographer",
            "max_centering_iterations": 3,
            "tolerance_mm": 0.015,
            "travel_speed": 100.0,
            "approach_speed": 25.0,
            "z_speed": 10.0,
            "safe_z": 35.0,
            "camera_approach_x": 150.0,
            "camera_approach_y": 35.0,
            "camera_x": 150.0,
            "camera_y": 10.0,
            "camera_focal_z": 22.0,
            "offset_config_path": self.config_path,
        }
        self.config = DummyConfig(self.printer, self.config_data)

    def tearDown(self):
        if os.path.exists(self.test_dir):
            shutil.rmtree(self.test_dir)

    def test_full_calibration_cycle(self):
        """Simulate a complete 2-tool calibration cycle and assert offsets are saved."""
        calibrator = ToolCalibrator(self.config)

        # Mock vision queries:
        # 1. health -> ok
        # 2. detect_nozzle -> found
        # 3. calculate_offset -> converged on first try (offset [0.0, 0.0])
        def mock_query_vision(endpoint, payload=None, timeout=2.0):
            if endpoint == "health":
                return {"status": "ok", "service": "ToolCalibratorVision", "version": "1.0.0"}
            elif endpoint == "detect_nozzle":
                return {"found": True, "center_uv": [320, 240], "radius": 45}
            elif endpoint == "calculate_offset":
                return {"offset_xy": [0.0, 0.0]}
            return {}

        calibrator._query_vision = mock_query_vision

        # Mock Cartographer probing:
        calibrator.z_backend.probe_reference_tool = MagicMock(return_value={"baseline_z": 1.5, "source": "cartographer"})
        calibrator.z_backend.probe_secondary_tool = MagicMock(return_value={"suggested_z_offset": -0.125})

        # Run calibration command
        gcmd = DummyGCodeCommand({"CALIBRATE_XY": 1, "CALIBRATE_Z": 1, "SAVE_CONFIG": 1, "DRY_RUN": 0})
        calibrator.cmd_CALIBRATE_TOOL_OFFSETS(gcmd)

        # Check status and saved config
        self.assertEqual(calibrator.last_run_status, "SUCCESS")
        self.assertTrue(os.path.exists(self.config_path))

        with open(self.config_path, "r", encoding="utf-8") as f:
            content = f.read()

        # Tool offsets should be present in unified [tool_offsets] section
        self.assertIn("[tool_offsets]", content)
        self.assertIn("t0_z: 0.0000", content)
        self.assertIn("t1_z: -0.1250", content)

    def test_dry_run_does_not_modify_disk(self):
        """Dry run must compute offsets without saving to disk."""
        calibrator = ToolCalibrator(self.config)

        def mock_query_vision(endpoint, payload=None, timeout=2.0):
            if endpoint == "health":
                return {"status": "ok", "service": "ToolCalibratorVision", "version": "1.0.0"}
            elif endpoint == "detect_nozzle":
                return {"found": True, "center_uv": [320, 240], "radius": 45}
            elif endpoint == "calculate_offset":
                return {"offset_xy": [0.0, 0.0]}
            return {}

        calibrator._query_vision = mock_query_vision

        gcmd = DummyGCodeCommand({"CALIBRATE_XY": 1, "CALIBRATE_Z": 1, "SAVE_CONFIG": 1, "DRY_RUN": 1})
        calibrator.cmd_CALIBRATE_TOOL_OFFSETS(gcmd)

        self.assertEqual(calibrator.last_run_status, "SUCCESS")
        # In dry run, file must NOT be written to disk
        self.assertFalse(os.path.exists(self.config_path))

    def test_auto_teach_camera_persists_to_disk(self):
        """CALIBRATION_TEACH_STATION STATION=CAMERA must auto-derive approach vector and persist."""
        calibrator = ToolCalibrator(self.config)

        def mock_query_vision(endpoint, payload=None, timeout=2.0):
            if endpoint == "detect_nozzle":
                return {"found": True, "center_uv": [320, 240], "radius": 45}
            elif endpoint == "calculate_offset":
                return {"offset_xy": [0.0, 0.0]}
            return {}

        calibrator._query_vision = mock_query_vision
        self.toolhead.pos = [150.0, 10.0, 22.0, 0.0]

        gcmd = DummyGCodeCommand({"STATION": "CAMERA", "AUTO_CENTER": 1, "APPROACH_DIST": 25.0})
        calibrator.cmd_CALIBRATION_TEACH_STATION(gcmd)

        self.assertTrue(os.path.exists(self.config_path))
        with open(self.config_path, "r", encoding="utf-8") as f:
            content = f.read()

        self.assertIn("[tool_calibrator_station camera]", content)
        self.assertIn("target_x: 150.000", content)
        self.assertIn("target_y: 10.000", content)
        self.assertIn("approach_y: 35.000", content)

    def test_calibrate_camera_scale_star_pattern(self):
        """CALIBRATE_CAMERA_SCALE must run star-pattern moves and save MPP."""
        calibrator = ToolCalibrator(self.config)

        def mock_query_vision(endpoint, payload=None, timeout=2.0):
            if endpoint == "detect_nozzle":
                # Returns center shifted by toolhead position (scale ~ 0.01542 mm/px)
                dx = self.toolhead.pos[0] - 150.0
                dy = self.toolhead.pos[1] - 10.0
                return {"found": True, "center_uv": [320.0 + dx / 0.01542, 240.0 + dy / 0.01542], "radius": 45}
            elif endpoint == "calculate_offset":
                return {"offset_xy": [0.0, 0.0]}
            elif endpoint == "calibrate_mpp":
                return {"success": True, "mpp": 0.01542}
            elif endpoint == "solve_matrix":
                return {"success": True, "matrix": [[0.01542, 0.0, 0.0], [0.0, 0.01542, 0.0]]}
            return {}

        calibrator._query_vision = mock_query_vision
        self.toolhead.pos = [150.0, 10.0, 22.0, 0.0]

        gcmd = DummyGCodeCommand({"DISTANCE": 1.0})
        calibrator.cmd_CALIBRATE_CAMERA_SCALE(gcmd)

        self.assertTrue(os.path.exists(self.config_path))
        with open(self.config_path, "r", encoding="utf-8") as f:
            content = f.read()

        self.assertIn("[tool_calibrator_station camera]", content)
        self.assertIn("mpp: 0.015", content)
        self.assertAlmostEqual(calibrator.calibrated_mpp, 0.01542, places=4)

    def test_generic_tool_discovery(self):
        """Verifies toolhead discovery across diverse Klipper toolchanger architectures."""
        calibrator = ToolCalibrator(self.config)
        self.printer.objects["tool 2"] = MagicMock()
        self.printer.objects["tool 3"] = MagicMock()

        # Case 1: Explicit TOOLS parameter
        tools = calibrator._discover_tools(tools_param="2,3")
        self.assertEqual(tools, [0, 2, 3])

        # Validation: Rejection of non-existent tool
        with self.assertRaises(SafeNavigatorException) as ctx:
            calibrator._discover_tools(tools_param="99")
        self.assertIn("ERR_TOOL_NOT_FOUND", str(ctx.exception))

        # Case 2: Config tools parameter
        calibrator.configured_tools_str = "0, 1, 2"
        tools = calibrator._discover_tools(tools_param=None)
        self.assertEqual(tools, [0, 1, 2])
        calibrator.configured_tools_str = None

        # Case 3: Auto-discovered from printer objects (e.g. gcode_macro T0, T1, T2)
        calibrator.printer.lookup_object = lambda name, default=None: None
        calibrator.printer.lookup_objects = lambda: {
            "gcode_macro T0": MagicMock(),
            "gcode_macro T1": MagicMock(),
            "gcode_macro T2": MagicMock(),
            "toolhead": self.toolhead,
        }
        tools = calibrator._discover_tools(tools_param=None)
        self.assertEqual(tools, [0, 1, 2])

    def test_calibration_order_z_first(self):
        """ORDER=Z_FIRST must execute Z probing before XY camera inspection."""
        calibrator = ToolCalibrator(self.config)
        execution_order = []

        def mock_query_vision(endpoint, payload=None, timeout=2.0):
            if endpoint == "detect_nozzle":
                execution_order.append("XY_DETECT")
                return {"found": True, "center_uv": [320.0, 240.0], "radius": 45}
            elif endpoint == "calculate_offset":
                return {"offset_xy": [0.0, 0.0]}
            elif endpoint == "health":
                return {"service": "mock", "version": "1.0"}
            return {}

        calibrator._query_vision = mock_query_vision
        original_probe_ref = calibrator.z_backend.probe_reference_tool
        def mock_probe_ref(tool, gcmd):
            execution_order.append("Z_PROBE_REF")
            return {"source": "switch", "baseline_z": 15.0}
        calibrator.z_backend.probe_reference_tool = mock_probe_ref

        gcmd = DummyGCodeCommand({
            "CALIBRATE_XY": 1,
            "CALIBRATE_Z": 1,
            "SAVE_CONFIG": 0,
            "DRY_RUN": 0,
            "ORDER": "Z_FIRST",
            "TOOLS": "0"
        })
        calibrator.cmd_CALIBRATE_TOOL_OFFSETS(gcmd)
        self.assertEqual(calibrator.last_run_status, "SUCCESS")
        # Z should run before XY
        self.assertIn("Z_PROBE_REF", execution_order)
        self.assertIn("XY_DETECT", execution_order)
        self.assertLess(execution_order.index("Z_PROBE_REF"), execution_order.index("XY_DETECT"))

    def test_inspection_lighting_lifecycle(self):
        """Optical inspection lighting lifecycle: Camera light ON/OFF & Nozzle light OFF/ON."""
        self.config_data["camera_pin"] = "cam_light"
        self.config_data["camera_led_brightness"] = 0.4
        calibrator = ToolCalibrator(self.config)

        # Mock macros
        nozzle_off_mock = MagicMock()
        nozzle_on_mock = MagicMock()
        orig_lookup = self.printer.lookup_object

        def mock_lookup(name, default=None):
            if name == "gcode_macro _CALIBRATION_NOZZLE_LED_OFF":
                return nozzle_off_mock
            elif name == "gcode_macro _CALIBRATION_NOZZLE_LED_ON":
                return nozzle_on_mock
            return orig_lookup(name, default)

        self.printer.lookup_object = mock_lookup

        # Test enable lighting
        calibrator._set_inspection_lighting(True, tool_no=1)
        self.assertIn("SET_PIN PIN=cam_light VALUE=0.4", self.gcode.executed_scripts)
        self.assertIn("_CALIBRATION_NOZZLE_LED_OFF TOOL=1", self.gcode.executed_scripts)

        # Test disable lighting
        calibrator._set_inspection_lighting(False, tool_no=1)
        self.assertIn("SET_PIN PIN=cam_light VALUE=0", self.gcode.executed_scripts)
        self.assertIn("_CALIBRATION_NOZZLE_LED_ON TOOL=1", self.gcode.executed_scripts)

    def test_burst_sampling_median_filtering(self):
        """Burst sampling must discard single-frame outliers and compute accurate median UV and spread."""
        calibrator = ToolCalibrator(self.config)
        mock_frames = [
            {"found": True, "center_uv": [320.0, 240.0], "radius": 40.0, "confidence": 0.95, "tier": 0, "combo": 10},
            {"found": True, "center_uv": [320.2, 240.1], "radius": 40.5, "confidence": 0.98, "tier": 0, "combo": 10},
            {"found": True, "center_uv": [395.0, 310.0], "radius": 60.0, "confidence": 0.70, "tier": 2, "combo": 0},  # outlier
        ]
        calibrator._query_vision = MagicMock(side_effect=mock_frames)

        res = calibrator._sample_burst(self.toolhead, samples=3)
        self.assertIsNotNone(res)
        self.assertTrue(res["found"])
        self.assertEqual(res["center_uv"], [320.1, 240.05])
        self.assertEqual(res["radius_px"], 40.25)
        self.assertEqual(res["burst_count"], 2)
        self.assertEqual(res["burst_total"], 3)
        self.assertEqual(res["spread_px"], 0.2)


    def test_burst_sampling_partial_drop(self):
        """Burst sampling handles dropped/unfound frames gracefully if at least 1 valid frame is captured."""
        calibrator = ToolCalibrator(self.config)
        mock_frames = [
            {"found": False},
            {"found": True, "center_uv": [319.8, 239.9], "radius": 41.0, "confidence": 0.92, "tier": 1},
            {"found": True, "center_uv": [320.0, 240.1], "radius": 41.2, "confidence": 0.94, "tier": 1},
        ]
        calibrator._query_vision = MagicMock(side_effect=mock_frames)

        res = calibrator._sample_burst(self.toolhead, samples=3)
        self.assertIsNotNone(res)
        self.assertTrue(res["found"])
        self.assertEqual(res["burst_count"], 2)
        self.assertEqual(res["burst_total"], 3)
        self.assertEqual(res["center_uv"], [319.9, 240.0])

    def test_adaptive_wiggle_recovery_success(self):
        """When initial detection fails, adaptive wiggle moves toolhead and recovers optical lock."""
        calibrator = ToolCalibrator(self.config)
        gcmd = DummyGCodeCommand()
        self.toolhead.pos = [150.0, 10.0, 22.0, 0.0]

        def mock_vision(endpoint, payload=None, timeout=3.0):
            if endpoint == "detect_nozzle":
                cur_x = self.toolhead.get_position()[0]
                if abs(cur_x - 150.1) < 0.01:
                    return {"found": True, "center_uv": [320.0, 240.0], "radius": 42.0, "confidence": 0.95, "tier": 0, "combo": 10}
                return {"found": False}
            elif endpoint == "calculate_offset":
                return {"offset_xy": [0.0, 0.0]}
            return {}

        calibrator._query_vision = mock_vision
        calibrator._center_nozzle(self.toolhead, gcmd)

        info_str = " ".join(gcmd.info_messages)
        self.assertIn("Wiggle Recovery", info_str)
        self.assertIn("Regained optical lock", info_str)
        self.assertIn("Convergence achieved", info_str)

    def test_adaptive_wiggle_recovery_failure_resets_position(self):
        """If all wiggle recovery attempts fail, toolhead must return to anchor position and raise ERR_CV_201."""
        calibrator = ToolCalibrator(self.config)
        gcmd = DummyGCodeCommand()
        self.toolhead.pos = [150.0, 10.0, 22.0, 0.0]

        calibrator._query_vision = MagicMock(return_value={"found": False})

        with self.assertRaises(SafeNavigatorException) as ctx:
            calibrator._center_nozzle(self.toolhead, gcmd)

        self.assertIn("[ERR_CV_201]", str(ctx.exception))
        self.assertEqual(self.toolhead.pos[:2], [150.0, 10.0])
        info_str = " ".join(gcmd.info_messages)
        self.assertIn("exhausted", info_str.lower())

    def test_centering_with_wiggle_disabled(self):
        """When enable_wiggle=False, centering aborts immediately on first unfound burst without wiggling."""
        calibrator = ToolCalibrator(self.config)
        gcmd = DummyGCodeCommand()
        calibrator._query_vision = MagicMock(return_value={"found": False})

        with self.assertRaises(SafeNavigatorException) as ctx:
            calibrator._center_nozzle(self.toolhead, gcmd, enable_wiggle=False)

        self.assertIn("[ERR_CV_201]", str(ctx.exception))
        info_str = " ".join(gcmd.info_messages)
        self.assertNotIn("Wiggle Recovery", info_str)

    def test_calibration_navigate_camera_and_depart(self):
        """CALIBRATION_NAVIGATE navigates safely to camera and departs to safe altitude."""
        calibrator = ToolCalibrator(self.config)
        gcmd_approach = DummyGCodeCommand({"STATION": "CAMERA"})
        calibrator.cmd_CALIBRATION_NAVIGATE(gcmd_approach)

        # Check toolhead arrived at camera target position [150.0, 10.0, 22.0]
        pos = self.toolhead.get_position()
        self.assertEqual(pos[:3], [150.0, 10.0, 22.0])
        self.assertTrue(any("Reached Camera Station" in m for m in gcmd_approach.info_messages))

        # Depart station
        gcmd_depart = DummyGCodeCommand({"STATION": "DEPART"})
        calibrator.cmd_CALIBRATION_NAVIGATE(gcmd_depart)
        pos_depart = self.toolhead.get_position()
        self.assertEqual(pos_depart[2], calibrator.navigator.safe_z)
        self.assertTrue(any("Departed station" in m for m in gcmd_depart.info_messages))

    def test_calibration_center_nozzle_cmd(self):
        """CALIBRATION_CENTER_NOZZLE executes visual servoing centering on active tool."""
        calibrator = ToolCalibrator(self.config)
        self.toolhead.pos = [150.0, 10.0, 22.0, 0.0]

        def mock_vision(endpoint, payload=None, timeout=3.0):
            if endpoint == "detect_nozzle":
                return {"found": True, "center_uv": [320.0, 240.0], "radius": 40.0, "confidence": 0.95, "tier": 0, "combo": 10}
            elif endpoint == "calculate_offset":
                return {"offset_xy": [0.0, 0.0]}
            return {}

        calibrator._query_vision = mock_vision
        gcmd = DummyGCodeCommand({"SAMPLES": 3, "WIGGLE": 1})
        calibrator.cmd_CALIBRATION_CENTER_NOZZLE(gcmd)

        self.assertTrue(any("centered successfully" in m for m in gcmd.info_messages))

    def test_calibration_test_vision_cmd(self):
        """CALIBRATION_TEST_VISION outputs full inspection report with center UV and confidence."""
        calibrator = ToolCalibrator(self.config)
        calibrator._query_vision = MagicMock(return_value={
            "found": True, "center_uv": [320.5, 240.2], "radius": 42.1, "confidence": 0.98, "tier": 0, "combo": 10
        })

        gcmd = DummyGCodeCommand({"SAMPLES": 2})
        calibrator.cmd_CALIBRATION_TEST_VISION(gcmd)

        info_str = " ".join(gcmd.info_messages)
        self.assertIn("Vision Inspection Report", info_str)
        self.assertIn("U320.50 px, V240.20 px", info_str)
        self.assertIn("98.0%", info_str)

    def test_calibrate_xy_only_preserves_existing_z(self):
        """When CALIBRATE_Z=0, optical XY calibration updates X and Y offsets while strictly preserving existing Z offsets."""
        # Pre-seed tool_offsets.cfg with non-zero Z offsets
        initial_cfg = (
            "[tool 0]\n"
            "gcode_x_offset = 0.000\n"
            "gcode_y_offset = 0.000\n"
            "gcode_z_offset = 1.250\n"
            "\n"
            "[tool 1]\n"
            "gcode_x_offset = 0.000\n"
            "gcode_y_offset = 0.000\n"
            "gcode_z_offset = -0.450\n"
        )
        with open(self.config_path, "w") as f:
            f.write(initial_cfg)

        calibrator = ToolCalibrator(self.config)
        gcmd = DummyGCodeCommand({"CALIBRATE_XY": 1, "CALIBRATE_Z": 0, "SAVE_CONFIG": 1})

        def mock_vision(endpoint, payload=None, timeout=3.0):
            if endpoint == "detect_nozzle":
                return {"found": True, "center_uv": [320.0, 240.0], "radius": 40.0, "confidence": 0.95, "tier": 0, "combo": 10}
            elif endpoint == "calculate_offset":
                return {"offset_xy": [0.0, 0.0]}
            return {}

        calibrator._query_vision = mock_vision

        current_tool = [0]
        original_run_script = calibrator.gcode.run_script_from_command
        def mock_run_script(script):
            if script.startswith("T"):
                try:
                    current_tool[0] = int(script[1:])
                except ValueError:
                    pass
            original_run_script(script)
        calibrator.gcode.run_script_from_command = mock_run_script

        def mock_center(th, cmd, samples=3, enable_wiggle=True):
            if current_tool[0] == 0:
                self.toolhead.pos = [150.0, 10.0, 22.0, 0.0]
            else:
                self.toolhead.pos = [150.25, 10.75, 22.0, 0.0]
            return {"u": 320.0, "v": 240.0}

        calibrator._center_nozzle = mock_center
        calibrator.cmd_CALIBRATE_TOOL_OFFSETS(gcmd)

        # Read back tool_offsets.cfg
        with open(self.config_path, "r") as f:
            content = f.read()

        # Tool 1 XY should be offset (dx = 150.25 - 150.0 = 0.25, dy = 10.75 - 10.0 = 0.75)
        self.assertIn("gcode_x_offset: 0.250", content)
        self.assertIn("gcode_y_offset: 0.750", content)
        # Tool 0 and Tool 1 Z offsets MUST be preserved without being overwritten by 0.000!
        self.assertIn("gcode_z_offset = 1.250", content)
        self.assertIn("gcode_z_offset = -0.450", content)

    def test_calibrate_z_only_preserves_existing_xy(self):
        """When CALIBRATE_XY=0, Z probing updates Z offsets while strictly preserving existing X and Y offsets."""
        # Pre-seed tool_offsets.cfg with non-zero XY offsets
        initial_cfg = (
            "[tool 0]\n"
            "gcode_x_offset = 0.000\n"
            "gcode_y_offset = 0.000\n"
            "gcode_z_offset = 0.000\n"
            "\n"
            "[tool 1]\n"
            "gcode_x_offset = 5.678000\n"
            "gcode_y_offset = 12.345000\n"
            "gcode_z_offset = 0.000\n"
        )
        with open(self.config_path, "w") as f:
            f.write(initial_cfg)

        calibrator = ToolCalibrator(self.config)
        gcmd = DummyGCodeCommand({"CALIBRATE_XY": 0, "CALIBRATE_Z": 1, "SAVE_CONFIG": 1})

        calibrator.z_backend.probe_reference_tool = MagicMock(return_value={"baseline_z": 1.5, "source": "cartographer"})
        calibrator.z_backend.probe_secondary_tool = MagicMock(return_value={"suggested_z_offset": 0.250})

        calibrator.cmd_CALIBRATE_TOOL_OFFSETS(gcmd)

        with open(self.config_path, "r") as f:
            content = f.read()

        # Tool 1 XY MUST be preserved
        self.assertIn("gcode_x_offset = 5.678000", content)
        self.assertIn("gcode_y_offset = 12.345000", content)
        # Tool 1 Z should be updated
        self.assertIn("gcode_z_offset: 0.250", content)

    def test_non_convergence_raises_safe_navigator_exception(self):
        """If centering corrections remain above tolerance after max iterations, an exception must be raised."""
        calibrator = ToolCalibrator(self.config)
        calibrator.max_centering_iterations = 3
        gcmd = DummyGCodeCommand()
        self.toolhead.pos = [150.0, 10.0, 22.0, 0.0]

        # Always return a 0.1mm offset_xy (well above tolerance_mm=0.015)
        calibrator._query_vision = MagicMock(return_value={
            "found": True,
            "center_uv": [340.0, 260.0],
            "offset_xy": [0.10, 0.10],
            "radius": 40.0,
            "confidence": 0.99
        })

        with self.assertRaises(SafeNavigatorException) as ctx:
            calibrator._center_nozzle(self.toolhead, gcmd)

        self.assertIn("[ERR_CV_202]", str(ctx.exception))

    def test_safe_z_roundtrip_persistence(self):
        """Safe Z taught via command must persist to disk and restore upon reinitialization."""
        calibrator = ToolCalibrator(self.config)
        self.toolhead.pos = [150.0, 10.0, 70.0, 0.0]
        gcmd = DummyGCodeCommand({"STATION": "CAMERA", "TYPE": "SAFE_Z", "SAVE": 1})

        calibrator.cmd_CALIBRATION_SET_SAFE_POS(gcmd)
        self.assertEqual(calibrator.navigator.safe_z, 70.0)

        # Re-initialize calibrator to verify load_saved_stations restores 70.0mm
        self.gcode.commands.clear()
        new_calibrator = ToolCalibrator(self.config)
        self.assertEqual(new_calibrator.navigator.safe_z, 70.0)


    def test_teaching_step_by_step_no_none_strings(self):
        """Teaching SAFE_Z before TARGET must never write the literal string 'None' to config."""
        calibrator = ToolCalibrator(self.config)
        self.toolhead.pos = [150.0, 10.0, 45.0, 0.0]
        gcmd = DummyGCodeCommand({"STATION": "CAMERA", "TYPE": "SAFE_Z", "SAVE": 1})

        calibrator.cmd_CALIBRATION_SET_SAFE_POS(gcmd)

        with open(self.config_path, "r") as f:
            content = f.read()

        self.assertNotIn("None", content)
        self.assertIn("safe_z: 45.0", content)

    def test_discover_tools_handles_tuple_list(self):
        """Klipper lookup_objects() returns list of (name, obj) tuples; _discover_tools must parse correctly."""
        calibrator = ToolCalibrator(self.config)
        calibrator.printer.toolchanger = None  # Remove toolchanger to test lookup_objects fallback
        self.printer.lookup_objects = MagicMock(return_value=[
            ("tool 0", MagicMock()),
            ("tool 1", MagicMock()),
            ("tool 2", MagicMock()),
            ("extruder", MagicMock())
        ])
        tools = calibrator._discover_tools()
        self.assertEqual(tools, [0, 1, 2])

    def test_batch_saving_creates_single_backup(self):
        """Multi-tool calibration must execute atomic batch save with a single backup per session."""
        # Pre-seed config file so create_backup has a source file
        with open(self.config_path, "w", encoding="utf-8") as f:
            f.write("[tool_offsets]\nt1_x: 0.0\n")

        calibrator = ToolCalibrator(self.config)
        gcmd = DummyGCodeCommand({"CALIBRATE_XY": 1, "CALIBRATE_Z": 1, "SAVE_CONFIG": 1})

        calibrator._query_vision = MagicMock(return_value={
            "found": True,
            "center_uv": [320.0, 240.0],
            "offset_xy": [0.001, 0.001],
            "confidence": 0.99,
            "radius": 40.0,
            "burst_count": 3,
            "burst_total": 3,
            "spread_px": 0.1
        })
        calibrator.z_backend.probe_reference_tool = MagicMock(return_value={"baseline_z": 1.5, "source": "cartographer"})
        calibrator.z_backend.probe_secondary_tool = MagicMock(return_value={"suggested_z_offset": 0.120})

        calibrator.cmd_CALIBRATE_TOOL_OFFSETS(gcmd)

        # Inspect backup directory: exactly 1 backup file created for the 2-tool run
        backups = [f for f in os.listdir(self.test_dir) if f.startswith("tool_offsets.cfg.calib_backup_")]
        self.assertEqual(len(backups), 1)


    def test_switch_teaching_records_contact_z(self):
        """cmd_CALIBRATION_TEACH_STATION on SWITCH must read contact_z from probe result."""
        calibrator = ToolCalibrator(self.config)
        calibrator.z_backend_type = "switch"
        self.toolhead.pos = [220.0, 345.0, 15.0, 0.0]
        gcmd = DummyGCodeCommand({"STATION": "SWITCH", "SAVE": 1})

        calibrator.z_backend.probe_reference_tool = MagicMock(return_value={"success": True, "contact_z": 12.345})
        calibrator.cmd_CALIBRATION_TEACH_STATION(gcmd)

        self.assertEqual(calibrator.navigator.switch_target_z, 12.345)

    def test_camera_thermal_check_err_pre_002(self):
        """Nozzle temperature > 100°C must trigger ERR_PRE_002 before camera entry."""
        calibrator = ToolCalibrator(self.config)
        # Mock hot extruder
        mock_extruder = MagicMock()
        mock_extruder.get_status.return_value = {"temperature": 185.0}
        self.printer.objects["extruder"] = mock_extruder

        gcmd = DummyGCodeCommand({"CALIBRATE_XY": 1, "CALIBRATE_Z": 0})
        calibrator._query_vision = MagicMock(return_value={"status": "ok", "service": "test", "version": "1.0"})

        with self.assertRaises(Exception) as ctx:
            calibrator.cmd_CALIBRATE_TOOL_OFFSETS(gcmd)
        self.assertIn("ERR_PRE_002", str(ctx.exception))

    def test_safe_z_lifted_before_first_toolchange(self):
        """Toolhead must be lifted to Safe_Z before the first toolchange command is executed."""
        calibrator = ToolCalibrator(self.config)
        # Start at low Z
        self.toolhead.pos = [100.0, 100.0, 3.0, 0.0]
        gcmd = DummyGCodeCommand({"CALIBRATE_XY": 0, "CALIBRATE_Z": 0, "DRY_RUN": 1})

        calibrator.cmd_CALIBRATE_TOOL_OFFSETS(gcmd)
        # Must have lifted to at least Safe_Z (35.0)
        self.assertGreaterEqual(self.toolhead.pos[2], 35.0)

    def test_no_duplicate_macros_with_python_commands(self):
        """Verify macros/safe_staging_macros.cfg does not define commands colliding with Python module."""
        calibrator = ToolCalibrator(self.config)
        macro_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "macros", "safe_staging_macros.cfg"))
        self.assertTrue(os.path.exists(macro_path))
        with open(macro_path, "r", encoding="utf-8") as f:
            content = f.read()

        macro_names = re.findall(r"^\[gcode_macro\s+([^\]]+)\]", content, re.MULTILINE | re.IGNORECASE)
        for m_name in macro_names:
            m_name = m_name.strip()
            # Registering each macro in DummyGCode must NOT collide with already registered python commands!
            self.assertNotIn(m_name, self.gcode.commands)

    def test_z_probing_secondary_tool_xy_compensation(self):
        """Verify secondary tool Z probing compensates carriage position by XY offsets."""
        calibrator = ToolCalibrator(self.config)
        calibrator.z_backend_type = "switch"
        calibrator.navigator.switch_target_x = 200.0
        calibrator.navigator.switch_target_y = 200.0
        calibrator.navigator.approach_switch = MagicMock()

        gcmd = DummyGCodeCommand()
        # Secondary tool with X=+2.0, Y=-1.0 offset
        tool_offsets = {"x": 2.0, "y": -1.0}
        calibrator.z_backend.probe_secondary_tool = MagicMock(return_value={"contact_z": 5.0, "suggested_z_offset": 0.1})

        calibrator._execute_z_calibration(1, self.toolhead, self.printer.gcode_move, gcmd, {"contact_z": 4.9}, tool_offsets)
        calibrator.navigator.approach_switch.assert_called_once_with(
            self.toolhead, self.printer.gcode_move, offset_xy=(2.0, -1.0)
        )

    def test_camera_thermal_check_on_active_tool(self):
        """Active tool T1 at 180°C must block CENTER_NOZZLE and CALIBRATION_NAVIGATE even if T0 is 25°C."""
        calibrator = ToolCalibrator(self.config)
        t0_ext = MagicMock()
        t0_ext.get_status.return_value = {"temperature": 25.0}
        t0_ext.name = "extruder"
        t1_ext = MagicMock()
        t1_ext.get_status.return_value = {"temperature": 180.0}
        t1_ext.name = "extruder1"

        self.printer.objects["extruder"] = t0_ext
        self.printer.objects["extruder1"] = t1_ext

        # Set active toolhead extruder to T1
        self.toolhead.get_extruder = MagicMock(return_value=t1_ext)
        gcmd = DummyGCodeCommand({"STATION": "CAMERA"})

        with self.assertRaises(Exception) as ctx:
            calibrator.cmd_CALIBRATION_NAVIGATE(gcmd)
        self.assertIn("ERR_PRE_002", str(ctx.exception))

        with self.assertRaises(Exception) as ctx2:
            calibrator.cmd_CALIBRATION_CENTER_NOZZLE(DummyGCodeCommand())
        self.assertIn("ERR_PRE_002", str(ctx2.exception))

    def test_tool_discovery_no_phantom_extruder_tools(self):
        """Toolchanger with tools [0, 5] must not discover phantom T1 from extruder1."""
        calibrator = ToolCalibrator(self.config)
        tc = MagicMock()
        tc.tool_numbers = [0, 5]
        tc.tools = {0: MagicMock(), 5: MagicMock()}
        self.printer.objects["toolchanger"] = tc

        self.printer.lookup_objects = MagicMock(return_value=[
            ("toolchanger", tc),
            ("extruder", MagicMock()),
            ("extruder1", MagicMock()),
        ])

        discovered = calibrator._discover_tools()
        self.assertEqual(discovered, [0, 5])

    def test_burst_filter_strict_majority_rejection(self):
        """Burst filter with 2-2 tie across 15px spread must reject and not return phantom median."""
        calibrator = ToolCalibrator(self.config)
        frames = [
            {"found": True, "center_uv": [300.0, 200.0], "radius": 40.0, "confidence": 0.9},
            {"found": True, "center_uv": [301.0, 200.0], "radius": 40.0, "confidence": 0.9},
            {"found": True, "center_uv": [500.0, 200.0], "radius": 40.0, "confidence": 0.9},
            {"found": True, "center_uv": [501.0, 200.0], "radius": 40.0, "confidence": 0.9},
        ]
        calibrator._query_vision = MagicMock(side_effect=frames)

        result = calibrator._sample_burst(self.toolhead, samples=4)
        self.assertIsNotNone(result)
        self.assertFalse(result["found"])
        self.assertIn("without strict majority consensus", result["reason"])

    def test_pre_calibration_failure_releases_lock(self):
        """If start_gcode or move_to_safe_z fails after acquire_lock, lock must be released."""
        calibrator = ToolCalibrator(self.config)
        gcmd = DummyGCodeCommand({"CALIBRATE_XY": 1, "CALIBRATE_Z": 0})
        calibrator._ensure_vision_sync = MagicMock()
        calibrator._query_vision = MagicMock(side_effect=[
            {"success": True, "session_token": "tok_123"}, # acquire_lock
            {"service": "test", "version": "1.0"}, # health
            {"success": True} # release_lock
        ])
        calibrator.navigator.move_to_safe_z = MagicMock(side_effect=Exception("Z rail limit exceeded"))

        with self.assertRaises(Exception):
            calibrator.cmd_CALIBRATE_TOOL_OFFSETS(gcmd)

        self.assertIn("FAILED", calibrator.last_run_status)
        calibrator._query_vision.assert_called_with("release_lock", {"session_id": "tok_123", "session_token": "tok_123"}, timeout=2.0)

    def test_dynamic_centering_iteration_budget_converges_large_offset(self):
        """Simulate T2 initial error of 0.865mm: dynamic budget expands beyond 5 steps and converges at step 7."""
        calibrator = ToolCalibrator(self.config)
        calibrator._ensure_vision_sync = MagicMock()
        calibrator.tolerance_mm = 0.015
        calibrator.max_centering_iterations = 5  # Initial configured limit is 5

        # Simulate visual servoing decay: E_{k+1} = E_k * 0.45
        # E1 = 0.865 -> E2 = 0.38925 -> E3 = 0.17516 -> E4 = 0.07882 -> E5 = 0.03547 -> E6 = 0.01596 -> E7 = 0.00718 (<0.015)
        errors = [0.865, 0.38925, 0.17516, 0.07882, 0.03547, 0.01596, 0.00718]
        burst_responses = [
            {"found": True, "center_uv": [640.0 + e * 44.0, 360.0], "radius_px": 25.0, "spread_px": 0.5, "burst_count": 5, "burst_total": 5}
            for e in errors
        ]
        calibrator._sample_burst = MagicMock(side_effect=burst_responses)

        def mock_calc_offset(endpoint, payload, timeout=None):
            uv = payload.get("center_uv", [640.0, 360.0])
            err_px = uv[0] - 640.0
            err_mm = err_px / 44.0
            return {
                "offset_xy": [-0.55 * err_mm, 0.0],
                "raw_error_mm": [err_mm, 0.0]
            }

        calibrator._query_vision = MagicMock(side_effect=mock_calc_offset)
        gcmd = DummyGCodeCommand()

        # Under the old fixed 5-step limit, this would fail with ERR_CV_202.
        # With dynamic budgeting, budget expands to at least 7 steps and succeeds.
        calibrator._center_nozzle(self.toolhead, gcmd)

        # Confirm convergence message was logged
        self.assertTrue(any("Convergence achieved at Step 7" in msg for msg in gcmd.info_messages))
        self.assertTrue(any("Centering budget adjusted" in msg for msg in gcmd.info_messages))

    def test_cmd_calibration_abort_and_run_record(self):
        """CALIBRATION_ABORT sets cancel_requested flag when RUNNING and aborts cleanly."""
        calibrator = ToolCalibrator(self.config)
        gcmd = DummyGCodeCommand()

        # When IDLE, abort responds that no run is active
        calibrator.cmd_CALIBRATION_ABORT(gcmd)
        self.assertFalse(calibrator.cancel_requested)
        self.assertTrue(any("No calibration cycle is currently running" in m for m in gcmd.info_messages))

        # When RUNNING, abort sets cancel_requested
        calibrator.run_record["state"] = "RUNNING"
        calibrator.cmd_CALIBRATION_ABORT(gcmd)
        self.assertTrue(calibrator.cancel_requested)
        self.assertTrue(any("Abort requested" in m for m in gcmd.info_messages))

    def test_run_record_invalidation_on_aborted_run(self):
        """When a cycle aborts midway, run_record marks valid=False and cached_offsets are not overwritten with partial data."""
        calibrator = ToolCalibrator(self.config)
        calibrator._ensure_vision_sync = MagicMock()
        calibrator.cached_offsets = {0: {"x": 0.0, "y": 0.0}, 1: {"x": -0.15, "y": -0.28}}

        # Setup mock for CALIBRATE_TOOL_OFFSETS that aborts on tool 2
        calibrator._discover_tools = MagicMock(return_value=[0, 1, 2])
        calibrator._query_vision = MagicMock(return_value={"success": True, "session_token": "tok_test"})
        calibrator.navigator.move_to_safe_z = MagicMock()
        calibrator.navigator.approach_camera = MagicMock()
        calibrator.navigator.depart_station = MagicMock()

        # Toolhead position returns
        def mock_xy_calib(tool_no, *args, **kwargs):
            if tool_no == 2:
                raise SafeNavigatorException("Centering failed at T2: [ERR_CV_202]")
            return [150.0, 150.0]
        calibrator._execute_xy_calibration = MagicMock(side_effect=mock_xy_calib)

        gcmd = DummyGCodeCommand({"CALIBRATE_XY": 1, "CALIBRATE_Z": 0})
        with self.assertRaises(Exception) as ctx:
            calibrator.cmd_CALIBRATE_TOOL_OFFSETS(gcmd)

        self.assertIn("ERR_CV_202", str(ctx.exception))
        # Verify run_record marked FAILED and invalid
        self.assertEqual(calibrator.run_record["state"], "FAILED")
        self.assertFalse(calibrator.run_record["valid"])
        self.assertEqual(calibrator.run_record["active_tool"], 2)

        # Verify old cached_offsets were NOT corrupted with incomplete cycle data
        self.assertEqual(calibrator.cached_offsets, {0: {"x": 0.0, "y": 0.0}, 1: {"x": -0.15, "y": -0.28}})

        # Verify get_status exposes run_valid: False
        status = calibrator.get_status(0.0)
        self.assertFalse(status["run_valid"])
        self.assertEqual(status["status"], "FAILED")

    def test_xy_compensation_sign_convention_physical_station(self):
        """Verify physical station XY compensation adds offsets (P1 sign fix: carriage = station + offset)."""
        calibrator = ToolCalibrator(self.config)
        calibrator.navigator.switch_target_x = 68.0
        calibrator.navigator.switch_target_y = 10.0
        calibrator.navigator.safe_z = 40.0
        calibrator.navigator.switch_target_z = 15.0

        # Tool 2 with positive offset: X=+0.865mm, Y=+0.270mm
        # Carriage MUST move to 68.0 + 0.865 = 68.865mm, NOT 67.135mm!
        moves = []
        def mock_move(pos, spd):
            moves.append(list(pos))
            for i, val in enumerate(pos):
                if val is not None:
                    self.toolhead.pos[i] = val
        self.toolhead.manual_move = MagicMock(side_effect=mock_move)

        calibrator.navigator.approach_switch(self.toolhead, self.printer.gcode_move, offset_xy=(0.865, 0.270))
        # Apex move is the final move onto switch pin
        apex_move = moves[-1]
        self.assertAlmostEqual(apex_move[0], 68.865, places=3)
        self.assertAlmostEqual(apex_move[1], 10.270, places=3)

        # Also verify Cartographer path adds offset
        calibrator.z_backend_type = "cartographer"
        calibrator.z_backend = MagicMock()
        calibrator.z_backend.get_probe_xy.return_value = (150.0, 150.0)
        calibrator.z_backend.probe_secondary_tool.return_value = {"suggested_z_offset": 0.05}
        moves.clear()

        gcmd = DummyGCodeCommand()
        tool_offsets = {"x": 0.865, "y": 0.270}
        calibrator._execute_z_calibration(2, self.toolhead, self.printer.gcode_move, gcmd, {}, tool_offsets)
        self.assertAlmostEqual(self.toolhead.pos[0], 150.865, places=3)
        self.assertAlmostEqual(self.toolhead.pos[1], 150.270, places=3)

    def test_out_of_band_webhook_abort_during_centering(self):
        """Verify out-of-band webhook abort immediately halts centering loop at the next check."""
        calibrator = ToolCalibrator(self.config)
        calibrator._ensure_vision_sync = MagicMock()
        calibrator._query_vision = MagicMock(return_value={"found": True, "center_uv": [640.0, 360.0], "offset_xy": [0.1, 0.1]})

        # Invoke webhook handler to trigger cancellation
        req = MagicMock()
        calibrator._handle_webhook_abort(req)
        self.assertTrue(calibrator.cancel_requested)
        req.send.assert_called_once_with({"status": "ok", "message": "Calibration abort requested."})

        # Calling _center_nozzle must immediately raise SafeNavigatorException
        gcmd = DummyGCodeCommand()
        with self.assertRaises(SafeNavigatorException) as ctx:
            calibrator._center_nozzle(self.toolhead, gcmd)
        self.assertIn("CALIBRATION_ABORT", str(ctx.exception))

    def test_camera_scale_lock_failure_aborts_before_motion(self):
        """If acquiring lock fails in CALIBRATE_CAMERA_SCALE, abort immediately before motion."""
        calibrator = ToolCalibrator(self.config)
        calibrator._ensure_vision_sync = MagicMock()
        calibrator._query_vision = MagicMock(side_effect=Exception("Connection refused to vision daemon"))
        calibrator.navigator.approach_camera = MagicMock()

        gcmd = DummyGCodeCommand({"DISTANCE": 0.5})
        with self.assertRaises(Exception) as ctx:
            calibrator.cmd_CALIBRATE_CAMERA_SCALE(gcmd)
        self.assertIn("Failed to acquire vision server session lock", str(ctx.exception))
        calibrator.navigator.approach_camera.assert_not_called()

    def test_camera_scale_centering_failure_warning(self):
        """If post-fit centering fails, report WARNING and preserve original station target coordinates."""
        calibrator = ToolCalibrator(self.config)
        calibrator.navigator.cam_target_x = 170.910
        calibrator.navigator.cam_target_y = 18.917

        # Mock scale moves succeeding, but _center_nozzle failing
        def mock_query(endpoint, payload=None, timeout=None):
            if endpoint == "detect_nozzle":
                dx = self.toolhead.pos[0] - 150.0
                dy = self.toolhead.pos[1] - 10.0
                return {"found": True, "center_uv": [320.0 + dx / 0.023, 240.0 + dy / 0.023], "radius": 22.0}
            if endpoint == "acquire_lock":
                return {"success": True, "session_token": "tok_scale"}
            if endpoint == "solve_matrix":
                return {"success": True, "matrix": [[0.023, 0.0, 0.0], [0.0, 0.023, 0.0]]}
            return {"success": True}

        calibrator._query_vision = MagicMock(side_effect=mock_query)
        calibrator._center_nozzle = MagicMock(side_effect=SafeNavigatorException("ERR_CV_202 Centering failed"))

        gcmd = DummyGCodeCommand({"DISTANCE": 0.5})
        calibrator.cmd_CALIBRATE_CAMERA_SCALE(gcmd)

        self.assertIn("WARNING: Scale fitted, but centering failed", calibrator.last_run_status)
        self.assertEqual(calibrator.navigator.cam_target_x, 170.910)
        self.assertEqual(calibrator.navigator.cam_target_y, 18.917)

    def test_physical_burst_spread_rejects_5px_spread_at_0023_mpp(self):
        """At MPP=0.023, a 5.0px spread (0.115mm > 0.08mm) must be rejected without consensus."""
        calibrator = ToolCalibrator(self.config)
        calibrator.calibrated_mpp = 0.023
        calibrator.physical_spread_limit_mm = 0.08

        # 4 frames with a 5.0px spread (e.g. 640.0 vs 645.0) in 2-2 tie
        frames = [
            {"found": True, "center_uv": [640.0, 360.0], "radius": 22.0, "confidence": 0.99},
            {"found": True, "center_uv": [640.0, 360.0], "radius": 22.0, "confidence": 0.99},
            {"found": True, "center_uv": [645.0, 360.0], "radius": 22.0, "confidence": 0.99},
            {"found": True, "center_uv": [645.0, 360.0], "radius": 22.0, "confidence": 0.99},
        ]
        calibrator._query_vision = MagicMock(side_effect=frames)

        result = calibrator._sample_burst(self.toolhead, samples=4)
        self.assertFalse(result["found"])
        self.assertIn("without strict majority consensus", result["reason"])


    def test_auto_teach_camera_gracefully_handles_uncalibrated_matrix(self):
        """When AUTO_TEACH_CAMERA is called on an uncalibrated machine (no matrix), skip centering and save waypoint."""
        calibrator = ToolCalibrator(self.config)
        self.toolhead.pos = [170.910, 18.917, 22.0, 0.0]

        # Health endpoint reports no matrix
        def mock_query(endpoint, payload=None, timeout=None):
            if endpoint == "health":
                return {
                    "status": "ok",
                    "matrix_solved": False,
                    "has_matrix": False,
                    "calibrated_mpp": None
                }
            return {"success": True}

        calibrator._query_vision = MagicMock(side_effect=mock_query)
        calibrator._center_nozzle = MagicMock()

        gcmd = DummyGCodeCommand({})
        calibrator.cmd_AUTO_TEACH_CAMERA(gcmd)

        # _center_nozzle should NOT be called
        calibrator._center_nozzle.assert_not_called()

        # Waypoints should be saved directly based on current toolhead position
        self.assertEqual(calibrator.navigator.cam_target_x, 170.910)
        self.assertEqual(calibrator.navigator.cam_target_y, 18.917)
        self.assertEqual(calibrator.navigator.cam_target_z, 22.0)

        # Check that tool_offsets.cfg was written with the target coordinates
        with open(self.config_path, "r") as f:
            cfg_content = f.read()
        self.assertIn("target_x: 170.91", cfg_content)
        self.assertIn("target_y: 18.917", cfg_content)

    def test_cmd_test_vision_synchronizes_camera_url_and_mpp(self):
        """cmd_CALIBRATION_TEST_VISION must invoke _ensure_vision_sync to configure camera before sampling."""
        calibrator = ToolCalibrator(self.config)
        calibrator.camera_stream_url = "http://127.0.0.1:8080/snapshot.jpg"

        sync_called = []
        original_sync = calibrator._ensure_vision_sync
        def mock_sync():
            sync_called.append(True)
            original_sync()
        calibrator._ensure_vision_sync = mock_sync

        def mock_sample(toolhead, gcmd, samples=3):
            return {
                "found": True,
                "center_uv": [640.0, 360.0],
                "radius_px": 22.0,
                "confidence": 0.99,
                "spread_px": 0.1,
                "burst_count": 3,
                "burst_total": 3,
                "tier": 0,
                "combo": 10
            }
        calibrator._sample_burst = mock_sample
        calibrator._query_vision = MagicMock(return_value={"status": "ok"})

        gcmd = DummyGCodeCommand({})
        calibrator.cmd_CALIBRATION_TEST_VISION(gcmd)

        self.assertTrue(len(sync_called) > 0, "_ensure_vision_sync was not called during cmd_CALIBRATION_TEST_VISION")


if __name__ == "__main__":
    unittest.main()


