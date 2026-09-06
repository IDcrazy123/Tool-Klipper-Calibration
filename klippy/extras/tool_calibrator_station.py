"""
Tool-Klipper-Calibration Station Configuration Module.

Registers and validates [tool_calibrator_station <name>] sections in Klipper config.
Allows tool_offsets.cfg to be included into printer.cfg without triggering Klipper's
'Section ... is not a valid config section' error.
"""

import logging

logger = logging.getLogger("tool_calibrator.station")


class ToolCalibratorStation:
    """
    Holds persistent physical waypoints and optical calibration parameters for a named station
    (e.g. camera, switch).
    """

    def __init__(self, config) -> None:
        self.printer = config.get_printer()
        self.name = config.get_name()  # e.g. "tool_calibrator_station camera"

        # Coordinates
        self.target_x = config.getfloat("target_x", None)
        self.target_y = config.getfloat("target_y", None)
        self.target_z = config.getfloat("target_z", None)

        self.approach_x = config.getfloat("approach_x", None)
        self.approach_y = config.getfloat("approach_y", None)
        self.approach_z = config.getfloat("approach_z", None)

        self.safe_z = config.getfloat("safe_z", None)

        # Optical parameters (for camera station)
        self.mpp = config.getfloat("mpp", None)
        self.matrix_a = config.getfloat("matrix_a", None)
        self.matrix_b = config.getfloat("matrix_b", None)
        self.matrix_tx = config.getfloat("matrix_tx", None)
        self.matrix_c = config.getfloat("matrix_c", None)
        self.matrix_d = config.getfloat("matrix_d", None)
        self.matrix_ty = config.getfloat("matrix_ty", None)

        logger.info(f"Loaded station '{self.name}' (Target: X={self.target_x}, Y={self.target_y}, Z={self.target_z})")

    def get_status(self, eventtime=None):
        return {
            "target_x": self.target_x,
            "target_y": self.target_y,
            "target_z": self.target_z,
            "approach_x": self.approach_x,
            "approach_y": self.approach_y,
            "safe_z": self.safe_z,
            "mpp": self.mpp,
            "matrix_a": self.matrix_a,
            "matrix_b": self.matrix_b,
            "matrix_tx": self.matrix_tx,
            "matrix_c": self.matrix_c,
            "matrix_d": self.matrix_d,
            "matrix_ty": self.matrix_ty,
        }


def load_config_prefix(config):
    return ToolCalibratorStation(config)
