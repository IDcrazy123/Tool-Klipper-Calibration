"""
Abstract Base Interface for Z Calibration Backends in Tool-Klipper-Calibration.
"""

from abc import ABC, abstractmethod
from typing import Dict, Any, Tuple


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
        Defaults to bed_mesh zero_reference_position if available,
        otherwise the exact bed center to eliminate bed mesh / tilt bias.
        """
        try:
            bed_mesh = self.printer.lookup_object("bed_mesh", None)
            if bed_mesh is not None:
                zrp = getattr(bed_mesh, "zero_ref_pos", None)
                if zrp and len(zrp) >= 2:
                    return round(float(zrp[0]), 3), round(float(zrp[1]), 3)
                bmc = getattr(bed_mesh, "bmc", None)
                if bmc is not None and hasattr(bmc, "zero_ref_pos"):
                    bzrp = getattr(bmc, "zero_ref_pos")
                    if bzrp and len(bzrp) >= 2:
                        return round(float(bzrp[0]), 3), round(float(bzrp[1]), 3)
        except Exception:
            pass

        try:
            toolhead = self.printer.lookup_object("toolhead")
            status = toolhead.get_status(self.printer.get_reactor().monotonic())
            axis_min = status.get("axis_minimum", [0.0, 0.0, 0.0])
            axis_max = status.get("axis_maximum", [300.0, 300.0, 300.0])
            cx = (axis_min[0] + axis_max[0]) / 2.0
            cy = (axis_min[1] + axis_max[1]) / 2.0
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
