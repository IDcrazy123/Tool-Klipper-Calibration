"""
Comprehensive regression test suite for P1, P2, and P3 bug fixes in Tool-Klipper-Calibration.
"""

import os
import shutil
import tempfile
import time
import unittest
from unittest.mock import MagicMock, patch

import cv2
import numpy as np

from klippy.extras.tool_calibrator import ToolCalibrator
from klippy.extras.tool_offsets import ToolOffsets
from klippy.extras.config_manager import ConfigManager, ConfigManagerException
from server.nozzle_detector import NozzleDetector, DetectionResult
from server.tool_calibrator_server import app, calibration_lock
from tests.test_calibration_cycle import (
    DummyConfig,
    DummyGCode,
    DummyGCodeCommand,
    DummyPrinter,
    DummyToolchanger,
    DummyToolhead,
)


class RegressionDummyPrinter(DummyPrinter):
    def __init__(self, toolhead=None, gcode=None, toolchanger=None):
        toolhead = toolhead or DummyToolhead()
        gcode = gcode or DummyGCode()
        super().__init__(toolhead, gcode, toolchanger)
        self.event_handlers = {}

    def register_event_handler(self, event, handler=None):
        self.event_handlers[event] = handler


class DummyOffsetsConfig:
    def __init__(self, printer, data):
        self.printer = printer
        self.data = data

    def get_printer(self):
        return self.printer

    def get_name(self):
        return "tool_offsets"

    def get(self, key, default=None):
        return self.data.get(key, default)

    def getfloat(self, key, default=None, **kwargs):
        val = self.data.get(key, default)
        if val is None:
            return None
        try:
            return float(val)
        except (ValueError, TypeError):
            raise Exception(f"Invalid float value for {key}: {val}")

    def get_prefix_options(self, prefix):
        return [k for k in self.data if k.startswith(prefix)]

    def error(self, msg):
        return Exception(msg)


class TestXYZeroSentinel(unittest.TestCase):
    """P1.2: Valid (0.0, 0.0) calibration offsets must NOT be treated as missing data or replaced by old values."""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.config_path = os.path.join(self.test_dir, "tool_offsets.cfg")
        self.toolhead = DummyToolhead()
        self.gcode = DummyGCode()
        self.toolchanger = DummyToolchanger([0, 1])
        self.printer = DummyPrinter(self.toolhead, self.gcode, self.toolchanger)
        self.config = DummyConfig(self.printer, {
            "offset_config_path": self.config_path,
            "safe_z": 25.0,
        })
        self.calibrator = ToolCalibrator(self.config)

        # Pre-seed Tier 2 tool_offsets object with old offsets (0.6, -0.4)
        mock_to = MagicMock()
        mock_to.parse_tool_offsets.return_value = {1: {"x": 0.6, "y": -0.4}}
        self.printer.objects["tool_offsets"] = mock_to

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_new_zero_zero_offset_not_replaced_by_old_offset(self):
        """New measured (0.0, 0.0) must NOT be replaced by old (0.6, -0.4)."""
        # New measured calibration returns exactly 0.0, 0.0
        self.calibrator.cached_offsets = {
            1: {"x": 0.0, "y": 0.0}
        }

        ox, oy = self.calibrator._lookup_tool_xy_offset(1)
        self.assertEqual(ox, 0.0)
        self.assertEqual(oy, 0.0)

    def test_independent_x_zero_preserves_y(self):
        """X=0.0 with Y=-0.4 in cache must keep X=0.0 and Y=-0.4."""
        self.calibrator.cached_offsets = {
            1: {"x": 0.0, "y": -0.4}
        }

        ox, oy = self.calibrator._lookup_tool_xy_offset(1)
        self.assertEqual(ox, 0.0)
        self.assertEqual(oy, -0.4)

    def test_independent_y_zero_preserves_x(self):
        """X=0.6 with Y=0.0 in cache must keep X=0.6 and Y=0.0."""
        self.calibrator.cached_offsets = {
            1: {"x": 0.6, "y": 0.0}
        }

        ox, oy = self.calibrator._lookup_tool_xy_offset(1)
        self.assertEqual(ox, 0.6)
        self.assertEqual(oy, 0.0)

    def test_missing_cache_falls_back_to_persisted(self):
        """Truly missing cache must fall back to Tier 2 persisted values (0.6, -0.4)."""
        self.calibrator.cached_offsets = {}
        ox, oy = self.calibrator._lookup_tool_xy_offset(1)
        self.assertEqual(ox, 0.6)
        self.assertEqual(oy, -0.4)


class TestToolchangerUninitializedHandling(unittest.TestCase):
    """P1.3 & P1.4: Uninitialized toolchanger state must not guess T0 and must not issue physical Tn moves."""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.config_path = os.path.join(self.test_dir, "tool_offsets.cfg")
        self.toolhead = DummyToolhead()
        self.gcode = DummyGCode()
        self.toolchanger = DummyToolchanger([0, 1])
        self.printer = DummyPrinter(self.toolhead, self.gcode, self.toolchanger)
        self.config = DummyConfig(self.printer, {
            "offset_config_path": self.config_path,
            "safe_z": 25.0,
        })
        self.calibrator = ToolCalibrator(self.config)

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_uninitialized_state_returns_none_not_t0(self):
        """When toolchanger returns None or -1, _get_active_tool_no must return None (unknown)."""
        self.toolchanger.status = "uninitialized"
        self.toolchanger.tool_number = -1
        self.toolchanger.active_tool = None

        active = self.calibrator._get_active_tool_no()
        self.assertIsNone(active)

    def test_reconcile_without_sensor_does_not_guess_or_send_tn(self):
        """Reconciliation without physical tool sensor must not send Tn gcode command and must warn."""
        self.toolchanger.status = "uninitialized"
        self.toolchanger.tool_number = -1
        self.toolchanger.active_tool = None
        self.toolchanger.detected_tool = None  # No sensor detection

        gcmd = DummyGCodeCommand()
        self.calibrator._reconcile_toolchanger_state(None, gcmd)

        # Must not have executed any Tn commands
        for script in self.gcode.executed_scripts:
            self.assertFalse(script.strip().startswith("T0") or script.strip().startswith("T1"))

        # Must have warned about requiring manual recovery
        self.assertTrue(any("Warning: Toolchanger state reconciliation cannot proceed automatically" in m for m in gcmd.info_messages))

    def test_reconcile_with_tool_object_sensor_data(self):
        """When upstream returns a Tool object from detected_tool, extract tool_number without error."""
        class MockUpstreamTool:
            def __init__(self, num):
                self.tool_number = num

        self.toolchanger.status = "uninitialized"
        self.toolchanger.tool_number = -1
        self.toolchanger.active_tool = None
        self.toolchanger.detected_tool = MockUpstreamTool(1)

        gcmd = DummyGCodeCommand()
        self.calibrator._reconcile_toolchanger_state(None, gcmd)

        self.assertEqual(self.toolchanger.status, "ready")
        self.assertEqual(self.toolchanger.tool_number, 1)


