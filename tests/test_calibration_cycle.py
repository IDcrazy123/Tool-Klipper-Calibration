"""
Integration test for full calibration cycle in ToolCalibrator.
"""

import unittest
import os
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


class DummyGCodeMacro:
    def load_template(self, config, name, default):
        return ""

    def run_script(self, name, script, context):
        pass


class DummyGCode:
    def __init__(self):
        self.commands = {}
        self.executed_scripts = []

    def register_command(self, name, func, desc=None):
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

    def get_reactor(self):
        return self.reactor

    def lookup_object(self, name, default=None):
        if name == "toolhead":
            return self.toolhead
        elif name == "gcode":
            return self.gcode
        elif name == "toolchanger":
            return self.toolchanger
        elif name == "gcode_move":
            return self.gcode_move
        elif name == "gcode_macro":
            return self.gcode_macro
        return default

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

        # Tool 0 reference offsets should be present
        self.assertIn("[tool 0]", content)
        # Tool 1 secondary offsets should be present
        self.assertIn("[tool 1]", content)
        self.assertIn("gcode_z_offset: -0.125", content)

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
                # Returns dummy center
                return {"found": True, "center_uv": [320.0, 240.0], "radius": 45}
            elif endpoint == "calculate_offset":
                return {"offset_xy": [0.0, 0.0]}
            elif endpoint == "calibrate_mpp":
                return {"success": True, "mpp": 0.01542}
            elif endpoint == "solve_matrix":
                return {"success": True}
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
        self.assertEqual(calibrator.calibrated_mpp, 0.01542)

    def test_generic_tool_discovery(self):
        """Verifies toolhead discovery across diverse Klipper toolchanger architectures."""
        calibrator = ToolCalibrator(self.config)

        # Case 1: Explicit TOOLS parameter
        tools = calibrator._discover_tools(tools_param="2,3")
        self.assertEqual(tools, [0, 2, 3])

        # Case 2: Config tools parameter
        calibrator.config.data["tools"] = "0, 1, 2"
        tools = calibrator._discover_tools(tools_param=None)
        self.assertEqual(tools, [0, 1, 2])
        del calibrator.config.data["tools"]

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
        self.assertEqual(res["center_uv"], [320.2, 240.1])
        self.assertEqual(res["radius_px"], 40.5)
        self.assertEqual(res["burst_count"], 3)
        self.assertEqual(res["burst_total"], 3)
        self.assertEqual(res["spread_px"], 75.0)

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


if __name__ == "__main__":
    unittest.main()
