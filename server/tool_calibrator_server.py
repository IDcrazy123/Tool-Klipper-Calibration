"""
Tool-Klipper-Calibration Vision Background Daemon.

Provides a multithreaded REST API and live MJPEG streaming server for Klipper toolhead
nozzle alignment via Computer Vision. Runs independently from Klipper's main thread
to avoid reactor latency and emergency shutdowns.
"""

import argparse
import logging
import os
import sys
import threading
import time
from typing import Dict, Any
import numpy as np

from flask import Flask, jsonify, request, Response, render_template
from waitress import serve

from .stream_grabber import StreamGrabber
from .nozzle_detector import NozzleDetector
from .affine_transform import TransformationSolver
from .visual_debugger import VisualDebugger

# Setup Logging
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] [%(name)s]: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger("tool_calibrator.server")

# Initialize Flask App
app = Flask(__name__, template_folder=os.path.join(os.path.dirname(__file__), "templates"))

# Core Service Instances
grabber = StreamGrabber()
detector = NozzleDetector()
solver = TransformationSolver(damping_factor=0.55)
debugger = VisualDebugger()

calibration_lock = {
    "session_id": None,
    "locked_at": 0.0,
    "token": os.environ.get("CALIBRATION_API_TOKEN", None)
}
lock_mutex = threading.Lock()
stream_lock = threading.Lock()
active_preview_streams = 0
MAX_PREVIEW_STREAMS = 2


def _check_auth(req) -> bool:
    """Verifies optional API token or active calibration session."""
    expected = calibration_lock.get("token")
    if not expected:
        return True
    header_token = req.headers.get("X-Calibration-Token") or req.headers.get("Authorization", "").replace("Bearer ", "")
    return header_token == expected


@app.route("/", methods=["GET"])
def dashboard():
    """Serves the interactive Vision Monitor & Diagnostics Dashboard."""
    return render_template("index.html")


@app.route("/health", methods=["GET"])
def health_check():
    """Service health and readiness check."""
    return jsonify({
        "status": "ok",
        "service": "tool_calibrator_server",
        "version": "0.8.8",
        "camera_url": grabber.camera_url,
        "matrix_solved": solver.transform_matrix is not None,
        "calibrated_mpp": solver.mpp,
        "session_locked": calibration_lock["session_id"] is not None
    }), 200


@app.route("/acquire_lock", methods=["POST"])
def acquire_lock():
    """Acquires an exclusive calibration session lock."""
    if not _check_auth(request):
        return jsonify({"success": False, "error": "Unauthorized"}), 401
    data = request.get_json(silent=True) or {}
    req_session = data.get("session_id", str(time.time()))
    with lock_mutex:
        now = time.time()
        current = calibration_lock["session_id"]
        if current is None or (now - calibration_lock["locked_at"] > 600):
            calibration_lock["session_id"] = req_session
            calibration_lock["locked_at"] = now
            return jsonify({"success": True, "session_id": req_session}), 200
        elif current == req_session:
            calibration_lock["locked_at"] = now
            return jsonify({"success": True, "session_id": req_session}), 200
        return jsonify({"success": False, "error": "Session locked by another client"}), 409


@app.route("/release_lock", methods=["POST"])
def release_lock():
    """Releases the calibration session lock."""
    if not _check_auth(request):
        return jsonify({"success": False, "error": "Unauthorized"}), 401
    data = request.get_json(silent=True) or {}
    req_session = data.get("session_id")
    with lock_mutex:
        if calibration_lock["session_id"] is None or calibration_lock["session_id"] == req_session:
            calibration_lock["session_id"] = None
            calibration_lock["locked_at"] = 0.0
            return jsonify({"success": True}), 200
        return jsonify({"success": False, "error": "Session ID mismatch"}), 403


@app.route("/set_camera", methods=["POST"])
@app.route("/set_camera_url", methods=["POST"])
def set_camera():
    """Updates the snapshot camera URL."""
    if not _check_auth(request):
        return jsonify({"success": False, "error": "Unauthorized"}), 401
    try:
        data: Dict[str, Any] = request.get_json(force=True)
        new_url = data.get("camera_url") or data.get("url")
        if not new_url:
            return jsonify({"success": False, "error": "Missing 'camera_url' parameter"}), 400

        grabber.set_camera_url(new_url)
        return jsonify({"success": True, "camera_url": grabber.camera_url}), 200
    except Exception as ex:
        logger.exception("Error in /set_camera")
        return jsonify({"success": False, "error": str(ex)}), 500


