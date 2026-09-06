"""Unit tests for Z-calibration safety gating, baseline semantics, and status hardening."""

import os
import shutil
import tempfile
import time
import unittest
from unittest.mock import MagicMock

from klippy.extras.tool_calibrator import ToolCalibrator
from tests.test_calibration_cycle import (
    DummyConfig,
    DummyGCode,
    DummyGCodeCommand,
    DummyPrinter,
    DummyToolchanger,
    DummyToolhead,
)


class TestZGatingAndBaseline(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.config_path = os.path.join(self.test_dir, "tool_offsets.cfg")

        self.toolhead = DummyToolhead()
        self.gcode = DummyGCode()
        self.toolchanger = DummyToolchanger(tools=[0, 1])
        self.printer = DummyPrinter(self.toolhead, self.gcode, self.toolchanger)
        self.base_config_data = {
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
            "allow_shuttle_z": False,
        }

    def tearDown(self):
        if os.path.exists(self.test_dir):
            shutil.rmtree(self.test_dir)

    def test_shuttle_z_rejected_by_default(self):
        """Cartographer with measurement_reference='shuttle' must be rejected during CALIBRATE_TOOL_OFFSETS."""
        config = DummyConfig(self.printer, dict(self.base_config_data))
        calibrator = ToolCalibrator(config)
        gcmd = DummyGCodeCommand({"CALIBRATE_XY": 0, "CALIBRATE_Z": 1})

        with self.assertRaises(Exception) as ctx:
            calibrator.cmd_CALIBRATE_TOOL_OFFSETS(gcmd)
        self.assertIn("ERR_Z_003", str(ctx.exception))
        self.assertIn("shuttle", str(ctx.exception))

    def test_shuttle_z_override_via_gcode_parameter(self):
        """ALLOW_SHUTTLE_Z=1 in G-code command must allow calibration despite shuttle probe."""
        config = DummyConfig(self.printer, dict(self.base_config_data))
        calibrator = ToolCalibrator(config)
        calibrator.z_backend.probe_reference_tool = MagicMock(return_value={"contact_z": 0.0, "source": "cartographer"})
        calibrator.z_backend.probe_secondary_tool = MagicMock(return_value={"suggested_z_offset": 0.12})
        gcmd = DummyGCodeCommand({
            "CALIBRATE_XY": 0,
            "CALIBRATE_Z": 1,
            "ALLOW_SHUTTLE_Z": 1,
            "SAVE_CONFIG": 1,
            "TOOLS": "0,1"
        })

        calibrator.cmd_CALIBRATE_TOOL_OFFSETS(gcmd)
        self.assertEqual(calibrator.last_run_status, "SUCCESS")
        self.assertIn(1, calibrator.cached_offsets)
        self.assertAlmostEqual(calibrator.cached_offsets[1]["z"], 0.12)

    def test_shuttle_z_allowed_via_config(self):
        """allow_shuttle_z = True in config must permit calibration."""
        cfg_data = dict(self.base_config_data)
        cfg_data["allow_shuttle_z"] = True
        config = DummyConfig(self.printer, cfg_data)
        calibrator = ToolCalibrator(config)
        calibrator.z_backend.probe_reference_tool = MagicMock(return_value={"contact_z": 0.0, "source": "cartographer"})
        calibrator.z_backend.probe_secondary_tool = MagicMock(return_value={"suggested_z_offset": -0.05})
        gcmd = DummyGCodeCommand({
            "CALIBRATE_XY": 0,
            "CALIBRATE_Z": 1,
            "SAVE_CONFIG": 1,
            "TOOLS": "0,1"
        })

        calibrator.cmd_CALIBRATE_TOOL_OFFSETS(gcmd)
        self.assertEqual(calibrator.last_run_status, "SUCCESS")

    def test_secondary_tool_z_rejected_without_reference_baseline(self):
        """Direct call to _execute_z_calibration on secondary tool without reference baseline must fail with ERR_CAL_002."""
        cfg_data = dict(self.base_config_data)
        cfg_data["allow_shuttle_z"] = True
        config = DummyConfig(self.printer, cfg_data)
        calibrator = ToolCalibrator(config)
        calibrator.cached_reference_z_result = None

        gcmd = DummyGCodeCommand()
        tool_offsets = {}
        with self.assertRaises(Exception) as ctx:
            calibrator._execute_z_calibration(
                tool_no=1,
                toolhead=self.toolhead,
                gcode_move=self.printer.gcode_move,
                gcmd=gcmd,
                reference_z_result=None,
                tool_offsets=tool_offsets
            )
        self.assertIn("ERR_CAL_002", str(ctx.exception))
        self.assertIn("Reference tool T0 Z baseline must be measured first", str(ctx.exception))

    def test_status_hardening_and_frontend_contract(self):
        """get_status and cmd_CALIBRATION_STATUS must survive corrupted state dictionaries."""
        config = DummyConfig(self.printer, dict(self.base_config_data))
        calibrator = ToolCalibrator(config)

        # 1. Normal state status
        st = calibrator.get_status()
        self.assertEqual(st["status"], "IDLE")
        self.assertIn("active_tool", st)
        self.assertIn("cached_offsets", st)

        # 2. Running state with elapsed calculation
        calibrator.run_record = {
            "run_id": "test-run-123",
            "state": "RUNNING",
            "phase": "CALIBRATE_Z",
            "start_time": time.time() - 5.0,
            "physical_tool": 1,
            "calibrating_tool": 1,
            "valid": False,
        }
        st = calibrator.get_status()
        self.assertEqual(st["status"], "RUNNING")
        self.assertGreaterEqual(st["run_record"]["elapsed_sec"], 4.9)

        # 3. Status command formatting output
        gcmd = DummyGCodeCommand()
        calibrator.cmd_CALIBRATION_STATUS(gcmd)
        output = " ".join(gcmd.info_messages)
        self.assertIn("Tool-Calibrator Status: RUNNING", output)
        self.assertIn("Phase: CALIBRATE_Z", output)

        # 4. Corrupted run_record (None or string)
        calibrator.run_record = "corrupted_record_not_dict"
        st = calibrator.get_status()
        self.assertIsInstance(st, dict)
        self.assertEqual(st["status"], "IDLE")

        # 5. Exception handling in cmd_CALIBRATION_STATUS
        calibrator.run_record = None
        gcmd2 = DummyGCodeCommand()
        calibrator.cmd_CALIBRATION_STATUS(gcmd2)
        self.assertTrue(len(gcmd2.info_messages) > 0)
