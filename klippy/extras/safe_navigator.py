"""
Safe Navigator Module for Tool-Klipper-Calibration.

Implements the 3-tier safe navigation state machine (Safe_Z -> Safe_Approach -> Target)
to prevent toolhead collision with camera shrouds, docks, and bed clamps.
"""

from typing import Tuple, Optional
import logging

logger = logging.getLogger("tool_calibrator.safe_navigator")


class SafeNavigatorException(Exception):
    """Raised when safe motion constraints are violated."""
    pass


class SafeNavigator:
    """
    Coordinates multi-tier waypoints and anti-collision kinematics.
    """

    def __init__(self, config) -> None:
        self.printer = config.get_printer()

        # Speeds (mm/s converted to mm/min for G-code/toolhead moves where required)
        self.travel_speed = config.getfloat("travel_speed", 100.0, above=10.0)
        self.approach_speed = config.getfloat("approach_speed", 25.0, above=5.0)
        self.z_speed = config.getfloat("z_speed", 10.0, above=1.0)

        # Global Safe Z Clearance Altitude
        self.safe_z = config.getfloat("safe_z", 35.0, above=0.0)

        # Camera Station Waypoints
        self.cam_approach_x = config.getfloat("camera_approach_x", None)
        self.cam_approach_y = config.getfloat("camera_approach_y", None)
        self.cam_target_x = config.getfloat("camera_x", None)
        self.cam_target_y = config.getfloat("camera_y", None)
        self.cam_target_z = config.getfloat("camera_focal_z", 15.0)

        # Z Switch Station Waypoints (Optional if using Cartographer)
        self.switch_approach_x = config.getfloat("switch_approach_x", None)
        self.switch_approach_y = config.getfloat("switch_approach_y", None)
        self.switch_target_x = config.getfloat("zswitch_x_pos", None)
        self.switch_target_y = config.getfloat("zswitch_y_pos", None)
        self.switch_target_z = config.getfloat("zswitch_z_pos", None)

    def is_homed(self) -> bool:
        """Verifies if axes X, Y, and Z are homed."""
        toolhead = self.printer.lookup_object("toolhead")
        status = toolhead.get_status(self.printer.get_reactor().monotonic())
        homed_axes = status.get("homed_axes", "")
        return "x" in homed_axes and "y" in homed_axes and "z" in homed_axes

    def move_to_safe_z(self, toolhead, gcode_move) -> None:
        """
        Elevates Z vertically to safe_z if currently lower.
        Always waits for moves to finish before returning.
        """
        toolhead.wait_moves()
        cur_pos = toolhead.get_position()
        if cur_pos[2] < self.safe_z:
            logger.info(f"Lifting Z from {cur_pos[2]:.2f}mm to Safe_Z ({self.safe_z:.2f}mm)")
            toolhead.manual_move([None, None, self.safe_z], self.z_speed)
            toolhead.wait_moves()

    def approach_camera(self, toolhead, gcode_move) -> None:
        """
        Executes 3-tier safe transition into the Camera Station:
        1. Raise Z to Safe_Z.
        2. Rapid XY travel to Camera Safe Approach point.
        3. Descend Z to focal height.
        4. Slow creep XY into Camera optical center.
        """
        if not self.is_homed():
            raise SafeNavigatorException("Printer axes must be homed before entering camera station.")

        if self.cam_target_x is None or self.cam_target_y is None:
            raise SafeNavigatorException("Camera target coordinates (camera_x, camera_y) not configured.")

        # Default approach point: offset 25mm in Y if not explicitly taught
        app_x = self.cam_approach_x if self.cam_approach_x is not None else self.cam_target_x
        app_y = self.cam_approach_y if self.cam_approach_y is not None else max(0.0, self.cam_target_y - 25.0)

        # Step 1: Vertical lift to safe clearance
        self.move_to_safe_z(toolhead, gcode_move)

        # Step 2: Lateral XY travel to approach waypoint outside shroud
        toolhead.manual_move([app_x, app_y, None], self.travel_speed)
        toolhead.wait_moves()

        # Step 3: Descend Z to focal altitude
        toolhead.manual_move([None, None, self.cam_target_z], self.z_speed)
        toolhead.wait_moves()

        # Step 4: Controlled low-speed lateral entry into optical center
        toolhead.manual_move([self.cam_target_x, self.cam_target_y, None], self.approach_speed)
        toolhead.wait_moves()

    def approach_switch(self, toolhead, gcode_move, z_clearance: float = 5.0) -> None:
        """
        Executes 3-tier safe transition into the Physical Z Switch Station.
        """
        if not self.is_homed():
            raise SafeNavigatorException("Printer axes must be homed before entering switch station.")

        if self.switch_target_x is None or self.switch_target_y is None:
            raise SafeNavigatorException("Z Switch coordinates (zswitch_x_pos, zswitch_y_pos) not configured.")

        app_x = self.switch_approach_x if self.switch_approach_x is not None else self.switch_target_x
        app_y = self.switch_approach_y if self.switch_approach_y is not None else max(0.0, self.switch_target_y - 20.0)

        # Step 1: Raise to Safe_Z
        self.move_to_safe_z(toolhead, gcode_move)

        # Step 2: Move to Approach XY
        toolhead.manual_move([app_x, app_y, None], self.travel_speed)
        toolhead.wait_moves()

        # Step 3: Lower Z to switch clearance height
        target_z = (self.switch_target_z + z_clearance) if self.switch_target_z is not None else self.safe_z
        toolhead.manual_move([None, None, target_z], self.z_speed)
        toolhead.wait_moves()

        # Step 4: Move onto switch pin apex
        toolhead.manual_move([self.switch_target_x, self.switch_target_y, None], self.approach_speed)
        toolhead.wait_moves()

    def depart_station(self, toolhead, gcode_move) -> None:
        """
        Safely departs any station by elevating Z back to safe_z.
        """
        self.move_to_safe_z(toolhead, gcode_move)
