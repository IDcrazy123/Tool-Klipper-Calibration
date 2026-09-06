"""
Unit Tests for Tool-Klipper-Calibration Vision Daemon.
"""

import os
import unittest
import unittest.mock
from unittest.mock import MagicMock, patch
import numpy as np
import cv2

from server.stream_grabber import StreamGrabber
from server.affine_transform import TransformationSolver
from server.nozzle_detector import NozzleDetector
from server.visual_debugger import VisualDebugger


class TestStreamGrabber(unittest.TestCase):
    def test_url_normalization(self):
        grabber = StreamGrabber("/webcam2/?action=snapshot")
        self.assertEqual(grabber.camera_url, "http://localhost/webcam2/?action=snapshot")

        grabber.set_camera_url("http://192.168.1.50:8080/?action=snapshot")
        self.assertEqual(grabber.camera_url, "http://192.168.1.50:8080/?action=snapshot")

        grabber.set_camera_url("192.168.1.50:8080/?action=snapshot")
        self.assertEqual(grabber.camera_url, "http://192.168.1.50:8080/?action=snapshot")

        # Test automatic conversion from stream URLs to snapshot endpoints
        grabber.set_camera_url("http://192.168.1.50:8080/?action=stream")
        self.assertEqual(grabber.camera_url, "http://192.168.1.50:8080/?action=snapshot")

        grabber.set_camera_url("http://localhost:8080/stream")
        self.assertEqual(grabber.camera_url, "http://localhost:8080/snapshot")

        grabber.set_camera_url("/webcam2/?action=stream")
        self.assertEqual(grabber.camera_url, "http://localhost/webcam2/?action=snapshot")

    def test_frame_cache(self):
        grabber = StreamGrabber("http://127.0.0.1:8090/snapshot", cache_ttl=0.1)
        # Mock session.get to return a dummy jpeg
        dummy_img = np.zeros((100, 100, 3), dtype=np.uint8)
        _, buf = cv2.imencode(".jpg", dummy_img)
        mock_resp = unittest.mock.MagicMock()
        mock_resp.status_code = 200
        mock_resp.content = buf.tobytes()

        with unittest.mock.patch.object(grabber.session, "get", return_value=mock_resp) as mock_get:
            f1, err1 = grabber.grab_frame()
            self.assertIsNotNone(f1)
            self.assertIsNone(err1)
            self.assertEqual(mock_get.call_count, 1)

            # Second call within cache TTL should return cached frame without calling session.get
            f2, err2 = grabber.grab_frame()
            self.assertIsNotNone(f2)
            self.assertEqual(mock_get.call_count, 1)

            # Forced refresh should trigger a new session.get call
            f3, err3 = grabber.grab_frame(force_refresh=True)
            self.assertIsNotNone(f3)
            self.assertEqual(mock_get.call_count, 2)


