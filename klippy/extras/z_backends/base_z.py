"""
Abstract Base Interface for Z Calibration Backends in Tool-Klipper-Calibration.
"""

from abc import ABC, abstractmethod
from typing import Dict, Any


class BaseZBackend(ABC):
    """
    Abstract interface for multi-backend Z probing (Switch vs Cartographer).
    """

    def __init__(self, config) -> None:
        self.config = config
        self.printer = config.get_printer()
        self.gcode = self.printer.lookup_object("gcode")

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
