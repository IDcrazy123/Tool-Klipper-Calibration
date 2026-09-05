"""
Tool-Klipper-Calibration Master Klipper Extension.

Coordinates automated Computer Vision XY alignment and multi-backend Z calibration (Switch / Cartographer)
for multi-toolhead 3D printers running Klipper Toolchanger.
"""

from typing import Dict, Any, List, Optional
import json
import logging
import urllib.request
import urllib.error

from .safe_navigator import SafeNavigator, SafeNavigatorException
from .config_manager import ConfigManager, ConfigManagerException
from .z_backends.switch_backend import SwitchBackend
from .z_backends.cartographer_backend import CartographerBackend

logger = logging.getLogger("tool_calibrator")


class ToolCalibrator:
    """
    Main Klipper module orchestrating dual XY+Z calibration, safe kinematics,
    and offset persistence.
    """

    def __init__(self, config) -> None:
        self.config = config
        self.printer = config.get_printer()
        self.reactor = self.printer.get_reactor()
        self.gcode = self.printer.lookup_object("gcode")

        # Config parameters
        self.server_url = config.get("server_url", "http://localhost:8090").rstrip("/")
        self.reference_tool = config.getint("reference_tool", 0)
        self.z_backend_type = config.get("z_backend", "cartographer").strip().lower()
        self.max_centering_iterations = config.getint("max_centering_iterations", 5, minval=1, maxval=10)
        self.tolerance_mm = config.getfloat("tolerance_mm", 0.015, above=0.001)

        # Core Component Instances
        self.navigator = SafeNavigator(config)
        self.config_manager = ConfigManager(
            config.get("offset_config_path", "~/printer_data/config/tool_offsets.cfg")
        )

        # Initialize Z Backend
        if self.z_backend_type == "switch":
            self.z_backend = SwitchBackend(config)
        elif self.z_backend_type == "cartographer":
            self.z_backend = CartographerBackend(config)
        else:
            raise config.error(f"[tool_calibrator] Invalid z_backend '{self.z_backend_type}'. Must be 'switch' or 'cartographer'.")

        # Hooks
        self.gcode_macro = self.printer.load_object(config, "gcode_macro")
        self.start_gcode = self.gcode_macro.load_template(config, "start_gcode", "")
        self.before_pickup_gcode = self.gcode_macro.load_template(config, "before_pickup_gcode", "")
        self.after_pickup_gcode = self.gcode_macro.load_template(config, "after_pickup_gcode", "")
        self.finish_gcode = self.gcode_macro.load_template(config, "finish_gcode", "")

        # Calibration State
        self.cached_offsets: Dict[int, Dict[str, float]] = {}
        self.last_run_status = "UNINITIALIZED"

        # Auto-load saved station waypoints from tool_offsets.cfg if not explicitly set in printer.cfg
        self._load_saved_stations()

        # Register G-Code Commands
        self.gcode.register_command("CALIBRATE_TOOL_OFFSETS", self.cmd_CALIBRATE_TOOL_OFFSETS, desc="Automated Full Tool Offset Calibration")
        self.gcode.register_command("CALIBRATE_CAMERA_SCALE", self.cmd_CALIBRATE_CAMERA_SCALE, desc="Star-pattern Camera Scale and Affine Matrix Calibration")
        self.gcode.register_command("CALIBRATION_TEACH_STATION", self.cmd_CALIBRATION_TEACH_STATION, desc="1-Click Interactive Teaching & Auto-Persistence")
        self.gcode.register_command("CALIBRATION_SET_SAFE_POS", self.cmd_CALIBRATION_SET_SAFE_POS, desc="Interactive Safe Position Teaching")
        self.gcode.register_command("CALIBRATION_ROLLBACK_OFFSETS", self.cmd_CALIBRATION_ROLLBACK_OFFSETS, desc="Rollback to Previous Configuration Backup")
        self.gcode.register_command("CALIBRATION_STATUS", self.cmd_CALIBRATION_STATUS, desc="Display Tool Calibration Status & Offsets")

    def _load_saved_stations(self) -> None:
        """Loads saved camera and switch station waypoints and auto-inherits from tools_calibrate."""
        cam_saved = self.config_manager.load_section("tool_calibrator_station camera")
        if cam_saved:
            if self.navigator.cam_target_x is None and "target_x" in cam_saved:
                self.navigator.cam_target_x = float(cam_saved["target_x"])
            if self.navigator.cam_target_y is None and "target_y" in cam_saved:
                self.navigator.cam_target_y = float(cam_saved["target_y"])
            if "target_z" in cam_saved:
                self.navigator.cam_target_z = float(cam_saved["target_z"])
            if self.navigator.cam_approach_x is None and "approach_x" in cam_saved:
                self.navigator.cam_approach_x = float(cam_saved["approach_x"])
            if self.navigator.cam_approach_y is None and "approach_y" in cam_saved:
                self.navigator.cam_approach_y = float(cam_saved["approach_y"])
            if "mpp" in cam_saved:
                self.calibrated_mpp = float(cam_saved["mpp"])
                try:
                    self._query_vision("set_mpp", {"mpp": self.calibrated_mpp})
                except Exception:
                    pass

        switch_saved = self.config_manager.load_section("tool_calibrator_station switch")
        if switch_saved:
            if self.navigator.switch_target_x is None and "target_x" in switch_saved:
                self.navigator.switch_target_x = float(switch_saved["target_x"])
            if self.navigator.switch_target_y is None and "target_y" in switch_saved:
                self.navigator.switch_target_y = float(switch_saved["target_y"])
            if "target_z" in switch_saved:
                self.navigator.switch_target_z = float(switch_saved["target_z"])
            if self.navigator.switch_approach_x is None and "approach_x" in switch_saved:
                self.navigator.switch_approach_x = float(switch_saved["approach_x"])
            if self.navigator.switch_approach_y is None and "approach_y" in switch_saved:
                self.navigator.switch_approach_y = float(switch_saved["approach_y"])

        # Auto-inherit switch position from tools_calibrate if available and not configured
        if self.navigator.switch_target_x is None or self.navigator.switch_target_y is None:
            tools_cal = self.printer.lookup_object("tools_calibrate", None)
            if tools_cal is not None:
                if hasattr(tools_cal, "pin_loc_x"):
                    self.navigator.switch_target_x = float(tools_cal.pin_loc_x)
                if hasattr(tools_cal, "pin_loc_y"):
                    self.navigator.switch_target_y = float(tools_cal.pin_loc_y)
                if hasattr(tools_cal, "pin_loc_z"):
                    self.navigator.switch_target_z = float(tools_cal.pin_loc_z)
                logger.info(f"Auto-inherited Z-switch coordinates from tools_calibrate: ({self.navigator.switch_target_x}, {self.navigator.switch_target_y}, {self.navigator.switch_target_z})")

    def _query_vision(self, endpoint: str, payload: Optional[Dict[str, Any]] = None, timeout: float = 2.0) -> Dict[str, Any]:
        """
        Sends a non-blocking JSON query to the Vision Service with strict timeout.
        """
        url = f"{self.server_url}/{endpoint.lstrip('/')}"
        data_bytes = json.dumps(payload).encode("utf-8") if payload else None
        req = urllib.request.Request(
            url,
            data=data_bytes,
            headers={"Content-Type": "application/json", "User-Agent": "ToolCalibrator/1.0"}
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.URLError as ex:
            raise SafeNavigatorException(f"[ERR_CAM_101] Cannot connect to Vision Service on {url}: {ex}")
        except Exception as ex:
            raise SafeNavigatorException(f"[ERR_CAM_102] Vision communication error: {ex}")

    def _center_nozzle(self, toolhead, gcmd) -> None:
        """
        Visual servoing loop: pulls nozzle center from vision service and applies damped moves.
        """
        for iteration in range(1, self.max_centering_iterations + 1):
            toolhead.wait_moves()
            # Brief pause for frame stability
            self.reactor.pause(self.reactor.monotonic() + 0.15)

            vision_resp = self._query_vision("detect_nozzle", {"min_matches": 1, "timeout": 3.0})
            if not vision_resp.get("found"):
                raise SafeNavigatorException(f"[ERR_CV_201] Nozzle orifice not found during centering attempt {iteration}.")

            center_uv = vision_resp.get("center_uv")
            offset_resp = self._query_vision("calculate_offset", {"center_uv": center_uv})
            dx, dy = offset_resp.get("offset_xy", [0.0, 0.0])

            gcmd.respond_info(f"  -> Centering Step {iteration}: Delta X{dx:+.3f}mm Y{dy:+.3f}mm (UV: {center_uv})")

            # Check if converged within tolerance
            if abs(dx) <= self.tolerance_mm and abs(dy) <= self.tolerance_mm:
                gcmd.respond_info(f"  -> Convergence achieved within {self.tolerance_mm}mm tolerance.")
                return

            # Apply relative correction move at approach speed
            toolhead.manual_move([toolhead.get_position()[0] + dx, toolhead.get_position()[1] + dy, None], self.navigator.approach_speed)
            toolhead.wait_moves()

        gcmd.respond_info(f"  -> Warning: Centering reached max iterations ({self.max_centering_iterations}).")

    def cmd_CALIBRATE_TOOL_OFFSETS(self, gcmd) -> None:
        """
        Full 1-Click End-to-End Calibration Command.
        Parameters:
            CALIBRATE_XY (int): 1 to run XY vision (default: 1)
            CALIBRATE_Z (int): 1 to run Z probing (default: 1)
            SAVE_CONFIG (int): 1 to persist offsets to disk (default: 1)
            DRY_RUN (int): 1 to simulate without physical touch or disk saves (default: 0)
        """
        calibrate_xy = gcmd.get_int("CALIBRATE_XY", 1) == 1
        calibrate_z = gcmd.get_int("CALIBRATE_Z", 1) == 1
        save_config = gcmd.get_int("SAVE_CONFIG", 1) == 1
        dry_run = gcmd.get_int("DRY_RUN", 0) == 1

        toolhead = self.printer.lookup_object("toolhead")
        gcode_move = self.printer.lookup_object("gcode_move")
        toolchanger = self.printer.lookup_object("toolchanger", None)

        if not self.navigator.is_homed():
            gcmd.respond_error("[ERR_PRE_001] Printer must be fully homed (G28) before calibration.")
            return

        # Pre-flight ping to vision service
        try:
            health = self._query_vision("health")
            gcmd.respond_info(f"[tool_calibrator] Vision Service connected: {health.get('service')} v{health.get('version')}")
        except Exception as ex:
            gcmd.respond_error(str(ex))
            return

        # Discover tool sequence
        ordered_tools = [self.reference_tool]
        if toolchanger is not None and hasattr(toolchanger, "tool_numbers"):
            all_tools = list(toolchanger.tool_numbers)
            ordered_tools = [self.reference_tool] + [t for t in all_tools if t != self.reference_tool]
        else:
            gcmd.respond_info("[tool_calibrator] Note: [toolchanger] object not detected; calibrating active tool only.")

        gcmd.respond_info(f"[tool_calibrator] Starting Calibration Sequence across tools: {ordered_tools} (Dry Run: {dry_run})")

        # Execute start_gcode hook
        if self.start_gcode:
            self.gcode_macro.run_script("start_gcode", self.start_gcode, {})

        reference_origin_xy: Optional[List[float]] = None
        reference_z_result: Dict[str, Any] = {}
        results: Dict[int, Dict[str, float]] = {}

        try:
            for tool_no in ordered_tools:
                gcmd.respond_info(f"\n--- Calibrating Toolhead T{tool_no} ---")

                # Change tool
                if self.before_pickup_gcode:
                    self.gcode_macro.run_script("before_pickup_gcode", self.before_pickup_gcode, {"TOOL": tool_no})
                self.gcode.run_script_from_command(f"T{tool_no}")
                if self.after_pickup_gcode:
                    self.gcode_macro.run_script("after_pickup_gcode", self.after_pickup_gcode, {"TOOL": tool_no})

                toolhead.wait_moves()

                tool_offsets: Dict[str, float] = {"x": 0.0, "y": 0.0, "z": 0.0}

                # Step A: XY Vision Calibration
                if calibrate_xy:
                    gcmd.respond_info(f"[T{tool_no}] Entering Camera Station...")
                    self.navigator.approach_camera(toolhead, gcode_move)
                    self._center_nozzle(toolhead, gcmd)
                    raw_pos = toolhead.get_position()

                    if tool_no == self.reference_tool:
                        reference_origin_xy = [raw_pos[0], raw_pos[1]]
                        gcmd.respond_info(f"[T{tool_no}] Reference Optical Origin set to X{raw_pos[0]:.3f} Y{raw_pos[1]:.3f}")
                    else:
                        dx = round(raw_pos[0] - reference_origin_xy[0], 3)
                        dy = round(raw_pos[1] - reference_origin_xy[1], 3)
                        tool_offsets["x"] = dx
                        tool_offsets["y"] = dy
                        gcmd.respond_info(f"[T{tool_no}] Calculated XY Offsets: X{dx:+.3f}mm Y{dy:+.3f}mm")

                    self.navigator.depart_station(toolhead, gcode_move)

                # Step B: Z Probing Calibration
                if calibrate_z and not dry_run:
                    gcmd.respond_info(f"[T{tool_no}] Entering Z Probe Station...")
                    if self.z_backend_type == "switch":
                        self.navigator.approach_switch(toolhead, gcode_move)
                    else:
                        # Cartographer moves to probe point
                        self.navigator.move_to_safe_z(toolhead, gcode_move)
                        if self.z_backend.probe_x is not None and self.z_backend.probe_y is not None:
                            toolhead.manual_move([self.z_backend.probe_x, self.z_backend.probe_y, None], self.navigator.travel_speed)

                    if tool_no == self.reference_tool:
                        reference_z_result = self.z_backend.probe_reference_tool(tool_no, gcmd)
                        gcmd.respond_info(f"[T{tool_no}] Reference Z baseline established ({reference_z_result.get('source')})")
                    else:
                        z_res = self.z_backend.probe_secondary_tool(tool_no, reference_z_result, gcmd)
                        tool_offsets["z"] = z_res.get("suggested_z_offset", 0.0)
                        gcmd.respond_info(f"[T{tool_no}] Calculated Z Offset: Z{tool_offsets['z']:+.3f}mm")

                    self.navigator.depart_station(toolhead, gcode_move)

                results[tool_no] = tool_offsets

            # Restore Reference Tool
            self.gcode.run_script_from_command(f"T{self.reference_tool}")
            self.navigator.move_to_safe_z(toolhead, gcode_move)

            # Execute finish_gcode hook
            if self.finish_gcode:
                self.gcode_macro.run_script("finish_gcode", self.finish_gcode, {})

            # Persist Offsets
            if save_config and not dry_run:
                for t_num, offs in results.items():
                    self.config_manager.save_tool_offsets(t_num, offs)
                gcmd.respond_info(f"[tool_calibrator] Successfully saved offsets to {self.config_manager.config_path}")

            # Telemetry Summary
            gcmd.respond_info("\n================ CALIBRATION SUMMARY ================")
            for t_num, offs in results.items():
                gcmd.respond_info(f"Tool T{t_num}: X={offs['x']:+.3f}mm  Y={offs['y']:+.3f}mm  Z={offs['z']:+.3f}mm")
            gcmd.respond_info("=====================================================")

            self.cached_offsets = results
            self.last_run_status = "SUCCESS"

        except Exception as ex:
            self.last_run_status = f"FAILED: {ex}"
            self.navigator.depart_station(toolhead, gcode_move)
            gcmd.respond_error(f"[tool_calibrator] Calibration Aborted: {ex}")

    def cmd_CALIBRATION_TEACH_STATION(self, gcmd) -> None:
        """
        1-Click Interactive Teaching & Auto-Persistence Command.
        Automatically centers (via visual servoing) or probes contact height,
        computes safe approach vector towards bed center, and saves directly to tool_offsets.cfg!

        Usage:
            CALIBRATION_TEACH_STATION STATION=CAMERA [AUTO_CENTER=1] [APPROACH_DIST=25]
            CALIBRATION_TEACH_STATION STATION=SWITCH [AUTO_TOUCH=1] [APPROACH_DIST=20]
        """
        station = gcmd.get("STATION", "").upper()
        if station not in ("CAMERA", "SWITCH", "Z_SWITCH"):
            gcmd.respond_error("STATION must be CAMERA or SWITCH.")
            return

        toolhead = self.printer.lookup_object("toolhead")
        if not self.navigator.is_homed():
            gcmd.respond_error("[ERR_PRE_001] Printer must be fully homed (G28) before teaching station.")
            return

        if station == "CAMERA":
            auto_center = gcmd.get_int("AUTO_CENTER", 1) == 1
            approach_dist = gcmd.get_float("APPROACH_DIST", 25.0)

            if auto_center:
                gcmd.respond_info("[tool_calibrator] Auto-centering nozzle over camera via visual servoing...")
                try:
                    self._center_nozzle(toolhead, gcmd)
                except Exception as ex:
                    gcmd.respond_info(f"Auto-centering note: {ex}. Proceeding with current manual position.")

            pos = toolhead.get_position()
            target_x, target_y, target_z = round(pos[0], 3), round(pos[1], 3), round(pos[2], 3)
            app_x, app_y = self.navigator.calculate_auto_approach(target_x, target_y, approach_dist)

            self.navigator.set_camera_waypoints(target_x, target_y, target_z, app_x, app_y)

            # Auto-persist to tool_offsets.cfg
            self.config_manager.save_section("tool_calibrator_station camera", {
                "target_x": target_x,
                "target_y": target_y,
                "target_z": target_z,
                "approach_x": app_x,
                "approach_y": app_y,
                "approach_z": target_z,
                "safe_z": self.navigator.safe_z
            })

            gcmd.respond_info(
                f"✔ [CAMERA Station Configured & Saved Automatically]\n"
                f"  Target:   X{target_x:.3f} Y{target_y:.3f} Z{target_z:.3f}\n"
                f"  Approach: X{app_x:.3f} Y{app_y:.3f} (Vector towards bed center)\n"
                f"  Saved to: {self.config_manager.config_path}"
            )

        elif station in ("SWITCH", "Z_SWITCH"):
            auto_touch = gcmd.get_int("AUTO_TOUCH", 1) == 1
            approach_dist = gcmd.get_float("APPROACH_DIST", 20.0)

            pos = toolhead.get_position()
            target_x, target_y, target_z = round(pos[0], 3), round(pos[1], 3), round(pos[2], 3)

            if auto_touch and self.z_backend_type == "switch":
                gcmd.respond_info("[tool_calibrator] Auto-touching switch pin to determine contact height...")
                try:
                    res = self.z_backend.probe_reference_tool(self.reference_tool, gcmd)
                    target_z = round(res.get("trigger_z", pos[2]), 3)
                except Exception as ex:
                    gcmd.respond_info(f"Auto-touch note: {ex}. Using current Z height.")

            app_x, app_y = self.navigator.calculate_auto_approach(target_x, target_y, approach_dist)
            self.navigator.set_switch_waypoints(target_x, target_y, target_z, app_x, app_y)

            # Auto-persist to tool_offsets.cfg
            self.config_manager.save_section("tool_calibrator_station switch", {
                "target_x": target_x,
                "target_y": target_y,
                "target_z": target_z,
                "approach_x": app_x,
                "approach_y": app_y,
                "approach_z": target_z,
                "safe_z": self.navigator.safe_z
            })

            gcmd.respond_info(
                f"✔ [SWITCH Station Configured & Saved Automatically]\n"
                f"  Target:   X{target_x:.3f} Y{target_y:.3f} Z{target_z:.3f}\n"
                f"  Approach: X{app_x:.3f} Y{app_y:.3f} (Vector towards bed center)\n"
                f"  Saved to: {self.config_manager.config_path}"
            )

    def cmd_CALIBRATE_CAMERA_SCALE(self, gcmd) -> None:
        """
        Executes automated star-pattern displacement to calculate exact mm-per-pixel (MPP)
        and solves the affine rotation/scaling transformation matrix.
        Parameters:
            DISTANCE (float): Calibration displacement distance in mm (default: 1.0mm, range: 0.2 - 5.0mm)
        """
        dist = gcmd.get_float("DISTANCE", 1.0)
        if dist < 0.2 or dist > 5.0:
            gcmd.respond_error("DISTANCE must be between 0.2mm and 5.0mm.")
            return

        toolhead = self.printer.lookup_object("toolhead")
        gcode_move = self.printer.lookup_object("gcode_move")

        if not self.navigator.is_homed():
            gcmd.respond_error("[ERR_PRE_001] Printer must be fully homed (G28) before camera calibration.")
            return

        gcmd.respond_info(f"[tool_calibrator] Starting Star-Pattern Camera Calibration (Displacement: ±{dist:.2f}mm)...")

        # 1. Approach Camera safely
        self.navigator.approach_camera(toolhead, gcode_move)

        # 2. Initial Center
        gcmd.respond_info("  -> Performing initial nozzle optical centering...")
        try:
            self._center_nozzle(toolhead, gcmd)
        except Exception as ex:
            gcmd.respond_info(f"Centering note: {ex}")

        toolhead.wait_moves()
        self.reactor.pause(self.reactor.monotonic() + 0.2)

        # Baseline detection
        base_resp = self._query_vision("detect_nozzle", {"min_matches": 1, "timeout": 3.0})
        if not base_resp.get("found"):
            self.navigator.depart_station(toolhead, gcode_move)
            gcmd.respond_error("[ERR_CV_201] Could not detect nozzle center at baseline position.")
            return

        base_uv = base_resp.get("center_uv")
        center_pos = toolhead.get_position()
        cx, cy, cz = center_pos[0], center_pos[1], center_pos[2]
        gcmd.respond_info(f"  -> Baseline established: Pos ({cx:.3f}, {cy:.3f}), UV ({base_uv[0]:.2f}, {base_uv[1]:.2f})")

        # 3. Displacements in 4 orthogonal directions (+X, -X, +Y, -Y)
        moves = [
            ("+X", cx + dist, cy, dist, 0.0),
            ("-X", cx - dist, cy, -dist, 0.0),
            ("+Y", cx, cy + dist, 0.0, dist),
            ("-Y", cx, cy - dist, 0.0, -dist)
        ]

        mpp_samples = []
        matrix_points = [
            [[0.0, 0.0], list(base_uv)]
        ]

        try:
            for label, tx, ty, rdx, rdy in moves:
                toolhead.manual_move([tx, ty, None], self.navigator.approach_speed)
                toolhead.wait_moves()
                self.reactor.pause(self.reactor.monotonic() + 0.2)

                det = self._query_vision("detect_nozzle", {"min_matches": 1, "timeout": 3.0})
                if not det.get("found"):
                    gcmd.respond_error(f"Failed to detect nozzle during displacement {label}.")
                    continue

                curr_uv = det.get("center_uv")
                pixel_dist = ((curr_uv[0] - base_uv[0])**2 + (curr_uv[1] - base_uv[1])**2)**0.5
                physical_dist = abs(dist)

                mpp_samples.append([physical_dist, pixel_dist])
                matrix_points.append([[rdx, rdy], list(curr_uv)])
                gcmd.respond_info(f"  -> {label} displacement: Shift {pixel_dist:.2f}px (UV: {curr_uv[0]:.2f}, {curr_uv[1]:.2f})")

            # Return to center
            toolhead.manual_move([cx, cy, None], self.navigator.approach_speed)
            toolhead.wait_moves()

            if len(mpp_samples) < 3:
                gcmd.respond_error("Insufficient valid points acquired for camera scale calibration.")
                self.navigator.depart_station(toolhead, gcode_move)
                return

            # Query server to calculate average MPP and solve affine matrix
            mpp_resp = self._query_vision("calibrate_mpp", {"samples": mpp_samples})
            solved_mpp = float(mpp_resp.get("mpp", 0.0))

            matrix_resp = self._query_vision("solve_matrix", {"calibration_points": matrix_points})
            matrix_ok = matrix_resp.get("success", False)

            # Persist calibrated MPP into tool_offsets.cfg under [tool_calibrator_station camera]
            self.config_manager.save_section("tool_calibrator_station camera", {
                "mpp": solved_mpp,
                "target_x": round(cx, 3),
                "target_y": round(cy, 3),
                "target_z": round(cz, 3),
                "safe_z": self.navigator.safe_z
            })

            gcmd.respond_info(
                f"\n✔ ================= CAMERA CALIBRATION SUCCESS ================\n"
                f"  Calculated Scale (MPP): {solved_mpp:.5f} mm/pixel\n"
                f"  Affine Matrix Solved:   {matrix_ok}\n"
                f"  Saved to Configuration: {self.config_manager.config_path}\n"
                f"================================================================"
            )

        finally:
            self.navigator.depart_station(toolhead, gcode_move)

    def cmd_CALIBRATION_SET_SAFE_POS(self, gcmd) -> None:
        """
        Interactive Teaching Command to save current toolhead position.
        Usage: CALIBRATION_SET_SAFE_POS STATION=CAMERA|Z_SWITCH TYPE=APPROACH|TARGET|SAFE_Z [SAVE=1]
        """
        toolhead = self.printer.lookup_object("toolhead")
        pos = toolhead.get_position()
        station = gcmd.get("STATION", "").upper()
        pos_type = gcmd.get("TYPE", "").upper()
        save_to_disk = gcmd.get_int("SAVE", 1) == 1

        if station == "CAMERA":
            if pos_type == "APPROACH":
                self.navigator.cam_approach_x = round(pos[0], 3)
                self.navigator.cam_approach_y = round(pos[1], 3)
                gcmd.respond_info(f"Camera Safe Approach set to X:{pos[0]:.3f} Y:{pos[1]:.3f}")
            elif pos_type == "TARGET":
                self.navigator.cam_target_x = round(pos[0], 3)
                self.navigator.cam_target_y = round(pos[1], 3)
                self.navigator.cam_target_z = round(pos[2], 3)
                gcmd.respond_info(f"Camera Optical Center set to X:{pos[0]:.3f} Y:{pos[1]:.3f} Z:{pos[2]:.3f}")
            elif pos_type == "SAFE_Z":
                self.navigator.safe_z = round(pos[2], 3)
                gcmd.respond_info(f"Global Safe_Z set to Z:{pos[2]:.3f}")

            if save_to_disk:
                self.config_manager.save_section("tool_calibrator_station camera", {
                    "target_x": self.navigator.cam_target_x,
                    "target_y": self.navigator.cam_target_y,
                    "target_z": self.navigator.cam_target_z,
                    "approach_x": self.navigator.cam_approach_x,
                    "approach_y": self.navigator.cam_approach_y,
                    "safe_z": self.navigator.safe_z
                })
        elif station in ("SWITCH", "Z_SWITCH"):
            if pos_type == "APPROACH":
                self.navigator.switch_approach_x = round(pos[0], 3)
                self.navigator.switch_approach_y = round(pos[1], 3)
                gcmd.respond_info(f"Z-Switch Safe Approach set to X:{pos[0]:.3f} Y:{pos[1]:.3f}")
            elif pos_type == "TARGET":
                self.navigator.switch_target_x = round(pos[0], 3)
                self.navigator.switch_target_y = round(pos[1], 3)
                self.navigator.switch_target_z = round(pos[2], 3)
                gcmd.respond_info(f"Z-Switch Target Pin set to X:{pos[0]:.3f} Y:{pos[1]:.3f} Z:{pos[2]:.3f}")

            if save_to_disk:
                self.config_manager.save_section("tool_calibrator_station switch", {
                    "target_x": self.navigator.switch_target_x,
                    "target_y": self.navigator.switch_target_y,
                    "target_z": self.navigator.switch_target_z,
                    "approach_x": self.navigator.switch_approach_x,
                    "approach_y": self.navigator.switch_approach_y,
                    "safe_z": self.navigator.safe_z
                })
        else:
            gcmd.respond_error("Invalid STATION. Must be CAMERA or SWITCH.")

    def cmd_CALIBRATION_ROLLBACK_OFFSETS(self, gcmd) -> None:
        """Emergency rollback command restoring previous configuration backup."""
        try:
            restored_file = self.config_manager.rollback()
            gcmd.respond_info(f"[tool_calibrator] Restored configuration from {restored_file}. Please issue FIRMWARE_RESTART.")
        except Exception as ex:
            gcmd.respond_error(f"[tool_calibrator] Rollback failed: {ex}")

    def cmd_CALIBRATION_STATUS(self, gcmd) -> None:
        """Emits current calibration state and cached offsets."""
        gcmd.respond_info(f"Tool-Calibrator Status: {self.last_run_status}")
        gcmd.respond_info(f"Safe_Z: {self.navigator.safe_z:.2f}mm | Backend: {self.z_backend_type}")
        for t, offs in self.cached_offsets.items():
            gcmd.respond_info(f"  T{t}: X={offs['x']:+.3f} Y={offs['y']:+.3f} Z={offs['z']:+.3f}")

    def get_status(self, eventtime) -> Dict[str, Any]:
        """Provides state dictionaries to Mainsail and Fluidd frontend templates."""
        return {
            "status": self.last_run_status,
            "reference_tool": self.reference_tool,
            "z_backend": self.z_backend_type,
            "safe_z": self.navigator.safe_z,
            "cached_offsets": self.cached_offsets
        }


def load_config(config):
    """Klipper module loader entrypoint."""
    return ToolCalibrator(config)
