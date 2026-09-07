"""
Cartographer Touch V4 Z-Backend Implementation for Tool-Klipper-Calibration.

Integrates with Cartographer 3D eddy-current nozzle touch home and probing routines.
Accurately computes relative delta Z offsets across multi-toolheads with thermal safety,
auto bed-center fallback, and safe nozzle liftoff retraction.
"""

from typing import Dict, Any, Optional, Tuple
import logging
import math
import os
from .base_z import BaseZBackend

logger = logging.getLogger("tool_calibrator.cartographer_backend")


class CartographerBackend(BaseZBackend):
    """
    Measures toolhead contact heights using Cartographer Touch routines.
    Computes precise relative delta Z-offsets between Reference Tool (T0)
    and secondary tools (T1..Tn).
    """

    def __init__(self, config) -> None:
        super().__init__(config)
        self.configured_touch_home_gcode = config.get("touch_home_gcode", None)
        self.configured_touch_probe_gcode = config.get("touch_probe_gcode", None)
        self.touch_speed = config.getfloat("carto_touch_speed", None, above=0.0)
        self.touch_retries = config.getint("carto_touch_retries", None, minval=1)
        self.touch_tolerance = config.getfloat("carto_touch_tolerance", None, above=0.0)
        self.probe_x = config.getfloat("carto_probe_x", None)
        self.probe_y = config.getfloat("carto_probe_y", None)
        self.max_touch_temp = config.getfloat("carto_max_touch_temp", 150.0, above=50.0, maxval=200.0)
        self.retract_z = config.getfloat("carto_retract_z", 5.0, above=1.0, maxval=30.0)
        self.touch_model_config_path = os.path.expanduser(
            config.get("touch_model_config_path", "~/printer_data/config/printer.cfg")
        )
        self.touch_model_z_offset = self._load_touch_model_offset()
        self.cartographer_touch_model = config.get("cartographer_touch_model", None)
        self.cartographer_touch_threshold = config.getfloat("cartographer_touch_threshold", None)
        ref = config.get("measurement_reference", "shuttle").strip().lower()
        if ref not in ("nozzle", "shuttle"):
            raise config.error(f"Invalid measurement_reference '{ref}' in [tool_calibrator]. Must be 'nozzle' or 'shuttle'.")
        self.measurement_reference = ref
        if self.measurement_reference == "shuttle":
            logger.info(
                "[cartographer_backend] measurement_reference is set to 'shuttle'. "
                "Fixed carriage/shuttle eddy probe does not track individual nozzle tip lengths across tool changes."
            )

    def _resolve_touch_cmd(self, configured: Optional[str], primary: str, fallbacks: Tuple[str, ...]) -> str:
        """Finds the active command registered in Klipper gcode command registry."""
        if configured:
            return configured
        registered = set()
        for attr in ("ready_gcode_handlers", "base_gcode_handlers", "gcode_handlers", "commands"):
            handlers = getattr(self.gcode, attr, None)
            if handlers and isinstance(handlers, dict):
                registered.update(handlers.keys())
        if registered:
            if primary in registered:
                return primary
            for fb in fallbacks:
                if fb in registered:
                    return fb
        return primary

    def _build_command_str(self, base_cmd: str) -> str:
        """Appends official Cartographer SPEED, TOLERANCE, RETRIES parameters if configured."""
        parts = [base_cmd]
        if self.touch_speed is not None and "SPEED=" not in base_cmd:
            parts.append(f"SPEED={self.touch_speed:.1f}")
        if self.touch_tolerance is not None and "TOLERANCE=" not in base_cmd:
            parts.append(f"TOLERANCE={self.touch_tolerance:.4f}")
        if self.touch_retries is not None and "RETRIES=" not in base_cmd:
            parts.append(f"RETRIES={self.touch_retries}")
        return " ".join(parts)

    @property
    def touch_home_gcode(self) -> str:
        raw = self._resolve_touch_cmd(
            self.configured_touch_home_gcode,
            "CARTOGRAPHER_TOUCH_HOME",
            ("CARTOGRAPHER_TOUCH", "SCANNER_TOUCH")
        )
        return self._build_command_str(raw)

    @property
    def touch_probe_gcode(self) -> str:
        raw = self._resolve_touch_cmd(
            self.configured_touch_probe_gcode,
            "CARTOGRAPHER_TOUCH_PROBE",
            ("CARTOGRAPHER_TOUCH", "SCANNER_TOUCH_PROBE", "SCANNER_TOUCH", "CARTOGRAPHER_TOUCH_HOME")
        )
        return self._build_command_str(raw)

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

    def get_probe_xy(self) -> Tuple[float, float]:
        """
        Returns designated touch probing coordinates (X, Y).
        Defaults to the exact bed center to eliminate bed mesh / tilt bias.
        """
        if self.probe_x is not None and self.probe_y is not None:
            return self.probe_x, self.probe_y
        return super().get_probe_xy()

    def _check_thermal_safety(self, tool_number: int, gcmd) -> None:
        """Ensures nozzle temperature does not exceed safe touch limit (protecting PEI bed)."""
        extruder_name = f"extruder{tool_number}" if tool_number > 0 else "extruder"
        extruder = self.printer.lookup_object(extruder_name, None)
        if extruder is not None and hasattr(extruder, "get_status"):
            try:
                status = extruder.get_status(self.printer.get_reactor().monotonic())
                temp = status.get("temperature", 0.0)
                if temp > self.max_touch_temp:
                    raise gcmd.error(
                        f"[ERR_PRE_002] Tool T{tool_number} nozzle temperature ({temp:.1f}°C) exceeds safe Cartographer touch limit ({self.max_touch_temp:.1f}°C). Cool nozzle before touch probing."
                    )
            except Exception as ex:
                if "ERR_PRE_002" in str(ex):
                    raise
                logger.debug(f"Thermal check skipped for {extruder_name}: {ex}")

    @staticmethod
    def _extract_float(val: Any) -> Optional[float]:
        """Extracts a finite float, safely discarding MagicMock objects."""
        if val is None or hasattr(val, "_mock_name"):
            return None
        try:
            f = float(val)
            return f if math.isfinite(f) else None
        except (ValueError, TypeError):
            return None

    def _get_last_z_result(self) -> Optional[float]:
        """Queries the last measured probe Z result from printer objects."""
        # 1. Cartographer / Scanner touch plugin
        for obj_name in ("cartographer", "scanner"):
            obj = self.printer.lookup_object(obj_name, None)
            if obj is not None:
                # Check touch_mode or touch submodule
                for sub_attr in ("touch_mode", "touch"):
                    sub = getattr(obj, sub_attr, None)
                    if sub is not None and hasattr(sub, "last_z_result"):
                        res = self._extract_float(sub.last_z_result)
                        if res is not None:
                            return res
                # Check direct attribute
                if hasattr(obj, "last_z_result"):
                    res = self._extract_float(obj.last_z_result)
                    if res is not None:
                        return res
                # Check get_status(eventtime)
                if hasattr(obj, "get_status") and not hasattr(obj.get_status, "_mock_name"):
                    try:
                        eventtime = self.printer.get_reactor().monotonic()
                        st = obj.get_status(eventtime)
                        if isinstance(st, dict):
                            touch_st = st.get("touch")
                            if isinstance(touch_st, dict):
                                res = self._extract_float(touch_st.get("last_z_result"))
                                if res is not None:
                                    return res
                            res = self._extract_float(st.get("last_z_result"))
                            if res is not None:
                                return res
                    except Exception:
                        pass

        # 2. Check probe or probe macro objects
        for obj_name in (
            "probe",
            "touch_probe",
            "cartographer_touch_probe",
            "gcode_macro CARTOGRAPHER_TOUCH_PROBE",
            "gcode_macro TOUCH_PROBE",
        ):
            obj = self.printer.lookup_object(obj_name, None)
            if obj is not None:
                if hasattr(obj, "last_z_result"):
                    res = self._extract_float(obj.last_z_result)
                    if res is not None:
                        return res
                for pos_attr in ("last_trigger_position", "last_probe_position"):
                    pos = getattr(obj, pos_attr, None)
                    if pos is not None:
                        if isinstance(pos, (list, tuple)) and len(pos) >= 3:
                            res = self._extract_float(pos[2])
                            if res is not None:
                                return res
                        else:
                            res = self._extract_float(pos)
                            if res is not None:
                                return res
                if hasattr(obj, "get_status") and not hasattr(obj.get_status, "_mock_name"):
                    try:
                        eventtime = self.printer.get_reactor().monotonic()
                        st = obj.get_status(eventtime)
                        if isinstance(st, dict):
                            res = self._extract_float(st.get("last_z_result"))
                            if res is not None:
                                return res
                    except Exception:
                        pass

        return None

    def probe_reference_tool(self, tool_number: int, gcmd) -> Dict[str, Any]:
        """
        Executes baseline Cartographer touch measurement for Reference Tool (T0).
        Records physical contact height Z_ref. When a homing routine (e.g. TOUCH_HOME)
        is executed, it redefines the coordinate origin (Z=0) at the touch point,
        so the reference contact in the active coordinate system becomes 0.0.
        """
        self._check_thermal_safety(tool_number, gcmd)
        toolhead = self.printer.lookup_object("toolhead")
        toolhead.wait_moves()

        cmd = self.touch_home_gcode
        gcmd.respond_info(f"[tool_calibrator] Running {cmd} on Reference Tool T{tool_number}")
        self.gcode.run_script_from_command(cmd)
        toolhead.wait_moves()

        measured_z = self._get_last_z_result()
        if measured_z is None:
            raise gcmd.error(f"[tool_calibrator] Failed to read contact Z result from Cartographer touch on Reference Tool T{tool_number}")

        # Immediate safe liftoff from the bed surface
        cur_pos = toolhead.get_position()
        toolhead.manual_move([None, None, cur_pos[2] + self.retract_z], 15.0)
        toolhead.wait_moves()

        is_homing = ("HOME" in cmd.upper() or "G28" in cmd.upper())
        effective_contact_z = 0.0 if is_homing else measured_z
        ref_x = round(float(cur_pos[0]), 3)
        ref_y = round(float(cur_pos[1]), 3)

        return {
            "source": "cartographer_touch_reference",
            "contact_z": effective_contact_z,
            "raw_contact_z": measured_z,
            "is_homed": is_homing,
            "suggested_z_offset": 0.0,
            "touch_model_z_offset": self.touch_model_z_offset,
            "tool_number": tool_number,
            "probe_x": ref_x,
            "probe_y": ref_y,
            "probe_xy": (ref_x, ref_y)
        }

    def probe_secondary_tool(self, tool_number: int, reference_result: Dict[str, Any], gcmd) -> Dict[str, Any]:
        """
        Executes Cartographer touch measurement for secondary tools (T1..Tn).
        If Reference Tool established Z=0 via TOUCH_HOME, the secondary measured Z
        in that coordinate system directly represents the relative physical delta.
        """
        self._check_thermal_safety(tool_number, gcmd)
        toolhead = self.printer.lookup_object("toolhead")
        toolhead.wait_moves()

        cmd = self.touch_probe_gcode
        gcmd.respond_info(f"[tool_calibrator] Running {cmd} on Tool T{tool_number}")
        self.gcode.run_script_from_command(cmd)
        toolhead.wait_moves()

        measured_z = self._get_last_z_result()
        if measured_z is None:
            raise gcmd.error(f"[tool_calibrator] Failed to read contact Z result from Cartographer touch on Secondary Tool T{tool_number}")

        # Immediate safe liftoff from the bed surface
        cur_pos = toolhead.get_position()
        toolhead.manual_move([None, None, cur_pos[2] + self.retract_z], 15.0)
        toolhead.wait_moves()

        sec_x = round(float(cur_pos[0]), 3)
        sec_y = round(float(cur_pos[1]), 3)

        is_homed = reference_result.get("is_homed", False)
        if is_homed:
            ref_z = 0.0
        else:
            ref_z = reference_result.get("contact_z", 0.0)
        delta_z = round(measured_z - ref_z, 3)

        return {
            "source": "cartographer_touch",
            "contact_z": measured_z,
            "suggested_z_offset": delta_z,
            "touch_model_z_offset": self.touch_model_z_offset,
            "tool_number": tool_number,
            "probe_x": sec_x,
            "probe_y": sec_y,
            "probe_xy": (sec_x, sec_y)
        }