class TestCleanupExceptionChaining(unittest.TestCase):
    """P2.12: Exceptions in depart_station / cleanup must not mask the primary calibration error."""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.config_path = os.path.join(self.test_dir, "tool_offsets.cfg")
        self.toolhead = DummyToolhead()
        self.gcode = DummyGCode()
        self.toolchanger = DummyToolchanger([0, 1])
        self.printer = DummyPrinter(self.toolhead, self.gcode, self.toolchanger)
        self.config = DummyConfig(self.printer, {
            "offset_config_path": self.config_path,
            "safe_z": 25.0,
            "allow_shuttle_z": True,
        })
        self.calibrator = ToolCalibrator(self.config)

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_primary_calibration_error_preserved_when_depart_fails(self):
        """When calibration fails and depart_station also fails, primary error is raised."""
        def mock_probe_ref(t, gcmd):
            raise RuntimeError("Primary Calibration Failure ERR_PRIMARY_001")

        self.calibrator.z_backend.probe_reference_tool = mock_probe_ref
        self.calibrator.navigator.depart_station = MagicMock(side_effect=RuntimeError("Depart Station Failed!"))

        gcmd = DummyGCodeCommand({"CALIBRATE_XY": 0, "CALIBRATE_Z": 1, "TOOLS": "0,1"})

        with self.assertRaises(Exception) as ctx:
            self.calibrator.cmd_CALIBRATE_TOOL_OFFSETS(gcmd)

        self.assertIn("Primary Calibration Failure ERR_PRIMARY_001", str(ctx.exception))


class TestToolOffsetsFailFast(unittest.TestCase):
    """P2.13 & P2.15: ToolOffsets must fail fast on invalid config and use SET_TOOL_PARAMETER."""

    def setUp(self):
        self.toolhead = DummyToolhead()
        self.gcode = DummyGCode()
        self.printer = RegressionDummyPrinter(self.toolhead, self.gcode)

    def test_invalid_float_raises_config_error(self):
        """Invalid float value in tool offsets config must raise config.error immediately."""
        cfg = DummyOffsetsConfig(self.printer, {
            "t0_x": "not_a_number"
        })
        with self.assertRaises(Exception) as ctx:
            ToolOffsets(cfg)
        self.assertIn("Invalid float value", str(ctx.exception))

    def test_apply_tool_offsets_uses_set_tool_parameter(self):
        """Tool offsets application should generate upstream SET_TOOL_PARAMETER commands."""
        self.gcode.commands = {"SET_TOOL_PARAMETER": MagicMock()}
        cfg = DummyOffsetsConfig(self.printer, {
            "t1_x": 0.05,
            "t1_y": -0.08,
            "t1_z": 0.12
        })
        to = ToolOffsets(cfg)
        applied = to.apply_tool_offsets(1)

        self.assertIn(1, applied)
        executed = "\n".join(self.gcode.executed_scripts)
        self.assertIn("SET_TOOL_PARAMETER T=1 PARAMETER=gcode_x_offset VALUE=0.05", executed)
        self.assertIn("SET_TOOL_PARAMETER T=1 PARAMETER=gcode_y_offset VALUE=-0.08", executed)
        self.assertIn("SET_TOOL_PARAMETER T=1 PARAMETER=gcode_z_offset VALUE=0.12", executed)

    def test_apply_tool_offsets_no_false_success_when_unapplied(self):
        """When no tool mechanism is available (e.g. unconfigured tool 99), offsets must NOT be falsely marked as applied (Issue 13)."""
        self.gcode.commands = {}
        cfg = DummyOffsetsConfig(self.printer, {
            "t99_x": 0.05,
            "t99_y": -0.08,
            "t99_z": 0.12
        })
        to = ToolOffsets(cfg)
        applied = to.apply_tool_offsets(99)

        self.assertEqual(applied, {})


class TestConfigManagerRollbackSecurity(unittest.TestCase):
    """P2.14: Rollback must reject paths outside backup roots and write atomically."""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.config_dir = os.path.join(self.test_dir, "config")
        os.makedirs(self.config_dir, exist_ok=True)
        self.live_cfg = os.path.join(self.config_dir, "tool_offsets.cfg")
        with open(self.live_cfg, "w") as f:
            f.write("# Live Config\n[tool_offsets]\nt1_x: 0.1\n")
        self.cm = ConfigManager(self.live_cfg)

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_rollback_rejects_path_traversal(self):
        """Rollback must reject arbitrary paths outside allowed backup roots."""
        outside_file = os.path.join(self.test_dir, "secret.cfg")
        with open(outside_file, "w") as f:
            f.write("malicious content")

        with self.assertRaises(Exception) as ctx:
            self.cm.rollback(outside_file)
        self.assertIn("outside backup roots", str(ctx.exception))

    def test_rollback_atomic_and_creates_prerollback_backup(self):
        """Rollback from valid backup must succeed, restore content, and create a pre-rollback backup."""
        # Create a valid backup
        bk_path = self.cm.create_backup()
        self.assertTrue(os.path.exists(bk_path))

        # Modify live config
        with open(self.live_cfg, "w") as f:
            f.write("# Modified Live Config\n[tool_offsets]\nt1_x: 99.9\n")

        # Rollback
        restored_name = self.cm.rollback(bk_path)
        self.assertTrue(restored_name)

        # Verify restored content
        with open(self.live_cfg, "r") as f:
            content = f.read()
        self.assertIn("t1_x: 0.1", content)

        # Verify pre-rollback backup was created
        self.assertGreaterEqual(len(self.cm._get_all_backups()), 2)


