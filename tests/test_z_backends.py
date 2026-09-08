"""
Unit & Integration Tests for Z-Backends in Tool-Klipper-Calibration.
Validates Cartographer Touch V4 and Physical Switch logic, mathematical relative delta Z,
thermal safety guards, bed center auto-fallback, and safe liftoff retraction.
"""

import unittest
from unittest.mock import MagicMock, patch
import os
import tempfile
import shutil

from klippy.extras.z_backends.cartographer_backend import CartographerBackend
from klippy.extras.z_backends.switch_backend import SwitchBackend


class DummyGCodeCommand:
    def __init__(self, params=None):
        self.params = params or {}
        self.info_messages = []
        self.error_messages = []

    def respond_info(self, msg):
        self.info_messages.append(msg)

    def respond_error(self, msg):
        self.error_messages.append(msg)

    def error(self, msg):
        return RuntimeError(msg)


class DummyReactor:
    def monotonic(self):
        return 100.0

    def pause(self, until):
        pass


class DummyToolhead:
    def __init__(self, pos=(150.0, 150.0, 20.0, 0.0)):
        self.pos = list(pos)

    def get_position(self):
        return list(self.pos)

    def manual_move(self, new_pos, speed):
        for i, val in enumerate(new_pos):
            if val is not None:
                self.pos[i] = val

    def move(self, new_pos, speed):
        self.manual_move(new_pos, speed)

    def set_position(self, new_pos):
        self.pos = list(new_pos)

    def wait_moves(self):
        pass

    def get_status(self, eventtime):
        return {
            "homed_axes": "xyz",
            "axis_minimum": [0.0, 0.0, 0.0, 0.0],
            "axis_maximum": [350.0, 350.0, 300.0, 0.0]
        }


class DummyExtruder:
    def __init__(self, temp=22.0):
        self.temp = temp

    def get_status(self, eventtime):
        return {"temperature": self.temp}


class DummyGCode:
    def __init__(self):
        self.executed_scripts = []

    def run_script_from_command(self, script):
        self.executed_scripts.append(script)


class DummyPrinter:
    def __init__(self, toolhead=None, gcode=None, extruders=None, cartographer=None):
        self.reactor = DummyReactor()
        self.toolhead = toolhead or DummyToolhead()
        self.gcode = gcode or DummyGCode()
        self.objects = {
            "toolhead": self.toolhead,
            "gcode": self.gcode,
        }
        if extruders:
            self.objects.update(extruders)
        if cartographer:
            self.objects["cartographer"] = cartographer

    def get_reactor(self):
        return self.reactor

    def lookup_object(self, name, default=None):
        return self.objects.get(name, default)

    def load_object(self, config, name):
        return self.lookup_object(name)


class DummyConfig:
    def __init__(self, printer, values=None):
        self.printer = printer
        self.values = values or {}

    def get_printer(self):
        return self.printer

    def get(self, key, default=None):
        return self.values.get(key, default)

    def getfloat(self, key, default=None, above=None, maxval=None):
        val = self.values.get(key, default)
        return float(val) if val is not None else None

    def getint(self, key, default=None, minval=None, maxval=None):
        val = self.values.get(key, default)
        return int(val) if val is not None else None

    def has_section(self, section):
        return False

    def error(self, msg):
        return RuntimeError(msg)


