"""
Unit Tests for Tool-Klipper-Calibration Vision Daemon.
"""

import os
import unittest
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
            self.assertIsNotNone(frame, f"Failed to load {img_name}")
            result = detector.detect(frame)
            self.assertTrue(result.found, f"Failed detection on {img_name}")
            self.assertIsNotNone(result.center_uv)


if __name__ == "__main__":
    unittest.main()
