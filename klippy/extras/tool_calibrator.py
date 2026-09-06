"""
Tool-Klipper-Calibration Master Klipper Extension.

Coordinates automated Computer Vision XY alignment and multi-backend Z calibration (Switch / Cartographer)
for multi-toolhead 3D printers running Klipper Toolchanger.
"""

from typing import Dict, Any, List, Optional, Set, Tuple
import json
import logging
import math
import re
import threading
import urllib.request
import urllib.error
import statistics

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

        # Config parameters (supports service_url and server_url aliases)
        srv_url = config.get("server_url", None)
        if srv_url is None:
            srv_url = config.get("service_url", "http://localhost:8090")
        self.server_url = srv_url.rstrip("/")

        self.reference_tool = config.getint("reference_tool", 0)
        self.configured_tools_str = config.get("tools", None)
        self.z_backend_type = config.get("z_backend", "cartographer").strip().lower()
        self.max_centering_iterations = config.getint("max_centering_iterations", 5, minval=1, maxval=10)
        self.tolerance_mm = config.getfloat("tolerance_mm", 0.015, above=0.001)
        self.centering_samples = config.getint("centering_samples", 3, minval=1, maxval=7)
        self.sample_delay = config.getfloat("sample_delay", 0.08, minval=0.01, maxval=0.5)
        self.wiggle_distance = config.getfloat("wiggle_distance", 0.1, above=0.01, maxval=1.0)
        if hasattr(config, "getboolean"):
            self.wiggle_on_failure = config.getboolean("wiggle_on_failure", True)
        else:
            self.wiggle_on_failure = bool(config.get("wiggle_on_failure", True))

        # Core Component Instances
        self.navigator = SafeNavigator(config)
        cfg_path = config.get("offset_config_path", None)
        if cfg_path is None:
            cfg_path = config.get("offsets_config_path", "~/printer_data/config/tool_offsets.cfg")
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

        # Calibration State
        self.cached_offsets: Dict[int, Dict[str, float]] = {}
        self.calibrated_mpp: Optional[float] = None
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
        self.gcode.register_command("CALIBRATION_NAVIGATE", self.cmd_CALIBRATION_NAVIGATE, desc="Safely navigate toolhead between stations")
        self.gcode.register_command("CALIBRATION_CENTER_NOZZLE", self.cmd_CALIBRATION_CENTER_NOZZLE, desc="Perform visual servoing centering on active tool")
        self.gcode.register_command("CALIBRATION_TEST_VISION", self.cmd_CALIBRATION_TEST_VISION, desc="Test nozzle vision detection and report coordinates")

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

        # Preserve the maximum safe_z taught across camera, switch, and config
        loaded_safe_zs = []
        if self.navigator.safe_z is not None:
            loaded_safe_zs.append(self.navigator.safe_z)
        if cam_saved and "safe_z" in cam_saved and cam_saved["safe_z"] not in ("None", ""):
            loaded_safe_zs.append(float(cam_saved["safe_z"]))
        if switch_saved and "safe_z" in switch_saved and switch_saved["safe_z"] not in ("None", ""):
            loaded_safe_zs.append(float(switch_saved["safe_z"]))
        if loaded_safe_zs:
            self.navigator.safe_z = max(loaded_safe_zs)

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

        has_mpp = health.get("calibrated_mpp", False)
        has_matrix = health.get("has_matrix", False)

        if self.camera_stream_url:
            try:
                self._query_vision("set_camera_url", {"url": self.camera_stream_url}, timeout=2.0)
            except Exception as ex:
                logger.debug(f"Could not forward camera_stream_url: {ex}")

        if not has_mpp and self.calibrated_mpp is not None:
            try:
                self._query_vision("set_mpp", {"mpp": self.calibrated_mpp}, timeout=2.0)
            except Exception as ex:
                logger.debug(f"Failed to re-sync MPP: {ex}")

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
                    logger.debug(f"Failed to re-sync matrix: {ex}")

    def _query_vision(self, endpoint: str, payload: Optional[Dict[str, Any]] = None, timeout: float = 2.0) -> Dict[str, Any]:
        """
        Sends a non-blocking JSON query to the Vision Service with strict timeout.
        Dispatches network I/O to a background thread while pumping reactor.pause()
        on the Klipper main thread to avoid blocking Klipper's reactor event loop.
        """
        url = f"{self.server_url}/{endpoint.lstrip('/')}"
        data_bytes = json.dumps(payload).encode("utf-8") if payload else None
        headers = {"Content-Type": "application/json", "User-Agent": "ToolCalibrator/1.0"}
        if getattr(self, "session_token", None):
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
            self.reactor.pause(self.reactor.monotonic() + 0.05)

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

    def _sample_burst(self, toolhead, gcmd=None, samples: Optional[int] = None) -> Optional[Dict[str, Any]]:
        """
        Multi-frame Burst Sampling with Consensus Filtering:
        Pulls consecutive frames separated by `sample_delay` to filter mechanical vibrations,
        streamer buffer lag, and transient sensor noise. Rejects inconsistent bursts where dispersion
        exceeds 15px and no majority consensus cluster exists.
        """
        n_samples = samples if samples is not None else self.centering_samples
        toolhead.wait_moves()
        settle_time = max(0.12, self.sample_delay)
        self.reactor.pause(self.reactor.monotonic() + settle_time)

        valid_frames = []
        for i in range(n_samples):
            if i > 0:
                self.reactor.pause(self.reactor.monotonic() + self.sample_delay)
            try:
                resp = self._query_vision("detect_nozzle", {"min_matches": 1, "timeout": 3.0}, timeout=5.0)
                if resp.get("found") and resp.get("center_uv") and len(resp.get("center_uv")) >= 2:
                    valid_frames.append(resp)
            except Exception as ex:
                logger.debug(f"[tool_calibrator] Query vision burst exception: {ex}")

        if not valid_frames:
            return None

        u_vals = [float(f["center_uv"][0]) for f in valid_frames]
        v_vals = [float(f["center_uv"][1]) for f in valid_frames]
        r_vals = [float(f.get("radius_px", f.get("radius", 0.0))) for f in valid_frames if f.get("radius_px") or f.get("radius")]

        spread_px = 0.0
        if len(u_vals) > 1:
            u_spread = max(u_vals) - min(u_vals)
            v_spread = max(v_vals) - min(v_vals)
            spread_px = round(max(u_spread, v_spread), 2)

        # Check consensus when dispersion exceeds 15.0 px
        if len(valid_frames) > 1 and spread_px > 15.0:
            consensus_cluster = []
            for f in valid_frames:
                u0, v0 = float(f["center_uv"][0]), float(f["center_uv"][1])
                cluster = [
                    f_other for f_other in valid_frames
                    if math.hypot(float(f_other["center_uv"][0]) - u0, float(f_other["center_uv"][1]) - v0) <= 15.0
                ]
                if len(cluster) > len(consensus_cluster):
                    consensus_cluster = cluster

            majority_needed = (len(valid_frames) + 1) // 2
            if len(consensus_cluster) < 2 or len(consensus_cluster) < majority_needed:
                logger.warning(
                    f"[tool_calibrator] Inconsistent burst rejected: spread={spread_px}px across {len(valid_frames)} frames with no consensus."
                )
                return {
                    "found": False,
                    "reason": f"Inconsistent burst dispersion ({spread_px}px) without consensus"
                }

        med_u = float(statistics.median(u_vals))
        med_v = float(statistics.median(v_vals))
        med_r = float(statistics.median(r_vals)) if r_vals else 0.0

        best_meta = valid_frames[0]
        return {
            "found": True,
            "center_uv": [round(med_u, 2), round(med_v, 2)],
            "radius_px": round(med_r, 2),
            "confidence": best_meta.get("confidence", 0.0),
            "tier": best_meta.get("tier", 1),
            "combo": best_meta.get("combo", 0),
            "burst_count": len(valid_frames),
            "burst_total": n_samples,
            "spread_px": spread_px
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
        until nozzle center converges within `tolerance_mm`. Employs Burst Sampling and
        Adaptive Wiggle Recovery on lost frames.
        """
        wiggle_enabled = enable_wiggle if enable_wiggle is not None else self.wiggle_on_failure

        self._ensure_vision_sync()

        dx, dy = 0.0, 0.0
        for iteration in range(1, self.max_centering_iterations + 1):
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
            dx, dy = offset_resp.get("offset_xy", [0.0, 0.0])
            raw_err = offset_resp.get("raw_error_mm", [dx / 0.55 if abs(dx) > 1e-6 else 0.0, dy / 0.55 if abs(dy) > 1e-6 else 0.0])
            raw_dx, raw_dy = float(raw_err[0]), float(raw_err[1])

            tier_desc = "Tier 0 Curvature" if burst_resp.get("combo") == 10 else f"Tier {burst_resp.get('tier', 1)}"
            burst_desc = f"Burst {burst_resp.get('burst_count')}/{burst_resp.get('burst_total')} (spread: {burst_resp.get('spread_px')}px)"
            if gcmd:
                gcmd.respond_info(
                    f"  -> Centering Step {iteration}: True Error X{raw_dx:+.3f}mm Y{raw_dy:+.3f}mm "
                    f"(Damped Delta: X{dx:+.3f}mm Y{dy:+.3f}mm, UV: {center_uv}, {burst_desc}, {tier_desc})"
                )

            # Check convergence against true physical error before damping
            if abs(raw_dx) <= self.tolerance_mm and abs(raw_dy) <= self.tolerance_mm:
                if gcmd:
                    gcmd.respond_info(f"  -> Convergence achieved within {self.tolerance_mm}mm tolerance.")
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
            if abs(float(v_raw[0])) <= self.tolerance_mm and abs(float(v_raw[1])) <= self.tolerance_mm:
                if gcmd:
                    gcmd.respond_info(
                        f"  -> Final verification confirmed convergence within {self.tolerance_mm}mm "
                        f"(True Error: X{float(v_raw[0]):+.3f}mm Y{float(v_raw[1]):+.3f}mm)."
                    )
                return

        raise SafeNavigatorException(
            f"[ERR_CV_202] Centering failed to converge within {self.tolerance_mm}mm after {self.max_centering_iterations} iterations (last delta: X{dx:+.3f}mm Y{dy:+.3f}mm)."
        )

    def _get_known_printer_tools(self) -> Set[int]:
        """Discovers all valid toolhead numbers existing in Klipper configuration."""
        known: Set[int] = {self.reference_tool}

        # 1. Query toolchanger object if present
        tc = self.printer.lookup_object("toolchanger", None)
        if tc is not None:
            tool_nums = getattr(tc, "tool_numbers", None)
            if tool_nums and isinstance(tool_nums, (list, tuple, set)):
                for n in tool_nums:
                    try:
                        known.add(int(n))
                    except (ValueError, TypeError):
                        pass
            tools_dict = getattr(tc, "tools", None)
            if tools_dict and isinstance(tools_dict, dict):
                for k in tools_dict.keys():
                    try:
                        known.add(int(str(k).lstrip("tT")))
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

        for name in names:
            m_tool = re.match(r"^tool\s+(?:t)?(\d+)$", name, re.IGNORECASE)
            if m_tool:
                known.add(int(m_tool.group(1)))
                continue
            m_macro = re.match(r"^gcode_macro\s+t(\d+)$", name, re.IGNORECASE)
            if m_macro:
                known.add(int(m_macro.group(1)))
                continue
            m_ext = re.match(r"^extruder(\d*)$", name)
            if m_ext:
                idx = int(m_ext.group(1)) if m_ext.group(1) else 0
                known.add(idx)

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
            if self.reference_tool not in selected:
                return [self.reference_tool] + selected
            return [self.reference_tool] + [t for t in selected if t != self.reference_tool]

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
                    return [self.reference_tool] + [t for t in sorted(set(cfg_tools)) if t != self.reference_tool]
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

    def _execute_xy_calibration(self, tool_no: int, toolhead, gcode_move, gcmd, reference_origin_xy: Optional[List[float]], tool_offsets: Dict[str, float], target_focal_z: Optional[float] = None, samples: Optional[int] = None, enable_wiggle: Optional[bool] = None) -> List[float]:
        """Executes optical camera alignment for a single tool."""
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

    def _execute_z_calibration(self, tool_no: int, toolhead, gcode_move, gcmd, reference_z_result: Dict[str, Any], tool_offsets: Dict[str, float]) -> Dict[str, Any]:
        """Executes Z probing alignment for a single tool."""
        gcmd.respond_info(f"[T{tool_no}] Entering Z Probe Station...")
        if self.z_backend_type == "switch":
            self.navigator.approach_switch(toolhead, gcode_move)
        else:
            self.navigator.move_to_safe_z(toolhead, gcode_move)
            probe_x, probe_y = self.z_backend.get_probe_xy()
            toolhead.manual_move([probe_x, probe_y, None], self.navigator.travel_speed)

        if tool_no == self.reference_tool:
            ref_z = self.z_backend.probe_reference_tool(tool_no, gcmd)
            gcmd.respond_info(f"[T{tool_no}] Reference Z baseline established ({ref_z.get('source')})")
            tool_offsets["z"] = 0.0
            self.navigator.depart_station(toolhead, gcode_move)
            return ref_z
        else:
            z_res = self.z_backend.probe_secondary_tool(tool_no, reference_z_result, gcmd)
            tool_offsets["z"] = z_res.get("suggested_z_offset", 0.0)
            gcmd.respond_info(f"[T{tool_no}] Calculated Z Offset: Z{tool_offsets['z']:+.3f}mm")
            self.navigator.depart_station(toolhead, gcode_move)
            return reference_z_result

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
        calibrate_xy = gcmd.get_int("CALIBRATE_XY", 1) == 1
        calibrate_z = gcmd.get_int("CALIBRATE_Z", 1) == 1
        save_config = gcmd.get_int("SAVE_CONFIG", 1) == 1
        dry_run = gcmd.get_int("DRY_RUN", 0) == 1
        clean_nozzle = gcmd.get_int("CLEAN_NOZZLE", 0) == 1
        order = gcmd.get("ORDER", "XY_FIRST").upper()
        compensate_focal_z = gcmd.get_int("COMPENSATE_FOCAL_Z", 0) == 1
        tools_param = gcmd.get("TOOLS", None)
        samples_param = gcmd.get_int("SAMPLES", self.centering_samples)
        wiggle_param = gcmd.get_int("WIGGLE", 1 if self.wiggle_on_failure else 0) == 1

        toolhead = self.printer.lookup_object("toolhead")
        gcode_move = self.printer.lookup_object("gcode_move")

        if not self.navigator.is_homed():
            raise gcmd.error("[ERR_PRE_001] Printer must be fully homed (G28) before calibration.")

        session_token = None
        # Pre-flight ping to vision service & session lock
        if calibrate_xy:
            try:
                self._ensure_vision_sync()
                lock_resp = self._query_vision("acquire_lock", {"client_id": "klipper", "timeout_seconds": 600}, timeout=2.0)
                session_token = lock_resp.get("session_token")
                self.session_token = session_token
                health = self._query_vision("health")
                gcmd.respond_info(f"[tool_calibrator] Vision Service connected: {health.get('service')} v{health.get('version')}")
            except Exception as ex:
                raise gcmd.error(str(ex))

        # Dynamic inheritance check for switch coordinates
        if calibrate_z and self.z_backend_type == "switch":
            self._sync_switch_location_from_tools_calibrate()

        # Discover tool sequence across any toolchanger flavor
        try:
            ordered_tools = self._discover_tools(tools_param)
        except SafeNavigatorException as ex:
            if session_token:
                try:
                    self._query_vision("release_lock", {"session_token": session_token}, timeout=2.0)
                except Exception:
                    pass
                self.session_token = None
            raise gcmd.error(str(ex))

        gcmd.respond_info(f"[tool_calibrator] Starting Calibration Sequence across tools: {ordered_tools} (Order: {order}, Dry Run: {dry_run})")

        # Execute start_gcode hook
        if self.start_gcode is not None:
            self._run_tool_hook(self.start_gcode, ordered_tools[0])

        reference_origin_xy: Optional[List[float]] = None
        reference_z_result: Dict[str, Any] = {}
        results: Dict[int, Dict[str, float]] = {}

        try:
            for tool_no in ordered_tools:
                gcmd.respond_info(f"\n--- Calibrating Toolhead T{tool_no} ---")

                # Change tool
                if self.before_pickup_gcode is not None:
                    self._run_tool_hook(self.before_pickup_gcode, tool_no)
                self.gcode.run_script_from_command(f"T{tool_no}")
                if self.after_pickup_gcode is not None:
                    self._run_tool_hook(self.after_pickup_gcode, tool_no)

                toolhead.wait_moves()

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
                        reference_z_result = self._execute_z_calibration(
                            tool_no, toolhead, gcode_move, gcmd, reference_z_result, tool_offsets
                        )
                    if calibrate_xy:
                        focal_z = None
                        if compensate_focal_z and tool_no != self.reference_tool and "z" in tool_offsets:
                            focal_z = self.navigator.cam_target_z + tool_offsets["z"]
                        reference_origin_xy = self._execute_xy_calibration(
                            tool_no, toolhead, gcode_move, gcmd, reference_origin_xy, tool_offsets, focal_z,
                            samples=samples_param, enable_wiggle=wiggle_param
                        )
                else:
                    if calibrate_xy:
                        reference_origin_xy = self._execute_xy_calibration(
                            tool_no, toolhead, gcode_move, gcmd, reference_origin_xy, tool_offsets,
                            samples=samples_param, enable_wiggle=wiggle_param
                        )
                    if calibrate_z and not dry_run:
                        reference_z_result = self._execute_z_calibration(
                            tool_no, toolhead, gcode_move, gcmd, reference_z_result, tool_offsets
                        )

                results[tool_no] = tool_offsets

            # Restore Reference Tool
            self.gcode.run_script_from_command(f"T{self.reference_tool}")
            self.navigator.move_to_safe_z(toolhead, gcode_move)

            # Execute finish_gcode hook
            if self.finish_gcode is not None:
                self._run_tool_hook(self.finish_gcode, self.reference_tool)

            # Persist Offsets
            if save_config and not dry_run:
                self.config_manager.save_all_tool_offsets(results)
                gcmd.respond_info(f"[tool_calibrator] Successfully saved offsets to {self.config_manager.config_path}")

            # Telemetry Summary
            gcmd.respond_info("\n================ CALIBRATION SUMMARY ================")
            for t_num, offs in results.items():
                parts = [f"{k.upper()}={v:+.3f}mm" for k, v in offs.items()]
                gcmd.respond_info(f"Tool T{t_num}: {'  '.join(parts) if parts else 'No new offsets measured'}")
            gcmd.respond_info("=====================================================")

            self.cached_offsets = results
            self.last_run_status = "SUCCESS"

        except Exception as ex:
            self.last_run_status = f"FAILED: {ex}"
            self.navigator.depart_station(toolhead, gcode_move)
            gcmd.respond_info(f"!! [tool_calibrator] Calibration Aborted: {ex}")
            raise gcmd.error(f"[tool_calibrator] Calibration Aborted: {ex}")
        finally:
            if session_token:
                try:
                    self._query_vision("release_lock", {"session_token": session_token}, timeout=2.0)
                except Exception:
                    pass
                self.session_token = None

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
            raise gcmd.error("STATION must be CAMERA or SWITCH.")

        toolhead = self.printer.lookup_object("toolhead")
        if not self.navigator.is_homed():
            raise gcmd.error("[ERR_PRE_001] Printer must be fully homed (G28) before teaching station.")

        if station == "CAMERA":
            auto_center = gcmd.get_int("AUTO_CENTER", 1) == 1
            approach_dist = gcmd.get_float("APPROACH_DIST", 25.0)

            if auto_center:
                gcmd.respond_info("[tool_calibrator] Auto-centering nozzle over camera via visual servoing...")
                self._set_inspection_lighting(True, self.reference_tool)
                try:
                    self._center_nozzle(toolhead, gcmd)
                except Exception as ex:
                    raise gcmd.error(f"[ERR_TEACH_CAM] Auto-centering failed during teach station: {ex}. Station not saved.")
                finally:
                    self._set_inspection_lighting(False, self.reference_tool)

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

            gcmd.respond_info(
                f"✔ [CAMERA Station Configured & Saved Automatically]\n"
                f"  Target:   X{target_x:.3f} Y{target_y:.3f} Z{target_z:.3f}\n"
                f"  Approach: X{app_x:.3f} Y{app_y:.3f} (Vector towards bed center)\n"
                f"  Safe Z:   {self.navigator.safe_z} mm\n"
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

            gcmd.respond_info(
                f"✔ [SWITCH Station Configured & Saved Automatically]\n"
                f"  Target:   X{target_x:.3f} Y{target_y:.3f} Z{target_z:.3f}\n"
                f"  Approach: X{app_x:.3f} Y{app_y:.3f} (Vector towards bed center)\n"
                f"  Safe Z:   {self.navigator.safe_z} mm\n"
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

        session_token = None
        try:
            self._ensure_vision_sync()
            lock_resp = self._query_vision("acquire_lock", {"client_id": "klipper", "timeout_seconds": 600}, timeout=2.0)
            session_token = lock_resp.get("session_token")
            self.session_token = session_token
        except Exception:
            pass

        gcmd.respond_info(f"[tool_calibrator] Starting Star-Pattern Camera Calibration (Displacement: ±{dist:.2f}mm)...")
        self._set_inspection_lighting(True, self.reference_tool)

        try:
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
                toolhead.manual_move([tx, ty, None], self.navigator.approach_speed)
                toolhead.wait_moves()

                det = self._sample_burst(toolhead, gcmd)
                if not det or not det.get("found"):
                    gcmd.respond_info(f"!! Failed to detect nozzle during displacement {label}.")
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
                self.navigator.depart_station(toolhead, gcode_move)
                raise gcmd.error("Insufficient valid points acquired for camera scale calibration.")

            # Query server to calculate average MPP and solve affine matrix
            mpp_resp = self._query_vision("calibrate_mpp", {"samples": mpp_samples})
            solved_mpp = float(mpp_resp.get("mpp", 0.0))

            matrix_resp = self._query_vision("solve_matrix", {"calibration_points": matrix_points})
            matrix_ok = matrix_resp.get("success", False)
            matrix_vals = matrix_resp.get("matrix", [])
            self.calibrated_mpp = solved_mpp

            cam_dict: Dict[str, Any] = {
                "mpp": solved_mpp,
                "target_x": round(cx, 3),
                "target_y": round(cy, 3),
                "target_z": round(cz, 3),
                "safe_z": self.navigator.safe_z
            }
            if matrix_vals and len(matrix_vals) >= 2 and len(matrix_vals[0]) >= 2:
                cam_dict["matrix_a"] = matrix_vals[0][0]
                cam_dict["matrix_b"] = matrix_vals[0][1]
                cam_dict["matrix_tx"] = matrix_vals[0][2] if len(matrix_vals[0]) >= 3 else 0.0
                cam_dict["matrix_c"] = matrix_vals[1][0]
                cam_dict["matrix_d"] = matrix_vals[1][1]
                cam_dict["matrix_ty"] = matrix_vals[1][2] if len(matrix_vals[1]) >= 3 else 0.0

            # Persist calibrated MPP and matrix into tool_offsets.cfg under [tool_calibrator_station camera]
            self.config_manager.save_section("tool_calibrator_station camera", cam_dict)

            gcmd.respond_info(
                f"\n✔ ================= CAMERA CALIBRATION SUCCESS ================\n"
                f"  Calculated Scale (MPP): {solved_mpp:.6f} mm/pixel\n"
                f"  Affine Matrix Solved:   {matrix_ok}\n"
                f"  Saved to Configuration: {self.config_manager.config_path}\n"
                f"================================================================"
            )

        finally:
            if session_token:
                try:
                    self._query_vision("release_lock", {"session_token": session_token}, timeout=2.0)
                except Exception:
                    pass
                self.session_token = None
            self._set_inspection_lighting(False, self.reference_tool)
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

        if pos_type == "SAFE_Z":
            self.navigator.safe_z = round(pos[2], 3)
            gcmd.respond_info(f"Global Safe_Z set to Z:{pos[2]:.3f}")
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
        try:
            restored_file = self.config_manager.rollback()
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
            gcmd.respond_info("[tool_calibrator] Approaching Camera Station via safe 3-tier waypoints...")
            self._set_inspection_lighting(True, self.reference_tool)
            try:
                self.navigator.approach_camera(toolhead, gcode_move)
                pos = toolhead.get_position()
                gcmd.respond_info(f"✔ Reached Camera Station: X{pos[0]:.3f} Y{pos[1]:.3f} Z{pos[2]:.3f}")
            except Exception as ex:
                self._set_inspection_lighting(False, self.reference_tool)
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
            gcmd.respond_info("[tool_calibrator] Departing station to safe Z altitude...")
            self._set_inspection_lighting(False, self.reference_tool)
            self.navigator.depart_station(toolhead, gcode_move)
            pos = toolhead.get_position()
            gcmd.respond_info(f"✔ Departed station. Safe altitude: Z{pos[2]:.3f}")
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
        wiggle = gcmd.get_int("WIGGLE", 1 if self.wiggle_on_failure else 0) == 1

        if not self.navigator.is_homed():
            raise gcmd.error("[ERR_PRE_001] Printer must be fully homed (G28).")

        gcmd.respond_info("[tool_calibrator] Centering active nozzle over camera...")
        self._set_inspection_lighting(True, self.reference_tool)
        try:
            self._center_nozzle(toolhead, gcmd, samples=samples, enable_wiggle=wiggle)
            pos = toolhead.get_position()
            gcmd.respond_info(f"✔ Nozzle centered successfully at X{pos[0]:.3f} Y{pos[1]:.3f}")
        except Exception as ex:
            raise gcmd.error(f"Centering failed: {ex}")
        finally:
            self._set_inspection_lighting(False, self.reference_tool)

    def cmd_CALIBRATION_TEST_VISION(self, gcmd) -> None:
        """
        Test vision detection at current toolhead position without moving.
        Reports detected center UV, radius, confidence, and burst dispersion.
        """
        toolhead = self.printer.lookup_object("toolhead")
        samples = gcmd.get_int("SAMPLES", self.centering_samples)

        gcmd.respond_info(f"[tool_calibrator] Sampling {samples} vision frames at current position...")
        self._set_inspection_lighting(True, self.reference_tool)
        try:
            burst = self._sample_burst(toolhead, gcmd, samples=samples)
            if not burst or not burst.get("found"):
                raise gcmd.error("❌ Nozzle NOT detected at current position. Check lighting, focal distance, or nozzle alignment.")

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
            raise gcmd.error(f"Vision test error: {ex}")
        finally:
            self._set_inspection_lighting(False, self.reference_tool)

    def cmd_CALIBRATION_STATUS(self, gcmd) -> None:
        """Emits current calibration state and cached offsets."""
        gcmd.respond_info(f"Tool-Calibrator Status: {self.last_run_status}")
        gcmd.respond_info(f"Safe_Z: {self.navigator.safe_z:.2f}mm | Backend: {self.z_backend_type}")
        for t, offs in self.cached_offsets.items():
            parts = [f"{k.upper()}={v:+.3f}" for k, v in offs.items()]
            gcmd.respond_info(f"  T{t}: {' '.join(parts) if parts else 'None'}")

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