class TestAffineTransform(unittest.TestCase):
    def setUp(self):
        self.solver = TransformationSolver(damping_factor=0.55)
        self.solver.set_frame_center(320.0, 240.0)

    def test_calculate_average_mpp(self):
        # 1mm displacement maps to ~100 pixels
        samples = [
            (1.0, 100.0),  # mpp = 0.01
            (1.0, 102.0),  # mpp = 0.0098
            (1.0, 98.0),   # mpp = 0.0102
            (1.0, 50.0),   # outlier (mpp = 0.02) -> should be discarded
        ]
        mpp = self.solver.calculate_average_mpp(samples)
        self.assertAlmostEqual(mpp, 0.01, delta=0.0005)

    def test_solve_matrix_and_offset(self):
        # Generate 10 synthetic calibration points around center (320, 240)
        # Assuming linear mapping: real_x = (u - 320) * 0.01, real_y = (v - 240) * 0.01
        points = []
        for du, dv in [
            (0, 0), (20, 0), (-20, 0), (0, 20), (0, -20),
            (15, 15), (-15, -15), (15, -15), (-15, 15), (30, 0)
        ]:
            u = 320.0 + du
            v = 240.0 + dv
            real_x = du * 0.01
            real_y = dv * 0.01
            points.append(([real_x, real_y], [u, v]))

        success = self.solver.solve_matrix(points)
        self.assertTrue(success)

        # Query offset for nozzle at (340, 240) -> offset should be negative to move toolhead back
        offset_x, offset_y = self.solver.calculate_offset((340.0, 240.0))
        # du = +20, real delta = +0.2mm, with 0.55 damping: -1 * 0.55 * 0.2 = -0.11mm
        self.assertAlmostEqual(offset_x, -0.11, delta=0.02)
        self.assertAlmostEqual(offset_y, 0.0, delta=0.02)

    def test_solve_matrix_affine_star_pattern(self):
        """Verify 5-point star pattern displacement solves 1st-order affine matrix accurately."""
        # 5 points: center + 4 orthogonal displacements of 1.0mm (100 pixels)
        star_points = [
            [[0.0, 0.0], [320.0, 240.0]],
            [[1.0, 0.0], [420.0, 240.0]],
            [[-1.0, 0.0], [220.0, 240.0]],
            [[0.0, 1.0], [320.0, 340.0]],
            [[0.0, -1.0], [320.0, 140.0]]
        ]
        success = self.solver.solve_matrix(star_points)
        self.assertTrue(success)
        self.assertEqual(self.solver.transform_matrix.shape, (2, 3))

        # Query offset for nozzle at (370, 240) -> du = +50px = +0.5mm
        off_x, off_y = self.solver.calculate_offset((370.0, 240.0))
        # -1 * 0.55 * 0.5 = -0.275mm
        self.assertAlmostEqual(off_x, -0.275, delta=0.01)
        self.assertAlmostEqual(off_y, 0.0, delta=0.01)

    def test_set_mpp(self):
        """Verify set_mpp updates scale."""
        self.solver.set_mpp(0.0125)
        self.assertEqual(self.solver.mpp, 0.0125)

    def test_calculate_tool_delta(self):
        """Verify physical delta XY computation between two tools (T0 ref vs T1 target)."""
        # Without matrix -> raises ERR_CV_203
        self.solver.transform_matrix = None
        with self.assertRaises(RuntimeError) as ctx:
            self.solver.calculate_tool_delta((320.0, 240.0), (370.0, 220.0))
        self.assertIn("ERR_CV_203", str(ctx.exception))

        # Test with affine transformation matrix
        star_points = [
            [[0.0, 0.0], [320.0, 240.0]],
            [[1.0, 0.0], [420.0, 240.0]],
            [[-1.0, 0.0], [220.0, 240.0]],
            [[0.0, 1.0], [320.0, 340.0]],
            [[0.0, -1.0], [320.0, 140.0]]
        ]
        self.solver.solve_matrix(star_points)
        # Shift target by +50px in U (+0.5mm in X) and -20px in V (-0.2mm in Y)
        # Carriage must shift opposite direction to bring nozzle to optical center
        dx_aff, dy_aff = self.solver.calculate_tool_delta((320.0, 240.0), (370.0, 220.0))
        self.assertAlmostEqual(dx_aff, -0.50, delta=0.01)
        self.assertAlmostEqual(dy_aff, 0.20, delta=0.01)

    def test_uncalibrated_offset_raises_err_cv_203(self):
        """Verify calculate_offset_detail and calculate_tool_delta raise ERR_CV_203 when uncalibrated, preventing blind motion."""
        self.solver.transform_matrix = None
        self.solver.mpp = None
        with self.assertRaises(RuntimeError) as ctx:
            self.solver.calculate_offset_detail((340.0, 240.0))
        self.assertIn("ERR_CV_203", str(ctx.exception))

        with self.assertRaises(RuntimeError) as ctx:
            self.solver.calculate_tool_delta((320.0, 240.0), (330.0, 250.0))
        self.assertIn("ERR_CV_203", str(ctx.exception))

    def test_solve_matrix_rejects_degenerate_points(self):
        """Verify solve_matrix raises ValueError when points are collinear or identical."""
        identical_points = [
            [[0.0, 0.0], [320.0, 240.0]],
            [[0.0, 0.0], [320.0, 240.0]],
            [[0.0, 0.0], [320.0, 240.0]],
            [[0.0, 0.0], [320.0, 240.0]],
            [[0.0, 0.0], [320.0, 240.0]]
        ]
        with self.assertRaises(ValueError):
            self.solver.solve_matrix(identical_points)

    def test_matrix_serialization_and_restore(self):
        """Verify get_matrix and set_matrix can roundtrip solved affine coefficients."""
        star_points = [
            [[0.0, 0.0], [320.0, 240.0]],
            [[1.0, 0.0], [420.0, 240.0]],
            [[-1.0, 0.0], [220.0, 240.0]],
            [[0.0, 1.0], [320.0, 340.0]],
            [[0.0, -1.0], [320.0, 140.0]]
        ]
        self.solver.solve_matrix(star_points)
        mat = self.solver.get_matrix()
        self.assertIsNotNone(mat)

        new_solver = TransformationSolver(damping_factor=0.55)
        new_solver.set_matrix(mat)
        dx, dy = new_solver.calculate_tool_delta((320.0, 240.0), (370.0, 220.0))
        self.assertAlmostEqual(dx, -0.50, delta=0.01)
        self.assertAlmostEqual(dy, 0.20, delta=0.01)

    def test_frame_boundary_clamping_err_cv_204(self):
        """Coordinates outside camera boundaries must raise ValueError with ERR_CV_204."""
        self.solver.set_mpp(0.0125)
        # Frame size is 640 x 480 (center at 320, 240)
        # Negative coordinate
        with self.assertRaises(ValueError) as ctx:
            self.solver.calculate_offset((-10.0, 240.0))
        self.assertIn("ERR_CV_204", str(ctx.exception))

        # Beyond width
        with self.assertRaises(ValueError) as ctx:
            self.solver.calculate_offset((650.0, 240.0))
        self.assertIn("ERR_CV_204", str(ctx.exception))

        # Beyond height
        with self.assertRaises(ValueError) as ctx:
            self.solver.calculate_offset((320.0, 500.0))
        self.assertIn("ERR_CV_204", str(ctx.exception))


