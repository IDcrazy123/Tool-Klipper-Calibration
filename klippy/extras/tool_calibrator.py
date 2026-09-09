# -*- coding: utf-8 -*-
"""
Tool-Klipper-Calibration Master Klipper Extension.

Coordinates automated Computer Vision XY alignment and multi-backend Z calibration (Switch / Cartographer)
for multi-toolhead 3D printers running Klipper Toolchanger.
"""

from typing import Dict, Any, List, Optional, Set, Tuple
import os
import json
import logging
import math
import re
import threading
import time
import urllib.request
import urllib.error
import statistics

from .safe_navigator import SafeNavigator, SafeNavigatorException
from .config_manager import ConfigManager, ConfigManagerException
from .z_backends.switch_backend import SwitchBackend
from .z_backends.cartographer_backend import CartographerBackend
from .tool_offsets import is_command_registered

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

        # Config parameters (supports service_url and server_url aliases)
        srv_url = config.get("server_url", None)
        if srv_url is None:
            srv_url = config.get("service_url", "http://localhost:8090")
        self.server_url = srv_url.rstrip("/")
        self.api_token = config.get("api_token", None)

        self.reference_tool = config.getint("reference_tool", 0)
        self.configured_tools_str = config.get("tools", None)
        self.z_backend_type = config.get("z_backend", "cartographer").strip().lower()
        self.max_centering_iterations = config.getint("max_centering_iterations", 8, minval=1, maxval=20)
        self.tolerance_mm = config.getfloat("tolerance_mm", 0.015, above=0.001)
        self.centering_samples = config.getint("centering_samples", 3, minval=1, maxval=7)
        self.sample_delay = config.getfloat("sample_delay", 0.08, minval=0.01, maxval=0.5)
        self.wiggle_distance = config.getfloat("wiggle_distance", 0.1, above=0.01, maxval=1.0)
        if hasattr(config, "getboolean"):
            self.wiggle_on_failure = config.getboolean("wiggle_on_failure", True)
            self.allow_shuttle_z = config.getboolean("allow_shuttle_z", False)
        else:
            self.wiggle_on_failure = bool(config.get("wiggle_on_failure", True))
            self.allow_shuttle_z = bool(config.get("allow_shuttle_z", False))
        self.max_camera_temp = config.getfloat("max_camera_temp", 100.0, above=30.0, maxval=200.0)
        self.cached_reference_z_result: Optional[Dict[str, Any]] = None

        # Core Component Instances
        self.navigator = SafeNavigator(config)
        cfg_path = config.get("offset_config_path", None)
        if cfg_path is None:
            cfg_path = config.get("offsets_config_path", None)
        if cfg_path is None:
            candidate_paths = [
                os.path.expanduser("~/printer_data/config/Printer-Setup/tool_calibrator/tool_offsets.cfg"),
                os.path.expanduser("~/printer_data/config/tool_calibrator/tool_offsets.cfg"),
                os.path.expanduser("~/printer_data/config/tool_offsets.cfg"),
            ]
            cfg_path = candidate_paths[1]
            for cp in candidate_paths:
                if os.path.exists(cp):
                    cfg_path = cp
                    break
        self.config_manager = ConfigManager(cfg_path)

        # Initialize Z Backend
        if self.z_backend_type == "switch":
            self.z_backend = SwitchBackend(config)
        elif self.z_backend_type == "cartographer":
            self.z_backend = CartographerBackend(config)
        else:
            raise config.error(f"[tool_calibrator] Invalid z_backend '{self.z_backend_type}'. Must be 'switch' or 'cartographer'.")

        # Optional parameters for Klipper config validation
        self.camera_stream_url = config.get("camera_stream_url", None)
        self.switch_approach_z = config.getfloat("switch_approach_z", None)
        self.probing_speed = config.getfloat("probing_speed", None)
        self.lift_speed = config.getfloat("lift_speed", None)
        self.samples = config.getint("samples", None)
        self.samples_tolerance = config.getfloat("samples_tolerance", None)
        self.force_safe_z = config.getboolean("force_safe_z", False)
        self.samples_retract_dist = config.getfloat("samples_retract_dist", None)
        self.switch_pin = config.get("switch_pin", None)

        # Hooks (None if not explicitly configured in printer.cfg)
        self.gcode_macro = self.printer.load_object(config, "gcode_macro")
        def _load_optional_template(option_name: str):
            if config.get(option_name, None) is not None:
                return self.gcode_macro.load_template(config, option_name, "")
            return None

        self.start_gcode = _load_optional_template("start_gcode")
        self.before_pickup_gcode = _load_optional_template("before_pickup_gcode")
        self.after_pickup_gcode = _load_optional_template("after_pickup_gcode")
        self.clean_nozzle_gcode = _load_optional_template("clean_nozzle_gcode")
        self.finish_gcode = _load_optional_template("finish_gcode")

        # Optical Lighting Configuration
        self.camera_pin = config.get("camera_pin", config.get("camera_led", None))
        self.camera_led_brightness = config.getfloat("camera_led_brightness", 1.0, minval=0.0, maxval=1.0)
        self.camera_led_macro_on = config.get("camera_led_macro_on", "_CALIBRATION_CAMERA_LED_ON")
        self.camera_led_macro_off = config.get("camera_led_macro_off", "_CALIBRATION_CAMERA_LED_OFF")
        self.nozzle_led_macro_off = config.get("nozzle_led_macro_off", "_CALIBRATION_NOZZLE_LED_OFF")
        self.nozzle_led_macro_on = config.get("nozzle_led_macro_on", "_CALIBRATION_NOZZLE_LED_ON")

        # Calibration State & Run Telemetry Record
        self.cached_offsets: Dict[int, Dict[str, float]] = {}
        self.calibrated_mpp: Optional[float] = None
        self.physical_spread_limit_mm = config.getfloat("burst_spread_limit_mm", 0.08, minval=0.01, maxval=0.5)
        self.min_detection_confidence = config.getfloat("min_detection_confidence", 0.70, minval=0.10, maxval=1.0)
        self.min_nozzle_radius = config.getfloat("min_nozzle_radius", 10.0, minval=2.0, maxval=100.0)
        self.max_nozzle_radius = config.getfloat("max_nozzle_radius", 55.0, minval=5.0, maxval=200.0)
        if self.min_nozzle_radius > self.max_nozzle_radius:
            raise config.error(
                f"[tool_calibrator] min_nozzle_radius ({self.min_nozzle_radius:.1f}px) cannot be greater than "
                f"max_nozzle_radius ({self.max_nozzle_radius:.1f}px)"
            )
        self.last_run_status = "UNINITIALIZED"
        self.cancel_requested = False
        self.run_record: Dict[str, Any] = {
            "run_id": None,
            "state": "IDLE",
            "phase": "IDLE",
            "calibrating_tool": None,
            "physical_tool": None,
            "start_time": None,
            "start_monotonic": None,
            "end_time": None,
            "duration_sec": 0.0,
            "error": None,
            "completed_tools": [],
            "offsets": {},
            "valid": False,
            "tool_errors": {}
        }

        # Auto-load saved station waypoints from tool_offsets.cfg if not explicitly set in printer.cfg
        self._load_saved_stations()

        # Register G-Code Commands (Core & Direct Python Convenience Aliases)
        self.gcode.register_command("CALIBRATE_TOOL_OFFSETS", self.cmd_CALIBRATE_TOOL_OFFSETS, desc="Automated Full Tool Offset Calibration")
        self.gcode.register_command("CALIBRATE_CAMERA_SCALE", self.cmd_CALIBRATE_CAMERA_SCALE, desc="Star-pattern Camera Scale and Affine Matrix Calibration")
        self.gcode.register_command("CALIBRATION_TEACH_STATION", self.cmd_CALIBRATION_TEACH_STATION, desc="1-Click Interactive Teaching & Auto-Persistence")
        self.gcode.register_command("AUTO_TEACH_CAMERA", self.cmd_AUTO_TEACH_CAMERA, desc="1-Click Auto-Teach Camera Station")
        self.gcode.register_command("AUTO_TEACH_SWITCH", self.cmd_AUTO_TEACH_SWITCH, desc="1-Click Auto-Teach Switch Station")
        self.gcode.register_command("CENTER_NOZZLE", self.cmd_CALIBRATION_CENTER_NOZZLE, desc="Center active nozzle over camera")
        self.gcode.register_command("CALIBRATION_CENTER_NOZZLE", self.cmd_CALIBRATION_CENTER_NOZZLE, desc="Center active nozzle over camera")
        self.gcode.register_command("TEST_NOZZLE_VISION", self.cmd_CALIBRATION_TEST_VISION, desc="Test nozzle vision detection and report coordinates")
        self.gcode.register_command("CALIBRATION_TEST_VISION", self.cmd_CALIBRATION_TEST_VISION, desc="Test nozzle vision detection and report coordinates")
        self.gcode.register_command("CALIBRATION_SET_SAFE_POS", self.cmd_CALIBRATION_SET_SAFE_POS, desc="Interactive Safe Position Teaching")
        self.gcode.register_command("CALIBRATION_ROLLBACK_OFFSETS", self.cmd_CALIBRATION_ROLLBACK_OFFSETS, desc="Rollback to Previous Configuration Backup")
        self.gcode.register_command("CALIBRATION_ABORT", self.cmd_CALIBRATION_ABORT, desc="Aborts active calibration run cleanly")
        self.gcode.register_command("ABORT_CALIBRATION", self.cmd_CALIBRATION_ABORT, desc="Aborts active calibration run cleanly (Alias)")
        self.gcode.register_command("TOOL_CALIBRATOR_STATUS", self.cmd_CALIBRATION_STATUS, desc="Display Tool Calibration Status & Offsets")
        self.gcode.register_command("TKC_STATUS", self.cmd_CALIBRATION_STATUS, desc="Display Tool Calibration Status & Offsets (Short Alias)")
        if "CALIBRATION_STATUS" not in getattr(self.gcode, "commands", {}):
            try:
                self.gcode.register_command("CALIBRATION_STATUS", self.cmd_CALIBRATION_STATUS, desc="Display Tool Calibration Status & Offsets (Legacy Alias)")
            except Exception:
                pass
        self.gcode.register_command("CALIBRATION_NAVIGATE", self.cmd_CALIBRATION_NAVIGATE, desc="Safely navigate toolhead between stations")

        # Register Webhooks for out-of-band non-blocking control
        webhooks = self.printer.lookup_object('webhooks', None)
        if webhooks is not None:
            try:
                webhooks.register_endpoint("tool_calibrator/abort", self._handle_webhook_abort)
                webhooks.register_endpoint("tool_calibrator/status", self._handle_webhook_status)
            except Exception as ex:
                logger.warning(f"[tool_calibrator] Failed to register webhooks: {ex}")

        # Register event handlers for startup sync and coordinate epoch tracking
        if hasattr(self.printer, "register_event_handler"):
            self.printer.register_event_handler("klippy:ready", self._handle_klippy_ready)
            self.printer.register_event_handler("homing:home_rails_end", self._handle_homing_event)

    def _handle_homing_event(self, *args, **kwargs) -> None:
        """Invalidate reference Z baseline cache when machine is re-homed (new coordinate epoch)."""
        self.cached_reference_z_result = None
        logger.debug("[tool_calibrator] Homing event received; reference Z baseline cache invalidated.")

    def _handle_klippy_ready(self) -> None:
        """Called when Klipper is fully initialized; schedules vision sync and resets baseline cache."""
        self.cached_reference_z_result = None
        if hasattr(self.reactor, "register_callback"):
            self.reactor.register_callback(self._delayed_vision_sync)
        else:
            try:
                self._ensure_vision_sync()
            except Exception as ex:
                logger.warning(f"[tool_calibrator] Background vision sync on ready failed: {ex}")

    def _delayed_vision_sync(self, eventtime: float) -> None:
        """Executed on main reactor loop after ready dispatch completes with pause enabled."""
        try:
            self._ensure_vision_sync()
        except Exception as ex:
            logger.warning(f"[tool_calibrator] Scheduled vision sync on ready failed: {ex}")

    def _load_saved_stations(self) -> None:
        """Loads saved camera and switch station waypoints and auto-inherits from tools_calibrate."""
        cam_saved = self.config_manager.load_section("tool_calibrator_station camera")
        if cam_saved:
            if self.navigator.cam_target_x is None and "target_x" in cam_saved and cam_saved["target_x"] not in ("None", ""):
                self.navigator.cam_target_x = float(cam_saved["target_x"])
            if self.navigator.cam_target_y is None and "target_y" in cam_saved and cam_saved["target_y"] not in ("None", ""):
                self.navigator.cam_target_y = float(cam_saved["target_y"])
            if "target_z" in cam_saved and cam_saved["target_z"] not in ("None", ""):
                self.navigator.cam_target_z = float(cam_saved["target_z"])
            if self.navigator.cam_approach_x is None and "approach_x" in cam_saved and cam_saved["approach_x"] not in ("None", ""):
                self.navigator.cam_approach_x = float(cam_saved["approach_x"])
            if self.navigator.cam_approach_y is None and "approach_y" in cam_saved and cam_saved["approach_y"] not in ("None", ""):
                self.navigator.cam_approach_y = float(cam_saved["approach_y"])
            if "mpp" in cam_saved and cam_saved["mpp"] not in ("None", ""):
                self.calibrated_mpp = float(cam_saved["mpp"])

        switch_saved = self.config_manager.load_section("tool_calibrator_station switch")
        if switch_saved:
            if self.navigator.switch_target_x is None and "target_x" in switch_saved and switch_saved["target_x"] not in ("None", ""):
                self.navigator.switch_target_x = float(switch_saved["target_x"])
            if self.navigator.switch_target_y is None and "target_y" in switch_saved and switch_saved["target_y"] not in ("None", ""):
                self.navigator.switch_target_y = float(switch_saved["target_y"])
            if "target_z" in switch_saved and switch_saved["target_z"] not in ("None", ""):
                self.navigator.switch_target_z = float(switch_saved["target_z"])
            if self.navigator.switch_approach_x is None and "approach_x" in switch_saved and switch_saved["approach_x"] not in ("None", ""):
                self.navigator.switch_approach_x = float(switch_saved["approach_x"])
            if self.navigator.switch_approach_y is None and "approach_y" in switch_saved and switch_saved["approach_y"] not in ("None", ""):
                self.navigator.switch_approach_y = float(switch_saved["approach_y"])

        # Strict Precedence & State Rules for safe_z:
        # 1. safe_z > 0 declared in configuration: ENABLED and strictly respects the declared value.
        #    Old saved station data CANNOT overwrite it.
        # 2. safe_z = 0, commented out, or omitted: DISABLED completely.
        #    Old saved station data CANNOT reactivate safe_z.
        cfg_sz = getattr(self.navigator, "configured_safe_z", None)
        if cfg_sz is not None and cfg_sz > 0.0:
            self.navigator.safe_z = cfg_sz
            self.navigator.safe_z_enabled = True
            logger.info(f"[tool_calibrator] Declared safe_z in configuration: Z{self.navigator.safe_z:.3f} (ENABLED)")
        else:
            self.navigator.safe_z = None
            self.navigator.safe_z_enabled = False
            logger.info("[tool_calibrator] safe_z is 0, commented out, or omitted: DISABLED (no global safe_z lifts, old saved safe_z ignored)")

        self._sync_switch_location_from_tools_calibrate()

    def _sync_switch_location_from_tools_calibrate(self) -> None:
        """Dynamically queries tools_calibrate object for sensor_location."""
        tools_cal = self.printer.lookup_object("tools_calibrate", None)
        if tools_cal is None:
            return
        loc = getattr(tools_cal, "sensor_location", None)
        if loc is None and hasattr(tools_cal, "get_status"):
            try:
                st = tools_cal.get_status(self.reactor.monotonic())
                if isinstance(st, dict):
                    loc = st.get("sensor_location")
            except Exception:
                pass

        if loc is not None and isinstance(loc, (list, tuple)) and len(loc) >= 3:
            if self.navigator.switch_target_x is None:
                self.navigator.switch_target_x = float(loc[0])
            if self.navigator.switch_target_y is None:
                self.navigator.switch_target_y = float(loc[1])
            if self.navigator.switch_target_z is None:
                self.navigator.switch_target_z = float(loc[2])
            logger.info(f"Auto-inherited switch location from tools_calibrate.sensor_location: ({loc[0]}, {loc[1]}, {loc[2]})")
            return

        if self.navigator.switch_target_x is None and hasattr(tools_cal, "pin_loc_x") and tools_cal.pin_loc_x is not None:
            self.navigator.switch_target_x = float(tools_cal.pin_loc_x)
        if self.navigator.switch_target_y is None and hasattr(tools_cal, "pin_loc_y") and tools_cal.pin_loc_y is not None:
            self.navigator.switch_target_y = float(tools_cal.pin_loc_y)
        if self.navigator.switch_target_z is None and hasattr(tools_cal, "pin_loc_z") and tools_cal.pin_loc_z is not None:
            self.navigator.switch_target_z = float(tools_cal.pin_loc_z)

    def _get_source_git_commit(self) -> str:
        """Helper to retrieve current git commit hash of the local repository checkout."""
        try:
            import subprocess
            repo_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            if os.path.isdir(os.path.join(repo_dir, ".git")):
                return subprocess.check_output(
                    ["git", "rev-parse", "--short", "HEAD"],
                    cwd=repo_dir,
                    stderr=subprocess.DEVNULL,
                    universal_newlines=True
                ).strip()
        except Exception:
            pass
        return ""

    def _ensure_vision_sync(self) -> None:
        """
        Validates Vision Service connectivity and re-synchronizes camera_stream_url,
        calibrated MPP, and full 6-element affine matrix if server was started/restarted.
        """
        try:
            health = self._query_vision("health", timeout=2.0)
        except Exception as ex:
            logger.warning(f"Could not contact vision service health: {ex}")
            return

        daemon_commit = str(health.get("commit", "unknown"))
        src_commit = self._get_source_git_commit()
        if src_commit and daemon_commit not in ("unknown", "") and src_commit != daemon_commit and not daemon_commit.startswith(src_commit):
            logger.warning(
                f"[tool_calibrator] Running vision daemon commit ({daemon_commit}) differs from active source commit ({src_commit})! "
                f"Please restart vision service ('systemctl --user restart tool_calibrator.service' or 'sudo systemctl restart tool_calibrator.service')."
            )

        has_mpp = bool(health.get("calibrated_mpp", False) or health.get("mpp", None))
        has_matrix = bool(health.get("matrix_solved", health.get("has_matrix", False)))

        if self.camera_stream_url:
            try:
                self._query_vision("set_camera_url", {"url": self.camera_stream_url}, timeout=2.0)
            except Exception as ex:
                logger.warning(f"Could not forward camera_stream_url: {ex}")

        if not has_mpp and self.calibrated_mpp is not None:
            try:
                self._query_vision("set_mpp", {"mpp": self.calibrated_mpp}, timeout=2.0)
            except Exception as ex:
                logger.warning(f"Failed to re-sync MPP: {ex}")

        if not has_matrix:
            cam_saved = self.config_manager.load_section("tool_calibrator_station camera")
            if cam_saved and all(k in cam_saved for k in ("matrix_a", "matrix_b", "matrix_c", "matrix_d")):
                try:
                    ma = float(cam_saved["matrix_a"])
                    mb = float(cam_saved["matrix_b"])
                    mtx = float(cam_saved.get("matrix_tx", 0.0))
                    mc = float(cam_saved["matrix_c"])
                    md = float(cam_saved["matrix_d"])
                    mty = float(cam_saved.get("matrix_ty", 0.0))
                    self._query_vision("set_matrix", {"matrix": [[ma, mb, mtx], [mc, md, mty]]}, timeout=2.0)
                except Exception as ex:
                    logger.warning(f"Failed to re-sync matrix: {ex}")

    def _query_vision(self, endpoint: str, payload: Optional[Dict[str, Any]] = None, timeout: float = 2.0) -> Dict[str, Any]:
        """
        Sends a non-blocking JSON query to the Vision Service with strict timeout.
        Dispatches network I/O to a background thread while pumping reactor.pause()
        on the Klipper main thread to avoid blocking Klipper's reactor event loop.
        """
        url = f"{self.server_url}/{endpoint.lstrip('/')}"
        data_bytes = json.dumps(payload).encode("utf-8") if payload else None
        headers = {"Content-Type": "application/json", "User-Agent": "ToolCalibrator/1.0"}
        if getattr(self, "api_token", None):
            headers["X-API-Token"] = self.api_token
            headers["Authorization"] = f"Bearer {self.api_token}"
        if getattr(self, "session_token", None):
            headers["X-Session-Token"] = self.session_token
            headers["X-Calibration-Token"] = self.session_token

        req = urllib.request.Request(url, data=data_bytes, headers=headers)

        result_holder: List[Any] = []
        error_holder: List[Exception] = []
        done_flag = threading.Event()

        def _worker():
            try:
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    parsed = json.loads(resp.read().decode("utf-8"))
                    result_holder.append(parsed)
            except Exception as e:
                error_holder.append(e)
            finally:
                done_flag.set()

        t = threading.Thread(target=_worker, daemon=True)
        t.start()

        start_time = self.reactor.monotonic()
        while not done_flag.is_set():
            if self.reactor.monotonic() - start_time > timeout + 0.5:
                break
            if getattr(self.reactor, "pause_allowed", True):
                try:
                    self.reactor.pause(self.reactor.monotonic() + 0.05)
                except Exception:
                    done_flag.wait(0.05)
            else:
                done_flag.wait(0.05)

        if not done_flag.is_set():
            raise SafeNavigatorException(f"[ERR_CAM_101] HTTP request timed out after {timeout}s: {url}")

        if error_holder:
            ex = error_holder[0]
            if isinstance(ex, urllib.error.URLError):
                raise SafeNavigatorException(f"[ERR_CAM_101] Cannot connect to Vision Service on {url}: {ex}")
            raise SafeNavigatorException(f"[ERR_CAM_102] Vision communication error: {ex}")

        if result_holder:
            return result_holder[0]
        raise SafeNavigatorException(f"[ERR_CAM_102] Empty response received from {url}")

    def _handle_webhook_abort(self, web_request) -> None:
        """Out-of-band webhook handler to abort active calibration without waiting for G-code lock."""
        self.cancel_requested = True
        try:
            self._query_vision("abort_calibration", {}, timeout=1.0)
        except Exception:
            pass
        web_request.send({"status": "ok", "message": "Calibration abort requested."})

    def _handle_webhook_status(self, web_request) -> None:
        """Out-of-band webhook handler to query calibration status."""
        status = self.get_status(self.reactor.monotonic())
        web_request.send(status)

    def _check_cancellation(self, gcmd=None) -> None:
        """Checks if cancellation has been requested via webhook, G-code, or vision daemon."""
        if self.cancel_requested:
            raise SafeNavigatorException("Calibration aborted by user request (CALIBRATION_ABORT).")

    def _sample_burst(self, toolhead, gcmd=None, samples: Optional[int] = None) -> Optional[Dict[str, Any]]:
        """
        Samples multiple detection frames to establish a noise-filtered consensus median.
        Enforces minimum valid frame count and calibrated physical dispersion limits.
        """
        n_samples = samples or self.centering_samples
        valid_frames: List[Dict[str, Any]] = []

        for i in range(n_samples):
            self._check_cancellation(gcmd)
            if i > 0:
                self.reactor.pause(self.reactor.monotonic() + self.sample_delay)
            try:
                resp = self._query_vision("detect_nozzle", {
                    "min_matches": 1,
                    "timeout": 3.0,
                    "min_confidence": self.min_detection_confidence,
                    "min_radius": self.min_nozzle_radius,
                    "max_radius": self.max_nozzle_radius
                }, timeout=5.0)
                if resp.get("found") and resp.get("center_uv") and len(resp.get("center_uv")) >= 2:
                    conf = float(resp.get("confidence", 1.0))
                    rad = float(resp.get("radius", resp.get("radius_px", 20.0)))
                    if conf >= self.min_detection_confidence and (self.min_nozzle_radius <= rad <= self.max_nozzle_radius):
                        valid_frames.append(resp)
                    else:
                        logger.warning(
                            f"[tool_calibrator] Frame rejected by quality gate: conf={conf*100:.1f}% (min {self.min_detection_confidence*100:.1f}%), "
                            f"radius={rad:.1f}px (bounds {self.min_nozzle_radius:.1f}-{self.max_nozzle_radius:.1f}px)"
                        )
            except Exception as ex:
                logger.debug(f"[tool_calibrator] Query vision burst exception: {ex}")

        # Quality Gate 1: Require minimum valid frames in burst
        min_valid = max(1 if n_samples == 1 else 2, (n_samples // 2) + 1)
        if len(valid_frames) < min_valid:
            return {
                "found": False,
                "reason": f"Insufficient valid frames in burst ({len(valid_frames)}/{n_samples}, required min {min_valid})"
            }

        u_raw = [float(f["center_uv"][0]) for f in valid_frames]
        v_raw = [float(f["center_uv"][1]) for f in valid_frames]
        raw_spread_px = 0.0
        if len(u_raw) > 1:
            raw_spread_px = round(max(max(u_raw) - min(u_raw), max(v_raw) - min(v_raw)), 2)

        accepted_frames = valid_frames
        # Quality Gate 2: Physical dispersion threshold scaled by MPP (~0.08mm)
        mpp_val = self.calibrated_mpp or 0.040
        raw_spread_mm = round(raw_spread_px * mpp_val, 4)
        # Pixel limit derived strictly from physical limit, with a minimal quantization floor of 2.5px
        spread_px_limit = max(2.5, round(self.physical_spread_limit_mm / mpp_val, 2))

        if len(valid_frames) > 1 and raw_spread_px > spread_px_limit:
            consensus_cluster = []
            for f in valid_frames:
                u0, v0 = float(f["center_uv"][0]), float(f["center_uv"][1])
                cluster = [
                    f_other for f_other in valid_frames
                    if math.hypot(float(f_other["center_uv"][0]) - u0, float(f_other["center_uv"][1]) - v0) <= spread_px_limit
                ]
                if len(cluster) > len(consensus_cluster):
                    consensus_cluster = cluster

            # Strict majority required: strictly > total // 2 and >= 2
            if len(consensus_cluster) <= (len(valid_frames) // 2) or len(consensus_cluster) < 2:
                logger.warning(
                    f"[tool_calibrator] Inconsistent burst rejected: spread={raw_spread_px}px ({raw_spread_mm:.3f}mm, limit={self.physical_spread_limit_mm:.3f}mm / {spread_px_limit:.1f}px) across {len(valid_frames)} frames with no strict majority consensus ({len(consensus_cluster)}/{len(valid_frames)})."
                )
                return {
                    "found": False,
                    "reason": f"Inconsistent burst dispersion ({raw_spread_px}px / {raw_spread_mm:.3f}mm > {spread_px_limit:.1f}px / {self.physical_spread_limit_mm:.3f}mm) without strict majority consensus"
                }
            accepted_frames = consensus_cluster

        u_vals = [float(f["center_uv"][0]) for f in accepted_frames]
        v_vals = [float(f["center_uv"][1]) for f in accepted_frames]
        r_vals = [float(f.get("radius_px", f.get("radius", 0.0))) for f in accepted_frames if f.get("radius_px") or f.get("radius")]

        spread_px = 0.0
        if len(u_vals) > 1:
            spread_px = round(max(max(u_vals) - min(u_vals), max(v_vals) - min(v_vals)), 2)
        spread_mm = round(spread_px * mpp_val, 4)

        med_u = float(statistics.median(u_vals))
        med_v = float(statistics.median(v_vals))
        med_r = float(statistics.median(r_vals)) if r_vals else 0.0

        best_meta = accepted_frames[0]
        return {
            "found": True,
            "center_uv": [round(med_u, 2), round(med_v, 2)],
            "radius_px": round(med_r, 2),
            "confidence": best_meta.get("confidence", 0.0),
            "tier": best_meta.get("tier", 1),
            "combo": best_meta.get("combo", 0),
            "burst_count": len(accepted_frames),
            "burst_total": n_samples,
            "spread_px": spread_px,
            "spread_mm": spread_mm
        }


    def _recover_with_wiggle(self, toolhead, gcmd=None) -> Optional[Dict[str, Any]]:
        """
        Adaptive Wiggle Recovery:
        Executes 4 orthogonal micro-displacements around anchor position to break specular reflection / flare
        angles before giving up. Validates movement bounds against printer limits.
        """
        anchor_pos = toolhead.get_position()
        dist = self.wiggle_distance
        wiggle_moves = [
            (+dist, 0.0),
            (-dist, 0.0),
            (0.0, +dist),
            (0.0, -dist)
        ]

        if gcmd:
            gcmd.respond_info(f"  [Wiggle Recovery] Optical lock lost at X{anchor_pos[0]:.3f} Y{anchor_pos[1]:.3f}. Executing micro-moves (±{dist:.2f}mm)...")

        for idx, (dx, dy) in enumerate(wiggle_moves, 1):
            wx = anchor_pos[0] + dx
            wy = anchor_pos[1] + dy
            try:
                self.navigator.validate_coordinate_safety(x=wx, y=wy)
            except SafeNavigatorException:
                continue

            toolhead.manual_move([wx, wy, None], self.navigator.approach_speed)
            toolhead.wait_moves()
            self.reactor.pause(self.reactor.monotonic() + 0.12)

            burst = self._sample_burst(toolhead, gcmd, samples=max(2, self.centering_samples - 1))
            if burst and burst.get("found"):
                if gcmd:
                    gcmd.respond_info(f"  [Wiggle Recovery] Regained optical lock on attempt {idx}/4 at X{wx:.3f} Y{wy:.3f}!")
                return burst

        # If all attempts fail, safely revert to anchor position
        toolhead.manual_move([anchor_pos[0], anchor_pos[1], None], self.navigator.approach_speed)
        toolhead.wait_moves()
        if gcmd:
            gcmd.respond_info("  [Wiggle Recovery] All micro-moves exhausted; optical lock could not be regained.")
        return None

    def _center_nozzle(self, toolhead, gcmd=None, samples: Optional[int] = None, enable_wiggle: Optional[bool] = None) -> None:
        """
        Visual Servoing Centering Loop:
        Iteratively calculates pixel deviation from optical center and commands toolhead moves
        until nozzle center converges within `tolerance_mm`. Employs Burst Sampling,
        Dynamic Iteration Budgeting for large tool offsets, and Adaptive Wiggle Recovery on lost frames.
        """
        wiggle_enabled = enable_wiggle if enable_wiggle is not None else self.wiggle_on_failure

        self._ensure_vision_sync()

        dx, dy = 0.0, 0.0
        raw_dx, raw_dy = 0.0, 0.0
        max_steps = self.max_centering_iterations

        iteration = 0
        while iteration < max_steps:
            self._check_cancellation(gcmd)
            iteration += 1
            burst_resp = self._sample_burst(toolhead, gcmd, samples=samples)

            if not burst_resp or not burst_resp.get("found"):
                if wiggle_enabled:
                    burst_resp = self._recover_with_wiggle(toolhead, gcmd)

                if not burst_resp or not burst_resp.get("found"):
                    raise SafeNavigatorException(
                        f"[ERR_CV_201] Could not reliably detect nozzle center (Burst sampling and Wiggle recovery exhausted at iteration {iteration})."
                    )

            center_uv = burst_resp.get("center_uv")
            offset_resp = self._query_vision("calculate_offset", {"center_uv": center_uv})
            if offset_resp.get("abort_requested"):
                self.cancel_requested = True
                self._check_cancellation(gcmd)

            dx, dy = offset_resp.get("offset_xy", [0.0, 0.0])
            raw_err = offset_resp.get("raw_error_mm", [dx / 0.55 if abs(dx) > 1e-6 else 0.0, dy / 0.55 if abs(dy) > 1e-6 else 0.0])
            raw_dx, raw_dy = float(raw_err[0]), float(raw_err[1])

            # Dynamic Iteration Budget Allocation on Step 1 for large initial offsets (e.g. T2 with 0.865mm)
            if iteration == 1:
                initial_err = max(abs(raw_dx), abs(raw_dy))
                if initial_err > self.tolerance_mm:
                    try:
                        needed = int(math.ceil(math.log(self.tolerance_mm / initial_err) / math.log(1.0 - 0.55))) + 2
                        allocated = max(self.max_centering_iterations, min(15, needed))
                        if allocated > max_steps:
                            max_steps = allocated
                            if gcmd:
                                gcmd.respond_info(f"  [tool_calibrator] Initial offset {initial_err:.3f}mm detected. Centering budget adjusted to {max_steps} steps.")
                    except Exception:
                        pass

            tier_desc = "Tier 0 Curvature" if burst_resp.get("combo") == 10 else f"Tier {burst_resp.get('tier', 1)}"
            burst_desc = f"Burst {burst_resp.get('burst_count')}/{burst_resp.get('burst_total')} ({burst_resp.get('spread_px')}px / {burst_resp.get('spread_mm', 0.0):.3f}mm)"
            logger.debug(
                f"[tool_calibrator] Step {iteration}/{max_steps}: raw=({raw_dx:+.4f}, {raw_dy:+.4f}) damped=({dx:+.4f}, {dy:+.4f}) UV={center_uv} {burst_desc} {tier_desc}"
            )
            if gcmd:
                gcmd.respond_info(
                    f"  -> Step {iteration}/{max_steps}: Err X{raw_dx:+.3f} Y{raw_dy:+.3f}mm | Move X{dx:+.3f} Y{dy:+.3f}mm (UV: {center_uv[0]:.1f},{center_uv[1]:.1f})"
                )

            # Check convergence against true physical error before damping
            if abs(raw_dx) <= self.tolerance_mm and abs(raw_dy) <= self.tolerance_mm:
                if gcmd:
                    gcmd.respond_info(
                        f"  -> Convergence achieved at Step {iteration}: True Error X{raw_dx:+.4f}mm Y{raw_dy:+.4f}mm (within {self.tolerance_mm}mm tolerance)."
                    )
                return

            # Apply relative correction move at approach speed
            cur_pos = toolhead.get_position()
            target_x = cur_pos[0] + dx
            target_y = cur_pos[1] + dy
            self.navigator.validate_coordinate_safety(x=target_x, y=target_y)
            toolhead.manual_move([target_x, target_y, None], self.navigator.approach_speed)
            toolhead.wait_moves()

        # Final verification frame after exhausting iterations
        verify_burst = self._sample_burst(toolhead, gcmd, samples=samples)
        if verify_burst and verify_burst.get("found"):
            verify_uv = verify_burst.get("center_uv")
            verify_resp = self._query_vision("calculate_offset", {"center_uv": verify_uv})
            v_raw = verify_resp.get("raw_error_mm", verify_resp.get("offset_xy", [0.0, 0.0]))
            v_x, v_y = float(v_raw[0]), float(v_raw[1])
            if gcmd:
                gcmd.respond_info(f"  -> Centering verification: True Error X{v_x:+.4f}mm Y{v_y:+.4f}mm (tolerance: {self.tolerance_mm:.4f}mm).")
            if abs(v_x) <= self.tolerance_mm and abs(v_y) <= self.tolerance_mm:
                if gcmd:
                    gcmd.respond_info(f"  -> Final verification confirmed convergence within {self.tolerance_mm}mm.")
                return
            else:
                raw_dx, raw_dy = v_x, v_y

        raise SafeNavigatorException(
            f"[ERR_CV_202] Centering failed to converge within {self.tolerance_mm}mm after {max_steps} iterations (Final True Error: X{raw_dx:+.4f}mm Y{raw_dy:+.4f}mm)."
        )

    def _get_known_printer_tools(self) -> Set[int]:
        """Discovers all valid toolhead numbers existing in Klipper configuration."""
        known: Set[int] = set()

        # 1. Query toolchanger object if present
        has_toolchanger_tools = False
        tc = self.printer.lookup_object("toolchanger", None)
        if tc is not None:
            tool_nums = getattr(tc, "tool_numbers", None)
            if tool_nums and isinstance(tool_nums, (list, tuple, set)):
                for n in tool_nums:
                    try:
                        known.add(int(n))
                        has_toolchanger_tools = True
                    except (ValueError, TypeError):
                        pass
            tools_dict = getattr(tc, "tools", None)
            if tools_dict and isinstance(tools_dict, dict):
                for k in tools_dict.keys():
                    try:
                        known.add(int(str(k).lstrip("tT")))
                        has_toolchanger_tools = True
                    except (ValueError, TypeError):
                        pass

        # 2. Inspect registered printer objects
        names = []
        if callable(getattr(self.printer, "lookup_objects", None)):
            try:
                ret = self.printer.lookup_objects()
                if isinstance(ret, dict):
                    names = list(ret.keys())
                elif isinstance(ret, (list, tuple)):
                    for item in ret:
                        if isinstance(item, (tuple, list)) and len(item) > 0:
                            names.append(str(item[0]))
                        elif isinstance(item, str):
                            names.append(item)
            except Exception:
                pass

        if not names:
            objs = getattr(self.printer, "objects", {})
            if isinstance(objs, dict):
                names = list(objs.keys())

        has_explicit_tools = False
        for name in names:
            m_tool = re.match(r"^tool\s+(?:t)?(\d+)$", name, re.IGNORECASE)
            if m_tool:
                known.add(int(m_tool.group(1)))
                has_explicit_tools = True
                continue
            m_macro = re.match(r"^gcode_macro\s+t(\d+)$", name, re.IGNORECASE)
            if m_macro:
                known.add(int(m_macro.group(1)))
                has_explicit_tools = True
                continue

        # Only scan extruder(\d*) if NO toolchanger or explicit tool/macro definitions were found
        if not has_toolchanger_tools and not has_explicit_tools:
            for name in names:
                m_ext = re.match(r"^extruder(\d*)$", name)
                if m_ext:
                    idx = int(m_ext.group(1)) if m_ext.group(1) else 0
                    known.add(idx)

        # Fallback to T0 if single extruder setup exists on printer
        if not known:
            for name in names:
                if name.startswith("extruder"):
                    known.add(0)
                    break

        return known


    def _discover_tools(self, tools_param: Optional[str] = None) -> List[int]:
        """
        Discovers and validates toolhead sequence:
        1. Explicit TOOLS parameter (e.g. TOOLS=1 or TOOLS=0,1,2,3).
        2. Configured tools in [tool_calibrator] (e.g. tools: 0, 1, 2, 3).
        3. [toolchanger] object (toolchanger.tool_numbers or toolchanger.tools).
        4. Auto-scanned Klipper objects: [tool 0], [tool 1], ... or [gcode_macro T0], ...
        5. Fallback to reference_tool.
        Rejects any requested tool not present on the printer.
        """
        known_tools = self._get_known_printer_tools()

        if self.reference_tool not in known_tools:
            raise SafeNavigatorException(
                f"[ERR_TOOL_NOT_FOUND] Configured reference_tool T{self.reference_tool} does not exist in printer configuration. Available tools: {sorted(known_tools) if known_tools else 'None'}"
            )

        if tools_param is not None:
            try:
                selected = [int(p.strip()) for p in str(tools_param).split(",") if p.strip()]
            except ValueError:
                raise SafeNavigatorException(f"Invalid TOOLS parameter: '{tools_param}'. Must be comma-separated integers.")
            if not selected:
                raise SafeNavigatorException("TOOLS parameter cannot be empty.")
            for t in selected:
                if t not in known_tools:
                    raise SafeNavigatorException(
                        f"[ERR_TOOL_NOT_FOUND] Tool T{t} does not exist in printer configuration. Available tools: {sorted(known_tools)}"
                    )
            # Deduplicate while preserving order, ensuring reference_tool is strictly at index 0
            seen = set()
            deduped = [t for t in selected if not (t in seen or seen.add(t))]
            return [self.reference_tool] + [t for t in deduped if t != self.reference_tool]


        # Configured tools in [tool_calibrator]
        if self.configured_tools_str:
            try:
                cfg_tools = [int(p.strip()) for p in str(self.configured_tools_str).split(",") if p.strip()]
                if cfg_tools:
                    for t in cfg_tools:
                        if t not in known_tools:
                            raise SafeNavigatorException(
                                f"[ERR_TOOL_NOT_FOUND] Configured Tool T{t} does not exist in printer configuration. Available tools: {sorted(known_tools)}"
                            )
                    seen_cfg = set()
                    deduped_cfg = [t for t in cfg_tools if not (t in seen_cfg or seen_cfg.add(t))]
                    return [self.reference_tool] + [t for t in deduped_cfg if t != self.reference_tool]
            except ValueError:
                pass

        # Discovered tools from printer configuration
        if known_tools:
            return [self.reference_tool] + [t for t in sorted(known_tools) if t != self.reference_tool]

        return [self.reference_tool]

    def _run_tool_hook(self, hook_template, tool_no: int) -> None:
        """Executes a Klipper macro hook template providing the full standard printer context."""
        if hook_template is None:
            return
        context: Dict[str, Any] = {}
        if hasattr(self.gcode_macro, "create_template_context"):
            try:
                context = self.gcode_macro.create_template_context()
            except Exception:
                context = {}
        context["TOOL"] = tool_no
        context["params"] = {"TOOL": str(tool_no)}
        hook_template.run_gcode_from_command(context)

    def _set_inspection_lighting(self, enable: bool, tool_no: int = 0) -> None:
        """
        Manages optical lighting:
        - When enable=True: turns ON camera ring LED and turns OFF toolhead nozzle LED.
        - When enable=False: turns OFF camera ring LED and restores toolhead nozzle LED.
        """
        try:
            if enable:
                if self.camera_pin:
                    self.gcode.run_script_from_command(f"SET_PIN PIN={self.camera_pin} VALUE={self.camera_led_brightness}")
                else:
                    macro = self.printer.lookup_object(f"gcode_macro {self.camera_led_macro_on}", None)
                    if macro is not None:
                        self.gcode.run_script_from_command(f"{self.camera_led_macro_on}")

                nozzle_off_macro = self.printer.lookup_object(f"gcode_macro {self.nozzle_led_macro_off}", None)
                if nozzle_off_macro is not None:
                    self.gcode.run_script_from_command(f"{self.nozzle_led_macro_off} TOOL={tool_no}")
            else:
                if self.camera_pin:
                    self.gcode.run_script_from_command(f"SET_PIN PIN={self.camera_pin} VALUE=0")
                else:
                    macro = self.printer.lookup_object(f"gcode_macro {self.camera_led_macro_off}", None)
                    if macro is not None:
                        self.gcode.run_script_from_command(f"{self.camera_led_macro_off}")

                nozzle_on_macro = self.printer.lookup_object(f"gcode_macro {self.nozzle_led_macro_on}", None)
                if nozzle_on_macro is not None:
                    self.gcode.run_script_from_command(f"{self.nozzle_led_macro_on} TOOL={tool_no}")
        except Exception as ex:
            logger.debug(f"[tool_calibrator] Lighting control error: {ex}")

    def _get_active_tool_no(self) -> Optional[int]:
        """
        Determines the active tool number from toolchanger if initialized.
        Strictly returns None if the toolchanger is uninitialized, unmounted (-1), or unknown.
        Does NOT guess T0 or extrapolate from extruder name.
        """
        tc = self.printer.lookup_object("toolchanger", None)
        if tc is not None:
            # 1. Primary check: toolchanger.tool_number
            t_num = getattr(tc, "tool_number", None)
            if t_num is not None and not hasattr(t_num, "_mock_name"):
                try:
                    val = int(t_num)
                    if val >= 0:
                        return val
                except (ValueError, TypeError):
                    pass

            # 2. Check tc.active_tool
            active_tool = getattr(tc, "active_tool", None)
            if active_tool is not None and not hasattr(active_tool, "_mock_name"):
                if isinstance(active_tool, int) and active_tool >= 0:
                    return active_tool
                t_num = getattr(active_tool, "tool_number", None)
                if t_num is not None and not hasattr(t_num, "_mock_name"):
                    try:
                        val = int(t_num)
                        if val >= 0:
                            return val
                    except (ValueError, TypeError):
                        pass

        return None

    def _reconcile_toolchanger_state(self, initial_tool: Optional[int] = None, gcmd=None) -> None:
        """
        Reconciles toolchanger logical state to the physically active/detected tool
        upon failure or aborted runs to prevent uninitialized toolchanger state (-1).
        Only reconciles when reliable physical sensor detection is available.
        Never executes a Tn physical tool change command as cleanup.
        """
        try:
            tc = self.printer.lookup_object("toolchanger", None)
            if tc is None:
                return

            target_tool: Optional[int] = None

            # 1. Query physical sensor detection if available on toolchanger
            for det_method in ("detect_tool", "get_detected_tool"):
                meth = getattr(tc, det_method, None)
                if callable(meth):
                    try:
                        detected = meth()
                        if detected is not None and detected != -1:
                            if hasattr(detected, "tool_number"):
                                target_tool = int(detected.tool_number)
                            elif isinstance(detected, int) and detected >= 0:
                                target_tool = detected
                            break
                    except Exception:
                        pass

            # 2. Check sensor attributes on tc
            if target_tool is None:
                for attr in ("detected_tool", "detected_tool_number", "physical_tool", "current_tool"):
                    val = getattr(tc, attr, None)
                    if val is not None and val != -1:
                        if hasattr(val, "tool_number"):
                            try:
                                target_tool = int(val.tool_number)
                                break
                            except (ValueError, TypeError):
                                pass
                        elif isinstance(val, int) and val >= 0:
                            target_tool = val
                            break
                        else:
                            try:
                                parsed_val = int(val)
                                if parsed_val >= 0:
                                    target_tool = parsed_val
                                    break
                            except (ValueError, TypeError):
                                pass

            # Check get_status for detected_tool
            if target_tool is None:
                get_status_fn = getattr(tc, "get_status", None)
                if callable(get_status_fn):
                    try:
                        status_data = get_status_fn(None)
                        if isinstance(status_data, dict):
                            det = status_data.get("detected_tool")
                            if det is not None and det != -1:
                                if hasattr(det, "tool_number"):
                                    target_tool = int(det.tool_number)
                                elif isinstance(det, int) and det >= 0:
                                    target_tool = det
                    except Exception:
                        pass

            # 3. Check toollock object if present
            if target_tool is None:
                toollock = self.printer.lookup_object("toollock", None)
                if toollock is not None:
                    for attr in ("detected_tool", "detected_tool_number", "current_tool", "tool_current"):
                        val = getattr(toollock, attr, None)
                        if val is not None and val != -1:
                            if hasattr(val, "tool_number"):
                                try:
                                    target_tool = int(val.tool_number)
                                    break
                                except (ValueError, TypeError):
                                    pass
                            elif isinstance(val, int) and val >= 0:
                                target_tool = val
                                break
                            else:
                                try:
                                    parsed_val = int(val)
                                    if parsed_val >= 0:
                                        target_tool = parsed_val
                                        break
                                except (ValueError, TypeError):
                                    pass

            # Only reconcile when sensor data is reliable. DO NOT guess T0 or extrapolate without sensors!
            if target_tool is None:
                warn_msg = (
                    "Warning: Toolchanger state reconciliation cannot proceed automatically: "
                    "no physical tool sensor detection was available. "
                    "Manual INITIALIZE_TOOLCHANGER or operator recovery is required."
                )
                if gcmd is not None:
                    gcmd.respond_info(warn_msg)
                logger.warning(warn_msg)
                return

            tc_status = getattr(tc, "status", None)
            tc_tool_num = getattr(tc, "tool_number", None)
            tc_active = getattr(tc, "active_tool", None)

            needs_reconciliation = (
                tc_status == "uninitialized"
                or tc_tool_num == -1
                or tc_active is None
            )

            if needs_reconciliation:
                logger.info(f"[tool_calibrator] Reconciling uninitialized toolchanger state to T{target_tool}")
                if is_command_registered(self.gcode, "INITIALIZE_TOOLCHANGER"):
                    try:
                        self.gcode.run_script_from_command(f"INITIALIZE_TOOLCHANGER T={target_tool}")
                        logger.info(f"[tool_calibrator] Executed INITIALIZE_TOOLCHANGER T={target_tool}")
                    except Exception as cmd_err:
                        logger.warning(f"[tool_calibrator] INITIALIZE_TOOLCHANGER T={target_tool} failed: {cmd_err}")

                cur_st = getattr(tc, "status", None)
                cur_num = getattr(tc, "tool_number", None)
                if (cur_st == "uninitialized" or cur_num == -1) and hasattr(tc, "initialize"):
                    tools_dict = getattr(tc, "tools", {})
                    tool_obj = tools_dict.get(target_tool) if isinstance(tools_dict, dict) else None
                    if tool_obj is not None:
                        try:
                            tc.initialize(tool_obj)
                            logger.info(f"[tool_calibrator] Initialized toolchanger via tc.initialize(tool_obj)")
                        except Exception as obj_err:
                            logger.warning(f"[tool_calibrator] tc.initialize failed: {obj_err}")

                # Note: NEVER issue a Tn command during failure cleanup to avoid unintended physical moves!

                post_status = getattr(tc, "status", None)
                post_tool_num = getattr(tc, "tool_number", None)
                is_still_uninit = (
                    post_status == "uninitialized"
                    or post_tool_num == -1
                )

                if not is_still_uninit and (post_tool_num == target_tool or (post_status is not None and post_status != "uninitialized")):
                    msg = f"[tool_calibrator] Toolchanger state reconciled to physical tool T{target_tool} (status: {post_status})."
                    if gcmd is not None:
                        gcmd.respond_info(msg)
                    logger.info(msg)
                else:
                    warn_msg = (
                        f"!! [tool_calibrator] Warning: Toolchanger state reconciliation to T{target_tool} "
                        f"could not be confirmed (status={post_status}, tool_number={post_tool_num}). "
                        "Manual INITIALIZE_TOOLCHANGER or homing may be required."
                    )
                    if gcmd is not None:
                        gcmd.respond_info(warn_msg)
                    logger.warning(warn_msg)
        except Exception as rec_err:
            logger.warning(f"[tool_calibrator] Failed to reconcile toolchanger state: {rec_err}")

    def _check_camera_thermal_safety(self, tool_no: Optional[int] = None, gcmd = None) -> None:
        """Ensures nozzle temperature does not exceed safe optical camera limit (100C) to prevent lens fogging/damage."""
        extruder = None

        if tool_no is None:
            try:
                toolhead = self.printer.lookup_object("toolhead", None)
                if toolhead is not None and hasattr(toolhead, "get_extruder"):
                    extruder = toolhead.get_extruder()
            except Exception:
                pass
            if extruder is None:
                tool_no = self._get_active_tool_no()
        # 1. Inspect toolchanger object if present for custom extruder name
        tc = self.printer.lookup_object("toolchanger", None)
        if tc is not None:
            tools = getattr(tc, "tools", {})
            tool_obj = tools.get(tool_no) if isinstance(tools, dict) else None
            if tool_obj is not None:
                ext_name = getattr(tool_obj, "extruder_name", None)
                if ext_name:
                    extruder = self.printer.lookup_object(ext_name, None)

        # 2. Check individual [tool <number>] object
        if extruder is None:
            tool_obj = self.printer.lookup_object(f"tool {tool_no}", None)
            if tool_obj is not None:
                ext_name = getattr(tool_obj, "extruder_name", None)
                if ext_name:
                    extruder = self.printer.lookup_object(ext_name, None)

        # 3. Standard Klipper naming fallback: extruder / extruder<number>
        if extruder is None:
            std_name = f"extruder{tool_no}" if tool_no > 0 else "extruder"
            extruder = self.printer.lookup_object(std_name, None)

        # 4. Check active toolhead extruder fallback if tool is currently active
        if extruder is None:
            try:
                toolhead = self.printer.lookup_object("toolhead", None)
                if toolhead is not None and hasattr(toolhead, "get_extruder"):
                    active_ext = toolhead.get_extruder()
                    if active_ext is not None and tool_no == self._get_active_tool_no():
                        extruder = active_ext
            except Exception:
                pass

        # Fail-closed: If extruder cannot be identified or status verified, raise error
        if extruder is None or not hasattr(extruder, "get_status"):
            err_msg = (
                f"[ERR_PRE_002] Cannot determine extruder or verify thermal sensor for Tool T{tool_no}. "
                "Optical safety check is fail-closed to prevent lens heat damage."
            )
            if gcmd is not None:
                raise gcmd.error(err_msg)
            raise SafeNavigatorException(err_msg)

        try:
            status = extruder.get_status(self.reactor.monotonic())
            temp = status.get("temperature", None)
            if temp is None:
                heater = getattr(extruder, "heater", None)
                if heater is not None and hasattr(heater, "get_status"):
                    temp = heater.get_status(self.reactor.monotonic()).get("temperature", None)

            if temp is None:
                err_msg = (
                    f"[ERR_PRE_002] Unable to read temperature for Tool T{tool_no}. "
                    "Optical safety check is fail-closed to prevent camera damage."
                )
                if gcmd is not None:
                    raise gcmd.error(err_msg)
                raise SafeNavigatorException(err_msg)

            if temp > self.max_camera_temp:
                err_msg = (
                    f"[ERR_PRE_002] Tool T{tool_no} nozzle temperature ({temp:.1f}C) "
                    f"exceeds safe camera limit ({self.max_camera_temp:.1f}C). "
                    f"Allow nozzle to cool before entering camera station."
                )
                if gcmd is not None:
                    raise gcmd.error(err_msg)
                raise SafeNavigatorException(err_msg)
        except (SafeNavigatorException, Exception) as ex:
            if "ERR_PRE_002" in str(ex):
                raise
            err_msg = f"[ERR_PRE_002] Thermal safety check failed for Tool T{tool_no}: {ex}"
            if gcmd is not None:
                raise gcmd.error(err_msg)
            raise SafeNavigatorException(err_msg)


    def _execute_xy_calibration(self, tool_no: int, toolhead, gcode_move, gcmd, reference_origin_xy: Optional[List[float]], tool_offsets: Dict[str, float], target_focal_z: Optional[float] = None, samples: Optional[int] = None, enable_wiggle: Optional[bool] = None) -> List[float]:
        """Executes optical camera alignment for a single tool."""
        self._check_camera_thermal_safety(tool_no, gcmd)
        gcmd.respond_info(f"[T{tool_no}] Entering Camera Station...")
        self._set_inspection_lighting(True, tool_no)
        try:
            self.navigator.approach_camera(toolhead, gcode_move, target_z=target_focal_z)
            self._center_nozzle(toolhead, gcmd, samples=samples, enable_wiggle=enable_wiggle)
            raw_pos = toolhead.get_position()

            if tool_no == self.reference_tool:
                ref_xy = [raw_pos[0], raw_pos[1]]
                gcmd.respond_info(f"[T{tool_no}] Reference Optical Origin set to X{raw_pos[0]:.3f} Y{raw_pos[1]:.3f}")
                tool_offsets["x"] = 0.0
                tool_offsets["y"] = 0.0
                self.navigator.depart_station(toolhead, gcode_move)
                return ref_xy
            else:
                if reference_origin_xy is None:
                    raise gcmd.error("[tool_calibrator] Reference origin not established before secondary tool.")
                dx = round(raw_pos[0] - reference_origin_xy[0], 3)
                dy = round(raw_pos[1] - reference_origin_xy[1], 3)
                tool_offsets["x"] = dx
                tool_offsets["y"] = dy
                gcmd.respond_info(f"[T{tool_no}] Calculated XY Offsets: X{dx:+.3f}mm Y{dy:+.3f}mm")
                self.navigator.depart_station(toolhead, gcode_move)
                return reference_origin_xy
        finally:
            self._set_inspection_lighting(False, tool_no)

    def _lookup_tool_xy_offset(self, tool_no: int) -> Tuple[float, float]:
        """
        Resolve XY offset for a secondary tool using a 3-tier fallback hierarchy:
        1. In-memory cached_offsets from previous calibration runs
        2. Persisted tool_offsets from tool_offsets.cfg
        3. Live Klipper [tool Tn] objects or toolchanger runtime
        Preserves valid 0.0 offsets independently on each axis without confusing 0.0 with missing data.
        """
        x: Optional[float] = None
        y: Optional[float] = None

        # Tier 1: in-memory cache
        cached = self.cached_offsets.get(tool_no, {})
        if "x" in cached and cached["x"] is not None:
            try:
                x = float(cached["x"])
            except (ValueError, TypeError):
                pass
        if "y" in cached and cached["y"] is not None:
            try:
                y = float(cached["y"])
            except (ValueError, TypeError):
                pass

        # Tier 2: Persisted tool_offsets
        if x is None or y is None:
            to_obj = self.printer.lookup_object("tool_offsets", None)
            if to_obj is not None and hasattr(to_obj, "parse_tool_offsets"):
                try:
                    saved = to_obj.parse_tool_offsets().get(tool_no, {})
                    if x is None and "x" in saved and saved["x"] is not None:
                        x = float(saved["x"])
                    if y is None and "y" in saved and saved["y"] is not None:
                        y = float(saved["y"])
                except Exception:
                    pass

        # Tier 3: Klipper [tool Tn] objects or toolchanger runtime
        if x is None or y is None:
            tool_obj = None
            for lookup_name in [f"tool {tool_no}", f"tool T{tool_no}", f"tool t{tool_no}"]:
                tool_obj = self.printer.lookup_object(lookup_name, None)
                if tool_obj is not None:
                    break
            if tool_obj is None:
                tc = self.printer.lookup_object("toolchanger", None)
                if tc is not None:
                    tc_tools = getattr(tc, "tools", None)
                    if isinstance(tc_tools, dict):
                        tool_obj = tc_tools.get(tool_no) or tc_tools.get(f"T{tool_no}")

            if tool_obj is not None:
                if x is None:
                    gx = getattr(tool_obj, "gcode_x_offset", None)
                    if isinstance(gx, (int, float)) or (isinstance(gx, str) and str(gx).strip()):
                        try:
                            x = float(gx)
                        except (ValueError, TypeError):
                            pass
                    elif hasattr(tool_obj, "offset"):
                        offs = getattr(tool_obj, "offset", None)
                        if isinstance(offs, (list, tuple)) and len(offs) > 0:
                            elem = offs[0]
                            if isinstance(elem, (int, float)) or (isinstance(elem, str) and str(elem).strip()):
                                try:
                                    x = float(elem)
                                except (ValueError, TypeError):
                                    pass
                if y is None:
                    gy = getattr(tool_obj, "gcode_y_offset", None)
                    if isinstance(gy, (int, float)) or (isinstance(gy, str) and str(gy).strip()):
                        try:
                            y = float(gy)
                        except (ValueError, TypeError):
                            pass
                    elif hasattr(tool_obj, "offset"):
                        offs = getattr(tool_obj, "offset", None)
                        if isinstance(offs, (list, tuple)) and len(offs) > 1:
                            elem = offs[1]
                            if isinstance(elem, (int, float)) or (isinstance(elem, str) and str(elem).strip()):
                                try:
                                    y = float(elem)
                                except (ValueError, TypeError):
                                    pass

        return (0.0 if x is None else x), (0.0 if y is None else y)

    def _execute_z_calibration(self, tool_no: int, toolhead, gcode_move, gcmd, reference_z_result: Dict[str, Any], tool_offsets: Dict[str, float]) -> Dict[str, Any]:
        """Executes Z probing alignment for a single tool with XY offset compensation."""
        meas_ref = getattr(self.z_backend, "measurement_reference", "nozzle")
        allow_shuttle = self.allow_shuttle_z or (getattr(gcmd, "get_int", None) and gcmd.get_int("ALLOW_SHUTTLE_Z", 0) == 1)
        if meas_ref != "nozzle" and not allow_shuttle:
            raise gcmd.error(
                f"[ERR_Z_003] The active Z backend '{self.z_backend_type}' has measurement_reference='{meas_ref}'. "
                "A shuttle-mounted probe cannot observe individual nozzle tip lengths across tool changes. "
                "Per-tool Z calibration is rejected to prevent applying invalid Z offsets. "
                "Use a nozzle-contact switch backend (e.g. PF2 switch/Axiscope) or pass ALLOW_SHUTTLE_Z=1 if experimenting."
            )

        # Derive XY compensation for secondary tool so nozzle hits exact center
        # Check presence per-axis: 0.0 is a valid calibrated offset and must NOT be replaced by fallback!
        has_x = "x" in tool_offsets and tool_offsets["x"] is not None
        has_y = "y" in tool_offsets and tool_offsets["y"] is not None

        offset_x = float(tool_offsets["x"]) if has_x else 0.0
        offset_y = float(tool_offsets["y"]) if has_y else 0.0

        if tool_no != self.reference_tool and (not has_x or not has_y):
            fallback_x, fallback_y = self._lookup_tool_xy_offset(tool_no)
            if not has_x:
                offset_x = fallback_x
            if not has_y:
                offset_y = fallback_y

        offset_xy = (offset_x, offset_y) if (tool_no != self.reference_tool and (offset_x != 0.0 or offset_y != 0.0)) else None
        if offset_xy is not None:
            gcmd.respond_info(f"[T{tool_no}] Compensating Z-probe position with XY offsets: X{offset_x:+.3f}mm Y{offset_y:+.3f}mm")

        if self.z_backend_type == "switch":
            self.navigator.approach_switch(toolhead, gcode_move, offset_xy=offset_xy)
        else:
            if getattr(self.navigator, "carto_speedup", False) or not self.navigator.is_safe_z_enabled():
                logger.info(f"[T{tool_no}] Cartographer speed-up active (safe_z omitted/disabled): fast local Z-probing without redundant safe_z lift.")
                cur_pos = toolhead.get_position()
                if cur_pos[2] < 2.0:
                    toolhead.manual_move([None, None, 2.0], self.navigator.z_speed)
                    toolhead.wait_moves()
            else:
                self.navigator.move_to_safe_z(toolhead, gcode_move)
            if tool_no == self.reference_tool:
                probe_x, probe_y = self.z_backend.get_probe_xy()
                gcmd.respond_info(f"[T{tool_no}] Moving to Z-probe coordinates: X{probe_x:.3f} Y{probe_y:.3f}")
                self.navigator.validate_coordinate_safety(x=probe_x, y=probe_y)
                toolhead.manual_move([probe_x, probe_y, None], self.navigator.travel_speed)
                toolhead.wait_moves()
            else:
                active_ref = reference_z_result or getattr(self, "cached_reference_z_result", None)
                ref_xy = active_ref.get("probe_xy") if isinstance(active_ref, dict) else None
                if ref_xy and len(ref_xy) >= 2:
                    base_x, base_y = float(ref_xy[0]), float(ref_xy[1])
                else:
                    base_x, base_y = self.z_backend.get_probe_xy()

                target_x = base_x + (offset_x if offset_xy else 0.0)
                target_y = base_y + (offset_y if offset_xy else 0.0)
                if offset_xy:
                    gcmd.respond_info(
                        f"[T{tool_no}] Moving to Z-probe coordinates: X{target_x:.3f} Y{target_y:.3f} "
                        f"(Compensated from baseline X{base_x:.3f} Y{base_y:.3f})"
                    )
                else:
                    gcmd.respond_info(f"[T{tool_no}] Moving to Z-probe coordinates: X{target_x:.3f} Y{target_y:.3f}")
                self.navigator.validate_coordinate_safety(x=target_x, y=target_y)
                toolhead.manual_move([target_x, target_y, None], self.navigator.travel_speed)
                toolhead.wait_moves()

        if tool_no == self.reference_tool:
            ref_z = self.z_backend.probe_reference_tool(tool_no, gcmd)
            pos = toolhead.get_position()
            actual_ref_x = ref_z.get("probe_x", round(pos[0], 3))
            actual_ref_y = ref_z.get("probe_y", round(pos[1], 3))
            ref_z["probe_x"] = actual_ref_x
            ref_z["probe_y"] = actual_ref_y
            ref_z["probe_xy"] = (actual_ref_x, actual_ref_y)
            gcmd.respond_info(f"[T{tool_no}] Reference Z baseline established at X{actual_ref_x:.3f} Y{actual_ref_y:.3f} ({ref_z.get('source')})")
            tool_offsets["z"] = 0.0
            self.cached_reference_z_result = ref_z
            if not self.navigator.carto_speedup:
                self.navigator.depart_station(toolhead, gcode_move)
            return ref_z
        else:
            active_ref = reference_z_result or getattr(self, "cached_reference_z_result", None)
            has_ref = (
                isinstance(active_ref, dict)
                and (
                    active_ref.get("contact_z") is not None
                    or active_ref.get("baseline_z") is not None
                    or active_ref.get("source") is not None
                )
            )
            if not has_ref:
                raise gcmd.error(
                    f"[ERR_CAL_002] Reference tool T{self.reference_tool} Z baseline must be measured first before secondary tools. "
                    "Please calibrate the reference tool or include it in the TOOLS parameter."
                )
            z_res = self.z_backend.probe_secondary_tool(tool_no, active_ref, gcmd)

            # Same-point coordinate verification check
            if self.z_backend_type != "switch":
                ref_xy = active_ref.get("probe_xy")
                if ref_xy and len(ref_xy) >= 2:
                    base_x, base_y = float(ref_xy[0]), float(ref_xy[1])
                    expected_x = base_x + (offset_x if offset_xy else 0.0)
                    expected_y = base_y + (offset_y if offset_xy else 0.0)
                    res_xy = z_res.get("probe_xy")
                    if res_xy and isinstance(res_xy, (list, tuple)) and len(res_xy) >= 2:
                        actual_x = float(res_xy[0])
                        actual_y = float(res_xy[1])
                    else:
                        actual_x = float(z_res.get("probe_x", toolhead.get_position()[0]))
                        actual_y = float(z_res.get("probe_y", toolhead.get_position()[1]))
                    dev_x = abs(actual_x - expected_x)
                    dev_y = abs(actual_y - expected_y)
                    if dev_x > 0.5 or dev_y > 0.5:
                        raise gcmd.error(
                            f"[ERR_Z_004] Probe coordinate mismatch: Tool T{tool_no} probed at ({actual_x:.3f}, {actual_y:.3f}), "
                            f"differing from Reference T{self.reference_tool} target ({expected_x:.3f}, {expected_y:.3f}) by dx={dev_x:.3f}mm, dy={dev_y:.3f}mm (tolerance: 0.5mm)."
                        )

            tool_offsets["z"] = z_res.get("suggested_z_offset", 0.0)
            gcmd.respond_info(f"[T{tool_no}] Calculated Z Offset: Z{tool_offsets['z']:+.3f}mm")
            if not self.navigator.carto_speedup:
                self.navigator.depart_station(toolhead, gcode_move)
            return active_ref

    def _parse_bool_param(self, gcmd, param_name: str, default: bool) -> bool:
        val = gcmd.get(param_name, None)
        if val is None:
            return default
        val_str = str(val).strip().lower()
        if val_str in ("1", "true", "yes", "on"):
            return True
        elif val_str in ("0", "false", "no", "off"):
            return False
        else:
            raise gcmd.error(f"[tool_calibrator] Invalid boolean value for {param_name}='{val}'. Expected 0, 1, True, or False.")

    def cmd_CALIBRATE_TOOL_OFFSETS(self, gcmd) -> None:
        """
        Full 1-Click End-to-End Calibration Command.
        Parameters:
            CALIBRATE_XY (int): 1 to run XY vision (default: 1)
            CALIBRATE_Z (int): 1 to run Z probing (default: 1)
            SAVE_CONFIG (int): 1 to persist offsets to disk (default: 1)
            DRY_RUN (int): 1 to simulate without physical touch or disk saves (default: 0)
            CLEAN_NOZZLE (int): 1 to run nozzle cleaning hook prior to vision if available (default: 0)
            ORDER (str): 'XY_FIRST' (default) or 'Z_FIRST'
            COMPENSATE_FOCAL_Z (int): 1 to adjust camera height by measured Z offset (default: 0)
            TOOLS (str): Comma-separated list of tool indices (e.g. TOOLS=1 or TOOLS=1,2)
            SAMPLES (int): Number of burst sampling frames per centering step (default: 3)
            WIGGLE (int): 1 to enable adaptive wiggle recovery, 0 to disable (default: 1)
        """
        calibrate_xy = self._parse_bool_param(gcmd, "CALIBRATE_XY", True)
        calibrate_z = self._parse_bool_param(gcmd, "CALIBRATE_Z", True)
        save_config = self._parse_bool_param(gcmd, "SAVE_CONFIG", True)
        dry_run = self._parse_bool_param(gcmd, "DRY_RUN", False)
        clean_nozzle = self._parse_bool_param(gcmd, "CLEAN_NOZZLE", False)
        order = gcmd.get("ORDER", "XY_FIRST").upper()
        if order not in ("XY_FIRST", "Z_FIRST"):
            raise gcmd.error(f"[tool_calibrator] Invalid ORDER='{order}'. Valid options are 'XY_FIRST' or 'Z_FIRST'.")
        compensate_focal_z = self._parse_bool_param(gcmd, "COMPENSATE_FOCAL_Z", False)
        continue_on_error = self._parse_bool_param(gcmd, "CONTINUE_ON_ERROR", False)
        restore_tool = self._parse_bool_param(gcmd, "RESTORE_TOOL", True)
        tools_param = gcmd.get("TOOLS", None)
        samples_param = gcmd.get_int("SAMPLES", self.centering_samples)
        if samples_param < 3:
            gcmd.respond_info(f"[tool_calibrator] Notice: Clamping SAMPLES from {samples_param} to 3 for calibration reliability.")
            samples_param = 3
        elif samples_param > 20:
            gcmd.respond_info(f"[tool_calibrator] Notice: Clamping SAMPLES from {samples_param} to 20 for calibration reliability.")
            samples_param = 20
        wiggle_param = self._parse_bool_param(gcmd, "WIGGLE", self.wiggle_on_failure)

        toolhead = self.printer.lookup_object("toolhead")
        gcode_move = self.printer.lookup_object("gcode_move")

        if not self.navigator.is_homed():
            raise gcmd.error("[ERR_PRE_001] Printer must be fully homed (G28) before calibration.")

        self.cancel_requested = False
        import time as _sys_time
        import os
        run_id = f"run_{int(_sys_time.time())}"
        active_t = self._get_active_tool_no()
        start_mono = self.reactor.monotonic()
        self.run_record = {
            "run_id": run_id,
            "state": "RUNNING",
            "phase": "INITIALIZING",
            "calibrating_tool": None,
            "physical_tool": active_t,
            "active_tool": active_t,
            "start_time": _sys_time.time(),
            "start_monotonic": start_mono,
            "end_time": None,
            "duration_sec": 0.0,
            "error": None,
            "completed_tools": [],
            "offsets": {},
            "valid": False,
            "tool_errors": {}
        }
        self.last_run_status = "RUNNING"

        if dry_run:
            gcmd.respond_info(
                "\n*** [tool_calibrator] DRY RUN MODE ACTIVE ***\n"
                "  -> Optical centering and offset measurements WILL be performed.\n"
                "  -> NO offsets will be saved to disk, and NO offsets will be applied to toolchanger runtime.\n"
                "  -> Physical Z switch probing touch is skipped.\n"
            )

        session_token = None
        active_t = None
        try:
            if not calibrate_xy and not calibrate_z:
                raise gcmd.error(
                    "[tool_calibrator] Neither XY nor Z calibration was selected (CALIBRATE_XY=0 and CALIBRATE_Z=0). "
                    "At least one calibration measurement must be enabled."
                )

            # Pre-flight ping to vision service & session lock
            if calibrate_xy:
                self._ensure_vision_sync()
                unique_session = f"klipper_{int(_sys_time.time()*1000)}_{os.getpid()}"
                lock_resp = self._query_vision("acquire_lock", {"client_id": "klipper", "session_id": unique_session, "timeout_seconds": 600}, timeout=2.0)
                session_token = lock_resp.get("session_token") or lock_resp.get("session_id")
                self.session_token = session_token
                health = self._query_vision("health")
                commit_info = f" (commit: {health.get('commit')})" if health.get('commit') and health.get('commit') != 'unknown' else ""
                gcmd.respond_info(f"[tool_calibrator] Vision Service connected: {health.get('service')} v{health.get('version')}{commit_info}")

            # Dynamic inheritance check for switch coordinates
            if calibrate_z and self.z_backend_type == "switch":
                self._sync_switch_location_from_tools_calibrate()

            # Pre-flight check for Z backend capability
            is_experimental_shuttle_z = False
            if calibrate_z and not dry_run:
                meas_ref = getattr(self.z_backend, "measurement_reference", "nozzle")
                allow_shuttle = self.allow_shuttle_z or self._parse_bool_param(gcmd, "ALLOW_SHUTTLE_Z", False)
                if meas_ref != "nozzle":
                    if not allow_shuttle:
                        raise gcmd.error(
                            f"[ERR_Z_003] The active Z backend '{self.z_backend_type}' has measurement_reference='{meas_ref}'. "
                            "A shuttle-mounted probe cannot observe individual nozzle tip lengths across tool changes. "
                            "Per-tool Z calibration is rejected to prevent applying invalid Z offsets. "
                            "Use a nozzle-contact switch backend (e.g. PF2 switch/Axiscope) or pass ALLOW_SHUTTLE_Z=1 if experimenting."
                        )
                    else:
                        is_experimental_shuttle_z = True
                        if save_config:
                            gcmd.respond_info(
                                "[tool_calibrator] WARNING: Experimental ALLOW_SHUTTLE_Z=1 active with shuttle-mounted probe. "
                                "Forcing SAVE_CONFIG=0 to protect production configuration. Measured Z values will NOT be persisted."
                            )
                            save_config = False

            # Discover tool sequence across any toolchanger flavor
            ordered_tools = self._discover_tools(tools_param)

            if calibrate_xy and calibrate_z:
                mode_desc = f"Full Calibration (XY & Z) [Order: {order}]"
            elif calibrate_z:
                mode_desc = "Z-Offset Calibration"
            elif calibrate_xy:
                mode_desc = "Optical XY Calibration"
            else:
                mode_desc = "Calibration"

            flags = []
            if dry_run:
                flags.append("DRY-RUN")
            if continue_on_error:
                flags.append("CONTINUE-ON-ERROR")
            flags_str = f" ({', '.join(flags)})" if flags else ""

            gcmd.respond_info(f"[tool_calibrator] Starting {mode_desc} across tools: {ordered_tools}{flags_str}")

            # Execute start_gcode hook
            if self.start_gcode is not None:
                self._run_tool_hook(self.start_gcode, ordered_tools[0])

            # Safety First: Lift vertically to Safe_Z before any toolchange motion
            # In Z-only Cartographer speed-up mode, bypass global pre-calibration safe_z lift
            if calibrate_xy or not getattr(self.navigator, "carto_speedup", False):
                self.navigator.move_to_safe_z(toolhead, gcode_move)

            reference_origin_xy: Optional[List[float]] = None
            reference_z_result: Dict[str, Any] = {}
            results: Dict[int, Dict[str, float]] = {}

            for tool_no in ordered_tools:
                self._check_cancellation(gcmd)

                self.run_record["calibrating_tool"] = tool_no
                self.run_record["active_tool"] = tool_no
                self.run_record["phase"] = "CHANGING_TOOL"
                gcmd.respond_info(f"\n--- Calibrating Toolhead T{tool_no} ---")

                try:
                    # Safety invariant: only lift to safe Z clearance before a real physical toolchange
                    current_active = self.run_record.get("physical_tool")
                    if current_active is None:
                        current_active = self._get_active_tool_no()
                    if current_active != tool_no:
                        self.navigator.move_to_safe_z(toolhead, gcode_move)
                        if not self.navigator.is_safe_z_enabled():
                            cur_pos = toolhead.get_position()
                            if cur_pos[2] < 2.0:
                                toolhead.manual_move([None, None, 2.0], 15.0)
                                toolhead.wait_moves()

                    # Change tool
                    if self.before_pickup_gcode is not None:
                        self._run_tool_hook(self.before_pickup_gcode, tool_no)
                    self.gcode.run_script_from_command(f"T{tool_no}")
                    if self.after_pickup_gcode is not None:
                        self._run_tool_hook(self.after_pickup_gcode, tool_no)

                    toolhead.wait_moves()
                    self.run_record["physical_tool"] = tool_no
                    self.run_record["last_confirmed_tool"] = tool_no
                    self.run_record["phase"] = "CALIBRATING_TOOL"

                    # Optional nozzle cleaning prior to optical inspection (strictly non-intrusive)
                    if clean_nozzle:
                        if self.clean_nozzle_gcode is not None:
                            gcmd.respond_info(f"[T{tool_no}] Executing clean_nozzle_gcode hook...")
                            self._run_tool_hook(self.clean_nozzle_gcode, tool_no)
                            toolhead.wait_moves()
                        else:
                            clean_macro = self.printer.lookup_object("gcode_macro _CLEAN_NOZZLE", None)
                            if clean_macro is not None:
                                try:
                                    self.gcode.run_script_from_command(f"_CLEAN_NOZZLE TOOL={tool_no}")
                                    toolhead.wait_moves()
                                except Exception:
                                    pass
                            else:
                                gcmd.respond_info(f"[tool_calibrator] Note: CLEAN_NOZZLE requested, but no cleaning macro is configured on this printer. Skipping.")

                    tool_offsets: Dict[str, float] = {}

                    # Calibration sequence execution by order
                    if order == "Z_FIRST":
                        if calibrate_z and not dry_run:
                            self.run_record["phase"] = "PROBING_Z"
                            reference_z_result = self._execute_z_calibration(
                                tool_no, toolhead, gcode_move, gcmd, reference_z_result, tool_offsets
                            )
                        if calibrate_xy:
                            focal_z = None
                            if compensate_focal_z and tool_no != self.reference_tool and "z" in tool_offsets:
                                focal_z = self.navigator.cam_target_z + tool_offsets["z"]
                            self.run_record["phase"] = "CENTERING_NOZZLE"
                            reference_origin_xy = self._execute_xy_calibration(
                                tool_no, toolhead, gcode_move, gcmd, reference_origin_xy, tool_offsets, focal_z,
                                samples=samples_param, enable_wiggle=wiggle_param
                            )
                    else:
                        if calibrate_xy:
                            self.run_record["phase"] = "CENTERING_NOZZLE"
                            reference_origin_xy = self._execute_xy_calibration(
                                tool_no, toolhead, gcode_move, gcmd, reference_origin_xy, tool_offsets,
                                samples=samples_param, enable_wiggle=wiggle_param
                            )
                        if calibrate_z and not dry_run:
                            self.run_record["phase"] = "PROBING_Z"
                            reference_z_result = self._execute_z_calibration(
                                tool_no, toolhead, gcode_move, gcmd, reference_z_result, tool_offsets
                            )

                    results[tool_no] = tool_offsets
                    self.run_record["completed_tools"].append(tool_no)
                    self.run_record["offsets"][tool_no] = dict(tool_offsets)

                except Exception as tool_ex:
                    is_cancel = self.cancel_requested or "aborted" in str(tool_ex).lower() or "cancel" in str(tool_ex).lower()
                    if not continue_on_error or is_cancel or tool_no == self.reference_tool:
                        raise
                    gcmd.respond_info(f"!! [tool_calibrator] Tool T{tool_no} calibration failed: {tool_ex}")
                    gcmd.respond_info(f"!! [tool_calibrator] CONTINUE_ON_ERROR=1 active. Recording failure and continuing to next tool.")
                    self.run_record.setdefault("tool_errors", {})[tool_no] = str(tool_ex)
                    try:
                        self.navigator.depart_station(toolhead, gcode_move)
                    except Exception:
                        pass

            # Restore Reference Tool (or retain current tool if RESTORE_TOOL=0)
            if restore_tool:
                self.run_record["phase"] = "RESTORING_REFERENCE"
                current_active = self.run_record.get("physical_tool")
                if current_active is None:
                    current_active = self._get_active_tool_no()
                if current_active != self.reference_tool:
                    self.navigator.move_to_safe_z(toolhead, gcode_move)
                    if not self.navigator.is_safe_z_enabled():
                        cur_pos = toolhead.get_position()
                        if cur_pos[2] < 2.0:
                            toolhead.manual_move([None, None, 2.0], 15.0)
                            toolhead.wait_moves()
                    self.gcode.run_script_from_command(f"T{self.reference_tool}")
                    toolhead.wait_moves()
                    self.run_record["physical_tool"] = self.reference_tool
                    self.run_record["active_tool"] = self.reference_tool
                else:
                    self.run_record["physical_tool"] = self.reference_tool
                    self.run_record["active_tool"] = self.reference_tool
            else:
                gcmd.respond_info(f"[tool_calibrator] RESTORE_TOOL=0 active. Retaining current tool T{self.run_record.get('physical_tool')}.")
            self.run_record["calibrating_tool"] = None

            # Execute finish_gcode hook
            if self.finish_gcode is not None:
                active_hook_tool = self.reference_tool if restore_tool else self.run_record.get("physical_tool", self.reference_tool)
                self._run_tool_hook(self.finish_gcode, active_hook_tool)

            # Park at Safe_Z (bypassed in Z-only Cartographer speed-up mode)
            if calibrate_xy or not getattr(self.navigator, "carto_speedup", False):
                self.navigator.move_to_safe_z(toolhead, gcode_move)

            # Persist Offsets
            if save_config and not dry_run:
                self.config_manager.save_all_tool_offsets(results)
                gcmd.respond_info(f"[tool_calibrator] Successfully saved offsets to {self.config_manager.config_path}")
                # Dynamically apply offsets to toolchanger runtime
                tool_offsets_obj = self.printer.lookup_object("tool_offsets", None)
                if tool_offsets_obj is not None:
                    try:
                        tool_offsets_obj.set_results_and_apply(results)
                        gcmd.respond_info("[tool_calibrator] Applied new offsets to toolchanger runtime.")
                    except Exception as e:
                        logger.warning(f"Failed to apply offsets to toolchanger runtime: {e}")
            else:
                reason = "Experimental ALLOW_SHUTTLE_Z=1" if is_experimental_shuttle_z else ("SAVE_CONFIG=0" if not save_config else "DRY_RUN=1")
                gcmd.respond_info(f"[tool_calibrator] Note: {reason} active. Offsets retained in memory only.")

            # Telemetry Summary
            gcmd.respond_info("\n================ CALIBRATION SUMMARY ================")
            for t_num, offs in results.items():
                parts = []
                for k, v in offs.items():
                    if k.lower() == "z" and is_experimental_shuttle_z:
                        parts.append(f"Z={v:+.3f}mm [EXPERIMENTAL - NOT SAVED]")
                    else:
                        parts.append(f"{k.upper()}={v:+.3f}mm")
                gcmd.respond_info(f"Tool T{t_num}: {'  '.join(parts) if parts else 'No new offsets measured'}")

            if self.run_record.get("tool_errors"):
                gcmd.respond_info("\n-- Tool Errors Recorded (CONTINUE_ON_ERROR=1) --")
                for err_tool, err_msg in self.run_record["tool_errors"].items():
                    gcmd.respond_info(f"  Tool T{err_tool}: FAILED ({err_msg})")

            has_errors = bool(self.run_record.get("tool_errors"))
            if has_errors:
                status_text = "PARTIAL_SUCCESS" if results else "FAILED"
                gcmd.respond_info(f"\n⚠ ================= CALIBRATION {status_text} =================\n")
                gcmd.respond_info(f"  Calibration finished with errors on {len(self.run_record['tool_errors'])} tool(s).")
                gcmd.respond_info("=====================================================")
                self.run_record["state"] = status_text
                self.run_record["phase"] = "COMPLETED_WITH_ERRORS"
                self.run_record["valid"] = bool(results)
                self.last_run_status = status_text
            else:
                gcmd.respond_info("\n✔ ================= CALIBRATION COMPLETE =================\n")
                gcmd.respond_info("  All configured tools calibrated safely and successfully.")
                gcmd.respond_info("=====================================================")
                self.run_record["state"] = "SUCCESS"
                self.run_record["phase"] = "COMPLETED"
                self.run_record["valid"] = True
                self.last_run_status = "SUCCESS"

            # Merge results axis-by-axis into self.cached_offsets so Z-only runs do not overwrite existing XY offsets
            for t_num, offs in results.items():
                if t_num not in self.cached_offsets:
                    self.cached_offsets[t_num] = {}
                self.cached_offsets[t_num].update(offs)

        except Exception as ex:
            is_cancel = self.cancel_requested or "aborted" in str(ex).lower() or "cancel" in str(ex).lower()
            term_state = "CANCELLED" if is_cancel else "FAILED"
            self.run_record["state"] = term_state
            self.run_record["phase"] = term_state
            self.run_record["valid"] = False
            self.run_record["error"] = str(ex)
            self.last_run_status = f"{term_state}: {ex}"
            try:
                self.navigator.depart_station(toolhead, gcode_move)
            except Exception as cleanup_ex:
                logger.warning(f"Secondary error in depart_station during failure handling: {cleanup_ex}")
                gcmd.respond_info(f"!! [tool_calibrator] Note: Station departure cleanup reported: {cleanup_ex}")
            gcmd.respond_info(f"!! [tool_calibrator] Calibration {term_state.capitalize()}: {ex}")
            active_t = self.run_record.get("physical_tool")
            if active_t is None:
                active_t = self.run_record.get("calibrating_tool")
            if active_t is not None:
                gcmd.respond_info(f"!! [tool_calibrator] Failure occurred while T{active_t} was active. If using toolchanger, verify physical dock state before issuing further tool changes.")
            raise gcmd.error(f"[tool_calibrator] Calibration {term_state.capitalize()}: {ex}")
        finally:
            self.run_record["end_time"] = _sys_time.time()
            if self.run_record.get("start_monotonic"):
                try:
                    self.run_record["duration_sec"] = max(0.0, round(self.reactor.monotonic() - float(self.run_record["start_monotonic"]), 2))
                except Exception:
                    self.run_record["duration_sec"] = 0.0
            elif self.run_record.get("start_time"):
                try:
                    self.run_record["duration_sec"] = max(0.0, round(self.run_record["end_time"] - float(self.run_record["start_time"]), 2))
                except Exception:
                    self.run_record["duration_sec"] = 0.0

            # Reconcile toolchanger state to avoid uninitialized state on failure
            try:
                active_recon = self.run_record.get("physical_tool")
                if active_recon is None:
                    active_recon = self.run_record.get("calibrating_tool")
                self._reconcile_toolchanger_state(active_recon, gcmd)
            except Exception as recon_ex:
                logger.warning(f"Error during _reconcile_toolchanger_state in finally: {recon_ex}")

            if session_token:
                try:
                    self._query_vision("release_lock", {"session_id": session_token, "session_token": session_token}, timeout=2.0)
                except Exception:
                    pass
                self.session_token = None

    def cmd_AUTO_TEACH_CAMERA(self, gcmd) -> None:
        """1-Click: Visual auto-centering, automatic approach vector calculation, and instant save to config."""
        gcmd.respond_info("=== Auto-Teaching Camera Station ===")
        self.cmd_CALIBRATION_TEACH_STATION(gcmd, default_station="CAMERA")

    def cmd_AUTO_TEACH_SWITCH(self, gcmd) -> None:
        """1-Click: Probes contact height, calculates approach vector towards bed center, and saves to config."""
        gcmd.respond_info("=== Auto-Teaching Z Switch Station ===")
        self.cmd_CALIBRATION_TEACH_STATION(gcmd, default_station="SWITCH")

    def cmd_CALIBRATION_TEACH_STATION(self, gcmd, default_station: Optional[str] = None) -> None:
        """
        1-Click Interactive Teaching & Auto-Persistence Command.
        Automatically centers (via visual servoing) or probes contact height,
        computes safe approach vector towards bed center, and saves directly to tool_offsets.cfg!

        Usage:
            CALIBRATION_TEACH_STATION STATION=CAMERA [AUTO_CENTER=1] [APPROACH_DIST=25]
            CALIBRATION_TEACH_STATION STATION=SWITCH [AUTO_TOUCH=1] [APPROACH_DIST=20]
        """
        station = gcmd.get("STATION", default_station or "").upper()
        if station not in ("CAMERA", "SWITCH", "Z_SWITCH"):
            raise gcmd.error("STATION must be CAMERA or SWITCH.")

        toolhead = self.printer.lookup_object("toolhead")
        if not self.navigator.is_homed():
            raise gcmd.error("[ERR_PRE_001] Printer must be fully homed (G28) before teaching station.")

        if station == "CAMERA":
            self._check_camera_thermal_safety(tool_no=None, gcmd=gcmd)
            auto_center = gcmd.get_int("AUTO_CENTER", 1) == 1
            approach_dist = gcmd.get_float("APPROACH_DIST", 25.0)

            active_t = self._get_active_tool_no()
            if auto_center:
                # Synchronize and check if camera matrix is solved
                self._ensure_vision_sync()
                has_matrix = False
                try:
                    health = self._query_vision("health", timeout=2.0)
                    has_matrix = bool(health.get("has_matrix") or health.get("matrix_solved"))
                except Exception:
                    pass

                if not has_matrix:
                    gcmd.respond_info(
                        "[tool_calibrator] Note: No camera matrix calibrated yet (ERR_CV_203). "
                        "Recording current jog position as initial camera waypoint without auto-centering. "
                        "Please run CALIBRATE_CAMERA_SCALE to solve matrix, then re-run AUTO_TEACH_CAMERA."
                    )
                else:
                    gcmd.respond_info("[tool_calibrator] Auto-centering nozzle over camera via visual servoing...")
                    self._set_inspection_lighting(True, active_t)
                    try:
                        self._center_nozzle(toolhead, gcmd)
                    except Exception as ex:
                        raise gcmd.error(f"[ERR_TEACH_CAM] Auto-centering failed during teach station: {ex}. Station not saved.")
                    finally:
                        self._set_inspection_lighting(False, active_t)


            pos = toolhead.get_position()
            target_x, target_y, target_z = round(pos[0], 3), round(pos[1], 3), round(pos[2], 3)
            app_x, app_y = self.navigator.calculate_auto_approach(target_x, target_y, approach_dist)

            self.navigator.set_camera_waypoints(target_x, target_y, target_z, app_x, app_y)

            # Auto-persist to tool_offsets.cfg
            cam_data = {
                "target_x": target_x,
                "target_y": target_y,
                "target_z": target_z,
                "approach_x": app_x,
                "approach_y": app_y,
                "approach_z": target_z,
                "safe_z": self.navigator.safe_z
            }
            self.config_manager.save_section("tool_calibrator_station camera", cam_data)
            # Synchronize safe_z to switch station if it exists
            sw_existing = self.config_manager.load_section("tool_calibrator_station switch")
            if sw_existing:
                sw_existing["safe_z"] = self.navigator.safe_z
                self.config_manager.save_section("tool_calibrator_station switch", sw_existing)

            sz_info = f"{self.navigator.safe_z:.3f} mm" if self.navigator.is_safe_z_enabled() else "OFF"
            gcmd.respond_info(
                f"✔ [CAMERA Station Configured & Saved Automatically]\n"
                f"  Target:   X{target_x:.3f} Y{target_y:.3f} Z{target_z:.3f}\n"
                f"  Approach: X{app_x:.3f} Y{app_y:.3f} (Vector towards bed center)\n"
                f"  Safe Z:   {sz_info}\n"
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
                    target_z = round(res.get("contact_z", res.get("trigger_z", pos[2])), 3)
                except Exception as ex:
                    raise gcmd.error(f"[ERR_TEACH_SWITCH] Auto-touch probe failed during teach station: {ex}. Station not saved.")

            app_x, app_y = self.navigator.calculate_auto_approach(target_x, target_y, approach_dist)
            self.navigator.set_switch_waypoints(target_x, target_y, target_z, app_x, app_y)

            # Auto-persist to tool_offsets.cfg
            sw_data = {
                "target_x": target_x,
                "target_y": target_y,
                "target_z": target_z,
                "approach_x": app_x,
                "approach_y": app_y,
                "approach_z": target_z,
                "safe_z": self.navigator.safe_z
            }
            self.config_manager.save_section("tool_calibrator_station switch", sw_data)
            # Synchronize safe_z to camera station if it exists
            cam_existing = self.config_manager.load_section("tool_calibrator_station camera")
            if cam_existing:
                cam_existing["safe_z"] = self.navigator.safe_z
                self.config_manager.save_section("tool_calibrator_station camera", cam_existing)

            sz_sw_info = f"{self.navigator.safe_z:.3f} mm" if self.navigator.is_safe_z_enabled() else "OFF"
            gcmd.respond_info(
                f"✔ [SWITCH Station Configured & Saved Automatically]\n"
                f"  Target:   X{target_x:.3f} Y{target_y:.3f} Z{target_z:.3f}\n"
                f"  Approach: X{app_x:.3f} Y{app_y:.3f} (Vector towards bed center)\n"
                f"  Safe Z:   {sz_sw_info}\n"
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
            raise gcmd.error("DISTANCE must be between 0.2mm and 5.0mm.")

        toolhead = self.printer.lookup_object("toolhead")
        gcode_move = self.printer.lookup_object("gcode_move")

        if not self.navigator.is_homed():
            raise gcmd.error("[ERR_PRE_001] Printer must be fully homed (G28) before camera calibration.")

        self._check_camera_thermal_safety(tool_no=None, gcmd=gcmd)

        session_token = None
        self.cancel_requested = False

        self._ensure_vision_sync()
        try:
            import time as _sys_time
            unique_session = f"klipper_scale_{int(_sys_time.time()*1000)}"
            lock_resp = self._query_vision("acquire_lock", {"client_id": "klipper", "session_id": unique_session, "timeout_seconds": 600}, timeout=2.0)
            session_token = lock_resp.get("session_token") or lock_resp.get("session_id")
            self.session_token = session_token
        except Exception as ex:
            raise gcmd.error(f"[tool_calibrator] Failed to acquire vision server session lock before motion: {ex}")

        gcmd.respond_info(f"[tool_calibrator] Starting Star-Pattern Camera Calibration (Displacement: ±{dist:.2f}mm)...")
        self._set_inspection_lighting(True, self.reference_tool)

        try:
            # 1. Approach Camera safely
            self.navigator.approach_camera(toolhead, gcode_move)
            toolhead.wait_moves()
            self.reactor.pause(self.reactor.monotonic() + 0.2)

            # 2. Baseline detection at approach position
            base_resp = self._sample_burst(toolhead, gcmd)
            if not base_resp or not base_resp.get("found"):
                self.navigator.depart_station(toolhead, gcode_move)
                raise gcmd.error("[ERR_CV_201] Could not detect nozzle center at baseline position.")

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

            for label, tx, ty, rdx, rdy in moves:
                self._check_cancellation(gcmd)
                self.navigator.validate_coordinate_safety(x=tx, y=ty)
                toolhead.manual_move([tx, ty, None], self.navigator.approach_speed)
                toolhead.wait_moves()
                self.reactor.pause(self.reactor.monotonic() + 0.2)

                resp = self._sample_burst(toolhead, gcmd)
                if not resp or not resp.get("found"):
                    raise gcmd.error(f"[ERR_CV_201] Lost nozzle tracking during camera scale displacement {label}.")

                curr_uv = resp.get("center_uv")
                pixel_dist = math.hypot(curr_uv[0] - base_uv[0], curr_uv[1] - base_uv[1])
                if pixel_dist < 5.0:
                    raise gcmd.error(f"Measured displacement too small ({pixel_dist:.2f}px) in direction {label}.")

                computed_mpp = dist / pixel_dist
                mpp_samples.append(computed_mpp)
                matrix_points.append([[rdx, rdy], list(curr_uv)])
                gcmd.respond_info(f"  -> {label} displacement: Shift {pixel_dist:.2f}px (UV: {curr_uv[0]:.2f}, {curr_uv[1]:.2f})")

            # Return to baseline
            toolhead.manual_move([cx, cy, None], self.navigator.approach_speed)
            toolhead.wait_moves()

            solved_mpp = float(statistics.median(mpp_samples))
            self.calibrated_mpp = solved_mpp

            # Fit 1st-order 2D affine transformation matrix
            matrix_resp = self._query_vision("solve_matrix", {
                "points": matrix_points,
                "calibration_points": matrix_points
            }, timeout=5.0)
            matrix_ok = matrix_resp.get("success", False)

            # Update live scale & matrix in Vision Service
            self._query_vision("set_mpp", {"mpp": solved_mpp})
            if matrix_ok and "matrix" in matrix_resp:
                self._query_vision("set_matrix", {"matrix": matrix_resp["matrix"]})

            cam_dict = {
                "mpp": solved_mpp,
                "target_z": cz,
                "matrix_solved": matrix_ok
            }
            if matrix_ok and "matrix" in matrix_resp:
                matrix_vals = matrix_resp["matrix"]
                cam_dict["matrix_a"] = matrix_vals[0][0]
                cam_dict["matrix_b"] = matrix_vals[0][1]
                cam_dict["matrix_tx"] = matrix_vals[0][2] if len(matrix_vals[0]) >= 3 else 0.0
                cam_dict["matrix_c"] = matrix_vals[1][0]
                cam_dict["matrix_d"] = matrix_vals[1][1]
                cam_dict["matrix_ty"] = matrix_vals[1][2] if len(matrix_vals[1]) >= 3 else 0.0

            # Persist calibrated MPP and matrix into tool_offsets.cfg under [tool_calibrator_station camera]
            self.config_manager.save_section("tool_calibrator_station camera", cam_dict)

            # 4. Optical centering using newly calibrated transformation matrix
            gcmd.respond_info("  -> Performing post-calibration optical centering with solved matrix...")
            centering_ok = False
            try:
                self._center_nozzle(toolhead, gcmd)
                new_pos = toolhead.get_position()
                cam_dict["target_x"] = round(new_pos[0], 3)
                cam_dict["target_y"] = round(new_pos[1], 3)
                self.config_manager.save_section("tool_calibrator_station camera", cam_dict)
                self.navigator.cam_target_x = cam_dict["target_x"]
                self.navigator.cam_target_y = cam_dict["target_y"]
                gcmd.respond_info(f"  -> Station target position updated to centered coordinates: X{cam_dict['target_x']:.3f} Y{cam_dict['target_y']:.3f}")
                centering_ok = True
            except Exception as ex:
                gcmd.respond_info(f"  ⚠ [tool_calibrator] Post-calibration optical centering failed: {ex}")
                gcmd.respond_info("  -> Camera station target position NOT updated; retaining previous validated coordinates.")

            if centering_ok:
                self.last_run_status = "SUCCESS"
                gcmd.respond_info(
                    f"\n✔ ================= CAMERA CALIBRATION SUCCESS ================\n"
                    f"  Calculated Scale (MPP): {solved_mpp:.6f} mm/pixel\n"
                    f"  Affine Matrix Solved:   {matrix_ok}\n"
                    f"  Station Centered:       X{cam_dict['target_x']:.3f} Y{cam_dict['target_y']:.3f}\n"
                    f"  Saved to Configuration: {self.config_manager.config_path}\n"
                    f"================================================================"
                )
            else:
                self.last_run_status = "WARNING: Scale fitted, but centering failed"
                gcmd.respond_info(
                    f"\n⚠ ================= CAMERA SCALE FIT (PARTIAL) ================\n"
                    f"  Calculated Scale (MPP): {solved_mpp:.6f} mm/pixel\n"
                    f"  Affine Matrix Solved:   {matrix_ok} (Persisted to config)\n"
                    f"  Station Centering:      FAILED (Previous station coordinates retained)\n"
                    f"================================================================"
                )

        finally:
            if session_token:
                try:
                    self._query_vision("release_lock", {"session_id": session_token, "session_token": session_token}, timeout=2.0)
                except Exception:
                    pass
                self.session_token = None
            self._set_inspection_lighting(False, self.reference_tool)
            self.navigator.depart_station(toolhead, gcode_move)
            self._reconcile_toolchanger_state(self.reference_tool, gcmd)

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

        if pos_type == "SAFE_Z":
            target_z = gcmd.get_float("Z", pos[2])
            try:
                self.navigator.set_safe_z(target_z)
            except ValueError as val_err:
                raise gcmd.error(f"[tool_calibrator] Invalid SAFE_Z value: {val_err}")
            self.navigator.configured_safe_z = self.navigator.safe_z
            if self.navigator.is_safe_z_enabled():
                gcmd.respond_info(f"Global Safe_Z set to Z:{self.navigator.safe_z:.3f} (ENABLED)")
            else:
                gcmd.respond_info("Global Safe_Z set to DISABLED (OFF)")
            if save_to_disk:
                for sec in ("tool_calibrator_station camera", "tool_calibrator_station switch"):
                    existing = self.config_manager.load_section(sec) or {}
                    existing["safe_z"] = self.navigator.safe_z
                    self.config_manager.save_section(sec, existing)
            return

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

            if save_to_disk:
                cam_data = {
                    "target_x": self.navigator.cam_target_x,
                    "target_y": self.navigator.cam_target_y,
                    "target_z": self.navigator.cam_target_z,
                    "approach_x": self.navigator.cam_approach_x,
                    "approach_y": self.navigator.cam_approach_y,
                    "safe_z": self.navigator.safe_z
                }
                existing_cam = self.config_manager.load_section("tool_calibrator_station camera") or {}
                for k, v in cam_data.items():
                    if v is not None:
                        existing_cam[k] = v
                self.config_manager.save_section("tool_calibrator_station camera", existing_cam)
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
                switch_data = {
                    "target_x": self.navigator.switch_target_x,
                    "target_y": self.navigator.switch_target_y,
                    "target_z": self.navigator.switch_target_z,
                    "approach_x": self.navigator.switch_approach_x,
                    "approach_y": self.navigator.switch_approach_y,
                    "safe_z": self.navigator.safe_z
                }
                existing_sw = self.config_manager.load_section("tool_calibrator_station switch") or {}
                for k, v in switch_data.items():
                    if v is not None:
                        existing_sw[k] = v
                self.config_manager.save_section("tool_calibrator_station switch", existing_sw)
        else:
            raise gcmd.error("Invalid STATION. Must be CAMERA or SWITCH.")

    def cmd_CALIBRATION_ROLLBACK_OFFSETS(self, gcmd) -> None:
        """Emergency rollback command restoring previous configuration backup."""
        backup_name = gcmd.get("BACKUP", None)
        try:
            restored_file = self.config_manager.rollback(backup_name)
            gcmd.respond_info(f"[tool_calibrator] Restored configuration from {restored_file}. Please issue FIRMWARE_RESTART.")
        except Exception as ex:
            raise gcmd.error(f"[tool_calibrator] Rollback failed: {ex}")

    def cmd_CALIBRATION_NAVIGATE(self, gcmd) -> None:
        """
        Safely navigates toolhead to Camera station, Z Switch station, or departs safely.
        Usage: CALIBRATION_NAVIGATE STATION=CAMERA|SWITCH|DEPART
        """
        toolhead = self.printer.lookup_object("toolhead")
        gcode_move = self.printer.lookup_object("gcode_move")
        station = gcmd.get("STATION", "CAMERA").upper()

        if not self.navigator.is_homed():
            raise gcmd.error("[ERR_PRE_001] Printer must be fully homed (G28) before navigation.")

        if station in ("CAMERA", "CAM"):
            self._check_camera_thermal_safety(tool_no=None, gcmd=gcmd)
            active_t = self._get_active_tool_no()
            gcmd.respond_info("[tool_calibrator] Approaching Camera Station via safe 3-tier waypoints...")
            self._set_inspection_lighting(True, active_t)
            try:
                self.navigator.approach_camera(toolhead, gcode_move)
                pos = toolhead.get_position()
                gcmd.respond_info(f"✔ Reached Camera Station: X{pos[0]:.3f} Y{pos[1]:.3f} Z{pos[2]:.3f}")
            except Exception as ex:
                self._set_inspection_lighting(False, active_t)
                raise gcmd.error(f"Navigation error: {ex}")
        elif station in ("SWITCH", "Z_SWITCH"):
            gcmd.respond_info("[tool_calibrator] Approaching Z Switch Station via safe 3-tier waypoints...")
            try:
                self.navigator.approach_switch(toolhead, gcode_move)
                pos = toolhead.get_position()
                gcmd.respond_info(f"✔ Reached Switch Station: X{pos[0]:.3f} Y{pos[1]:.3f} Z{pos[2]:.3f}")
            except Exception as ex:
                raise gcmd.error(f"Navigation error: {ex}")
        elif station in ("DEPART", "LEAVE", "SAFE_Z"):
            active_t = self._get_active_tool_no()
            self._set_inspection_lighting(False, active_t)
            prev_z = toolhead.get_position()[2]
            self.navigator.depart_station(toolhead, gcode_move)
            pos = toolhead.get_position()
            if self.navigator.is_safe_z_enabled() and pos[2] > prev_z + 0.01:
                gcmd.respond_info(f"✔ Departed station. Safe altitude: Z{pos[2]:.3f}")
            else:
                gcmd.respond_info("✔ Departed station.")
        else:
            raise gcmd.error(f"Invalid STATION '{station}'. Must be CAMERA, SWITCH, or DEPART.")

    def cmd_CALIBRATION_CENTER_NOZZLE(self, gcmd) -> None:
        """
        Perform visual servoing centering on the active toolhead over the camera.
        Parameters:
            SAMPLES (int): Number of frames in burst (default: config value or 3)
            WIGGLE (int): 1 to enable adaptive wiggle recovery (default: 1)
        """
        toolhead = self.printer.lookup_object("toolhead")
        samples = gcmd.get_int("SAMPLES", self.centering_samples)
        if samples < 3:
            gcmd.respond_info(f"[tool_calibrator] Notice: Clamping SAMPLES from {samples} to 3 for visual centering safety.")
            samples = 3
        wiggle = gcmd.get_int("WIGGLE", 1 if self.wiggle_on_failure else 0) == 1

        if not self.navigator.is_homed():
            raise gcmd.error("[ERR_PRE_001] Printer must be fully homed (G28).")

        self._check_camera_thermal_safety(tool_no=None, gcmd=gcmd)

        active_t = self._get_active_tool_no()
        gcmd.respond_info(f"[tool_calibrator] Centering active nozzle (T{active_t}) over camera...")
        self._set_inspection_lighting(True, active_t)
        try:
            self._center_nozzle(toolhead, gcmd, samples=samples, enable_wiggle=wiggle)
            pos = toolhead.get_position()
            gcmd.respond_info(f"✔ Nozzle centered successfully at X{pos[0]:.3f} Y{pos[1]:.3f}")
        except Exception as ex:
            raise gcmd.error(f"Centering failed: {ex}")
        finally:
            self._set_inspection_lighting(False, active_t)
            self._reconcile_toolchanger_state(active_t, gcmd)


    def cmd_CALIBRATION_TEST_VISION(self, gcmd) -> None:
        """
        Test vision detection at current toolhead position without moving.
        Reports detected center UV, radius, confidence, and burst dispersion.
        Does not raise G-code CommandError on negative detection to protect toolchanger state.
        """
        self._ensure_vision_sync()
        toolhead = self.printer.lookup_object("toolhead")
        samples = gcmd.get_int("SAMPLES", self.centering_samples)
        active_t = self._get_active_tool_no()

        tc = self.printer.lookup_object("toolchanger", None)
        saved_active_tool = getattr(tc, "active_tool", None) if tc is not None else None

        gcmd.respond_info(f"[tool_calibrator] Sampling {samples} vision frames at current position...")
        self._set_inspection_lighting(True, active_t)
        try:
            burst = self._sample_burst(toolhead, gcmd, samples=samples)
            if not burst or not burst.get("found"):
                reason = burst.get("reason", "Nozzle NOT detected at current position") if burst else "Empty burst"
                gcmd.respond_info(
                    f"❌ [Vision Inspection Report]\n"
                    f"  Found:       NO (0/{samples} frames)\n"
                    f"  Reason:      {reason}\n"
                    f"  Suggestion:  Verify lighting, focal distance, or nozzle position over camera."
                )
                return

            uv = burst.get("center_uv")
            radius = burst.get("radius_px", 0.0)
            conf = burst.get("confidence", 0.0)
            spread = burst.get("spread_px", 0.0)
            tier = burst.get("tier", 1)
            combo = burst.get("combo", 0)
            tier_desc = "Tier 0 Curvature (Symmetric)" if combo == 10 else f"Tier {tier}"

            gcmd.respond_info(
                f"✔ [Vision Inspection Report]\n"
                f"  Found:       YES ({burst.get('burst_count')}/{burst.get('burst_total')} frames)\n"
                f"  Center UV:   U{uv[0]:.2f} px, V{uv[1]:.2f} px\n"
                f"  Radius:      {radius:.2f} px\n"
                f"  Confidence:  {conf*100:.1f}%\n"
                f"  Dispersion:  {spread:.2f} px\n"
                f"  Algorithm:   {tier_desc}"
            )
        except Exception as ex:
            gcmd.respond_info(f"!! [tool_calibrator] Vision test error: {ex}")
        finally:
            self._set_inspection_lighting(False, active_t)
            if tc is not None and saved_active_tool is not None:
                curr_active = getattr(tc, "active_tool", None)
                if curr_active is None:
                    try:
                        tc.active_tool = saved_active_tool
                        logger.info(f"[tool_calibrator] Preserved and restored toolchanger active_tool: {saved_active_tool}")
                    except Exception as err:
                        logger.warning(f"[tool_calibrator] Could not restore toolchanger active_tool: {err}")

    def cmd_CALIBRATION_ABORT(self, gcmd) -> None:
        """Aborts active calibration run cleanly at the next step."""
        if self.run_record.get("state") == "RUNNING":
            self.cancel_requested = True
            try:
                self._query_vision("abort_calibration", {}, timeout=1.0)
            except Exception:
                pass
            gcmd.respond_info("[tool_calibrator] Abort requested! Calibration sequence will halt safely at current step.")
        else:
            gcmd.respond_info("[tool_calibrator] No calibration cycle is currently running.")

    def cmd_CALIBRATION_STATUS(self, gcmd) -> None:
        """Outputs current calibration and hardware telemetry status."""
        raw_rec = getattr(self, "run_record", {})
        rec = raw_rec if isinstance(raw_rec, dict) else {}
        gcmd.respond_info(f"Tool-Calibrator Status: {rec.get('state', 'IDLE')} (Last Status: {self.last_run_status})")
        if rec:
            validity_str = "VALID" if rec.get("valid") else "INVALID/ABORTED"
            dur = 0.0
            if rec.get("end_time") and rec.get("start_time"):
                dur = round(rec["end_time"] - rec["start_time"], 1)
            elif rec.get("start_time") or rec.get("start_monotonic"):
                if rec.get("start_monotonic"):
                    try:
                        dur = max(0.0, round(self.reactor.monotonic() - float(rec["start_monotonic"]), 1))
                    except Exception:
                        pass
                elif rec.get("start_time"):
                    try:
                        dur = max(0.0, round(time.time() - float(rec["start_time"]), 1))
                    except Exception:
                        pass
            gcmd.respond_info(
                f"  Run ID: {rec.get('run_id')} | State: {rec.get('state')} ({validity_str}) | Phase: {rec.get('phase', 'IDLE')} | Physical Tool: {rec.get('physical_tool')} | Calibrating: {rec.get('calibrating_tool')} | Duration: {dur:.1f}s"
            )
            if rec.get("error"):
                gcmd.respond_info(f"  Last Error: {rec.get('error')}")
        is_sz_enabled = getattr(getattr(self, "navigator", None), "is_safe_z_enabled", lambda: False)()
        safe_z_val = getattr(getattr(self, "navigator", None), "safe_z", None)
        sz_display = f"{safe_z_val:.2f}mm" if (is_sz_enabled and safe_z_val is not None) else "OFF"
        is_speedup = getattr(getattr(self, "navigator", None), "carto_speedup", False)
        speedup_str = " [Carto Speed-Up: ENABLED (No Z-lift)]" if is_speedup else ""
        gcmd.respond_info(f"  Safe_Z: {sz_display} (Station/Dock){speedup_str} | Backend: {getattr(self, 'z_backend_type', 'unknown')}")

        # Vision service connectivity and version telemetry
        try:
            health = self._query_vision("health", timeout=1.0)
            if isinstance(health, dict) and health.get("status") in ("ok", "healthy", "up"):
                ver = health.get("version", "unknown")
                svc = health.get("service", "tkc-vision")
                commit = f" ({health.get('commit')})" if health.get("commit") and health.get("commit") != "unknown" else ""
                cam_state = "READY" if health.get("camera_ready") else "NOT_READY"
                gcmd.respond_info(f"  Vision Service: ONLINE ({svc} v{ver}{commit}) | Camera: {cam_state}")
            else:
                gcmd.respond_info("  Vision Service: OFFLINE / UNREACHABLE")
        except Exception:
            gcmd.respond_info("  Vision Service: OFFLINE / UNREACHABLE")

        raw_offsets = getattr(self, "cached_offsets", {})
        cached_offsets = raw_offsets if isinstance(raw_offsets, dict) else {}
        if cached_offsets:
            validity_note = "Valid" if rec.get("valid") else "Stale (Prior Completed Run)"
            gcmd.respond_info(f"  Cached Offsets ({validity_note}):")
            for t, offs in sorted(cached_offsets.items()):
                parts = [f"{k.upper()}={v:+.3f}" for k, v in offs.items()] if isinstance(offs, dict) else []
                gcmd.respond_info(f"    T{t}: {' '.join(parts) if parts else 'None'}")
        else:
            gcmd.respond_info("  Cached Offsets: None")

    def get_status(self, eventtime=None) -> Dict[str, Any]:
        """Provides rich runtime status dictionary for Moonraker/Mainsail/Fluidd telemetry."""
        try:
            raw_rec = getattr(self, "run_record", {})
            rec = dict(raw_rec) if isinstance(raw_rec, dict) else {}
            if rec.get("state") == "RUNNING":
                if rec.get("start_monotonic"):
                    try:
                        if eventtime is not None:
                            rec["elapsed_sec"] = max(0.0, round(float(eventtime) - float(rec["start_monotonic"]), 1))
                        elif self.reactor is not None:
                            rec["elapsed_sec"] = max(0.0, round(self.reactor.monotonic() - float(rec["start_monotonic"]), 1))
                        else:
                            rec["elapsed_sec"] = 0.0
                    except Exception:
                        rec["elapsed_sec"] = 0.0
                elif rec.get("start_time"):
                    try:
                        rec["elapsed_sec"] = max(0.0, round(time.time() - float(rec["start_time"]), 1))
                    except Exception:
                        rec["elapsed_sec"] = 0.0

            is_sz_enabled = getattr(getattr(self, "navigator", None), "is_safe_z_enabled", lambda: False)()
            safe_z_val = getattr(getattr(self, "navigator", None), "safe_z", None)
            raw_offsets = getattr(self, "cached_offsets", {})
            cached_offsets = dict(raw_offsets) if isinstance(raw_offsets, dict) else {}

            return {
                "status": rec.get("state", "IDLE"),
                "phase": rec.get("phase", "IDLE"),
                "last_run_status": getattr(self, "last_run_status", "IDLE"),
                "run_id": rec.get("run_id"),
                "calibrating_tool": rec.get("calibrating_tool"),
                "physical_tool": rec.get("physical_tool"),
                "active_tool": rec.get("physical_tool") if rec.get("physical_tool") is not None else rec.get("calibrating_tool"),
                "run_valid": rec.get("valid", False),
                "run_record": rec,
                "reference_tool": getattr(self, "reference_tool", 0),
                "z_backend": getattr(self, "z_backend_type", "cartographer"),
                "safe_z": safe_z_val if is_sz_enabled else None,
                "safe_z_enabled": is_sz_enabled,
                "carto_speedup": getattr(getattr(self, "navigator", None), "carto_speedup", False),
                "cached_offsets": cached_offsets
            }
        except Exception as ex:
            logger.error(f"[tool_calibrator] Unhandled exception in get_status: {ex}")
            return {
                "status": "ERROR",
                "phase": "ERROR",
                "last_run_status": "ERROR",
                "error": str(ex),
                "run_record": {},
                "cached_offsets": {}
            }


def load_config(config):
    """Klipper module loader entrypoint."""
    return ToolCalibrator(config)
