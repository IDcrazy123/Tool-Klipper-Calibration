"""
Cartographer Touch V4 Z-Backend Implementation for Tool-Klipper-Calibration.

Integrates with Cartographer 3D eddy-current nozzle touch home and probing routines.
"""

from typing import Dict, Any, Optional
import logging
import os
from .base_z import BaseZBackend

logger = logging.getLogger("tool_calibrator.cartographer_backend")


class CartographerBackend(BaseZBackend):
    """
    Measures toolhead contact heights using Cartographer Touch routines.
    """

    def __init__(self, config) -> None:
        super().__init__(config)
        self.touch_home_gcode = config.get("touch_home_gcode", "CARTOGRAPHER_TOUCH_HOME")
        self.touch_probe_gcode = config.get("touch_probe_gcode", "CARTOGRAPHER_TOUCH_PROBE")
        self.probe_x = config.getfloat("carto_probe_x", None)
        self.probe_y = config.getfloat("carto_probe_y", None)
        self.touch_model_config_path = os.path.expanduser(
            config.get("touch_model_config_path", "~/printer_data/config/printer.cfg")
        )
        self.touch_model_z_offset = self._load_touch_model_offset()

    def _load_touch_model_offset(self) -> float:
        """Parses saved touch-model z_offset from the #*# auto-save section."""
        if not os.path.exists(self.touch_model_config_path):
            return 0.0

        section_prefixes = ("#*# [cartographer touch_model", "#*# [scanner touch_model")
        in_section = False
        try:
            with open(self.touch_model_config_path, "r", encoding="utf-8") as f:
                for line in f:
                    stripped = line.strip()
                    if any(stripped.startswith(p) for p in section_prefixes):
                        in_section = True
                        continue
                    if in_section and stripped.startswith("#*# ["):
                        in_section = False
                    if in_section and stripped.startswith("#*# z_offset ="):
                        val = float(stripped.split("=", 1)[1].strip())
                        logger.info(f"Loaded Cartographer touch_model z_offset: {val:.5f}")
                        return val
        except Exception as ex:
            logger.warning(f"Could not parse Cartographer touch-model offset: {ex}")
        return 0.0

    def _get_last_z_result(self) -> Optional[float]:
        """Queries the last measured probe Z result from printer objects."""
        for obj_name in ("cartographer", "scanner", "probe"):
            obj = self.printer.lookup_object(obj_name, None)
            if obj is not None and hasattr(obj, "last_z_result"):
                return float(obj.last_z_result)
        return None

    def probe_reference_tool(self, tool_number: int, gcmd) -> Dict[str, Any]:
        toolhead = self.printer.lookup_object("toolhead")
        toolhead.wait_moves()

        gcmd.respond_info(f"[tool_calibrator] Running {self.touch_home_gcode} on Reference Tool T{tool_number}")
        self.gcode.run_script_from_command(self.touch_home_gcode)
        toolhead.wait_moves()

        return {
            "source": "cartographer_touch_reference",
            "contact_z": 0.0,
            "suggested_z_offset": 0.0,
            "touch_model_z_offset": self.touch_model_z_offset,
            "tool_number": tool_number
        }

    def probe_secondary_tool(self, tool_number: int, reference_result: Dict[str, Any], gcmd) -> Dict[str, Any]:
        toolhead = self.printer.lookup_object("toolhead")
        toolhead.wait_moves()

        gcmd.respond_info(f"[tool_calibrator] Running {self.touch_probe_gcode} on Tool T{tool_number}")
        self.gcode.run_script_from_command(self.touch_probe_gcode)
        toolhead.wait_moves()

        measured_z = self._get_last_z_result()
        if measured_z is None:
            # Fallback: toolhead Z position minus touch model offset
            cur_z = float(toolhead.get_position()[2])
            measured_z = cur_z - self.touch_model_z_offset

        return {
            "source": "cartographer_touch",
            "contact_z": measured_z,
            "suggested_z_offset": round(measured_z, 3),
            "touch_model_z_offset": self.touch_model_z_offset,
            "tool_number": tool_number
        }
