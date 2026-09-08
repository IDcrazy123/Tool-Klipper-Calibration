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
        self.assertIn("[tool_offsets]", content)
        self.assertIn("t0_x: 0.0000", content)

        # Save Tool 1
        self.manager.save_tool_offsets(1, {"x": 0.145, "y": -0.082, "z": 0.312})
        with open(self.cfg_file, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertIn("t1_x: 0.1450", content)
        self.assertIn("t1_y: -0.0820", content)
        self.assertIn("t1_z: 0.3120", content)

        # Update Tool 1 with new offset
        self.manager.save_tool_offsets(1, {"x": 0.150, "y": -0.080, "z": 0.315})
        with open(self.cfg_file, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertIn("t1_x: 0.1500", content)
        self.assertNotIn("t1_x: 0.1450", content)

    def test_backup_and_rollback(self):
        # Initial version
        self.manager.save_tool_offsets(1, {"x": 0.100, "y": 0.100, "z": 0.100})
        # Second version
        self.manager.save_tool_offsets(1, {"x": 0.999, "y": 0.999, "z": 0.999})

        with open(self.cfg_file, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertIn("t1_x: 0.9990", content)

        # Rollback
        restored_backup = self.manager.rollback()
        self.assertTrue(os.path.exists(restored_backup))

        with open(self.cfg_file, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertIn("t1_x: 0.1000", content)

    def test_rollback_specific_backup(self):
        self.manager.max_backups = 10
        # Version 1
        self.manager.save_tool_offsets(1, {"x": 0.111, "y": 0.111, "z": 0.111})
        b1 = self.manager.create_backup()
        # Version 2
        self.manager.save_tool_offsets(1, {"x": 0.222, "y": 0.222, "z": 0.222})

        # Rollback to b1 specifically (by filename only)
        restored = self.manager.rollback(os.path.basename(b1))
        self.assertEqual(os.path.abspath(restored), os.path.abspath(b1))
        with open(self.cfg_file, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertIn("t1_x: 0.1110", content)

        # Rollback to non-existent backup should raise ConfigManagerException
        from klippy.extras.config_manager import ConfigManagerException
        with self.assertRaises(ConfigManagerException):
            self.manager.rollback("non_existent_backup.calib_backup_9999")

    def test_backup_sort_mixed_directories_by_timestamp(self):
        """Backups from multiple directories must sort by timestamp/mtime rather than path prefix."""
        # Create two directories: dir_a (alphabetically first) and dir_z (alphabetically last)
        dir_a = os.path.join(self.test_dir, "aaa_new_dir")
        dir_z = os.path.join(self.test_dir, "zzz_legacy_dir")
        os.makedirs(dir_a, exist_ok=True)
        os.makedirs(dir_z, exist_ok=True)

        # File in dir_a with NEWER timestamp
        b_new = os.path.join(dir_a, "tool_offsets.cfg.calib_backup_20260908_180000")
        # File in dir_z with OLDER timestamp
        b_old = os.path.join(dir_z, "tool_offsets.cfg.calib_backup_20260908_120000")

        with open(b_new, "w", encoding="utf-8") as f:
            f.write("[tool_offsets]\nt1_x: 0.888\n")
        with open(b_old, "w", encoding="utf-8") as f:
            f.write("[tool_offsets]\nt1_x: 0.111\n")

        # Mock glob/discovery across both directories
        with unittest.mock.patch("glob.glob") as mock_glob:
            # Return old then new in random/reversed order
            mock_glob.side_effect = lambda pat: [b_old, b_new] if "calib_backup_" in pat else []

            backups = self.manager._get_all_backups()
            # Sorted oldest first, newest last
            self.assertEqual(len(backups), 2)
            self.assertEqual(backups[0], b_old)
            self.assertEqual(backups[1], b_new)

            # Rollback without args must restore b_new (the latest timestamp)
            restored = self.manager.rollback()
            self.assertEqual(restored, b_new)
            with open(self.cfg_file, "r", encoding="utf-8") as f:
                content = f.read()
            self.assertIn("t1_x: 0.888", content)

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


class TestToolOffsetsAndCommands(unittest.TestCase):
    def test_tool_offsets_module(self):
        """Verify ToolOffsets loads options and returns in get_status."""
        from klippy.extras.tool_offsets import ToolOffsets, load_config
        mock_config = MagicMock()
        mock_config.get_printer.return_value = MagicMock()
        mock_config.get_name.return_value = "tool_offsets"
        mock_config.get_prefix_options.return_value = ["t1_x", "t1_y", "t1_z"]
        mock_config.getfloat.side_effect = lambda k, default=None: {"t1_x": 0.123, "t1_y": -0.456, "t1_z": 0.045}.get(k, default)

        to = load_config(mock_config)
        status = to.get_status()
        self.assertEqual(status["t1_x"], 0.123)
        self.assertEqual(status["t1_y"], -0.456)
        self.assertEqual(status["t1_z"], 0.045)

    def test_save_all_tool_offsets_creates_unified_section(self):
        """Verify save_all_tool_offsets saves into [tool_offsets] without duplicate [tool T*] headers."""
        temp_dir = tempfile.mkdtemp()
        cfg_path = os.path.join(temp_dir, "tool_offsets.cfg")
        manager = ConfigManager(cfg_path)

        results = {
            1: {"x": 0.1234, "y": -0.5678, "z": 0.045},
            2: {"x": -0.2222, "y": 0.3333, "z": -0.010}
        }
        manager.save_all_tool_offsets(results)

        with open(cfg_path, "r", encoding="utf-8") as f:
            content = f.read()

        self.assertIn("[tool_offsets]", content)
        self.assertNotIn("[tool 1]", content)
        self.assertNotIn("[tool 2]", content)
        self.assertNotIn("[tool T1]", content)
        self.assertIn("t1_x: 0.1234", content)
        self.assertIn("t1_y: -0.5678", content)
        self.assertIn("t2_x: -0.2222", content)
        self.assertIn("t2_z: -0.0100", content)
        shutil.rmtree(temp_dir)

    def test_tool_offsets_applied_to_tool_objects(self):
        """Verify ToolOffsets applies offsets to printer [tool 1] object and runs SET_TOOL_OFFSET command."""
        from klippy.extras.tool_offsets import ToolOffsets, load_config
        mock_printer = MagicMock()
        mock_config = MagicMock()
        mock_config.get_printer.return_value = mock_printer
        mock_config.get_name.return_value = "tool_offsets"
        mock_config.get_prefix_options.return_value = ["t1_x", "t1_y", "t1_z"]
        mock_config.getfloat.side_effect = lambda k, default=None: {"t1_x": 0.1234, "t1_y": -0.4567, "t1_z": 0.089}.get(k, default)

        tool1_obj = MagicMock()
        tool1_obj.gcode_x_offset = 0.0
        tool1_obj.gcode_y_offset = 0.0
        tool1_obj.gcode_z_offset = 0.0

        mock_gcode = MagicMock()
        mock_gcode.commands = {"SET_TOOL_OFFSET": MagicMock()}

        def fake_lookup(name, default=None):
            if name in ("tool 1", "tool T1"):
                return tool1_obj
            if name == "gcode":
                return mock_gcode
            return default

        mock_printer.lookup_object.side_effect = fake_lookup

        to = load_config(mock_config)
        to._handle_ready()

        self.assertAlmostEqual(tool1_obj.gcode_x_offset, 0.1234, delta=1e-4)
        self.assertAlmostEqual(tool1_obj.gcode_y_offset, -0.4567, delta=1e-4)
        self.assertAlmostEqual(tool1_obj.gcode_z_offset, 0.089, delta=1e-4)
        mock_gcode.run_script_from_command.assert_called_with("SET_TOOL_OFFSET TOOL=1 X=0.1234 Y=-0.4567 Z=0.0890")


if __name__ == "__main__":
    unittest.main()