@app.route("/set_mpp", methods=["POST"])
def set_mpp():
    """Updates the calibrated mm-per-pixel scale."""
    if not _check_auth(request):
        return jsonify({"success": False, "error": "Unauthorized"}), 401
    try:
        data: Dict[str, Any] = request.get_json(force=True)
        mpp = data.get("mpp")
        if mpp is None:
            return jsonify({"success": False, "error": "Missing 'mpp' parameter"}), 400

        solver.set_mpp(mpp)
        return jsonify({"success": True, "mpp": solver.mpp}), 200
    except (ValueError, TypeError) as ex:
        return jsonify({"success": False, "error": str(ex)}), 400
    except Exception as ex:
        logger.exception("Error in /set_mpp")
        return jsonify({"success": False, "error": str(ex)}), 500


@app.route("/detect_nozzle", methods=["POST"])
def detect_nozzle():
    """
    Acquires a frame and performs cascade nozzle detection.
    Optional parameters:
        min_matches (int): Consecutive stable detections required (default: 1).
        timeout (float): Max seconds to wait for stable detection (default: 5.0).
        tolerance_px (float): Max pixel delta for consecutive stability (default: 1.5).
    """
    try:
        data = request.get_json(silent=True) or {}
        min_matches = int(data.get("min_matches", 1))
        timeout = float(data.get("timeout", 5.0))
        tolerance_px = float(data.get("tolerance_px", 1.5))

        start_time = time.time()
        last_uv = None
        consecutive_matches = 0

        while time.time() - start_time < timeout:
            frame, err = grabber.grab_frame()
            if frame is None:
                black_frame = np.zeros((480, 640, 3), dtype=np.uint8)
                debugger.update_frame(black_frame, f"CAMERA ERROR: {err}")
                return jsonify({
                    "success": False,
                    "found": False,
                    "error": f"Failed grabbing frame: {err}"
                }), 502

            h, w = frame.shape[:2]
            solver.set_frame_center(w / 2.0, h / 2.0)

            result = detector.detect(frame)
            status_text = f"FOUND (Tier {result.tier})" if result.found else "SEARCHING..."
            debugger.update_frame(result.annotated_frame, status_text)

            if result.found and result.center_uv is not None:
                if last_uv is not None:
                    du = abs(result.center_uv[0] - last_uv[0])
                    dv = abs(result.center_uv[1] - last_uv[1])
                    if du <= tolerance_px and dv <= tolerance_px:
                        consecutive_matches += 1
                        if consecutive_matches >= min_matches:
                            return jsonify({
                                "success": True,
                                "found": True,
                                "center_uv": result.center_uv,
                                "radius": result.radius,
                                "confidence": result.confidence,
                                "tier": result.tier,
                                "combo": result.combo,
                                "runtime": round(time.time() - start_time, 3)
                            }), 200
                    else:
                        consecutive_matches = 1
                else:
                    consecutive_matches = 1

                last_uv = result.center_uv
                if min_matches <= 1:
                    return jsonify({
                        "success": True,
                        "found": True,
                        "center_uv": result.center_uv,
                        "radius": result.radius,
                        "confidence": result.confidence,
                        "tier": result.tier,
                        "combo": result.combo,
                        "runtime": round(time.time() - start_time, 3)
                    }), 200

            # Brief delay between frame polls to allow camera streamer to update
            time.sleep(0.1)

        # Timeout reached without stable detection
        return jsonify({
            "success": True,
            "found": False,
            "error": "Nozzle detection timed out or did not stabilize",
            "runtime": round(time.time() - start_time, 3)
        }), 200

    except Exception as ex:
        logger.exception("Error in /detect_nozzle")
        return jsonify({"success": False, "error": str(ex)}), 500


@app.route("/calibrate_mpp", methods=["POST"])
def calibrate_mpp():
    """
    Computes millimeters per pixel from displacement samples:
    Payload: { "samples": [[dist_mm, dist_px], ...] }
    """
    try:
        data = request.get_json(force=True)
        samples = data.get("samples", [])
        if not samples:
            return jsonify({"success": False, "error": "No calibration samples provided"}), 400

        mpp = solver.calculate_average_mpp(samples)
        return jsonify({
            "success": True,
            "mpp": round(mpp, 5)
        }), 200
    except Exception as ex:
        logger.exception("Error in /calibrate_mpp")
        return jsonify({"success": False, "error": str(ex)}), 400


