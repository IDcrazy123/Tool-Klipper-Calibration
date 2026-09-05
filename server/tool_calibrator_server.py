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
import time
from typing import Dict, Any

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
        "version": "0.1.0",
        "camera_url": grabber.camera_url,
        "matrix_solved": solver.transform_matrix is not None,
        "calibrated_mpp": solver.mpp
    }), 200


@app.route("/set_camera", methods=["POST"])
def set_camera():
    """Updates the snapshot camera URL."""
    try:
        data: Dict[str, Any] = request.get_json(force=True)
        new_url = data.get("camera_url")
        if not new_url:
            return jsonify({"success": False, "error": "Missing 'camera_url' parameter"}), 400

        grabber.set_camera_url(new_url)
        return jsonify({"success": True, "camera_url": grabber.camera_url}), 200
    except Exception as ex:
        logger.exception("Error in /set_camera")
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
                debugger.update_frame(debugger.get_latest_jpeg() or np.zeros((480, 640, 3)), f"CAMERA ERROR: {err}")
                return jsonify({
                    "success": False,
                    "found": False,
                    "error": f"Failed grabbing frame: {err}"
                }), 502

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
    try:
        data = request.get_json(force=True)
        points = data.get("calibration_points", [])
        if not points:
            return jsonify({"success": False, "error": "No calibration points provided"}), 400

        solver.solve_matrix(points)
        return jsonify({
            "success": True,
            "matrix_solved": True
        }), 200
    except Exception as ex:
        logger.exception("Error in /solve_matrix")
        return jsonify({"success": False, "error": str(ex)}), 400


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

        offset_xy = solver.calculate_offset((float(center_uv[0]), float(center_uv[1])))
        return jsonify({
            "success": True,
            "offset_xy": offset_xy
        }), 200
    except Exception as ex:
        logger.exception("Error in /calculate_offset")
        return jsonify({"success": False, "error": str(ex)}), 400


@app.route("/preview", methods=["GET"])
def live_preview():
    """Serves real-time annotated MJPEG preview stream."""
    return Response(
        debugger.mjpeg_generator(),
        mimetype="multipart/x-mixed-replace; boundary=frame"
    )


@app.route("/snapshot", methods=["GET"])
def snapshot_jpeg():
    """Returns a single latest annotated JPEG frame."""
    jpeg_data = debugger.get_latest_jpeg()
    return Response(jpeg_data, mimetype="image/jpeg")


def main():
    parser = argparse.ArgumentParser(description="Tool-Klipper-Calibration Vision Daemon")
    parser.add_argument("--host", default="0.0.0.0", help="Host address to bind (default: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=8090, help="Port to listen on (default: 8090)")
    parser.add_argument("--threads", type=int, default=4, help="Waitress worker threads (default: 4)")
    args = parser.parse_args()

    logger.info(f"Starting Tool-Klipper-Calibration Vision Daemon on {args.host}:{args.port}")
    serve(app, host=args.host, port=args.port, threads=args.threads)


if __name__ == "__main__":
    main()
