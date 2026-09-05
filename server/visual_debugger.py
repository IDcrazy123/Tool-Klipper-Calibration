"""
Visual Debugger & Live Preview Streamer for Tool-Klipper-Calibration.

Provides a thread-safe frame buffer and MJPEG multi-part streaming generator
with real-time HUD annotations for web frontends (Mainsail/Fluidd).
"""

from typing import Optional, Generator
import threading
import time
import cv2
import numpy as np


class VisualDebugger:
    """
    Maintains the latest debug frame and streams it as MJPEG.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._current_frame: Optional[np.ndarray] = None
        self._fps: int = 10
        self._last_update_time: float = time.time()
        self.hud_text: str = "IDLE"

        # Generate a placeholder standby frame
        self._standby_frame = self._create_standby_frame()
        self._current_frame = self._standby_frame

    def _create_standby_frame(self, width: int = 640, height: int = 480) -> np.ndarray:
        """Creates a dark background frame indicating standby status."""
        frame = np.zeros((height, width, 3), dtype=np.uint8)
        # Background gradient or dark theme
        frame[:] = (28, 28, 30)

        # Crosshair center
        cx, cy = width // 2, height // 2
        cv2.line(frame, (cx, 0), (cx, height), (50, 50, 50), 1)
        cv2.line(frame, (0, cy), (width, cy), (50, 50, 50), 1)

        cv2.putText(
            frame, "Tool-Klipper-Calibration Vision Daemon", (50, cy - 20),
            cv2.FONT_HERSHEY_SIMPLEX, 0.65, (0, 200, 255), 2, cv2.LINE_AA
        )
        cv2.putText(
            frame, "Waiting for camera frame acquisition...", (50, cy + 20),
            cv2.FONT_HERSHEY_SIMPLEX, 0.5, (180, 180, 180), 1, cv2.LINE_AA
        )
        return frame

    def update_frame(self, frame: np.ndarray, status_overlay: Optional[str] = None) -> None:
        """
        Updates the internal frame buffer with optional status HUD.
        """
        display_frame = frame.copy()
        if status_overlay is not None:
            self.hud_text = status_overlay

        # Draw HUD header bar
        cv2.rectangle(display_frame, (0, 0), (display_frame.shape[1], 30), (0, 0, 0), -1)
        cv2.putText(
            display_frame, f"STATUS: {self.hud_text}", (10, 20),
            cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 128), 1, cv2.LINE_AA
        )

        with self._lock:
            self._current_frame = display_frame
            self._last_update_time = time.time()

    def get_latest_jpeg(self) -> bytes:
        """Encodes the current frame to a JPEG byte string."""
        with self._lock:
            frame_to_encode = self._current_frame if self._current_frame is not None else self._standby_frame

        success, buffer = cv2.imencode(".jpg", frame_to_encode, [cv2.IMWRITE_JPEG_QUALITY, 85])
        if success:
            return buffer.tobytes()
        return b""

    def mjpeg_generator(self) -> Generator[bytes, None, None]:
        """
        Yields multipart HTTP responses for MJPEG browser streams.
        """
        frame_interval = 1.0 / max(1, self._fps)
        while True:
            start = time.time()
            jpeg_bytes = self.get_latest_jpeg()
            if jpeg_bytes:
                yield (
                    b"--frame\r\n"
                    b"Content-Type: image/jpeg\r\n\r\n" + jpeg_bytes + b"\r\n"
                )

            elapsed = time.time() - start
            sleep_time = max(0.01, frame_interval - elapsed)
            time.sleep(sleep_time)