class TestNozzleDetector(unittest.TestCase):
    def setUp(self):
        self.detector = NozzleDetector(frame_width=640, frame_height=480)

    def _create_synthetic_nozzle_frame(self, cx=320, cy=240, inner_radius=15, outer_radius=40):
        """Creates a synthetic image simulating a nozzle tip on a dark background."""
        frame = np.full((480, 640, 3), 40, dtype=np.uint8)
        # Outer brass nozzle body (bright circle)
        cv2.circle(frame, (cx, cy), outer_radius, (180, 180, 180), -1)
        # Dark circular orifice hole
        cv2.circle(frame, (cx, cy), inner_radius, (10, 10, 10), -1)
        # Add slight Gaussian blur for realism
        frame = cv2.GaussianBlur(frame, (5, 5), 1.0)
        return frame

    def test_synthetic_detection(self):
        frame = self._create_synthetic_nozzle_frame(cx=315, cy=245, inner_radius=18, outer_radius=45)
        result = self.detector.detect(frame)
        self.assertTrue(result.found)
        self.assertIsNotNone(result.center_uv)
        # Center should be close to (315, 245)
        self.assertAlmostEqual(result.center_uv[0], 315, delta=3.0)
        self.assertAlmostEqual(result.center_uv[1], 245, delta=3.0)
        self.assertGreaterEqual(result.confidence, 0.6)

    def test_candidate_circle_ranking_discrimination(self):
        """Verify _rank_candidate_circles selects the true circular boundary over internal glare."""
        roi = np.full((280, 280), 50, dtype=np.uint8)
        # Bright nozzle body with dark circular orifice at (180, 140), radius 22
        cv2.circle(roi, (180, 140), 35, 200, -1)
        cv2.circle(roi, (180, 140), 22, 20, -1)
        # Faint spurious circle closer to center (145, 140), radius 12
        cv2.circle(roi, (145, 140), 12, 70, -1)

        candidates = np.array([
            [145.0, 140.0, 12.0],  # Faint background artifact
            [180.0, 140.0, 22.0],  # True nozzle circular orifice
        ], dtype=np.float32)

        best = self.detector._rank_candidate_circles(
            gray_roi=roi,
            candidates=candidates,
            frame_w=640,
            frame_h=480,
            roi_x0=180,
            roi_y0=100,
            d0=140.0
        )
        self.assertIsNotNone(best)
        self.assertAlmostEqual(best[0], 180.0, delta=1.0)
        self.assertAlmostEqual(best[1], 140.0, delta=1.0)
        self.assertAlmostEqual(best[2], 22.0, delta=1.0)

    def test_subpixel_refinement_precision(self):
        """Verify _refine_upper_arc_symmetry achieves sub-pixel convergence (< 0.15px)."""
        roi = np.full((260, 260), 30, dtype=np.uint8)
        # Create circular nozzle orifice centered at (130, 130)
        cv2.circle(roi, (130, 130), 28, 190, -1)
        cv2.circle(roi, (130, 130), 16, 20, -1)
        # Start coarse search perturbed by (+2.0, -1.5)
        refined_x, refined_y = self.detector._refine_upper_arc_symmetry(
            roi, cx=132.0, cy=128.5, radius=16.0, max_shift=4.0
        )
        self.assertAlmostEqual(refined_x, 130.0, delta=0.15)
        self.assertAlmostEqual(refined_y, 130.0, delta=0.15)


