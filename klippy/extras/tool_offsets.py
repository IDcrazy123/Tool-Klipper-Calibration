"""
Tool Offsets Configuration Module for Tool-Klipper-Calibration.

Registers the [tool_offsets] section in Klipper config so tool_offsets.cfg
can be safely included into printer.cfg without causing Klipper's
'Section ... is not a valid config section' error or colliding with
pre-existing [tool T*] headers.
"""

from typing import Dict, Any, Optional
import logging

logger = logging.getLogger("tool_calibrator.tool_offsets")


class ToolOffsets:
    """
    Holds persistent calibrated tool offsets (e.g. t1_x, t1_y, t1_z)
    loaded from tool_offsets.cfg.
    """

    def __init__(self, config) -> None:
        self.printer = config.get_printer()
        self.name = config.get_name()  # "tool_offsets"
        self.offsets: Dict[str, float] = {}

        # Read all configured options in [tool_offsets]
        for opt in config.get_prefix_options(""):
            try:
                val = config.getfloat(opt, None)
                if val is not None:
                    self.offsets[opt.lower()] = val
            except Exception:
                pass

        logger.info(f"Loaded [tool_offsets] parameters: {self.offsets}")

    def get_status(self, eventtime: Optional[float] = None) -> Dict[str, Any]:
        """Provides state for Klipper macros and web UI query."""
        return dict(self.offsets)


def load_config(config):
    return ToolOffsets(config)
