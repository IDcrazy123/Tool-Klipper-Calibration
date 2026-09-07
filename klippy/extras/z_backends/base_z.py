"""
Abstract Base Interface for Z Calibration Backends in Tool-Klipper-Calibration.
"""

import logging
from abc import ABC, abstractmethod
from typing import Dict, Any, Tuple, Optional

logger = logging.getLogger("tool_calibrator.z_backend")


def _extract_coordinate_pair(val: Any) -> Optional[Tuple[float, float]]:
    """Extracts rounded (X, Y) coordinate pair from list, tuple, or comma-separated string."""
    if val is None or hasattr(val, "_mock_name"):
        return None
    try:
        if isinstance(val, (list, tuple)) and len(val) >= 2:
            return round(float(val[0]), 3), round(float(val[1]), 3)
        if isinstance(val, str) and "," in val:
            parts = [float(p.strip()) for p in val.split(",") if p.strip()]
            if len(parts) >= 2:
                return round(parts[0], 3), round(parts[1], 3)
    except (ValueError, TypeError):
        pass
    return None


class BaseZBackend(ABC):
    """
    Abstract interface for multi-backend Z probing (Switch vs Cartographer).
    """

    measurement_reference: str = "nozzle"

    def __init__(self, config) -> None:
        self.config = config
        self.printer = config.get_printer()
        self.gcode = self.printer.lookup_object("gcode")

    def get_probe_xy(self) -> Tuple[float, float]:
        """
        Returns designated probing coordinates (X, Y).
        Prioritizes bed_mesh zero_reference_position across Klipper architecture variants:
          1. bed_mesh.zero_ref_pos
          2. bed_mesh.bmc.zero_ref_pos
          3. bed_mesh.bmc.probe_mgr.zero_ref_pos (modern Klipper)
          4. bed_mesh.probe_mgr.zero_ref_pos
          5. configfile [bed_mesh] zero_reference_position
        Otherwise falls back to the exact kinematic bed center to eliminate tilt bias.
        """
        # 1. Inspect bed_mesh runtime objects
        try:
            bed_mesh = self.printer.lookup_object("bed_mesh", None)
            if bed_mesh is not None:
                coords = _extract_coordinate_pair(getattr(bed_mesh, "zero_ref_pos", None))
                if coords:
                    logger.info(f"[z_backend] Resolved probe XY from bed_mesh.zero_ref_pos: {coords}")
                    return coords

                bmc = getattr(bed_mesh, "bmc", None)
                if bmc is not None:
                    coords = _extract_coordinate_pair(getattr(bmc, "zero_ref_pos", None))
                    if coords:
                        logger.info(f"[z_backend] Resolved probe XY from bed_mesh.bmc.zero_ref_pos: {coords}")
                        return coords

                    probe_mgr = getattr(bmc, "probe_mgr", None)
                    if probe_mgr is not None:
                        coords = _extract_coordinate_pair(getattr(probe_mgr, "zero_ref_pos", None))
                        if coords:
                            logger.info(f"[z_backend] Resolved probe XY from bed_mesh.bmc.probe_mgr.zero_ref_pos: {coords}")
                            return coords

                probe_mgr = getattr(bed_mesh, "probe_mgr", None)
                if probe_mgr is not None:
                    coords = _extract_coordinate_pair(getattr(probe_mgr, "zero_ref_pos", None))
                    if coords:
                        logger.info(f"[z_backend] Resolved probe XY from bed_mesh.probe_mgr.zero_ref_pos: {coords}")
                        return coords
        except Exception as ex:
            logger.debug(f"[z_backend] bed_mesh runtime inspection skipped: {ex}")

        # 2. Inspect configfile settings for [bed_mesh] zero_reference_position
        try:
            configfile = self.printer.lookup_object("configfile", None)
            if configfile is not None:
                settings = getattr(configfile, "settings", {})
                if isinstance(settings, dict):
                    bm_settings = settings.get("bed_mesh", {})
                    if isinstance(bm_settings, dict):
                        coords = _extract_coordinate_pair(bm_settings.get("zero_reference_position"))
                        if coords:
                            logger.info(f"[z_backend] Resolved probe XY from configfile settings [bed_mesh]: {coords}")
                            return coords

                cfg = getattr(configfile, "config", {})
                if isinstance(cfg, dict):
                    bm_cfg = cfg.get("bed_mesh", {})
                    if isinstance(bm_cfg, dict):
                        coords = _extract_coordinate_pair(bm_cfg.get("zero_reference_position"))
                        if coords:
                            logger.info(f"[z_backend] Resolved probe XY from configfile config [bed_mesh]: {coords}")
                            return coords
        except Exception as ex:
            logger.debug(f"[z_backend] configfile inspection skipped: {ex}")

        # 3. Kinematic bed center fallback
        try:
            toolhead = self.printer.lookup_object("toolhead")
            status = toolhead.get_status(self.printer.get_reactor().monotonic())
            axis_min = status.get("axis_minimum", [0.0, 0.0, 0.0])
            axis_max = status.get("axis_maximum", [300.0, 300.0, 300.0])
            cx = (axis_min[0] + axis_max[0]) / 2.0
            cy = (axis_min[1] + axis_max[1]) / 2.0
            logger.info(f"[z_backend] No zero_reference_position found; using bed center: ({cx:.3f}, {cy:.3f})")
            return round(cx, 3), round(cy, 3)
        except Exception:
            return 150.0, 150.0

    @abstractmethod
    def probe_reference_tool(self, tool_number: int, gcmd) -> Dict[str, Any]:
        """
        Executes baseline probe measurement for Reference Tool (default T0).

        Returns:
            Dict containing:
                - 'contact_z': float (measured contact height)
                - 'suggested_z_offset': float (0.0 for reference tool)
                - 'source': str (backend identification)
        """
        pass

    @abstractmethod
    def probe_secondary_tool(self, tool_number: int, reference_result: Dict[str, Any], gcmd) -> Dict[str, Any]:
        """
        Executes probe measurement for secondary tools (T1..Tn) against reference baseline.

        Returns:
            Dict containing:
                - 'contact_z': float
                - 'suggested_z_offset': float (relative difference to reference tool)
                - 'source': str
        """
        pass