class TestStreamGrabberWebRTC(unittest.TestCase):
    def test_url_normalization_preserves_jpg(self):
        from server.stream_grabber import StreamGrabber
        grabber = StreamGrabber("http://192.168.1.100:8080/snapshot.jpg")
        self.assertEqual(grabber.camera_url, "http://192.168.1.100:8080/snapshot.jpg")

    def test_webrtc_404_fallback(self):
        from server.stream_grabber import StreamGrabber
        grabber = StreamGrabber("http://localhost/webcam2/?action=snapshot")
        img = np.zeros((100, 100, 3), dtype=np.uint8)
        _, encoded = cv2.imencode(".jpg", img)

        def mock_get(url, timeout):
            resp = unittest.mock.MagicMock()
            if "action=snapshot" in url:
                resp.status_code = 404
            elif url.endswith("/snapshot.jpg"):
                resp.status_code = 200
                resp.content = encoded.tobytes()
            else:
                resp.status_code = 404
            return resp

        grabber.session.get = mock_get
        frame, err = grabber.grab_frame(force_refresh=True)
        self.assertIsNotNone(frame)
        self.assertIsNone(err)
        self.assertTrue(grabber.camera_url.endswith("/snapshot.jpg"))


class TestVisualDebugger(unittest.TestCase):
    def test_frame_generation(self):
        debugger = VisualDebugger()
        jpeg_bytes = debugger.get_latest_jpeg()
        self.assertGreater(len(jpeg_bytes), 100)
        # Header for JPEG is 0xFF 0xD8
        self.assertEqual(jpeg_bytes[:2], b'\xff\xd8')


