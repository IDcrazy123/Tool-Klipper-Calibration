"""
Safe Navigator Module for Tool-Klipper-Calibration.

Implements the 3-tier safe navigation state machine (Safe_Z -> Safe_Approach -> Target)
to prevent toolhead collision with camera shrouds, docks, and bed clamps.
"""

from typing import Tuple, Optional, Dict
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
        self.travel_speed = config.getfloat("travel_speed", 100.0, above=0.1)
        if self.travel_speed > 500.0:
            self.travel_speed /= 60.0

        self.approach_speed = config.getfloat("approach_speed", 25.0, above=0.1)
        if self.approach_speed > 500.0:
            self.approach_speed /= 60.0

        self.z_speed = config.getfloat("z_speed", 10.0, above=0.1)
        if self.z_speed > 500.0:
            self.z_speed /= 60.0

        # Global Safe Z Clearance Altitude (configured_safe_z is None if omitted/commented out)
        # safe_z=0 allows operators to bypass safe Z elevation when unobstructed trajectories are verified.
        # Smart default: when safe_z is omitted/commented out, Cartographer Touch defaults to speed-up mode (0.0mm).
        # If safe_z is explicitly configured with a number, that declared number is strictly respected.
        configured_sz = config.getfloat("safe_z", None, minval=0.0)
        self.configured_safe_z = configured_sz
        z_backend = "cartographer"
        if hasattr(config, "get"):
            z_backend = str(config.get("z_backend", "cartographer") or "cartographer").strip().lower()

        if configured_sz is not None:
            self.safe_z = configured_sz
        elif z_backend == "cartographer":
            self.safe_z = 0.0
        else:
            self.safe_z = 35.0

        # Camera Station Waypoints (supports camera_target_x and camera_x aliases)
        self.cam_approach_x = config.getfloat("camera_approach_x", None)
        self.cam_approach_y = config.getfloat("camera_approach_y", None)
        cam_tx = config.getfloat("camera_target_x", None)
        self.cam_target_x = cam_tx if cam_tx is not None else config.getfloat("camera_x", None)
        cam_ty = config.getfloat("camera_target_y", None)
        self.cam_target_y = cam_ty if cam_ty is not None else config.getfloat("camera_y", None)
        cam_tz = config.getfloat("camera_target_z", None)
        self.cam_target_z = cam_tz if cam_tz is not None else config.getfloat("camera_focal_z", 15.0)

        # Z Switch Station Waypoints (supports switch_target_x and zswitch_x_pos aliases)
        self.switch_approach_x = config.getfloat("switch_approach_x", None)
        self.switch_approach_y = config.getfloat("switch_approach_y", None)
        sw_tx = config.getfloat("switch_target_x", None)
        self.switch_target_x = sw_tx if sw_tx is not None else config.getfloat("zswitch_x_pos", None)
        sw_ty = config.getfloat("switch_target_y", None)
        self.switch_target_y = sw_ty if sw_ty is not None else config.getfloat("zswitch_y_pos", None)
        sw_tz = config.getfloat("switch_target_z", None)
        self.switch_target_z = sw_tz if sw_tz is not None else config.getfloat("zswitch_z_pos", None)

    def get_axis_limits(self) -> Dict[str, Tuple[float, float]]:
        """
        Dynamically extracts exact physical axis boundaries [min, max] directly from
        Klipper's active toolhead kinematics or configfile settings.
        Seamlessly adapts to any hardware frame (Voron V0 120mm, V2.4 250/300/350mm, custom).
        """
        # 1. Primary Source: Query active toolhead status (always exact runtime values)
        try:
            toolhead = self.printer.lookup_object("toolhead", None)
            if toolhead:
                status = toolhead.get_status(self.printer.get_reactor().monotonic())
                ax_min = status.get("axis_minimum")
                ax_max = status.get("axis_maximum")
                if ax_min and ax_max and len(ax_min) >= 3 and len(ax_max) >= 3:
                    return {
                        "x": (float(ax_min[0]), float(ax_max[0])),
                        "y": (float(ax_min[1]), float(ax_max[1])),
                        "z": (float(ax_min[2]), float(ax_max[2]))
                    }
        except Exception:
            pass

        # 2. Secondary Source: Parse configfile stepper settings from printer.cfg
        try:
            configfile = self.printer.lookup_object("configfile", None)
            if configfile and hasattr(configfile, "settings"):
                sx = configfile.settings.get("stepper_x", {})
                sy = configfile.settings.get("stepper_y", {})
                sz = configfile.settings.get("stepper_z", {})
                if sx and sy:
                    min_x = float(sx.get("position_min", 0.0))
                    max_x = float(sx.get("position_max", 300.0))
                    min_y = float(sy.get("position_min", 0.0))
                    max_y = float(sy.get("position_max", 300.0))
                    min_z = float(sz.get("position_min", 0.0))
                    max_z = float(sz.get("position_max", 250.0))
                    return {
                        "x": (min_x, max_x),
                        "y": (min_y, max_y),
                        "z": (min_z, max_z)
                    }
        except Exception:
            pass

        # 3. Graceful fallback if completely unconfigured or standalone mock
        return {
            "x": (0.0, 300.0),
            "y": (0.0, 300.0),
            "z": (0.0, 250.0)
        }

    def get_bed_center(self) -> Tuple[float, float]:
        """
        Determines the true center of the user's printer bed based on physical limits.
        """
        limits = self.get_axis_limits()
        min_x, max_x = limits["x"]
        min_y, max_y = limits["y"]
        return ((min_x + max_x) / 2.0, (min_y + max_y) / 2.0)

    def validate_coordinate_safety(self, x: Optional[float] = None, y: Optional[float] = None, z: Optional[float] = None) -> None:
        """
        Validates target move coordinates against the user's actual printer frame limits.
        Raises SafeNavigatorException if a move would exceed the physical axis travel.
        """
        limits = self.get_axis_limits()
        if x is not None:
            min_x, max_x = limits["x"]
            if x < min_x or x > max_x:
                raise SafeNavigatorException(f"[ERR_NAV_002] Target X {x:.2f}mm exceeds printer frame boundaries [{min_x:.2f}, {max_x:.2f}]mm")
        if y is not None:
            min_y, max_y = limits["y"]
            if y < min_y or y > max_y:
                raise SafeNavigatorException(f"[ERR_NAV_003] Target Y {y:.2f}mm exceeds printer frame boundaries [{min_y:.2f}, {max_y:.2f}]mm")
        if z is not None:
            min_z, max_z = limits["z"]
            if z < min_z or z > max_z:
                raise SafeNavigatorException(f"[ERR_NAV_004] Target Z {z:.2f}mm exceeds printer frame boundaries [{min_z:.2f}, {max_z:.2f}]mm")

    def clamp_to_hardware_margins(self, x: float, y: float, margin: float = 5.0) -> Tuple[float, float]:
        """Clamps an approach waypoint to remain safely within physical rail limits."""
        limits = self.get_axis_limits()
        min_x, max_x = limits["x"]
        min_y, max_y = limits["y"]
        clamped_x = max(min_x + margin, min(max_x - margin, x))
        clamped_y = max(min_y + margin, min(max_y - margin, y))
        return (round(clamped_x, 3), round(clamped_y, 3))

    def calculate_auto_approach(self, target_x: float, target_y: float, approach_distance: float = 25.0) -> Tuple[float, float]:
        """
        Calculates a safe approach waypoint pointing from the station inwards towards
        the active build area center. Clamped to the user's actual hardware frame.
        """
        center_x, center_y = self.get_bed_center()
        dx = center_x - target_x
        dy = center_y - target_y
        dist = (dx**2 + dy**2) ** 0.5
        if dist < 1e-4:
            return self.clamp_to_hardware_margins(target_x, target_y + approach_distance)

        nx = dx / dist
        ny = dy / dist
        raw_x = target_x + nx * approach_distance
        raw_y = target_y + ny * approach_distance
        return self.clamp_to_hardware_margins(raw_x, raw_y)

    def set_camera_waypoints(self, target_x: float, target_y: float, target_z: float, approach_x: Optional[float] = None, approach_y: Optional[float] = None) -> None:
        """Sets or auto-computes camera station waypoints."""
        self.cam_target_x = target_x
        self.cam_target_y = target_y
        self.cam_target_z = target_z
        if approach_x is not None and approach_y is not None:
            self.cam_approach_x = approach_x
            self.cam_approach_y = approach_y
        else:
            self.cam_approach_x, self.cam_approach_y = self.calculate_auto_approach(target_x, target_y, 25.0)

    def set_switch_waypoints(self, target_x: float, target_y: float, target_z: float, approach_x: Optional[float] = None, approach_y: Optional[float] = None) -> None:
        """Sets or auto-computes Z switch station waypoints."""
        self.switch_target_x = target_x
        self.switch_target_y = target_y
        self.switch_target_z = target_z
        if approach_x is not None and approach_y is not None:
            self.switch_approach_x = approach_x
            self.switch_approach_y = approach_y
        else:
            self.switch_approach_x, self.switch_approach_y = self.calculate_auto_approach(target_x, target_y, 20.0)

    def is_homed(self) -> bool:
        """Verifies if axes X, Y, and Z are homed."""
        toolhead = self.printer.lookup_object("toolhead")
        status = toolhead.get_status(self.printer.get_reactor().monotonic())
        homed_axes = status.get("homed_axes", "")
        return "x" in homed_axes and "y" in homed_axes and "z" in homed_axes

    def move_to_safe_z(self, toolhead, gcode_move) -> None:
        """
        Elevates Z vertically to safe_z if currently lower.
        Clamps safe_z against the user's physical frame max_z to avoid Move out of range.
        Always waits for moves to finish before returning.
        If safe_z <= 0.0, vertical elevation is completely bypassed.
        """
        if self.safe_z <= 0.0:
            logger.info("Safe_Z is <= 0.0; skipping safe Z elevation.")
            return

        toolhead.wait_moves()
        cur_pos = toolhead.get_position()

        limits = self.get_axis_limits()
        max_z = limits["z"][1]
        effective_safe_z = min(self.safe_z, max(limits["z"][0], max_z - 3.0))

        if cur_pos[2] < effective_safe_z:
            logger.info(f"Lifting Z from {cur_pos[2]:.2f}mm to Safe_Z ({effective_safe_z:.2f}mm)")
            self.validate_coordinate_safety(z=effective_safe_z)
            toolhead.manual_move([None, None, effective_safe_z], self.z_speed)
            toolhead.wait_moves()

    def approach_camera(self, toolhead, gcode_move, target_z: Optional[float] = None) -> None:
        """
        Executes 3-tier safe transition into the Camera Station:
        1. Raise Z to Safe_Z.
        2. Rapid XY travel to Camera Safe Approach point.
        3. Descend Z to focal height (or compensated focal altitude for secondary tools).
        4. Slow creep XY into Camera optical center.
        """
        if not self.is_homed():
            raise SafeNavigatorException("Printer axes must be homed before entering camera station.")

        if self.cam_target_x is None or self.cam_target_y is None:
            raise SafeNavigatorException("Camera target coordinates (camera_x, camera_y) not configured.")

        effective_focal_z = target_z if target_z is not None else self.cam_target_z

        # Automatic approach vector towards bed center if not explicitly taught
        if self.cam_approach_x is not None and self.cam_approach_y is not None:
            app_x, app_y = self.cam_approach_x, self.cam_approach_y
        else:
            app_x, app_y = self.calculate_auto_approach(self.cam_target_x, self.cam_target_y, 25.0)

        # Validate coordinate safety against user's physical printer boundaries
        self.validate_coordinate_safety(x=app_x, y=app_y)
        self.validate_coordinate_safety(x=self.cam_target_x, y=self.cam_target_y, z=effective_focal_z)

        # Step 1: Vertical lift to safe clearance
        self.move_to_safe_z(toolhead, gcode_move)

        # Step 2: Lateral XY travel to approach waypoint outside shroud
        toolhead.manual_move([app_x, app_y, None], self.travel_speed)
        toolhead.wait_moves()

        # Step 3: Descend Z to focal altitude
        toolhead.manual_move([None, None, effective_focal_z], self.z_speed)
        toolhead.wait_moves()

        # Step 4: Controlled low-speed lateral entry into optical center
        toolhead.manual_move([self.cam_target_x, self.cam_target_y, None], self.approach_speed)
        toolhead.wait_moves()

    def approach_switch(self, toolhead, gcode_move, z_clearance: float = 5.0, offset_xy: Optional[Tuple[float, float]] = None) -> None:
        """
        Executes 3-tier safe transition into the Physical Z Switch Station.
        If offset_xy=(dx, dy) is provided, compensates target carriage coordinates so the
        secondary tool nozzle touches the exact physical switch pin center:
            target_x = switch_target_x + dx
            target_y = switch_target_y + dy
        Coordinate convention:
            offset_xy is (raw_tool_carriage - raw_ref_carriage). To bring the tool nozzle
            to the reference switch pin, the machine carriage must displace by (+dx, +dy).
        """
        if not self.is_homed():
            raise SafeNavigatorException("Printer axes must be homed before entering switch station.")

        if self.switch_target_x is None or self.switch_target_y is None:
            raise SafeNavigatorException("Z Switch coordinates (zswitch_x_pos, zswitch_y_pos) not configured.")

        dx, dy = (0.0, 0.0) if offset_xy is None else offset_xy
        eff_target_x = round(self.switch_target_x + dx, 4)
        eff_target_y = round(self.switch_target_y + dy, 4)

        # Automatic approach vector towards bed center if not explicitly taught
        if self.switch_approach_x is not None and self.switch_approach_y is not None:
            app_x, app_y = round(self.switch_approach_x + dx, 4), round(self.switch_approach_y + dy, 4)
        else:
            app_x, app_y = self.calculate_auto_approach(eff_target_x, eff_target_y, 20.0)

        # Validate coordinates
        self.validate_coordinate_safety(x=app_x, y=app_y)
        self.validate_coordinate_safety(x=eff_target_x, y=eff_target_y)

        # Step 1: Raise to Safe_Z
        self.move_to_safe_z(toolhead, gcode_move)

        # Step 2: Move to Approach XY
        toolhead.manual_move([app_x, app_y, None], self.travel_speed)
        toolhead.wait_moves()

        # Step 3: Lower Z to switch clearance height
        target_z = (self.switch_target_z + z_clearance) if self.switch_target_z is not None else max(5.0, self.safe_z)
        toolhead.manual_move([None, None, target_z], self.z_speed)
        toolhead.wait_moves()

        # Step 4: Move onto switch pin apex with XY compensation
        toolhead.manual_move([eff_target_x, eff_target_y, None], self.approach_speed)
        toolhead.wait_moves()


    def depart_station(self, toolhead, gcode_move) -> None:
        """
        Safely departs any station by elevating Z back to safe_z.
        """
        self.move_to_safe_z(toolhead, gcode_move)
