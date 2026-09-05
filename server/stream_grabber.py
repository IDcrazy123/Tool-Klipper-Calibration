"""
Stream Grabber Module for Tool-Klipper-Calibration Vision Daemon.

Acquires static JPEG snapshot frames from Crowsnest / ustreamer / camera-streamer endpoints
with strict connection timeouts and automatic URL normalization.
"""

from typing import Optional, Tuple
import logging
import cv2
import numpy as np
import requests

logger = logging.getLogger("tool_calibrator.stream_grabber")


class StreamGrabberException(Exception):
    """Raised when frame acquisition fails."""
    pass


class StreamGrabber:
    """
    HTTP snapshot puller for nozzle alignment camera.

    Attributes:
        camera_url (str): Target snapshot endpoint (e.g., http://localhost/webcam2/?action=snapshot).
        timeout (float): Request timeout in seconds to prevent blocking.
    """

    def __init__(self, camera_url: str = "http://localhost/webcam2/?action=snapshot", timeout: float = 2.0) -> None:
        self.camera_url = self._normalize_url(camera_url)
        self.timeout = timeout
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
        to absolute localhost URLs for local service requests.
        """
        clean_url = url.strip()
        if clean_url.startswith("/"):
            return f"http://localhost{clean_url}"
        if not (clean_url.startswith("http://") or clean_url.startswith("https://")):
            return f"http://{clean_url}"
        return clean_url

    def set_camera_url(self, new_url: str) -> None:
        """Updates the target camera snapshot URL."""
        self.camera_url = self._normalize_url(new_url)
        logger.info(f"StreamGrabber camera URL updated to: {self.camera_url}")

    def grab_frame(self) -> Tuple[Optional[np.ndarray], Optional[str]]:
        """
        Pulls a single JPEG frame from the snapshot endpoint and decodes it to a BGR numpy array.

        Returns:
            Tuple[Optional[np.ndarray], Optional[str]]:
                - image: Decoded BGR image array if successful, None otherwise.
                - error: Error description if failed, None otherwise.
        """
        try:
            resp = self.session.get(self.camera_url, timeout=self.timeout)
            if resp.status_code != 200:
                err_msg = f"HTTP {resp.status_code} while fetching snapshot from {self.camera_url}"
                logger.warning(err_msg)
                return None, err_msg

            # Decode raw byte stream into OpenCV BGR format
            image_array = np.frombuffer(resp.content, dtype=np.uint8)
            frame = cv2.imdecode(image_array, cv2.IMREAD_COLOR)
            if frame is None or frame.size == 0:
                err_msg = f"Failed to decode JPEG image from {self.camera_url}"
                logger.warning(err_msg)
                return None, err_msg

            return frame, None

        except requests.exceptions.Timeout:
            err_msg = f"Timeout ({self.timeout}s) connecting to camera at {self.camera_url}"
            logger.error(err_msg)
            return None, err_msg
        except requests.exceptions.ConnectionError:
            err_msg = f"Connection refused connecting to camera at {self.camera_url}"
            logger.error(err_msg)
            return None, err_msg
        except Exception as ex:
            err_msg = f"Unexpected error grabbing frame: {str(ex)}"
            logger.exception(err_msg)
            return None, err_msg