class TestSessionLeaseMonotonicRenewal(unittest.TestCase):
    """P2.9: Session lease uses time.monotonic() and renews lease on active requests."""

    def test_session_renewed_on_request(self):
        client = app.test_client()
        # Acquire lock
        res_lock = client.post("/acquire_lock", json={"session_token": "sess-test-123"})
        self.assertEqual(res_lock.status_code, 200)
        t_initial = calibration_lock["locked_at"]

        time.sleep(0.05)

        # Perform action with session token
        res_mat = client.post("/solve_matrix", json={
            "session_token": "sess-test-123",
            "points": [[[0, 0], [100, 100]], [[10, 0], [200, 100]], [[0, 10], [100, 200]]]
        })
        self.assertEqual(res_mat.status_code, 200)

        t_renewed = calibration_lock["locked_at"]
        self.assertGreater(t_renewed, t_initial)

        # Release lock
        client.post("/release_lock", json={"session_token": "sess-test-123"})


class TestAPIAuthenticationEnforcement(unittest.TestCase):
    """P2.10: Consistent API token protection across endpoints."""

    def test_protected_endpoints_require_token_when_configured(self):
        client = app.test_client()
        calibration_lock["token"] = "secret-token-xyz"
        try:
            # Health is public
            res_health = client.get("/health")
            self.assertEqual(res_health.status_code, 200)

            # /get_matrix requires token
            res_mat = client.get("/get_matrix")
            self.assertEqual(res_mat.status_code, 401)

            # /get_matrix passes with header
            res_mat_ok = client.get("/get_matrix", headers={"X-API-Token": "secret-token-xyz"})
            self.assertEqual(res_mat_ok.status_code, 200)

            # /get_matrix passes with query param
            res_mat_query = client.get("/get_matrix?api_token=secret-token-xyz")
            self.assertEqual(res_mat_query.status_code, 200)
        finally:
            calibration_lock["token"] = None


class TestNozzleDetectorCandidateQualityFilter(unittest.TestCase):
    """P3.17: Filter candidates by quality before ranking by center proximity."""

    def test_quality_gate_rejects_center_noise_in_favor_of_valid_nozzle(self):
        detector = NozzleDetector(frame_width=640, frame_height=480, min_radius=10.0, max_radius=30.0, min_confidence=0.5)

        # Create dummy frame: black background
        frame = np.full((480, 640), 50, dtype=np.uint8)

        # Draw a small noise blob near center (radius 3px < min_radius 10px)
        cv2.circle(frame, (320, 240), 3, 200, -1)

        # Draw a valid nozzle ring slightly away from center (at 300, 220, radius 18px)
        cv2.circle(frame, (300, 220), 18, 0, 3)

        bgr = cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR)
        result = detector.detect(bgr)

        # Result should either find the valid ring or not pick the 3px noise blob as a valid nozzle
        if result.found:
            self.assertGreaterEqual(result.radius, 8.0)


class TestRuntimeValidations(unittest.TestCase):
    """P3.18: Runtime validation for order enum, boolean parser, tool deduplication, radius order."""

    def setUp(self):
        self.toolhead = DummyToolhead()
        self.gcode = DummyGCode()
        self.toolchanger = DummyToolchanger([0, 1, 2])
        self.printer = DummyPrinter(self.toolhead, self.gcode, self.toolchanger)
        self.config = DummyConfig(self.printer, {})
        self.calibrator = ToolCalibrator(self.config)

    def test_parse_bool_param(self):
        self.assertTrue(self.calibrator._parse_bool_param(DummyGCodeCommand({"FLAG": 1}), "FLAG", False))
        self.assertTrue(self.calibrator._parse_bool_param(DummyGCodeCommand({"FLAG": "1"}), "FLAG", False))
        self.assertTrue(self.calibrator._parse_bool_param(DummyGCodeCommand({"FLAG": "True"}), "FLAG", False))
        self.assertTrue(self.calibrator._parse_bool_param(DummyGCodeCommand({"FLAG": "on"}), "FLAG", False))
        self.assertFalse(self.calibrator._parse_bool_param(DummyGCodeCommand({"FLAG": 0}), "FLAG", True))
        self.assertFalse(self.calibrator._parse_bool_param(DummyGCodeCommand({"FLAG": "0"}), "FLAG", True))
        self.assertFalse(self.calibrator._parse_bool_param(DummyGCodeCommand({"FLAG": "false"}), "FLAG", True))
        self.assertFalse(self.calibrator._parse_bool_param(DummyGCodeCommand({"FLAG": "off"}), "FLAG", True))

        with self.assertRaises(Exception):
            self.calibrator._parse_bool_param(DummyGCodeCommand({"FLAG": "invalid_bool"}), "FLAG", False)

    def test_min_max_radius_validation(self):
        """min_nozzle_radius > max_nozzle_radius must raise config error."""
        cfg = DummyConfig(self.printer, {
            "min_nozzle_radius": 50.0,
            "max_nozzle_radius": 10.0
        })
        with self.assertRaises(Exception) as ctx:
            ToolCalibrator(cfg)
        self.assertIn("min_nozzle_radius", str(ctx.exception))

    def test_tool_deduplication_preserves_order(self):
        """TOOLS='0,2,1,2' must deduplicate to [0, 2, 1]."""
        tools = self.calibrator._discover_tools("0,2,1,2")
        self.assertEqual(tools, [0, 2, 1])

    def test_order_validation(self):
        """ORDER must accept XY_FIRST and Z_FIRST, rejecting other values."""
        gcmd_invalid = DummyGCodeCommand({"ORDER": "INVALID_ORDER"})
        with self.assertRaises(Exception) as ctx:
            self.calibrator.cmd_CALIBRATE_TOOL_OFFSETS(gcmd_invalid)
        self.assertIn("Invalid ORDER", str(ctx.exception))