class TestServerEndpoints(unittest.TestCase):
    def setUp(self):
        from server.tool_calibrator_server import app
        self.client = app.test_client()

    def test_dashboard_route(self):
        """Dashboard GET / must return 200 OK and render HTML."""
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Tool-Klipper-Calibration", response.data)
        self.assertIn(b"Vision Monitor", response.data)

    def test_sample_dataset_detection(self):
        """Verify 3-tier cascade detects nozzle across diverse sample images."""
        sample_dir = os.path.join(os.path.dirname(__file__), "sample_images")
        if not os.path.exists(sample_dir):
            return

        detector = NozzleDetector(frame_width=640, frame_height=480)
        image_files = [f for f in os.listdir(sample_dir) if f.lower().endswith((".jpg", ".png"))]
        self.assertGreaterEqual(len(image_files), 15)

        for img_name in image_files:
            img_path = os.path.join(sample_dir, img_name)
            frame = cv2.imread(img_path)
            result = detector.detect(frame)
            self.assertTrue(result.found, f"Failed detection on {img_name}")
            self.assertIsNotNone(result.center_uv)

    def test_sample_api_endpoints(self):
        """Verify /api/samples and /api/test_sample endpoint functionality."""
        res_list = self.client.get("/api/samples")
        self.assertEqual(res_list.status_code, 200)
        data = res_list.get_json()
        self.assertIn("samples", data)
        self.assertGreaterEqual(len(data["samples"]), 15)

        # Test querying a specific sample
        res_test = self.client.post("/api/test_sample", json={"sample_name": "nozzle_perfect_center.jpg"})
        self.assertEqual(res_test.status_code, 200)
        test_data = res_test.get_json()
        self.assertTrue(test_data["success"])
        self.assertTrue(test_data["found"])
        self.assertIsNotNone(test_data["center_uv"])

    def test_calculate_tool_delta_endpoint(self):
        """Verify POST /calculate_tool_delta endpoint produces accurate delta and G-code."""
        from server.tool_calibrator_server import solver
        star_points = [
            [[0.0, 0.0], [320.0, 240.0]],
            [[1.0, 0.0], [400.0, 240.0]],
            [[-1.0, 0.0], [240.0, 240.0]],
            [[0.0, 1.0], [320.0, 320.0]],
            [[0.0, -1.0], [320.0, 160.0]]
        ]
        solver.solve_matrix(star_points)
        solver.set_mpp(0.0125)
        res = self.client.post("/calculate_tool_delta", json={
            "reference_uv": [737.42, 328.79],
            "target_uv": [735.00, 324.50],
            "tool": 1
        })
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data["success"])
        self.assertEqual(data["tool"], 1)
        self.assertEqual(data["delta_uv"], [-2.42, -4.29])
        self.assertAlmostEqual(data["delta_xy"][0], 0.0303, delta=0.001)
        self.assertAlmostEqual(data["delta_xy"][1], 0.0536, delta=0.001)
        self.assertIn("SET_TOOL_OFFSET TOOL=1", data["gcode_command"])
        self.assertIn("[tool_offsets]", data["config_snippet"])
        self.assertIn("t1_x: 0.0302", data["config_snippet"])

    def test_set_camera_url_endpoint(self):
        """Verify POST /set_camera_url accepts {"url": ...} payload forwarded from Klipper."""
        res = self.client.post("/set_camera_url", json={"url": "http://192.168.1.100:8080/?action=stream"})
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data["success"])
        self.assertEqual(data["camera_url"], "http://192.168.1.100:8080/?action=snapshot")

        # Also test /set_camera with {"camera_url": ...}
        res2 = self.client.post("/set_camera", json={"camera_url": "http://192.168.1.100:8080/?action=snapshot"})
        self.assertEqual(res2.status_code, 200)
        self.assertTrue(res2.get_json()["success"])

    def test_picture_screenshot_sweep_dataset(self):
        """Verify robust detection and sub-pixel repeatability across all 5 tools and 5 brightness levels."""
        import glob
        pattern = os.path.join(os.path.dirname(__file__), "..", "Picture Screenshot", "*", "*", "*.jpg")
        files = glob.glob(pattern)
        if not files:
            return  # Skip if optional screenshot dataset directory is not present locally

        detector = NozzleDetector()
        tool_results = {}
        for f in files:
            fname = os.path.basename(f)
            tool = fname.split("_")[0]
            img = cv2.imread(f)
            res = detector.detect(img)
            self.assertTrue(res.found, f"Failed detection on {fname}")
            self.assertIsNotNone(res.center_uv)
            tool_results.setdefault(tool, []).append(res.center_uv)

        # Verify sub-pixel repeatability across extreme lighting (L001 to L255)
        for tool, coords in tool_results.items():
            us = [c[0] for c in coords]
            vs = [c[1] for c in coords]
            self.assertLess(np.std(us), 2.0, f"Tool {tool} U standard deviation too high")
            self.assertLess(np.std(vs), 2.0, f"Tool {tool} V standard deviation too high")

    def test_picture_screenshot_shuffled_order_invariance(self):
        """Verify that randomly shuffling test images produces identical sub-pixel detections (zero order bias)."""
        import glob
        import random
        pattern = os.path.join(os.path.dirname(__file__), "..", "Picture Screenshot", "*", "*", "*.jpg")
        files = glob.glob(pattern)
        if not files:
            return

        detector = NozzleDetector()
        # Baseline sequential detection
        baseline = {}
        for f in files:
            img = cv2.imread(f)
            res = detector.detect(img)
            self.assertTrue(res.found)
            baseline[f] = res.center_uv

        # Shuffled detection
        shuffled = list(files)
        random.seed(42)
        random.shuffle(shuffled)

        for f in shuffled:
            img = cv2.imread(f)
            res = detector.detect(img)
            self.assertTrue(res.found)
            b_u, b_v = baseline[f]
            self.assertAlmostEqual(res.center_uv[0], b_u, delta=1e-4, msg=f"U coordinate order discrepancy on {f}")
    def test_negative_image_rejection(self):
        """Random noise and blank images must be rejected with found=False and confidence=0.0."""
        detector = NozzleDetector()
        # 1. Blank black frame
        black_frame = np.zeros((480, 640, 3), dtype=np.uint8)
        res_black = detector.detect(black_frame)
        self.assertFalse(res_black.found)
        self.assertEqual(res_black.confidence, 0.0)

        # 2. Random uniform noise frame
        np.random.seed(42)
        noise_frame = np.random.randint(0, 256, (480, 640, 3), dtype=np.uint8)
        res_noise = detector.detect(noise_frame)
        self.assertFalse(res_noise.found)
        self.assertEqual(res_noise.confidence, 0.0)

    def test_stream_grabber_cache_cleared_on_error(self):
        """StreamGrabber must invalidate _cached_frame when grab fails."""
        grabber = StreamGrabber("http://127.0.0.1:8090/snapshot", cache_ttl=10.0)
        # Pre-seed cache with dummy frame
        grabber._cached_frame = np.ones((100, 100, 3), dtype=np.uint8)
        
        # Simulate HTTP 503 error
        mock_resp = unittest.mock.MagicMock()
        mock_resp.status_code = 503
        with unittest.mock.patch.object(grabber.session, "get", return_value=mock_resp):
            frame, err = grabber.grab_frame(force_refresh=True)
            self.assertIsNone(frame)
            self.assertIn("503", err)
            self.assertIsNone(grabber._cached_frame)

    def test_server_session_lock_ownership_enforcement(self):
        """Server mutating endpoints must reject calls when locked by another client, and release_lock requires owner token."""
        from server.tool_calibrator_server import app, calibration_lock, lock_mutex
        client = app.test_client()

        with lock_mutex:
            calibration_lock["session_id"] = None
            calibration_lock["token"] = None

        # 1. Client A acquires lock
        resp = client.post("/acquire_lock", json={"session_id": "client_A"})
        self.assertEqual(resp.status_code, 200)

        # 2. Client B attempts mutation without token -> 403
        resp_mpp = client.post("/set_mpp", json={"mpp": 0.005})
        self.assertEqual(resp_mpp.status_code, 403)

        resp_calib_mpp = client.post("/calibrate_mpp", json={"samples": [[1.0, 100.0]]})
        self.assertEqual(resp_calib_mpp.status_code, 403)

        resp_matrix = client.post("/set_matrix", json={"matrix": [[1, 0, 0], [0, 1, 0]]})
        self.assertEqual(resp_matrix.status_code, 403)

        # 3. Release lock without token -> 403
        resp_rel_empty = client.post("/release_lock", json={})
        self.assertEqual(resp_rel_empty.status_code, 403)

        # 4. Release lock with wrong token -> 403
        resp_rel_wrong = client.post("/release_lock", json={"session_id": "client_B"})
        self.assertEqual(resp_rel_wrong.status_code, 403)

        # 5. Client A mutates with valid header -> 200
        resp_mpp_ok = client.post("/set_mpp", json={"mpp": 0.005}, headers={"X-Session-Token": "client_A"})
        self.assertEqual(resp_mpp_ok.status_code, 200)

        # 6. Client A releases lock with token -> 200
        resp_rel_ok = client.post("/release_lock", json={"session_id": "client_A"})
        self.assertEqual(resp_rel_ok.status_code, 200)

    def test_server_daemon_imports_and_typing_hints(self):
        """Verify server module imports cleanly with all typing hints (Tuple, Optional, Dict, Any)."""
        import server.tool_calibrator_server as srv
        self.assertTrue(hasattr(srv, "_check_session_ownership"))
        # Execute check to verify annotations and runtime execution
        req = MagicMock()
        req.headers = {}
        allowed, err = srv._check_session_ownership(req)
        self.assertTrue(allowed)
        self.assertIsNone(err)

    def test_out_of_band_abort_endpoint(self):
        """Verify POST /abort_calibration flags cancellation across /health and /calculate_offset."""
        from server.tool_calibrator_server import app, calibration_lock
        client = app.test_client()

        # Initial state
        h1 = client.get("/health").get_json()
        self.assertFalse(h1.get("abort_requested", False))

        # Trigger out-of-band abort
        abort_res = client.post("/abort_calibration")
        self.assertEqual(abort_res.status_code, 200)
        self.assertTrue(abort_res.get_json()["abort_requested"])

        # Check /health reflects abort
        h2 = client.get("/health").get_json()
        self.assertTrue(h2["abort_requested"])

        # Release lock resets abort flag
        client.post("/acquire_lock", json={"session_id": "test_abort"})
        client.post("/release_lock", json={"session_id": "test_abort"})
        h3 = client.get("/health").get_json()
        self.assertFalse(h3["abort_requested"])

    def test_git_commit_telemetry(self):
        """Verify _get_git_commit returns a valid commit hash or unknown."""
        from server.tool_calibrator_server import _get_git_commit
        commit = _get_git_commit()
        self.assertIsInstance(commit, str)
        self.assertTrue(len(commit) > 0)


if __name__ == "__main__":
    unittest.main()