@app.route("/solve_matrix", methods=["POST"])
def solve_matrix():
    """
    Solves 2nd-order transformation matrix from calibration coordinates:
    Payload: { "calibration_points": [[[real_x, real_y], [u, v]], ...] }
    """
    if not _check_auth(request):
        return jsonify({"success": False, "error": "Unauthorized"}), 401
    try:
        data = request.get_json(force=True)
        points = data.get("calibration_points", [])
        if not points:
            return jsonify({"success": False, "error": "No calibration points provided"}), 400

        solver.solve_matrix(points)
        return jsonify({
            "success": True,
            "matrix_solved": True,
            "matrix": solver.get_matrix()
        }), 200
    except ValueError as ex:
        return jsonify({"success": False, "error": str(ex)}), 400
    except Exception as ex:
        logger.exception("Error in /solve_matrix")
        return jsonify({"success": False, "error": str(ex)}), 400


@app.route("/set_matrix", methods=["POST"])
def set_matrix_endpoint():
    """Loads a pre-computed transformation matrix into the solver."""
    if not _check_auth(request):
        return jsonify({"success": False, "error": "Unauthorized"}), 401
    try:
        data = request.get_json(force=True)
        mat = data.get("matrix")
        if not mat:
            return jsonify({"success": False, "error": "Missing 'matrix' parameter"}), 400
        solver.set_matrix(mat)
        return jsonify({"success": True, "matrix": solver.get_matrix()}), 200
    except ValueError as ex:
        return jsonify({"success": False, "error": str(ex)}), 400
    except Exception as ex:
        logger.exception("Error in /set_matrix")
        return jsonify({"success": False, "error": str(ex)}), 400


@app.route("/get_matrix", methods=["GET"])
def get_matrix_endpoint():
    """Retrieves the currently active transformation matrix."""
    return jsonify({"success": True, "matrix": solver.get_matrix()}), 200


@app.route("/calculate_offset", methods=["POST"])
def calculate_offset():
    """
    Calculates physical machine XY offset from detected nozzle center pixel coords:
    Payload: { "center_uv": [u, v] }
    """
    try:
        data = request.get_json(force=True)
        center_uv = data.get("center_uv")
        if not center_uv or len(center_uv) != 2:
            return jsonify({"success": False, "error": "Invalid 'center_uv' coordinate"}), 400

        damped_xy, raw_error = solver.calculate_offset_detail((float(center_uv[0]), float(center_uv[1])))
        return jsonify({
            "success": True,
            "offset_xy": list(damped_xy),
            "raw_error_mm": list(raw_error)
        }), 200
    except Exception as ex:
        logger.exception("Error in /calculate_offset")
        return jsonify({"success": False, "error": str(ex)}), 400


@app.route("/calculate_tool_delta", methods=["POST"])
def calculate_tool_delta():
    """
    Calculates physical machine XY offset between a reference tool (e.g. T0)
    and a target tool (e.g. T1) from detected nozzle pixel coordinates.
    Payload: {
        "reference_uv": [u0, v0],
        "target_uv": [u1, v1],
        "tool": 1
    }
    """
    try:
        data = request.get_json(force=True)
        ref_uv = data.get("reference_uv")
        tgt_uv = data.get("target_uv")
        tool_idx = int(data.get("tool", 1))

        if not ref_uv or len(ref_uv) != 2:
            return jsonify({"success": False, "error": "Invalid 'reference_uv' coordinate"}), 400
        if not tgt_uv or len(tgt_uv) != 2:
            return jsonify({"success": False, "error": "Invalid 'target_uv' coordinate"}), 400

        ref_pt = (float(ref_uv[0]), float(ref_uv[1]))
        tgt_pt = (float(tgt_uv[0]), float(tgt_uv[1]))

        delta_xy = solver.calculate_tool_delta(ref_pt, tgt_pt)
        dx, dy = delta_xy

        delta_uv = (round(tgt_pt[0] - ref_pt[0], 3), round(tgt_pt[1] - ref_pt[1], 3))

        return jsonify({
            "success": True,
            "tool": tool_idx,
            "delta_uv": delta_uv,
            "delta_xy": [dx, dy],
            "mpp": solver.mpp,
            "gcode_command": f"SET_TOOL_OFFSET TOOL={tool_idx} X={dx:.4f} Y={dy:.4f}",
            "config_snippet": f"[tool {tool_idx}]\ngcode_x_offset: {dx:.4f}\ngcode_y_offset: {dy:.4f}"
        }), 200
    except Exception as ex:
        logger.exception("Error in /calculate_tool_delta")
        return jsonify({"success": False, "error": str(ex)}), 400