class TestCartographerBackend(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.printer_cfg_path = os.path.join(self.test_dir, "printer.cfg")
        with open(self.printer_cfg_path, "w", encoding="utf-8") as f:
            f.write("""
[printer]
kinematics: corexy

#*# <---------------------- SAVE_CONFIG ---------------------->
#*# [cartographer touch_model default]
#*# z_offset = 0.04250
""")

        self.toolhead = DummyToolhead()
        self.gcode = DummyGCode()
        self.cartographer_obj = MagicMock()
        self.cartographer_obj.last_z_result = 0.050

        self.printer = DummyPrinter(
            toolhead=self.toolhead,
            gcode=self.gcode,
            extruders={"extruder": DummyExtruder(25.0), "extruder1": DummyExtruder(25.0)},
            cartographer=self.cartographer_obj
        )

        self.config = DummyConfig(self.printer, {
            "touch_model_config_path": self.printer_cfg_path,
            "carto_max_touch_temp": 150.0,
            "carto_retract_z": 5.0
        })

    def tearDown(self):
        if os.path.exists(self.test_dir):
            shutil.rmtree(self.test_dir)

    def test_load_touch_model_offset(self):
        """Cartographer touch_model offset should be parsed from save_config section."""
        backend = CartographerBackend(self.config)
        self.assertAlmostEqual(backend.touch_model_z_offset, 0.04250, delta=0.0001)

    def test_auto_bed_center_probing_coordinates(self):
        """When probe_x and probe_y are omitted, default to the exact bed center."""
        backend = CartographerBackend(self.config)
        px, py = backend.get_probe_xy()
        # With 350x350 max, center is 175.0, 175.0
        self.assertEqual(px, 175.0)
        self.assertEqual(py, 175.0)

    def test_explicit_probing_coordinates(self):
        """When probe_x and probe_y are set, use explicit values."""
        cfg = DummyConfig(self.printer, {
            "carto_probe_x": 120.0,
            "carto_probe_y": 140.0,
            "touch_model_config_path": self.printer_cfg_path
        })
        backend = CartographerBackend(cfg)
        px, py = backend.get_probe_xy()
        self.assertEqual(px, 120.0)
        self.assertEqual(py, 140.0)

    def test_relative_delta_z_longer_nozzle(self):
        """
        Reference Tool T0 executes CARTOGRAPHER_TOUCH_HOME, redefining Z=0 at physical touch.
        Secondary Tool T1 has a longer nozzle, touching earlier at Z=0.080 in the homed coordinate system.
        Relative delta should be +0.080mm.
        """
        backend = CartographerBackend(self.config)
        gcmd = DummyGCodeCommand()

        # 1. Reference Tool T0 touch homes (origin reset to 0.0)
        self.cartographer_obj.last_z_result = 0.050
        ref_result = backend.probe_reference_tool(0, gcmd)
        self.assertEqual(ref_result["source"], "cartographer_touch_reference")
        self.assertTrue(ref_result["is_homed"])
        self.assertAlmostEqual(ref_result["contact_z"], 0.0, delta=0.001)
        self.assertAlmostEqual(ref_result["raw_contact_z"], 0.050, delta=0.001)
        self.assertEqual(ref_result["suggested_z_offset"], 0.0)
        # Verify safe liftoff occurred
        self.assertGreater(self.toolhead.pos[2], 0.050)

        # 2. Secondary Tool T1 probes in homed coordinate system
        self.cartographer_obj.last_z_result = 0.080
        sec_result = backend.probe_secondary_tool(1, ref_result, gcmd)
        self.assertEqual(sec_result["source"], "cartographer_touch")
        self.assertAlmostEqual(sec_result["contact_z"], 0.080, delta=0.001)
        # Delta Z = 0.080mm directly in homed frame
        self.assertAlmostEqual(sec_result["suggested_z_offset"], 0.080, delta=0.001)

    def test_relative_delta_z_shorter_nozzle(self):
        """
        Reference Tool T0 touches at Z=0.050 (non-homing baseline).
        Secondary Tool T2 has a shorter nozzle, touching at Z=0.010.
        Relative delta should be -0.040mm.
        """
        backend = CartographerBackend(self.config)
        gcmd = DummyGCodeCommand()

        ref_result = {"contact_z": 0.050, "is_homed": False}
        self.cartographer_obj.last_z_result = 0.010
        sec_result = backend.probe_secondary_tool(2, ref_result, gcmd)
        # Delta Z = 0.010 - 0.050 = -0.040mm
        self.assertAlmostEqual(sec_result["suggested_z_offset"], -0.040, delta=0.001)

    def test_relative_delta_z_non_homing_probe(self):
        """
        When touch_home_gcode does not reset origin (e.g. TOUCH_PROBE),
        relative delta is computed by subtracting reference contact from secondary contact.
        """
        cfg = DummyConfig(self.printer, {
            "touch_home_gcode": "CARTOGRAPHER_TOUCH_PROBE",
            "touch_probe_gcode": "CARTOGRAPHER_TOUCH_PROBE",
            "touch_model_config_path": self.printer_cfg_path
        })
        backend = CartographerBackend(cfg)
        gcmd = DummyGCodeCommand()

        self.cartographer_obj.last_z_result = 0.050
        ref_result = backend.probe_reference_tool(0, gcmd)
        self.assertFalse(ref_result["is_homed"])
        self.assertAlmostEqual(ref_result["contact_z"], 0.050, delta=0.001)

        self.cartographer_obj.last_z_result = 0.130
        sec_result = backend.probe_secondary_tool(1, ref_result, gcmd)
        self.assertAlmostEqual(sec_result["suggested_z_offset"], 0.080, delta=0.001)

    def test_thermal_safety_guard_blocks_hot_nozzle(self):
        """Cartographer touch must abort if nozzle temperature exceeds safety limit (> 150°C)."""
        # Set extruder1 to hot printing temperature (210°C)
        self.printer.objects["extruder1"] = DummyExtruder(210.0)
        backend = CartographerBackend(self.config)
        gcmd = DummyGCodeCommand()

        ref_result = {"contact_z": 0.050}
        with self.assertRaises(RuntimeError) as ctx:
            backend.probe_secondary_tool(1, ref_result, gcmd)

        self.assertIn("ERR_PRE_002", str(ctx.exception))
        self.assertIn("exceeds safe Cartographer touch limit", str(ctx.exception))

    def test_resolve_touch_cmd_scanner(self):
        """When SCANNER_TOUCH is registered in Klipper gcode commands, resolve dynamically."""
        self.gcode.commands = {"SCANNER_TOUCH": MagicMock()}
        backend = CartographerBackend(self.config)
        self.assertEqual(backend.touch_probe_gcode, "SCANNER_TOUCH")
        self.assertEqual(backend.touch_home_gcode, "SCANNER_TOUCH")

    def test_build_command_str_with_parameters(self):
        """Cartographer Touch appends EXPERIMENTAL_RANDOM_RADIUS to HOME and MAX_SAMPLES to PROBE."""
        cfg = DummyConfig(self.printer, {
            "touch_home_gcode": "CARTOGRAPHER_TOUCH_HOME",
            "touch_probe_gcode": "CARTOGRAPHER_TOUCH_PROBE",
            "carto_random_radius": 2.5,
            "carto_max_samples": 5,
            "touch_model_config_path": self.printer_cfg_path
        })
        backend = CartographerBackend(cfg)
        self.assertEqual(backend.touch_home_gcode, "CARTOGRAPHER_TOUCH_HOME EXPERIMENTAL_RANDOM_RADIUS=2.50")
        self.assertEqual(backend.touch_probe_gcode, "CARTOGRAPHER_TOUCH_PROBE MAX_SAMPLES=5")

    def test_unsupported_carto_options_rejected(self):
        """Unsupported speed/tolerance options are rejected with configuration error."""
        cfg = DummyConfig(self.printer, {
            "carto_touch_speed": 3.0,
            "touch_model_config_path": self.printer_cfg_path
        })
        with self.assertRaises(Exception) as ctx:
            CartographerBackend(cfg)
        self.assertIn("not supported", str(ctx.exception).lower())

    def test_get_probe_xy_with_bed_mesh_zero_ref_pos(self):
        """When bed_mesh provides zero_ref_pos, get_probe_xy should prioritize it."""
        mock_mesh = MagicMock()
        mock_mesh.zero_ref_pos = [175.5, 175.5]
        self.printer.objects["bed_mesh"] = mock_mesh
        backend = CartographerBackend(self.config)
        px, py = backend.get_probe_xy()
        self.assertEqual((px, py), (175.5, 175.5))

    def test_touch_probe_gcode_never_falls_back_to_touch_home(self):
        """touch_probe_gcode must never fall back to CARTOGRAPHER_TOUCH_HOME (origin reset safety)."""
        self.gcode.commands = {"CARTOGRAPHER_TOUCH_HOME": MagicMock()}
        backend = CartographerBackend(self.config)
        # Must NOT be CARTOGRAPHER_TOUCH_HOME! Must default to CARTOGRAPHER_TOUCH_PROBE.
        self.assertNotEqual(backend.touch_probe_gcode, "CARTOGRAPHER_TOUCH_HOME")
        self.assertEqual(backend.touch_probe_gcode, "CARTOGRAPHER_TOUCH_PROBE")

    def test_vision_samples_not_passed_to_carto_max_samples(self):
        """Vision burst samples parameter must not be used as carto_max_samples."""
        cfg = DummyConfig(self.printer, {
            "samples": 3,
            "touch_model_config_path": self.printer_cfg_path
        })
        backend = CartographerBackend(cfg)
        self.assertIsNone(backend.max_samples)
        self.assertNotIn("MAX_SAMPLES=", backend.touch_probe_gcode)

    def test_thermal_safety_guard_permits_thermal_epsilon(self):
        """Nozzle temperature with minor overshoot (<= 150 + 2.0°C epsilon) is permitted, above fails."""
        # 151.5°C is within 150 + 2.0°C epsilon
        self.printer.objects["extruder1"] = DummyExtruder(151.5)
        backend = CartographerBackend(self.config)
        gcmd = DummyGCodeCommand()
        ref_result = {"contact_z": 0.050}
        self.cartographer_obj.last_z_result = 0.080

        sec_result = backend.probe_secondary_tool(1, ref_result, gcmd)
        self.assertIsNotNone(sec_result)

        # 152.5°C exceeds 150 + 2.0°C epsilon
        self.printer.objects["extruder1"] = DummyExtruder(152.5)
        with self.assertRaises(RuntimeError) as ctx:
            backend.probe_secondary_tool(1, ref_result, gcmd)
        self.assertIn("ERR_PRE_002", str(ctx.exception))

    def test_touch_telemetry_extracted_in_probe_result(self):
        """Touch telemetry exposed by cartographer plugin is captured in probe return dict."""
        mock_carto = MagicMock()
        mock_carto.get_status = MagicMock(return_value={
            "touch": {"spread": 0.004, "threshold": 1819, "retries": 1},
            "last_threshold": 1819
        })
        self.printer.objects["cartographer"] = mock_carto
        backend = CartographerBackend(self.config)
        telem = backend._get_touch_telemetry()
        self.assertEqual(telem.get("spread"), 0.004)
        self.assertEqual(telem.get("threshold"), 1819)


class TestSwitchBackend(unittest.TestCase):
    def test_switch_probe_relative_offset(self):
        """Switch probe measures contact height and computes difference to baseline."""
        printer = DummyPrinter()
        cfg = DummyConfig(printer, {"switch_samples": 3})
        backend = SwitchBackend(cfg)

        # Mock probe wrapper run_probe returning (x, y, z)
        mock_probe = MagicMock()
        mock_probe.run_probe = MagicMock(side_effect=[
            [150.0, 150.0, 5.200],  # T0 contact
            [150.0, 150.0, 5.350],  # T1 contact (+0.150mm)
        ])
        backend._get_active_probe = MagicMock(return_value=mock_probe)

        gcmd = DummyGCodeCommand()
        ref = backend.probe_reference_tool(0, gcmd)
        self.assertAlmostEqual(ref["contact_z"], 5.200, delta=0.001)
        self.assertEqual(ref["suggested_z_offset"], 0.0)

        sec = backend.probe_secondary_tool(1, ref, gcmd)
        self.assertAlmostEqual(sec["contact_z"], 5.350, delta=0.001)
        # Delta Z = 5.350 - 5.200 = 0.150mm
        self.assertAlmostEqual(sec["suggested_z_offset"], 0.150, delta=0.001)


if __name__ == "__main__":
    unittest.main()
