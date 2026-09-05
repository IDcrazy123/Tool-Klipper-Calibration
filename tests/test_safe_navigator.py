"""
Unit tests for SafeNavigator 3-tier safe waypointing kinematics.
"""

import unittest
from klippy.extras.safe_navigator import SafeNavigator, SafeNavigatorException


class DummyReactor:
    def monotonic(self):
        return 0.0


class DummyToolhead:
    def __init__(self, start_pos=None, homed_axes="xyz"):
        self.pos = list(start_pos or [150.0, 150.0, 10.0, 0.0])
        self.homed_axes = homed_axes
        self.moves = []
        self.axis_min = [0.0, 0.0, 0.0, 0.0]
        self.axis_max = [300.0, 300.0, 250.0, 0.0]

    def get_position(self):
        return list(self.pos)

    def manual_move(self, new_pos, speed):
        self.moves.append((list(new_pos), speed))
        for i, val in enumerate(new_pos):
            if val is not None:
                self.pos[i] = val

    def wait_moves(self):
        pass

    def get_status(self, eventtime):
        return {
            "homed_axes": self.homed_axes,
            "axis_minimum": self.axis_min,
            "axis_maximum": self.axis_max
        }


class DummyPrinter:
    def __init__(self, toolhead):
        self.toolhead = toolhead
        self.reactor = DummyReactor()

    def lookup_object(self, name, default=None):
        if name == "toolhead":
            return self.toolhead
        return default

    def get_reactor(self):
        return self.reactor


class DummyConfig:
    def __init__(self, printer, config_dict=None):
        self._printer = printer
        self.data = config_dict or {}

    def get_printer(self):
        return self._printer

    def getfloat(self, key, default=None, above=None, below=None):
        val = self.data.get(key, default)
        if val is None:
            return None
        val = float(val)
        if above is not None and val <= above:
            raise ValueError(f"{key} must be above {above}")
        if below is not None and val >= below:
            raise ValueError(f"{key} must be below {below}")
        return val


