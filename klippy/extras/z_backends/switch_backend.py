"""
Physical Switch Z-Backend Implementation for Tool-Klipper-Calibration.

Safely supports mechanical / optical switch endstops while preventing GPIO pin collision
with pre-existing [tools_calibrate] configurations.
"""

from typing import Dict, Any, Optional
import logging
from .base_z import BaseZBackend

logger = logging.getLogger("tool_calibrator.switch_backend")


class SwitchBackend(BaseZBackend):
    """
    Measures toolhead contact heights against a fixed physical switch endstop.
    """

    def __init__(self, config) -> None:
        super().__init__(config)
        self.samples = config.getint("switch_samples", None)
        if self.samples is None:
            self.samples = config.getint("samples", 3, minval=1, maxval=10)
        self.switch_pin = config.get("switch_pin", config.get("pin", None))
        self.probing_speed = config.getfloat("probing_speed", 3.0, above=0.0)
        self.lift_speed = config.getfloat("lift_speed", 5.0, above=0.0)
        self.samples_retract_dist = config.getfloat("samples_retract_dist", 2.0, above=0.0)
        self.probe = None

        # Anti-conflict: Check if tools_calibrate is already configured
        if config.has_section("tools_calibrate"):
            logger.info("Found pre-existing [tools_calibrate] in config; reusing existing probe wrapper.")
        elif self.switch_pin is not None:
            # Build switch probe wrapper if tools_calibrate is absent
            try:
                try:
                    from .. import tools_calibrate
                except ImportError:
                    import tools_calibrate
                self.probe = tools_calibrate.PrinterProbeMultiAxis(
                    config,
                    tools_calibrate.ProbeEndstopWrapper(config, 'x'),
                    tools_calibrate.ProbeEndstopWrapper(config, 'y'),
                    tools_calibrate.ProbeEndstopWrapper(config, 'z')
                )
                query_endstops = self.printer.load_object(config, 'query_endstops')
                query_endstops.register_endstop(
                    self.probe.mcu_probe[-1].mcu_endstop,
                    "ToolCalibratorSwitch"
                )
            except Exception as ex:
                logger.warning(f"Could not initialize dedicated switch probe wrapper: {ex}")

    def _get_active_probe(self):
        """Returns the active probe object from internal or tools_calibrate instance."""
        if self.probe is not None:
            return self.probe
        # Fallback to printer tools_calibrate if available
        tc = self.printer.lookup_object("tools_calibrate", None)
        if tc is not None:
            # In viesturz/klipper-toolchanger, probe_multi_axis holds run_probe
            if hasattr(tc, "probe_multi_axis"):
                return tc.probe_multi_axis
            return tc
        return None

    def probe_reference_tool(self, tool_number: int, gcmd) -> Dict[str, Any]:
        probe = self._get_active_probe()
        if probe is None:
            raise gcmd.error("[tool_calibrator] Switch backend selected but no switch probe is configured.")

        toolhead = self.printer.lookup_object("toolhead")
        start_pos = toolhead.get_position()

        # Run multi-axis / single Z probe sequence
        z_result = probe.run_probe("z-", gcmd, speed_ratio=0.5, max_distance=10.0, samples=self.samples)[2]

        # Retract to start altitude using configured lift_speed
        toolhead.move(start_pos, self.lift_speed)
        toolhead.set_position(start_pos)
        toolhead.wait_moves()

        return {
            "source": "switch_probe",
            "contact_z": z_result,
            "suggested_z_offset": 0.0,
            "tool_number": tool_number
        }

    def probe_secondary_tool(self, tool_number: int, reference_result: Dict[str, Any], gcmd) -> Dict[str, Any]:
        probe = self._get_active_probe()
        if probe is None:
            raise gcmd.error("[tool_calibrator] Switch backend selected but no switch probe is configured.")

        toolhead = self.printer.lookup_object("toolhead")
        start_pos = toolhead.get_position()

        z_result = probe.run_probe("z-", gcmd, speed_ratio=0.5, max_distance=10.0, samples=self.samples)[2]

        toolhead.move(start_pos, self.lift_speed)
        toolhead.set_position(start_pos)
        toolhead.wait_moves()

        ref_z = reference_result.get("contact_z", 0.0)
        z_offset = round(z_result - ref_z, 3)

        return {
            "source": "switch_probe",
            "contact_z": z_result,
            "suggested_z_offset": z_offset,
            "tool_number": tool_number
        }
