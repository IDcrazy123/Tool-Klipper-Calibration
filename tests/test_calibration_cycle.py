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

    def getint(self, key, default=None, minval=None, maxval=None):
        val = self.data.get(key, default)
        return int(val) if val is not None else default

    def getfloat(self, key, default=None, above=None, below=None):
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


if __name__ == "__main__":
    unittest.main()
