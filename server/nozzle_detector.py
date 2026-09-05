"""
Nozzle Detection Module for Tool-Klipper-Calibration Vision Daemon.

Implements a 3-tier cascade SimpleBlobDetector with multi-algorithm image preprocessing
derived and optimized from TAMV and kTAMV.
"""

from dataclasses import dataclass
from typing import Optional, Tuple, List
import logging
import math
import cv2
import numpy as np

logger = logging.getLogger("tool_calibrator.nozzle_detector")


@dataclass
class DetectionResult:
    """Represents the outcome of a nozzle orifice detection attempt."""
    found: bool
    center_uv: Optional[Tuple[float, float]] = None  # (u, v) in pixel coordinates
    radius: float = 0.0
    confidence: float = 0.0
    tier: int = 0  # 1: Standard, 2: Relaxed, 3: Super-Relaxed
    combo: int = 0  # Preprocessor and detector combination ID
    annotated_frame: Optional[np.ndarray] = None


class NozzleDetector:
    """
    Multi-stage OpenCV cascade blob detector for 3D printer nozzle orifices.
    """

    def __init__(self, frame_width: int = 640, frame_height: int = 480) -> None:
        self.frame_width = frame_width
        self.frame_height = frame_height
        self.image_center = (frame_width / 2.0, frame_height / 2.0)

        # Gamma lookup table for contrast enhancement
        self._gamma_table = self._build_gamma_table(gamma=1.2)

        # Instantiate the 3 cascade detector stages
        self.standard_detector = self._create_standard_detector()
        self.relaxed_detector = self._create_relaxed_detector()
        self.super_relaxed_detector = self._create_super_relaxed_detector()

        # Cache last successful combo to accelerate repetitive detections
        self.last_successful_combo: Optional[int] = None

    @staticmethod
    def _build_gamma_table(gamma: float = 1.2) -> np.ndarray:
        """Precomputes an 8-bit gamma correction lookup table."""
        inv_gamma = 1.0 / gamma
        table = np.array([((i / 255.0) ** inv_gamma) * 255 for i in range(256)]).astype("uint8")
        return table

    def _adjust_gamma(self, image: np.ndarray) -> np.ndarray:
        """Applies gamma correction to enhance low-light contrast."""
        return cv2.LUT(image, self._gamma_table)

    def _create_standard_detector(self) -> cv2.SimpleBlobDetector:
        """Tier 1: High-precision detector requiring strict circularity."""
        params = cv2.SimpleBlobDetector_Params()
        params.minThreshold = 5
        params.maxThreshold = 220
        params.thresholdStep = 5

        params.filterByArea = True
        params.minArea = 45
        params.maxArea = 20000

        params.filterByCircularity = True
        params.minCircularity = 0.60
        params.maxCircularity = 1.0

        params.filterByConvexity = True
        params.minConvexity = 0.35
        params.maxConvexity = 1.0

        params.filterByInertia = True
        params.minInertiaRatio = 0.30

        params.filterByColor = False
        return cv2.SimpleBlobDetector_create(params)

    def _create_relaxed_detector(self) -> cv2.SimpleBlobDetector:
        """Tier 2: Looser thresholds for discolored or slightly encrusted nozzles."""
        params = cv2.SimpleBlobDetector_Params()
        params.minThreshold = 5
        params.maxThreshold = 225
        params.thresholdStep = 5

        params.filterByArea = True
        params.minArea = 35
        params.maxArea = 25000

        params.filterByCircularity = True
        params.minCircularity = 0.50
        params.maxCircularity = 1.0

        params.filterByConvexity = True
        params.minConvexity = 0.20
        params.maxConvexity = 1.0

        params.filterByInertia = True
        params.minInertiaRatio = 0.20

        params.filterByColor = False
        return cv2.SimpleBlobDetector_create(params)

    def _create_super_relaxed_detector(self) -> cv2.SimpleBlobDetector:
        """Tier 3: Fallback detector for high-noise and heavily stained nozzles."""
        params = cv2.SimpleBlobDetector_Params()
        params.minThreshold = 5
        params.maxThreshold = 240
        params.thresholdStep = 10

        params.filterByArea = True
        params.minArea = 25
        params.maxArea = 35000

        params.filterByCircularity = True
        params.minCircularity = 0.38
        params.maxCircularity = 1.0

        params.filterByConvexity = True
        params.minConvexity = 0.15
        params.maxConvexity = 1.0

        params.filterByInertia = True
        params.minInertiaRatio = 0.15

        params.filterByColor = False
        params.minDistBetweenBlobs = 5
        return cv2.SimpleBlobDetector_create(params)

    def preprocess_image(self, frame: np.ndarray, algorithm: int = 0) -> np.ndarray:
        """
        Applies dedicated optical filtering before blob detection.

        Args:
            frame: Raw BGR input frame.
            algorithm:
                0: Gamma + YUV Y-channel + Gaussian Blur + Adaptive Gaussian Thresholding.
                1: Gamma + Grayscale + Triangle Thresholding + Gaussian Blur.
                2: Grayscale + Median Blur (salt-and-pepper noise suppression).
        """
        try:
            enhanced = self._adjust_gamma(frame)
        except Exception:
            enhanced = frame.copy()

        if algorithm == 0:
            # Alg 0: Isolates circular orifice edges in the luminance plane
            yuv = cv2.cvtColor(enhanced, cv2.COLOR_BGR2YUV)
            y_channel = cv2.split(yuv)[0]
            blurred = cv2.GaussianBlur(y_channel, (7, 7), 6)
            thresh = cv2.adaptiveThreshold(
                blurred, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 35, 1
            )
            return cv2.cvtColor(thresh, cv2.COLOR_GRAY2BGR)

        elif algorithm == 1:
            # Alg 1: Robust against specular glare on shiny brass tips
            gray = cv2.cvtColor(enhanced, cv2.COLOR_BGR2GRAY)
            _, thresh = cv2.threshold(gray, 127, 255, cv2.THRESH_BINARY | cv2.THRESH_TRIANGLE)
            blurred = cv2.GaussianBlur(thresh, (7, 7), 6)
            return cv2.cvtColor(blurred, cv2.COLOR_GRAY2BGR)

        else:
            # Alg 2: Median blur for carbonized debris suppression
            gray = cv2.cvtColor(enhanced, cv2.COLOR_BGR2GRAY)
            blurred = cv2.medianBlur(gray, 5)
            return cv2.cvtColor(blurred, cv2.COLOR_GRAY2BGR)

    def _find_closest_keypoint(self, keypoints: List[cv2.KeyPoint]) -> cv2.KeyPoint:
        """Selects the detected candidate closest to the optical center."""
        cx, cy = self.image_center
        return min(
            keypoints,
            key=lambda kp: math.hypot(kp.pt[0] - cx, kp.pt[1] - cy)
        )

    def detect(self, frame: np.ndarray) -> DetectionResult:
        """
        Runs the 3-tier cascade detection pipeline on the provided frame.

        Returns:
            DetectionResult with detected sub-pixel coordinates and annotated image.
        """
        annotated = frame.copy()
        height, width = frame.shape[:2]
        self.frame_width = width
        self.frame_height = height
        self.image_center = (width / 2.0, height / 2.0)

        # Detection cascade combinations: (preprocessor_alg, detector, tier, combo_id)
        # Alg 2: Grayscale + Median Blur (multi-threshold slice sweep - optimal for micro-orifices)
        # Alg 0: Gamma + YUV Adaptive Gaussian (for low-contrast or dark nozzles)
        # Alg 1: Gamma + Triangle Threshold (for shiny brass tip glare)
        # Tiered cascade stages: evaluate preprocessors within each tier and select
        # the candidate closest to the optical center to prevent latching onto peripheral debris.
        tier_stages = [
            (1, self.standard_detector, [(2, 1), (0, 2), (1, 3)]),
            (2, self.relaxed_detector, [(2, 4), (0, 5), (1, 6)]),
            (3, self.super_relaxed_detector, [(2, 7)]),
        ]

        chosen_keypoint: Optional[cv2.KeyPoint] = None
        matched_tier = 0
        matched_combo = 0

        for tier, detector, combos in tier_stages:
            tier_candidates = []
            for alg, combo_id in combos:
                preprocessed = self.preprocess_image(frame, algorithm=alg)
                keypoints = detector.detect(preprocessed)
                for kp in keypoints:
                    dist = math.hypot(kp.pt[0] - self.image_center[0], kp.pt[1] - self.image_center[1])
                    tier_candidates.append((dist, kp, combo_id))
            if tier_candidates:
                tier_candidates.sort(key=lambda item: item[0])
                chosen_keypoint = tier_candidates[0][1]
                matched_combo = tier_candidates[0][2]
                matched_tier = tier
                self.last_successful_combo = matched_combo
                break

        # Draw visual overlay on debug frame
        cx, cy = int(self.image_center[0]), int(self.image_center[1])
        # Crosshair lines
        cv2.line(annotated, (cx, 0), (cx, height), (0, 0, 0), 2, cv2.LINE_AA)
        cv2.line(annotated, (0, cy), (width, cy), (0, 0, 0), 2, cv2.LINE_AA)
        cv2.line(annotated, (cx, 0), (cx, height), (255, 255, 255), 1, cv2.LINE_AA)
        cv2.line(annotated, (0, cy), (width, cy), (255, 255, 255), 1, cv2.LINE_AA)

        if chosen_keypoint is not None:
            pt_x, pt_y = chosen_keypoint.pt
            radius = int(chosen_keypoint.size / 2.0)
            center = (int(round(pt_x)), int(round(pt_y)))

            # Color coding: Green (Standard), Orange (Relaxed), Blue (Super-Relaxed)
            color_map = {1: (0, 255, 0), 2: (0, 165, 255), 3: (255, 100, 0)}
            circle_color = color_map.get(matched_tier, (0, 255, 0))

            # Draw transparent overlay circle over nozzle
            overlay = annotated.copy()
            cv2.circle(overlay, center, radius, circle_color, -1, cv2.LINE_AA)
            cv2.addWeighted(overlay, 0.35, annotated, 0.65, 0, annotated)

            # Draw crisp circle boundary and center cross
            cv2.circle(annotated, center, radius, (0, 0, 0), 1, cv2.LINE_AA)
            cv2.line(annotated, (center[0] - 6, center[1]), (center[0] + 6, center[1]), (0, 0, 255), 2)
            cv2.line(annotated, (center[0], center[1] - 6), (center[0], center[1] + 6), (0, 0, 255), 2)

            confidence = 1.0 if matched_tier == 1 else (0.85 if matched_tier == 2 else 0.65)
            return DetectionResult(
                found=True,
                center_uv=(round(pt_x, 3), round(pt_y, 3)),
                radius=round(chosen_keypoint.size / 2.0, 2),
                confidence=confidence,
                tier=matched_tier,
                combo=matched_combo,
                annotated_frame=annotated
            )

        # Draw red warning circle when no nozzle detected
        cv2.circle(annotated, (cx, cy), 20, (0, 0, 255), 2, cv2.LINE_AA)
        return DetectionResult(
            found=False,
            center_uv=None,
            radius=0.0,
            confidence=0.0,
            tier=0,
            combo=0,
            annotated_frame=annotated
        )
