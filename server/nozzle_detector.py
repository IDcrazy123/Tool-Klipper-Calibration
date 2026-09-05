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

    def _refine_upper_arc_symmetry(
        self, gray: np.ndarray, cx: float, cy: float, radius: float, max_shift: float = 4.0
    ) -> Tuple[float, float]:
        """
        Refines nozzle center by optimizing radial gradient consistency along the upper arc
        (150-deg to 30-deg elevation), completely avoiding downward conical specular glare flares.
        """
        try:
            gx = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3)
            gy = cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3)
            angles = np.linspace(-np.pi * 0.85, -np.pi * 0.15, 30)
            cos_a = np.cos(angles).astype(np.float32)
            sin_a = np.sin(angles).astype(np.float32)

            best_score = -1e9
            best_c = (cx, cy)
            shifts = np.arange(-max_shift, max_shift + 0.5, 0.5, dtype=np.float32)
            h, w = gray.shape[:2]

            for dy in shifts:
                y = cy + dy
                for dx in shifts:
                    x = cx + dx
                    px = x + radius * cos_a
                    py = y + radius * sin_a
                    if px.min() < 1 or px.max() >= w - 1 or py.min() < 1 or py.max() >= h - 1:
                        continue
                    ix = np.round(px).astype(int)
                    iy = np.round(py).astype(int)
                    proj = gx[iy, ix] * cos_a + gy[iy, ix] * sin_a
                    score = float(np.mean(np.abs(proj)))
                    if score > best_score:
                        best_score = score
                        best_c = (float(x), float(y))
            return best_c
        except Exception:
            return cx, cy

    def _rank_candidate_circles(
        self,
        gray_roi: np.ndarray,
        candidates: np.ndarray,
        frame_w: int,
        frame_h: int,
        roi_x0: int,
        roi_y0: int,
        d0: float = 140.0
    ) -> Optional[np.ndarray]:
        """
        Ranks candidate circles returned by Hough transform using upper-arc radial gradient
        projection combined with a soft distance prior from the optical center.
        """
        if candidates is None or len(candidates) == 0:
            return None

        h, w = gray_roi.shape[:2]
        gx = cv2.Sobel(gray_roi, cv2.CV_32F, 1, 0, ksize=3)
        gy = cv2.Sobel(gray_roi, cv2.CV_32F, 0, 1, ksize=3)
        up_angles = np.linspace(-np.pi * 0.85, -np.pi * 0.15, 30)
        cos_up = np.cos(up_angles).astype(np.float32)
        sin_up = np.sin(up_angles).astype(np.float32)

        scored = []
        center_x, center_y = frame_w / 2.0, frame_h / 2.0

        for c in candidates:
            cx, cy, r = float(c[0]), float(c[1]), float(c[2])
            px = cx + r * cos_up
            py = cy + r * sin_up
            valid = (px >= 1) & (px < w - 1) & (py >= 1) & (py < h - 1)
            if np.sum(valid) < 15:
                continue
            ix = np.round(px[valid]).astype(int)
            iy = np.round(py[valid]).astype(int)
            proj = gx[iy, ix] * cos_up[valid] + gy[iy, ix] * sin_up[valid]
            up_score = float(np.mean(np.abs(proj)))

            # Distance weighting relative to optical center
            global_cx = roi_x0 + cx
            global_cy = roi_y0 + cy
            dist = math.hypot(global_cx - center_x, global_cy - center_y)
            w_dist = 1.0 / (1.0 + (dist / d0) ** 2)
            total_score = up_score * w_dist
            scored.append((total_score, c))

        if not scored:
            return candidates[0]

        scored.sort(key=lambda x: x[0], reverse=True)
        return scored[0][1]

    def detect_curvature_circle(self, frame: np.ndarray) -> Optional[Tuple[float, float, float]]:
        """
        High-precision Tier 0 detector using bilateral edge filtering, Hough circular curvature,
        and upper-arc radial gradient refinement in the central region of interest.
        """
        h, w = frame.shape[:2]
        cx_center, cy_center = w / 2.0, h / 2.0
        r_roi = min(int(min(w, h) * 0.35), 140)

        x0 = max(0, int(cx_center - r_roi))
        x1 = min(w, int(cx_center + r_roi))
        y0 = max(0, int(cy_center - r_roi))
        y1 = min(h, int(cy_center + r_roi))
        roi = frame[y0:y1, x0:x1]

        gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
        bilateral = cv2.bilateralFilter(gray, 9, 75, 75)

        circles = cv2.HoughCircles(
            bilateral,
            cv2.HOUGH_GRADIENT,
            dp=1,
            minDist=8,
            param1=60,
            param2=16,
            minRadius=7,
            maxRadius=25
        )
        proc_gray = gray
        if circles is None or len(circles) == 0:
            # Low-light & dim-illumination adaptive enhancement via CLAHE
            if gray.mean() < 90 or gray.std() < 35:
                clahe = cv2.createCLAHE(clipLimit=2.5, tileGridSize=(8, 8))
                proc_gray = clahe.apply(gray)
                bilateral_enh = cv2.bilateralFilter(proc_gray, 9, 75, 75)
                circles = cv2.HoughCircles(
                    bilateral_enh,
                    cv2.HOUGH_GRADIENT,
                    dp=1,
                    minDist=8,
                    param1=50,
                    param2=14,
                    minRadius=7,
                    maxRadius=25
                )

        if circles is None or len(circles) == 0:
            return None

        # Rank candidate circles by upper-arc radial gradient contrast and distance prior
        best = self._rank_candidate_circles(proc_gray, circles[0], w, h, x0, y0)
        if best is None:
            return None

        # Refine candidate within ROI using upper-arc gradient symmetry
        refined_local = self._refine_upper_arc_symmetry(gray, float(best[0]), float(best[1]), float(best[2]), max_shift=4.0)
        global_x = float(x0 + refined_local[0])
        global_y = float(y0 + refined_local[1])
        return global_x, global_y, float(best[2])

    def _find_closest_keypoint(self, keypoints: List[cv2.KeyPoint]) -> cv2.KeyPoint:
        """Selects the detected candidate closest to the optical center."""
        cx, cy = self.image_center
        return min(
            keypoints,
            key=lambda kp: math.hypot(kp.pt[0] - cx, kp.pt[1] - cy)
        )

    def detect(self, frame: np.ndarray) -> DetectionResult:
        """
        Runs the multi-stage detection pipeline on the provided frame:
        1. Primary: Radial Edge-Curvature Gradient Invariance with Upper-Arc Symmetry.
        2. Fallback: 3-tier cascade SimpleBlobDetector with multi-algorithm preprocessing.

        Returns:
            DetectionResult with detected sub-pixel coordinates and annotated image.
        """
        annotated = frame.copy()
        height, width = frame.shape[:2]
        self.frame_width = width
        self.frame_height = height
        self.image_center = (width / 2.0, height / 2.0)

        pt_x: Optional[float] = None
        pt_y: Optional[float] = None
        radius: float = 0.0
        matched_tier = 0
        matched_combo = 0

        # Tier 0 / Primary: Curvature Gradient Invariance
        curv_result = self.detect_curvature_circle(frame)
        if curv_result is not None:
            pt_x, pt_y, radius = curv_result
            matched_tier = 1
            matched_combo = 10  # Curvature Invariant combo ID
            self.last_successful_combo = matched_combo
        else:
            # Fallback: 3-tier cascade SimpleBlobDetector
            tier_stages = [
                (1, self.standard_detector, [(2, 1), (0, 2), (1, 3)]),
                (2, self.relaxed_detector, [(2, 4), (0, 5), (1, 6)]),
                (3, self.super_relaxed_detector, [(2, 7)]),
            ]

            chosen_keypoint: Optional[cv2.KeyPoint] = None
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

            if chosen_keypoint is not None:
                raw_x, raw_y = chosen_keypoint.pt
                radius = chosen_keypoint.size / 2.0
                gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                # Upper arc refinement to counteract glare flare
                pt_x, pt_y = self._refine_upper_arc_symmetry(gray, raw_x, raw_y, radius, max_shift=3.0)

        # Draw visual overlay on debug frame
        cx, cy = int(self.image_center[0]), int(self.image_center[1])
        # Crosshair lines
        cv2.line(annotated, (cx, 0), (cx, height), (0, 0, 0), 2, cv2.LINE_AA)
        cv2.line(annotated, (0, cy), (width, cy), (0, 0, 0), 2, cv2.LINE_AA)
        cv2.line(annotated, (cx, 0), (cx, height), (255, 255, 255), 1, cv2.LINE_AA)
        cv2.line(annotated, (0, cy), (width, cy), (255, 255, 255), 1, cv2.LINE_AA)

        if pt_x is not None and pt_y is not None:
            r_int = max(2, int(round(radius)))
            center = (int(round(pt_x)), int(round(pt_y)))

            # Color coding: Green (Standard/Curvature), Orange (Relaxed), Blue (Super-Relaxed)
            color_map = {1: (0, 255, 0), 2: (0, 165, 255), 3: (255, 100, 0)}
            circle_color = color_map.get(matched_tier, (0, 255, 0))

            # Draw transparent overlay circle over nozzle
            overlay = annotated.copy()
            cv2.circle(overlay, center, r_int, circle_color, -1, cv2.LINE_AA)
            cv2.addWeighted(overlay, 0.35, annotated, 0.65, 0, annotated)

            # Draw crisp circle boundary and center cross
            cv2.circle(annotated, center, r_int, (0, 0, 0), 1, cv2.LINE_AA)
            cv2.line(annotated, (center[0] - 6, center[1]), (center[0] + 6, center[1]), (0, 0, 255), 2)
            cv2.line(annotated, (center[0], center[1] - 6), (center[0], center[1] + 6), (0, 0, 255), 2)

            confidence = 0.98 if matched_combo == 10 else (1.0 if matched_tier == 1 else (0.85 if matched_tier == 2 else 0.65))
            return DetectionResult(
                found=True,
                center_uv=(round(pt_x, 3), round(pt_y, 3)),
                radius=round(radius, 2),
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
