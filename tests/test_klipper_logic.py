"""
Unit Tests for Tool-Klipper-Calibration Klipper Extension Logic.
"""

import os
import shutil
import tempfile
import unittest

from klippy.extras.config_manager import ConfigManager


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


if __name__ == "__main__":
    unittest.main()
