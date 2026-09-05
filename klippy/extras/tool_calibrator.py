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

        # Register G-Code Commands
        self.gcode.register_command("CALIBRATE_TOOL_OFFSETS", self.cmd_CALIBRATE_TOOL_OFFSETS, desc="Automated Full Tool Offset Calibration")
        self.gcode.register_command("CALIBRATION_SET_SAFE_POS", self.cmd_CALIBRATION_SET_SAFE_POS, desc="Interactive Safe Position Teaching")
        self.gcode.register_command("CALIBRATION_ROLLBACK_OFFSETS", self.cmd_CALIBRATION_ROLLBACK_OFFSETS, desc="Rollback to Previous Configuration Backup")
        self.gcode.register_command("CALIBRATION_STATUS", self.cmd_CALIBRATION_STATUS, desc="Display Tool Calibration Status & Offsets")

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

    def cmd_CALIBRATION_SET_SAFE_POS(self, gcmd) -> None:
        """
        Interactive Teaching Command to save current toolhead position.
        Usage: CALIBRATION_SET_SAFE_POS STATION=CAMERA|Z_SWITCH TYPE=APPROACH|TARGET|SAFE_Z
        """
        toolhead = self.printer.lookup_object("toolhead")
        pos = toolhead.get_position()
        station = gcmd.get("STATION", "").upper()
        pos_type = gcmd.get("TYPE", "").upper()

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
        elif station == "Z_SWITCH":
            if pos_type == "APPROACH":
                self.navigator.switch_approach_x = round(pos[0], 3)
                self.navigator.switch_approach_y = round(pos[1], 3)
                gcmd.respond_info(f"Z-Switch Safe Approach set to X:{pos[0]:.3f} Y:{pos[1]:.3f}")
            elif pos_type == "TARGET":
                self.navigator.switch_target_x = round(pos[0], 3)
                self.navigator.switch_target_y = round(pos[1], 3)
                self.navigator.switch_target_z = round(pos[2], 3)
                gcmd.respond_info(f"Z-Switch Target Pin set to X:{pos[0]:.3f} Y:{pos[1]:.3f} Z:{pos[2]:.3f}")
        else:
            gcmd.respond_error("Invalid STATION. Must be CAMERA or Z_SWITCH.")

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