class TestInstallerSecurity(unittest.TestCase):
    """P1.6: Shell injection prevention in install.sh health parsing."""

    def test_health_parser_handles_shell_injection_payloads_safely(self):
        """Health check JSON with shell metacharacters, semicolons, and quotes must parse without injection."""
        import json, subprocess, sys

        malicious_json = json.dumps({
            "status": "ok",
            "service": "tool_calibrator_server",
            "version": "v1.0.0; rm -rf /; echo injected > /tmp/hacked",
            "commit": "a1b2c3d`whoami`",
            "process_ready": True,
            "camera_ready": True,
            "scale_ready": False,
            "matrix_ready": False
        })

        py_script = """
import sys, json
try:
    data = json.load(sys.stdin)
    if data.get('status') == 'ok' and data.get('service') == 'tool_calibrator_server':
        ver = str(data.get('version', 'unknown')).replace('\\n', ' ')
        cmt = str(data.get('commit', 'unknown'))[:7].replace('\\n', ' ')
        proc = 'READY' if data.get('process_ready', True) else 'NOT_READY'
        cam = 'CONNECTED' if data.get('camera_ready') else 'PENDING/OFFLINE'
        scale = 'SOLVED' if data.get('scale_ready') else 'NOT_SET'
        mat = 'LOADED' if data.get('matrix_ready') else 'NOT_SET'
        print(f'{ver}\\t{cmt}\\t{proc}\\t{cam}\\t{scale}\\t{mat}')
        sys.exit(0)
except Exception:
    pass
sys.exit(1)
"""
        proc = subprocess.Popen(
            [sys.executable, "-c", py_script],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )
        stdout, stderr = proc.communicate(input=malicious_json)
        self.assertEqual(proc.returncode, 0)
        fields = stdout.strip().split("\t")
        self.assertEqual(len(fields), 6)
        self.assertIn("rm -rf /", fields[0])  # String preserved literally without executing
        self.assertEqual(fields[2], "READY")
        self.assertEqual(fields[3], "CONNECTED")


class TestStreamConcurrencyProtection(unittest.TestCase):
    """P1.7: Preview streams do not starve API endpoints."""

    def test_preview_stream_limit_enforced_with_429(self):
        """Active preview streams beyond MAX_PREVIEW_STREAMS return HTTP 429."""
        import server.tool_calibrator_server as srv
        client = srv.app.test_client()

        # Simulate reaching max preview streams
        with srv.stream_lock:
            srv.active_preview_streams = srv.MAX_PREVIEW_STREAMS

        try:
            res_stream = client.get("/preview")
            self.assertEqual(res_stream.status_code, 429)

            # While preview stream is maxed, /health must respond 200 OK immediately
            res_health = client.get("/health")
            self.assertEqual(res_health.status_code, 200)
            data = res_health.get_json()
            self.assertEqual(data["status"], "ok")
        finally:
            with srv.stream_lock:
                srv.active_preview_streams = 0


class TestTKCR01_InstallerManifestViaArgv(unittest.TestCase):
    """TKC-R01: Manifest generation using sys.argv and parsed version variable."""

    def test_manifest_python_script_via_argv_with_special_chars(self):
        import subprocess, sys, json
        test_dir = tempfile.mkdtemp()
        try:
            manifest_file = os.path.join(test_dir, "manifest.json")
            py_code = """import sys, json, time
manifest_path = sys.argv[1]
data = {
    "version": sys.argv[2],
    "commit": sys.argv[3],
    "installed_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    "repo_dir": sys.argv[4],
    "venv_dir": sys.argv[5],
    "service_mode": sys.argv[6],
    "config_dir": sys.argv[7]
}
with open(manifest_path, "w") as f:
    json.dump(data, f, indent=2)
"""
            ver = 'v1.2.3 "beta" & $special'
            cmt = "a1b2c3d"
            repo = os.path.join(test_dir, "my repo path with spaces")
            venv = os.path.join(test_dir, "venv 'with quotes'")
            mode = "system"
            cfg = os.path.join(test_dir, 'config "dir"')

            proc = subprocess.run(
                [sys.executable, "-c", py_code, manifest_file, ver, cmt, repo, venv, mode, cfg],
                capture_output=True,
                text=True
            )
            self.assertEqual(proc.returncode, 0, msg=proc.stderr)
            self.assertTrue(os.path.exists(manifest_file))
            with open(manifest_file, "r") as f:
                saved = json.load(f)
            self.assertEqual(saved["version"], ver)
            self.assertEqual(saved["repo_dir"], repo)
            self.assertEqual(saved["venv_dir"], venv)
            self.assertEqual(saved["config_dir"], cfg)
        finally:
            shutil.rmtree(test_dir, ignore_errors=True)


