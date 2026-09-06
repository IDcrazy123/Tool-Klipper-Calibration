"""
Visual Debugger & Live Preview Streamer for Tool-Klipper-Calibration.

Provides a thread-safe frame buffer and MJPEG multi-part streaming generator
with real-time HUD annotations for web frontends (Mainsail/Fluidd).
"""

from typing import Optional, Generator, Any
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

    def update_frame(self, frame: np.ndarray, status_overlay: Optional[str] = None, show_crosshairs: bool = True) -> None:
        """
        Updates the internal frame buffer with status HUD and optical reticle.
        """
        display_frame = frame.copy()
        if status_overlay is not None:
            self.hud_text = status_overlay

        h, w = display_frame.shape[:2]
        cx, cy = w // 2, h // 2

        # Draw subtle optical crosshairs and center circle if enabled
        if show_crosshairs:
            # Full screen axis lines in dim amber
            cv2.line(display_frame, (cx, 0), (cx, h), (0, 140, 200), 1, cv2.LINE_AA)
            cv2.line(display_frame, (0, cy), (w, cy), (0, 140, 200), 1, cv2.LINE_AA)
            # Center target bullseye rings
            cv2.circle(display_frame, (cx, cy), 15, (0, 220, 255), 1, cv2.LINE_AA)
            cv2.circle(display_frame, (cx, cy), 30, (0, 180, 230), 1, cv2.LINE_AA)

        # Draw HUD header bar
        cv2.rectangle(display_frame, (0, 0), (w, 32), (15, 23, 42), -1)
        cv2.putText(
            display_frame, f"STATUS: {self.hud_text}", (12, 21),
            cv2.FONT_HERSHEY_SIMPLEX, 0.5, (56, 189, 248), 1, cv2.LINE_AA
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

    def mjpeg_generator(
        self, frame_fetcher: Optional[Any] = None, max_duration_seconds: float = 120.0
    ) -> Generator[bytes, None, None]:
        """
        Yields multipart HTTP responses for MJPEG browser streams.
        Optionally polls frame_fetcher to continuously update live stream.
        Terminates after max_duration_seconds to prevent worker thread starvation.
        """
        frame_interval = 1.0 / max(1, self._fps)
        stream_start = time.time()
        while time.time() - stream_start < max_duration_seconds:
            start = time.time()
            if frame_fetcher is not None:
                try:
                    raw_frame = frame_fetcher()
                    if raw_frame is not None:
                        self.update_frame(raw_frame)
                except Exception:
                    pass

            jpeg_bytes = self.get_latest_jpeg()
            if jpeg_bytes:
                yield (
                    b"--frame\r\n"
                    b"Content-Type: image/jpeg\r\n\r\n" + jpeg_bytes + b"\r\n"
                )

            elapsed = time.time() - start
            sleep_time = max(0.02, frame_interval - elapsed)
            time.sleep(sleep_time)
