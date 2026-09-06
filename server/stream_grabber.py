"""
Stream Grabber Module for Tool-Klipper-Calibration Vision Daemon.

Acquires static JPEG snapshot frames from Crowsnest / ustreamer / camera-streamer endpoints
with strict connection timeouts and automatic URL normalization.
"""

from typing import Optional, Tuple
import logging
import threading
import time
import cv2
import numpy as np
import requests

logger = logging.getLogger("tool_calibrator.stream_grabber")


class StreamGrabberException(Exception):
    """Raised when frame acquisition fails."""
    pass


class StreamGrabber:
    """
    HTTP snapshot puller for nozzle alignment camera with frame buffer cache.

    Attributes:
        camera_url (str): Target snapshot endpoint (e.g., http://localhost/webcam2/?action=snapshot).
        timeout (float): Request timeout in seconds to prevent blocking.
        cache_ttl (float): Max age of cached frame in seconds to prevent redundant requests.
    """

    def __init__(self, camera_url: str = "http://localhost/webcam2/?action=snapshot", timeout: float = 2.0, cache_ttl: float = 0.08) -> None:
        self.camera_url = self._normalize_url(camera_url)
        self.timeout = timeout
        self.cache_ttl = cache_ttl
        self._cache_lock = threading.Lock()
        self._cached_frame: Optional[np.ndarray] = None
        self._cached_error: Optional[str] = None
        self._cached_time: float = 0.0
        self.session = requests.Session()
        # Disable caching to always pull the latest real-time frame from Crowsnest
        self.session.headers.update({
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0"
        })

    def _normalize_url(self, url: str) -> str:
        """
        Normalizes relative Nginx paths (e.g., /webcam2/?action=snapshot)
        to absolute localhost URLs, and automatically converts stream URLs
        (e.g., ?action=stream or /stream) to static snapshot endpoints.
        """
        clean_url = url.strip()
        if clean_url.startswith("/"):
            clean_url = f"http://localhost{clean_url}"
        elif not (clean_url.startswith("http://") or clean_url.startswith("https://")):
            clean_url = f"http://{clean_url}"

        # Convert Crowsnest / ustreamer / mjpg-streamer action=stream to snapshot
        if "action=stream" in clean_url:
            clean_url = clean_url.replace("action=stream", "action=snapshot")
        # Convert camera-streamer /stream endpoint to /snapshot
        elif clean_url.endswith("/stream"):
            clean_url = clean_url[:-7] + "/snapshot"
        elif clean_url.endswith("/stream/"):
            clean_url = clean_url[:-8] + "/snapshot"

        return clean_url

    def set_camera_url(self, new_url: str) -> None:
        """Updates the target camera snapshot URL."""
        with self._cache_lock:
            self.camera_url = self._normalize_url(new_url)
            self._cached_frame = None
            self._cached_error = None
            self._cached_time = 0.0
        logger.info(f"StreamGrabber camera URL updated to: {self.camera_url}")

    def grab_frame(self, force_refresh: bool = False) -> Tuple[Optional[np.ndarray], Optional[str]]:
        """
        Pulls a single JPEG frame from the snapshot endpoint and decodes it to a BGR numpy array.
        Uses a high-speed in-memory cache (~80ms TTL) to minimize redundant network I/O and JPEG decoding.

        Returns:
            Tuple[Optional[np.ndarray], Optional[str]]:
                - image: Decoded BGR image array if successful, None otherwise.
                - error: Error description if failed, None otherwise.
        """
        now = time.time()
        with self._cache_lock:
            if not force_refresh and self._cached_frame is not None and (now - self._cached_time < self.cache_ttl):
                return self._cached_frame.copy(), self._cached_error

        try:
            resp = self.session.get(self.camera_url, timeout=self.timeout)
            if resp.status_code != 200:
                err_msg = f"HTTP {resp.status_code} while fetching snapshot from {self.camera_url}"
                logger.warning(err_msg)
                with self._cache_lock:
                    self._cached_error = err_msg
                    self._cached_time = now
                return None, err_msg

            # Decode raw byte stream into OpenCV BGR format
            image_array = np.frombuffer(resp.content, dtype=np.uint8)
            frame = cv2.imdecode(image_array, cv2.IMREAD_COLOR)
            if frame is None or frame.size == 0:
                err_msg = f"Failed to decode JPEG image from {self.camera_url}"
                logger.warning(err_msg)
                with self._cache_lock:
                    self._cached_error = err_msg
                    self._cached_time = now
                return None, err_msg

            with self._cache_lock:
                self._cached_frame = frame
                self._cached_error = None
                self._cached_time = now

            return frame.copy(), None

        except requests.exceptions.Timeout:
            err_msg = f"Timeout ({self.timeout}s) connecting to camera at {self.camera_url}"
            logger.error(err_msg)
            with self._cache_lock:
                self._cached_error = err_msg
                self._cached_time = now
            return None, err_msg
        except requests.exceptions.ConnectionError:
            err_msg = f"Connection refused connecting to camera at {self.camera_url}"
            logger.error(err_msg)
            with self._cache_lock:
                self._cached_error = err_msg
                self._cached_time = now
            return None, err_msg
        except Exception as ex:
            err_msg = f"Unexpected error grabbing frame: {str(ex)}"
            logger.exception(err_msg)
            with self._cache_lock:
                self._cached_error = err_msg
                self._cached_time = now
            return None, err_msg