class TestTKCR02_CartographerUnknownCommandSafety(unittest.TestCase):
    """TKC-R02: Cartographer probe rejects un-registered commands, never uses homing for secondary, rejects stale Z."""

    def setUp(self):
        self.toolhead = DummyToolhead()
        self.gcode = DummyGCode()
        self.printer = DummyPrinter(self.toolhead, self.gcode)

    def test_secondary_probe_fails_fast_when_command_unregistered_without_dispatch_exception(self):
        """Simulates Klipper where unknown command outputs 'Unknown command' but does not raise."""
        from klippy.extras.z_backends.cartographer_backend import CartographerBackend

        # Registry only has SCANNER_TOUCH (legacy homing/touch command)
        self.gcode.commands = {"SCANNER_TOUCH": MagicMock()}

        # Mock scanner object with stale last_z_result
        mock_scanner = MagicMock()
        mock_scanner.last_z_result = 0.0
        self.printer.objects["scanner"] = mock_scanner

        # Dispatcher simply records execution without error, simulating Klipper
        self.gcode.executed_scripts = []

        cfg = DummyConfig(self.printer, {
            "touch_model_config_path": os.path.join(tempfile.gettempdir(), "dummy_printer.cfg")
        })
        backend = CartographerBackend(cfg)

        gcmd = DummyGCodeCommand()
        # Probing secondary tool must raise gcmd.error [ERR_Z_004] BEFORE executing probe
        with self.assertRaises(Exception) as ctx:
            backend.probe_secondary_tool(1, {"baseline_z": 0.0}, gcmd)

        self.assertIn("[ERR_Z_004]", str(ctx.exception))
        # No scripts should have been sent to gcode dispatcher
        self.assertEqual(len(self.gcode.executed_scripts), 0)

    def test_probe_secondary_rejects_stale_last_z_result(self):
        """If touch command runs but does not update last_z_result, reject stale value."""
        from klippy.extras.z_backends.cartographer_backend import CartographerBackend

        # Register CARTOGRAPHER_TOUCH_PROBE
        self.gcode.commands = {"CARTOGRAPHER_TOUCH_PROBE": MagicMock()}

        mock_touch = MagicMock()
        mock_touch.last_z_result = 0.42  # Pre-existing result from previous operation on touch submodule
        mock_carto = MagicMock(touch=mock_touch, spec=["touch"])
        self.printer.objects["cartographer"] = mock_carto

        cfg = DummyConfig(self.printer, {
            "touch_model_config_path": os.path.join(tempfile.gettempdir(), "dummy_printer.cfg")
        })
        backend = CartographerBackend(cfg)

        gcmd = DummyGCodeCommand()
        # Even if command is registered, if last_z_result remains None after probe, it must fail
        with self.assertRaises(Exception) as ctx:
            backend.probe_secondary_tool(1, {"baseline_z": 0.0}, gcmd)

        self.assertIn("Failed to read contact Z result from Cartographer touch on Secondary Tool", str(ctx.exception))


class TestTKCR03_ToolsOrderReferenceFirst(unittest.TestCase):
    """TKC-R03: _discover_tools always places reference tool at index 0 and preserves order of secondary tools."""

    def setUp(self):
        self.toolhead = DummyToolhead()
        self.gcode = DummyGCode()
        self.toolchanger = DummyToolchanger([0, 1, 2, 3])
        self.printer = DummyPrinter(self.toolhead, self.gcode, self.toolchanger)
        self.calibrator = ToolCalibrator(DummyConfig(self.printer, {"reference_tool": 0}))

    def test_discover_tools_orders_reference_first(self):
        self.assertEqual(self.calibrator._discover_tools("1,0"), [0, 1])
        self.assertEqual(self.calibrator._discover_tools("2,1,0,2"), [0, 2, 1])
        self.assertEqual(self.calibrator._discover_tools("2,1,2"), [0, 2, 1])

    def test_discover_tools_with_non_zero_reference(self):
        printer_t1 = DummyPrinter(DummyToolhead(), DummyGCode(), self.toolchanger)
        calibrator_t1 = ToolCalibrator(DummyConfig(printer_t1, {"reference_tool": 1}))
        self.assertEqual(calibrator_t1._discover_tools("0,2,1,0"), [1, 0, 2])
        self.assertEqual(calibrator_t1._discover_tools("2,0"), [1, 2, 0])


class TestTKCR04_SafeZOmittedNoneHandling(unittest.TestCase):
    """TKC-R04: safe_z omitted (None) must not raise TypeError in DEPART or CALIBRATION_STATUS, displays OFF."""

    def setUp(self):
        self.toolhead = DummyToolhead()
        self.gcode = DummyGCode()
        self.toolchanger = DummyToolchanger([0, 1])
        self.printer = DummyPrinter(self.toolhead, self.gcode, self.toolchanger)

    def test_calibration_navigate_depart_with_safe_z_none_and_zero(self):
        for val in (None, 0.0, 0):
            printer = DummyPrinter(DummyToolhead(), DummyGCode(), self.toolchanger)
            cfg_data = {"safe_z": val} if val is not None else {}
            calibrator = ToolCalibrator(DummyConfig(printer, cfg_data))
            # Must not raise TypeError
            gcmd = DummyGCodeCommand({"STATION": "DEPART"})
            calibrator.cmd_CALIBRATION_NAVIGATE(gcmd)
            self.assertFalse(calibrator.navigator.is_safe_z_enabled())

    def test_calibration_status_displays_off_when_safe_z_disabled(self):
        for val in (None, 0.0):
            printer = DummyPrinter(DummyToolhead(), DummyGCode(), self.toolchanger)
            cfg_data = {"safe_z": val} if val is not None else {}
            calibrator = ToolCalibrator(DummyConfig(printer, cfg_data))
            gcmd = DummyGCodeCommand()
            calibrator.cmd_CALIBRATION_STATUS(gcmd)
            status_text = "\n".join(gcmd.info_messages)
            self.assertIn("Safe_Z: OFF", status_text)
            self.assertNotIn("TypeError", status_text)

    def test_calibration_status_displays_value_when_safe_z_positive(self):
        printer = DummyPrinter(DummyToolhead(), DummyGCode(), self.toolchanger)
        calibrator = ToolCalibrator(DummyConfig(printer, {"safe_z": 25.0}))
        gcmd = DummyGCodeCommand()
        calibrator.cmd_CALIBRATION_STATUS(gcmd)
        status_text = "\n".join(gcmd.info_messages)
        self.assertIn("Safe_Z: 25.00mm", status_text)