class TestSafeNavigator(unittest.TestCase):
    def setUp(self):
        self.toolhead = DummyToolhead(start_pos=[50.0, 50.0, 10.0, 0.0], homed_axes="xyz")
        self.toolhead.axis_max = [350.0, 350.0, 300.0, 0.0]
        self.printer = DummyPrinter(self.toolhead)
        self.config_data = {
            "travel_speed": 100.0,
            "approach_speed": 25.0,
            "z_speed": 10.0,
            "safe_z": 35.0,
            "camera_approach_x": 150.0,
            "camera_approach_y": 35.0,
            "camera_x": 150.0,
            "camera_y": 10.0,
            "camera_focal_z": 22.0,
            "switch_approach_x": 220.0,
            "switch_approach_y": 320.0,
            "zswitch_x_pos": 220.0,
            "zswitch_y_pos": 345.0,
            "zswitch_z_pos": 15.0,
        }
        self.config = DummyConfig(self.printer, self.config_data)
        self.nav = SafeNavigator(self.config)

    def test_move_to_safe_z(self):
        """Test vertical elevation to safe_z."""
        self.toolhead.pos = [50.0, 50.0, 10.0, 0.0]
        self.nav.move_to_safe_z(self.toolhead, None)
        self.assertEqual(self.toolhead.pos[2], 35.0)

    def test_approach_camera_sequence(self):
        """
        Verify camera 3-tier sequence:
        1. Lift to Safe_Z
        2. Move to Camera Approach XY
        3. Descend to camera_focal_z
        4. Move to camera optical target XY
        """
        self.nav.approach_camera(self.toolhead, None)
        pos = self.toolhead.get_position()
        self.assertEqual(pos[0], 150.0)
        self.assertEqual(pos[1], 10.0)
        self.assertEqual(pos[2], 22.0)
        # Ensure moves happened in order
        self.assertGreaterEqual(len(self.toolhead.moves), 4)
        # First move should lift Z to 35.0
        self.assertEqual(self.toolhead.moves[0][0][2], 35.0)

    def test_approach_switch_sequence(self):
        """Verify Z switch 3-tier approach sequence."""
        self.nav.approach_switch(self.toolhead, None, z_clearance=5.0)
        pos = self.toolhead.get_position()
        self.assertEqual(pos[0], 220.0)
        self.assertEqual(pos[1], 345.0)
        self.assertEqual(pos[2], 20.0) # 15.0 + 5.0 clearance

    def test_depart_station(self):
        """Verify departing lifts to Safe_Z."""
        self.toolhead.pos = [150.0, 10.0, 22.0, 0.0]
        self.nav.depart_station(self.toolhead, None)
        self.assertEqual(self.toolhead.pos[2], 35.0)

    def test_unhomed_failsafe(self):
        """Verify navigation aborts if printer is not homed."""
        unhomed_toolhead = DummyToolhead(homed_axes="x") # Y and Z not homed
        printer = DummyPrinter(unhomed_toolhead)
        config = DummyConfig(printer, self.config_data)
        nav = SafeNavigator(config)

        with self.assertRaises(SafeNavigatorException):
            nav.approach_camera(unhomed_toolhead, None)

    def test_different_frame_sizes_voron_350(self):
        """Verify dynamic adaptation on a Voron 350 (0-350mm)."""
        v350_toolhead = DummyToolhead(start_pos=[175.0, 175.0, 10.0, 0.0])
        v350_toolhead.axis_min = [0.0, 0.0, 0.0, 0.0]
        v350_toolhead.axis_max = [350.0, 350.0, 350.0, 0.0]
        printer = DummyPrinter(v350_toolhead)
        nav = SafeNavigator(DummyConfig(printer, self.config_data))

        center_x, center_y = nav.get_bed_center()
        self.assertAlmostEqual(center_x, 175.0)
        self.assertAlmostEqual(center_y, 175.0)

    def test_different_frame_sizes_voron_v0(self):
        """Verify dynamic adaptation on a compact Voron V0 (0-120mm)."""
        v0_toolhead = DummyToolhead(start_pos=[60.0, 60.0, 10.0, 0.0])
        v0_toolhead.axis_min = [0.0, 0.0, 0.0, 0.0]
        v0_toolhead.axis_max = [120.0, 120.0, 120.0, 0.0]
        printer = DummyPrinter(v0_toolhead)
        nav = SafeNavigator(DummyConfig(printer, self.config_data))

        center_x, center_y = nav.get_bed_center()
        self.assertAlmostEqual(center_x, 60.0)
        self.assertAlmostEqual(center_y, 60.0)

    def test_out_of_bounds_target_rejection(self):
        """Target positions exceeding hardware limits must raise SafeNavigatorException."""
        self.nav.cam_target_x = 450.0 # Exceeds limit
        self.nav.cam_target_y = 10.0
        with self.assertRaises(SafeNavigatorException):
            self.nav.approach_camera(self.toolhead, None)

    def test_config_aliases_and_speed_normalization(self):
        """Verify camera_target_x/y/z aliases and mm/min to mm/s speed auto-conversion."""
        aliased_config = {
            "travel_speed": 12000.0, # 12000 mm/min -> 200 mm/s
            "approach_speed": 1800.0, # 1800 mm/min -> 30 mm/s
            "z_speed": 600.0,        # 600 mm/min -> 10 mm/s
            "safe_z": 40.0,
            "camera_target_x": 160.0,
            "camera_target_y": 12.0,
            "camera_target_z": 24.0,
            "switch_target_x": 230.0,
            "switch_target_y": 340.0,
            "switch_target_z": 16.0,
        }
        nav = SafeNavigator(DummyConfig(self.printer, aliased_config))
        self.assertAlmostEqual(nav.travel_speed, 200.0)
        self.assertAlmostEqual(nav.approach_speed, 30.0)
        self.assertAlmostEqual(nav.z_speed, 10.0)
        self.assertEqual(nav.cam_target_x, 160.0)
        self.assertEqual(nav.cam_target_y, 12.0)
        self.assertEqual(nav.cam_target_z, 24.0)
        self.assertEqual(nav.switch_target_x, 230.0)
        self.assertEqual(nav.switch_target_y, 340.0)
        self.assertEqual(nav.switch_target_z, 16.0)


if __name__ == "__main__":
    unittest.main()
