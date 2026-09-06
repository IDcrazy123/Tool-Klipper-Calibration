"""
Unit Tests for Tool-Klipper-Calibration Klipper Extension Logic.
"""

import os
import shutil
import tempfile
import unittest

from klippy.extras.config_manager import ConfigManager
from klippy.extras.tool_calibrator_station import ToolCalibratorStation
from unittest.mock import MagicMock


class TestConfigManager(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.cfg_file = os.path.join(self.test_dir, "tool_offsets.cfg")
        self.manager = ConfigManager(self.cfg_file)
        self.manager.max_backups = 3

    def tearDown(self):
        shutil.rmtree(self.test_dir)

    def test_save_and_update_offsets(self):
        # Save Tool 0
        self.manager.save_tool_offsets(0, {"x": 0.0, "y": 0.0, "z": 0.0})
        self.assertTrue(os.path.exists(self.cfg_file))

        with open(self.cfg_file, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertIn("[tool 0]", content)
        self.assertIn("gcode_x_offset: 0.000", content)

        # Save Tool 1
        self.manager.save_tool_offsets(1, {"x": 0.145, "y": -0.082, "z": 0.312})
        with open(self.cfg_file, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertIn("[tool 1]", content)
        self.assertIn("gcode_x_offset: 0.145", content)
        self.assertIn("gcode_y_offset: -0.082", content)
        self.assertIn("gcode_z_offset: 0.312", content)

        # Update Tool 1 with new offset
        self.manager.save_tool_offsets(1, {"x": 0.150, "y": -0.080, "z": 0.315})
        with open(self.cfg_file, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertIn("gcode_x_offset: 0.150", content)
        self.assertNotIn("gcode_x_offset: 0.145", content)

    def test_backup_and_rollback(self):
        # Initial version
        self.manager.save_tool_offsets(1, {"x": 0.100, "y": 0.100, "z": 0.100})
        # Second version
        self.manager.save_tool_offsets(1, {"x": 0.999, "y": 0.999, "z": 0.999})

        with open(self.cfg_file, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertIn("gcode_x_offset: 0.999", content)

        # Rollback
        restored_backup = self.manager.rollback()
        self.assertTrue(os.path.exists(restored_backup))

        with open(self.cfg_file, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertIn("gcode_x_offset: 0.100", content)

    def test_save_and_load_station_section(self):
        """Verify saving and loading custom sections like [tool_calibrator_station camera]."""
        station_data = {
            "target_x": 150.123,
            "target_y": 10.456,
            "target_z": 22.000,
            "approach_x": 150.123,
            "approach_y": 35.456,
            "safe_z": 35.000
        }
        self.manager.save_section("tool_calibrator_station camera", station_data)
        loaded = self.manager.load_section("tool_calibrator_station camera")

        self.assertEqual(float(loaded["target_x"]), 150.123)
        self.assertEqual(float(loaded["target_y"]), 10.456)
        self.assertEqual(float(loaded["approach_y"]), 35.456)


    def test_tool_t_prefix_matching(self):
        """Toolchanger allows [tool T1] or [tool 1]. ConfigManager must update [tool T1] in-place without creating duplicate [tool 1]."""
        with open(self.cfg_file, "w", encoding="utf-8") as f:
            f.write("[tool T1]\ngcode_x_offset: 0.050\ngcode_y_offset: 0.050\n")

        self.manager.save_tool_offsets(1, {"x": 0.123, "y": -0.456, "z": 0.789})

        with open(self.cfg_file, "r", encoding="utf-8") as f:
            content = f.read()

        # Must keep [tool T1] and NOT create [tool 1]
        self.assertIn("[tool T1]", content)
        self.assertNotIn("[tool 1]", content)
        self.assertIn("gcode_x_offset: 0.123", content)
        self.assertIn("gcode_y_offset: -0.456", content)
        self.assertIn("gcode_z_offset: 0.789", content)

    def test_save_affine_matrix_translation_precision(self):
        """Affine matrix translations matrix_tx and matrix_ty must be saved with 6-digit precision."""
        cam_data = {
            "mpp": 0.012543,
            "matrix_a": 0.012456,
            "matrix_b": -0.000123,
            "matrix_tx": 0.123456,
            "matrix_c": 0.000145,
            "matrix_d": 0.012501,
            "matrix_ty": -0.654321
        }
        self.manager.save_section("tool_calibrator_station camera", cam_data)
        loaded = self.manager.load_section("tool_calibrator_station camera")

        self.assertAlmostEqual(float(loaded["matrix_tx"]), 0.123456, places=6)
        self.assertAlmostEqual(float(loaded["matrix_ty"]), -0.654321, places=6)


class TestToolCalibratorStation(unittest.TestCase):
    def test_station_loads_all_matrix_parameters(self):
        """Verify ToolCalibratorStation loads matrix_tx, matrix_ty and exposes in get_status."""
        config_data = {
            "target_x": 150.0,
            "target_y": 10.0,
            "target_z": 22.0,
            "approach_x": 150.0,
            "approach_y": 35.0,
            "safe_z": 40.0,
            "mpp": 0.012543,
            "matrix_a": 0.012456,
            "matrix_b": -0.000123,
            "matrix_tx": 0.123456,
            "matrix_c": 0.000145,
            "matrix_d": 0.012501,
            "matrix_ty": -0.654321,
        }
        mock_config = MagicMock()
        mock_config.get_printer.return_value = MagicMock()
        mock_config.get_name.return_value = "tool_calibrator_station camera"
        mock_config.getfloat.side_effect = lambda key, default=None: config_data.get(key, default)

        station = ToolCalibratorStation(mock_config)
        self.assertEqual(station.target_x, 150.0)
        self.assertEqual(station.matrix_tx, 0.123456)
        self.assertEqual(station.matrix_ty, -0.654321)

        status = station.get_status()
        self.assertEqual(status["matrix_a"], 0.012456)
        self.assertEqual(status["matrix_tx"], 0.123456)
        self.assertEqual(status["matrix_ty"], -0.654321)
        self.assertEqual(status["mpp"], 0.012543)


if __name__ == "__main__":
    unittest.main()