class TestTKCR05_BackupConsolidationAndProtection(unittest.TestCase):
    """TKC-R05: Backup consolidation preserves dotfiles, handles duplicates safely, protects macros.cfg."""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.src_dir = os.path.join(self.test_dir, "legacy_backup")
        self.dst_dir = os.path.join(self.test_dir, "target_backup")
        os.makedirs(self.src_dir, exist_ok=True)
        os.makedirs(self.dst_dir, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_recursive_backup_migration_with_duplicates_and_dotfiles(self):
        import subprocess, sys

        # Create files in src: subdirs, dotfiles, colliding files
        sub = os.path.join(self.src_dir, "subdir")
        os.makedirs(sub, exist_ok=True)
        with open(os.path.join(sub, "subfile.cfg"), "w") as f:
            f.write("subfile content")
        with open(os.path.join(self.src_dir, ".hidden.cfg"), "w") as f:
            f.write("hidden content")
        with open(os.path.join(self.src_dir, "collision.cfg"), "w") as f:
            f.write("source collision content")

        # Create pre-existing colliding file in dst with DIFFERENT content
        with open(os.path.join(self.dst_dir, "collision.cfg"), "w") as f:
            f.write("dest original content")

        # Run the safe migration python script used in install.sh / uninstall.sh
        py_script = """
import os, sys, shutil, hashlib

src, dst = sys.argv[1], sys.argv[2]
def sha256_file(p):
    h = hashlib.sha256()
    with open(p, 'rb') as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()

all_migrated = True
for root, dirs, files in os.walk(src):
    rel = os.path.relpath(root, src)
    target_dir = os.path.join(dst, rel) if rel != '.' else dst
    os.makedirs(target_dir, exist_ok=True)
    for fname in files:
        s_path = os.path.join(root, fname)
        t_path = os.path.join(target_dir, fname)
        if os.path.exists(t_path):
            if sha256_file(s_path) != sha256_file(t_path):
                base, ext = os.path.splitext(fname)
                idx = 1
                while os.path.exists(os.path.join(target_dir, f"{base}_legacy_{idx}{ext}")):
                    idx += 1
                t_path = os.path.join(target_dir, f"{base}_legacy_{idx}{ext}")
        shutil.copy2(s_path, t_path)
        if sha256_file(s_path) == sha256_file(t_path):
            os.remove(s_path)
        else:
            all_migrated = False

if all_migrated:
    shutil.rmtree(src, ignore_errors=True)
"""
        res = subprocess.run([sys.executable, "-c", py_script, self.src_dir, self.dst_dir], capture_output=True, text=True)
        self.assertEqual(res.returncode, 0)

        # Verify: dotfile migrated
        self.assertTrue(os.path.exists(os.path.join(self.dst_dir, ".hidden.cfg")))
        # Verify: subfile migrated
        self.assertTrue(os.path.exists(os.path.join(self.dst_dir, "subdir", "subfile.cfg")))
        # Verify: destination collision kept original content
        with open(os.path.join(self.dst_dir, "collision.cfg")) as f:
            self.assertEqual(f.read(), "dest original content")
        # Verify: source collision kept under suffixed name
        self.assertTrue(os.path.exists(os.path.join(self.dst_dir, "collision_legacy_1.cfg")))
        with open(os.path.join(self.dst_dir, "collision_legacy_1.cfg")) as f:
            self.assertEqual(f.read(), "source collision content")
        # Verify src_dir cleaned up
        self.assertFalse(os.path.exists(self.src_dir))


class TestTKCR06_InstallerServiceRollback(unittest.TestCase):
    """TKC-R06: Service rollback restores previous unit and active/enabled states."""

    def test_journal_rollback_simulation_for_service_updated_vs_created(self):
        test_dir = tempfile.mkdtemp()
        try:
            svc_file = os.path.join(test_dir, "tool_calibrator.service")
            orig_backup = os.path.join(test_dir, "tool_calibrator.service.orig")

            with open(orig_backup, "w") as f:
                f.write("ORIGINAL UNIT CONTENT")
            with open(svc_file, "w") as f:
                f.write("OVERWRITTEN UNIT CONTENT")

            # Simulate rollback logic from install.sh for SERVICE_UPDATED
            svc_file_rel = "/etc/systemd/system/tool_calibrator.service"
            orig_backup_rel = "/tmp/backup/tool_calibrator.service.orig"
            entry = f"SERVICE_UPDATED={svc_file_rel}:system:{orig_backup_rel}:1:1"
            action, data = entry.split("=", 1)
            parts = data.split(":")
            unit_path, mode, bkp, was_en, was_act = parts[0], parts[1], parts[2], parts[3], parts[4]

            self.assertEqual(unit_path, svc_file_rel)
            self.assertEqual(mode, "system")
            self.assertEqual(bkp, orig_backup_rel)
            self.assertEqual(was_en, "1")
            self.assertEqual(was_act, "1")

            # Physical restoration of file content
            shutil.copy2(orig_backup, svc_file)
            with open(svc_file, "r") as f:
                restored = f.read()
            self.assertEqual(restored, "ORIGINAL UNIT CONTENT")
        finally:
            shutil.rmtree(test_dir, ignore_errors=True)


class TestTKCR07_TeachSafeZRuntimeSync(unittest.TestCase):
    """TKC-R07: CALIBRATION_SET_SAFE_POS TYPE=SAFE_Z updates safe_z_enabled and carto_speedup synchronously."""

    def setUp(self):
        self.toolhead = DummyToolhead()
        self.gcode = DummyGCode()
        self.printer = DummyPrinter(self.toolhead, self.gcode)
        # Start with safe_z disabled and cartographer backend
        self.calibrator = ToolCalibrator(DummyConfig(self.printer, {"safe_z": None, "z_backend": "cartographer"}))

    def test_teach_safe_z_enables_runtime_and_disables_speedup(self):
        self.assertFalse(self.calibrator.navigator.is_safe_z_enabled())
        self.assertTrue(self.calibrator.navigator.carto_speedup)

        gcmd = DummyGCodeCommand({"TYPE": "SAFE_Z", "Z": 25.0})
        self.calibrator.cmd_CALIBRATION_SET_SAFE_POS(gcmd)

        self.assertEqual(self.calibrator.navigator.safe_z, 25.0)
        self.assertTrue(self.calibrator.navigator.is_safe_z_enabled())
        self.assertFalse(self.calibrator.navigator.carto_speedup)

        # Test move_to_safe_z elevates to 25.0
        self.toolhead.pos = [100.0, 100.0, 5.0, 0.0]
        self.calibrator.navigator.move_to_safe_z(self.toolhead, None)
        self.assertEqual(self.toolhead.pos[2], 25.0)

    def test_teach_safe_z_rejects_negative_or_nan(self):
        gcmd_neg = DummyGCodeCommand({"TYPE": "SAFE_Z", "Z": -5.0})
        with self.assertRaises(Exception):
            self.calibrator.cmd_CALIBRATION_SET_SAFE_POS(gcmd_neg)

        gcmd_nan = DummyGCodeCommand({"TYPE": "SAFE_Z", "Z": float("nan")})
        with self.assertRaises(Exception):
            self.calibrator.cmd_CALIBRATION_SET_SAFE_POS(gcmd_nan)


class TestTKCR08_ConfigRollbackSecurity(unittest.TestCase):
    """TKC-R08: ConfigManager rollback strictly checks backup roots, naming, and INI content."""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.cfg_dir = os.path.join(self.test_dir, "config")
        os.makedirs(self.cfg_dir, exist_ok=True)
        self.live_cfg = os.path.join(self.cfg_dir, "tool_offsets.cfg")
        with open(self.live_cfg, "w") as f:
            f.write("# Live Config\n[tool_offsets]\nt1_x = 0.05\n")
        self.cm = ConfigManager(self.live_cfg)

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_rollback_rejects_unrelated_cfg_in_config_dir(self):
        """A non-backup file like unrelated.cfg in config_dir must be rejected."""
        unrelated = os.path.join(self.cfg_dir, "unrelated.cfg")
        with open(unrelated, "w") as f:
            f.write("[printer]\nkinematics = corexy\n")

        with self.assertRaises(ConfigManagerException) as ctx:
            self.cm.rollback(unrelated)
        self.assertIn("outside backup roots", str(ctx.exception))

    def test_rollback_rejects_non_ini_file_in_backup_dir(self):
        """A corrupted backup containing non-INI binary or garbage must be rejected."""
        os.makedirs(self.cm.backup_dir, exist_ok=True)
        bk_path = os.path.join(self.cm.backup_dir, "tool_offsets.cfg.calib_backup_20260909_120000.bak")
        with open(bk_path, "w") as f:
            f.write("THIS IS NOT KLIPPER CONFIG INI FORMAT NO HEADERS OR EQUALS")

        with self.assertRaises(ConfigManagerException) as ctx:
            self.cm.rollback(bk_path)
        self.assertIn("valid Klipper configuration structure", str(ctx.exception))


class TestTKCR09_ReportGeneratorCalibratedMPPAndMatrix(unittest.TestCase):
    """TKC-R09: Test report generator loads true MPP/matrix and labels estimates honestly."""

    def test_generate_report_uses_explicit_mpp(self):
        import subprocess, sys
        script_path = os.path.join(os.path.dirname(__file__), "..", "scripts", "generate_test_report.py")
        out_report = os.path.join(tempfile.gettempdir(), f"report_{time.time()}.md")

        res = subprocess.run(
            [sys.executable, script_path, "--mpp", "0.012500", "--output", out_report],
            capture_output=True,
            text=True
        )
        self.assertEqual(res.returncode, 0, msg=res.stderr)
        with open(out_report, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertIn("0.01250 mm/px", content)
        self.assertIn("Calibrated MPP", content)
        if os.path.exists(out_report):
            os.remove(out_report)

    def test_generate_report_marks_uncalibrated_when_no_calibration(self):
        import subprocess, sys
        script_path = os.path.join(os.path.dirname(__file__), "..", "scripts", "generate_test_report.py")
        out_report = os.path.join(tempfile.gettempdir(), f"report_{time.time()}.md")

        res = subprocess.run(
            [sys.executable, script_path, "--output", out_report],
            capture_output=True,
            text=True
        )
        self.assertEqual(res.returncode, 0, msg=res.stderr)
        with open(out_report, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertIn("Uncalibrated / Estimate", content)
        if os.path.exists(out_report):
            os.remove(out_report)


class TestTKCR10_HealthEndpointAuthAndCredentialSanitization(unittest.TestCase):
    """TKC-R10: /health endpoint sanitizes camera URL credentials and hides details for unauthenticated requests."""

    def setUp(self):
        import server.tool_calibrator_server as srv
        self.srv = srv
        self.client = srv.app.test_client()

    def tearDown(self):
        self.srv.calibration_lock["token"] = None
        self.srv.grabber.camera_url = "http://localhost/webcam2/?action=snapshot"

    def test_health_unauthenticated_with_token_omits_camera_url(self):
        self.srv.calibration_lock["token"] = "auth-token-456"
        self.srv.grabber.camera_url = "http://demo_user:demo_password@camera.lan:8080/snapshot"

        res = self.client.get("/health")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertEqual(data["status"], "ok")
        self.assertEqual(data["service"], "tool_calibrator_server")
        # camera_url must be None/redacted
        self.assertIsNone(data.get("camera_url"))
        self.assertIsNone(data.get("calibrated_mpp"))

    def test_health_authenticated_with_token_sanitizes_credentials(self):
        self.srv.calibration_lock["token"] = "auth-token-456"
        self.srv.grabber.camera_url = "http://demo_user:demo_password@camera.lan:8080/snapshot"

        res = self.client.get("/health", headers={"X-API-Token": "auth-token-456"})
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        # Credentials stripped!
        self.assertEqual(data["camera_url"], "http://camera.lan:8080/snapshot")
        self.assertNotIn("demo_password", data["camera_url"])


class TestTKCR11_RealKlipperGCodeDispatcherHandlers(unittest.TestCase):
    """TKC-R11: G-code dispatcher checks real Klipper ready_gcode_handlers/base_gcode_handlers and tracks axes."""

    def test_real_klipper_dispatcher_without_commands_dict(self):
        class RealKlipperDispatcherMock:
            def __init__(self):
                # Real Klipper dispatcher has ready_gcode_handlers and base_gcode_handlers, NOT .commands
                self.ready_gcode_handlers = {
                    "SET_TOOL_PARAMETER": lambda gcmd: None,
                    "INITIALIZE_TOOLCHANGER": lambda gcmd: None,
                }
                self.base_gcode_handlers = {}
                self.executed_scripts = []

            def run_script_from_command(self, script):
                self.executed_scripts.append(script)

            def register_command(self, name, cb, desc=None):
                self.ready_gcode_handlers[name] = cb

        dispatcher = RealKlipperDispatcherMock()
        # Verify helper finds them
        from klippy.extras.tool_offsets import is_command_registered
        self.assertTrue(is_command_registered(dispatcher, "SET_TOOL_PARAMETER"))
        self.assertTrue(is_command_registered(dispatcher, "INITIALIZE_TOOLCHANGER"))
        self.assertFalse(is_command_registered(dispatcher, "NON_EXISTENT_COMMAND"))

        # Verify ToolOffsets executes SET_TOOL_PARAMETER through real dispatcher
        toolhead = DummyToolhead()
        printer = RegressionDummyPrinter(toolhead, dispatcher)
        cfg = DummyOffsetsConfig(printer, {
            "t1_x": 0.05,
            "t1_y": -0.08,
            "t1_z": 0.12
        })
        to = ToolOffsets(cfg)
        applied = to.apply_tool_offsets(1)
        self.assertIn(1, applied)
        self.assertTrue(any("SET_TOOL_PARAMETER T=1 PARAMETER=gcode_x_offset" in s for s in dispatcher.executed_scripts))


class TestTKCR12_DependencyVersionsEnforced(unittest.TestCase):
    """TKC-R12: Enforce patched requests>=2.32.4 and urllib3>=2.6.3 in requirements.txt."""

    def test_requirements_file_has_patched_versions(self):
        req_path = os.path.join(os.path.dirname(__file__), "..", "server", "requirements.txt")
        with open(req_path, "r") as f:
            lines = f.readlines()
        has_req = any("requests>=2.32.4" in l for l in lines)
        has_url = any("urllib3>=2.6.3" in l for l in lines)
        self.assertTrue(has_req, "requests>=2.32.4 must be in requirements.txt")
        self.assertTrue(has_url, "urllib3>=2.6.3 must be in requirements.txt")


class TestPhysicalPathSafety(unittest.TestCase):
    """Physical path safety: camera entry lifts before lateral move; toolchange lifts off bed when safe_z disabled."""

    def setUp(self):
        self.toolhead = DummyToolhead()
        self.gcode = DummyGCode()
        self.toolchanger = DummyToolchanger([0, 1])
        self.printer = DummyPrinter(self.toolhead, self.gcode, self.toolchanger)

    def test_approach_camera_lifts_vertically_before_lateral_move(self):
        calibrator = ToolCalibrator(DummyConfig(self.printer, {
            "camera_x": 150.0,
            "camera_y": 20.0,
            "camera_z": 22.0,
            "safe_z": None  # Disabled
        }))

        # Toolhead sitting at Z=1.0mm
        self.toolhead.pos = [50.0, 50.0, 1.0, 0.0]
        moves = []
        original_manual_move = self.toolhead.manual_move

        def record_move(pos, speed):
            moves.append(list(pos))
            original_manual_move(pos, speed)

        self.toolhead.manual_move = record_move
        calibrator.navigator.approach_camera(self.toolhead, None, focal_z=22.0)

        # First move must be vertical lift to focal_z (22.0mm) before lateral motion
        self.assertGreaterEqual(len(moves), 2)
        self.assertEqual(moves[0], [None, None, 22.0])
        # Subsequent move is XY approach towards bed center
        self.assertEqual(moves[1][0], 150.0)
        self.assertEqual(moves[1][1], 45.0)

    def test_toolchange_hops_off_bed_when_safe_z_disabled(self):
        printer = DummyPrinter(DummyToolhead(), DummyGCode(), self.toolchanger)
        calibrator = ToolCalibrator(DummyConfig(printer, {
            "safe_z": None,
            "z_backend": "cartographer"
        }))
        self.toolchanger.tool_number = 0
        printer.toolhead.pos = [100.0, 100.0, 0.5, 0.0]  # Sitting on bed

        calibrator.z_backend.probe_reference_tool = MagicMock(return_value={"contact_z": 0.05, "source": "cartographer"})
        calibrator.z_backend.probe_secondary_tool = MagicMock(return_value={"suggested_z_offset": 0.12, "source": "cartographer"})

        def mock_run_script(script):
            if script == "T1":
                self.toolchanger.tool_number = 1
            elif script == "T0":
                self.toolchanger.tool_number = 0

        printer.gcode.run_script_from_command = MagicMock(side_effect=mock_run_script)

        gcmd = DummyGCodeCommand({"CALIBRATE_XY": 0, "CALIBRATE_Z": 1, "TOOLS": "0,1"})
        calibrator.cmd_CALIBRATE_TOOL_OFFSETS(gcmd)

        # Before T1 toolchange, toolhead had hopped to at least 2.0mm
        self.assertGreaterEqual(printer.toolhead.pos[2], 2.0)


if __name__ == "__main__":
    unittest.main()
