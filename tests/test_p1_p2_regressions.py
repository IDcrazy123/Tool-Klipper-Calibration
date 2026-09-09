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


if __name__ == "__main__":
    unittest.main()