@app.route("/api/samples", methods=["GET"])
def list_samples():
    """Lists available benchmark test samples."""
    sample_dir = os.path.join(os.path.dirname(__file__), "..", "tests", "sample_images")
    if not os.path.exists(sample_dir):
        return jsonify({"samples": []})
    files = sorted([f for f in os.listdir(sample_dir) if f.lower().endswith((".png", ".jpg", ".jpeg"))])
    return jsonify({"samples": files})


@app.route("/api/test_sample", methods=["POST"])
def test_sample():
    """Runs nozzle detection on a named benchmark image."""
    try:
        data = request.get_json(silent=True) or {}
        sample_name = data.get("sample_name")
        if not sample_name:
            return jsonify({"success": False, "error": "Missing 'sample_name'"}), 400

        sample_dir = os.path.join(os.path.dirname(__file__), "..", "tests", "sample_images")
        sample_path = os.path.abspath(os.path.join(sample_dir, os.path.basename(sample_name)))
        if not os.path.exists(sample_path):
            return jsonify({"success": False, "error": f"Sample file not found: {sample_name}"}), 404

        import cv2
        frame = cv2.imread(sample_path)
        if frame is None:
            return jsonify({"success": False, "error": "Failed to decode sample image"}), 400

        result = detector.detect(frame)
        status_text = f"SAMPLE: {sample_name} (Tier {result.tier})" if result.found else "SEARCHING..."
        debugger.update_frame(result.annotated_frame, status_text)

        return jsonify({
            "success": True,
            "found": result.found,
            "center_uv": result.center_uv,
            "radius": result.radius,
            "confidence": result.confidence,
            "tier": result.tier,
            "combo": result.combo,
            "sample_name": sample_name
        }), 200
    except Exception as ex:
        logger.exception("Error in /api/test_sample")
        return jsonify({"success": False, "error": str(ex)}), 500


def _fetch_live_frame():
    frame, _ = grabber.grab_frame()
    return frame


@app.route("/preview", methods=["GET"])
def live_preview():
    """Serves real-time annotated MJPEG preview stream with concurrency limiting."""
    global active_preview_streams
    with stream_lock:
        if active_preview_streams >= MAX_PREVIEW_STREAMS:
            return jsonify({
                "success": False,
                "error": f"Maximum concurrent preview streams ({MAX_PREVIEW_STREAMS}) reached. Please use /snapshot or close other streams."
            }), 429
        active_preview_streams += 1

    def _wrapped_generator():
        global active_preview_streams
        try:
            yield from debugger.mjpeg_generator(frame_fetcher=_fetch_live_frame, max_duration_seconds=120.0)
        finally:
            with stream_lock:
                active_preview_streams = max(0, active_preview_streams - 1)

    return Response(
        _wrapped_generator(),
        mimetype="multipart/x-mixed-replace; boundary=frame"
    )


@app.route("/snapshot", methods=["GET"])
def snapshot_jpeg():
    """Returns a single latest annotated JPEG frame."""
    jpeg_data = debugger.get_latest_jpeg()
    return Response(jpeg_data, mimetype="image/jpeg")


def main():
    parser = argparse.ArgumentParser(description="Tool-Klipper-Calibration Vision Daemon")
    parser.add_argument("--host", default="127.0.0.1", help="Host address to bind (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8090, help="Port to listen on (default: 8090)")
    parser.add_argument("--threads", type=int, default=8, help="Waitress worker threads (default: 8)")
    parser.add_argument("--camera-url", default=None, help="Camera snapshot stream URL")
    parser.add_argument("--mpp", type=float, default=None, help="Pre-calibrated mm-per-pixel scale")
    parser.add_argument("--api-token", default=None, help="Optional API authentication token")
    args = parser.parse_args()

    if args.api_token:
        calibration_lock["token"] = args.api_token

    # Configure camera URL if provided via CLI flag or env var
    cam_url = args.camera_url or os.environ.get("CAMERA_STREAM_URL")
    if cam_url:
        grabber.set_camera_url(cam_url)
        logger.info(f"Initialized Camera URL: {grabber.camera_url}")

    if args.mpp:
        solver.set_mpp(args.mpp)
        logger.info(f"Initialized Scale MPP: {solver.mpp:.5f} mm/px")

    logger.info(f"Starting Tool-Klipper-Calibration Vision Daemon on {args.host}:{args.port}")
    serve(app, host=args.host, port=args.port, threads=args.threads)


if __name__ == "__main__":
    main()
