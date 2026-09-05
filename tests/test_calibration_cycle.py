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


if __name__ == "__main__":
    unittest.main()
