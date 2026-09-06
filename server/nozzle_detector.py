"""
Nozzle Detection Module for Tool-Klipper-Calibration Vision Daemon.

Implements a 3-tier cascade SimpleBlobDetector with multi-algorithm image preprocessing
derived and optimized from TAMV and kTAMV.
"""

from dataclasses import dataclass
from typing import Optional, Tuple, List, Union
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

    @staticmethod
    def _sample_bilinear_vec(img: np.ndarray, x: np.ndarray, y: np.ndarray) -> np.ndarray:
        """Vectorized bilinear interpolation on 2D float image."""
        h, w = img.shape[:2]
        x0 = np.floor(x).astype(int)
        x1 = np.clip(x0 + 1, 0, w - 1)
        y0 = np.floor(y).astype(int)
        y1 = np.clip(y0 + 1, 0, h - 1)
        x0 = np.clip(x0, 0, w - 1)
        y0 = np.clip(y0, 0, h - 1)
        fx = (x - x0).astype(np.float32)
        fy = (y - y0).astype(np.float32)
        top = (1.0 - fx) * img[y0, x0] + fx * img[y0, x1]
        bot = (1.0 - fx) * img[y1, x0] + fx * img[y1, x1]
        return (1.0 - fy) * top + fy * bot

    def _refine_upper_arc_symmetry(
        self,
        gray: np.ndarray,
        cx: float,
        cy: float,
        radius: float,
        max_shift: float = 3.5,
        max_r_shift: float = 3.0,
        return_radius: bool = False
    ) -> Union[Tuple[float, float], Tuple[float, float, float]]:
        """
        Refines nozzle center and radius by jointly optimizing radial gradient consistency across
        360 degrees with robust trimmed-quantile scoring (discarding shadows/flares) and continuous
        sub-pixel bilinear sampling (< 0.05px resolution).
        """
        try:
            gx = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3)
            gy = cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3)
            angles = np.linspace(0, 2 * np.pi, 36, endpoint=False)
            cos_a = np.cos(angles).astype(np.float32)
            sin_a = np.sin(angles).astype(np.float32)
            h, w = gray.shape[:2]

            def eval_grid(bx: float, by: float, br: float, xy_shifts: np.ndarray, r_shifts: np.ndarray) -> Tuple[float, float, float]:
                best_score = -1.0
                best_res = (bx, by, br)

                dx_grid, dy_grid = np.meshgrid(xy_shifts, xy_shifts)
                x_cand = (bx + dx_grid).astype(np.float32)
                y_cand = (by + dy_grid).astype(np.float32)

                for dr in r_shifts:
                    r = br + dr
                    if r < 7.0 or r > 26.0:
                        continue
                    px = x_cand[:, :, None] + r * cos_a[None, None, :]
                    py = y_cand[:, :, None] + r * sin_a[None, None, :]
                    valid = (px >= 1) & (px < w - 2) & (py >= 1) & (py < h - 2)

                    samp_gx = self._sample_bilinear_vec(gx, px, py)
                    samp_gy = self._sample_bilinear_vec(gy, px, py)
                    proj = np.abs(samp_gx * cos_a[None, None, :] + samp_gy * sin_a[None, None, :])
                    proj = np.where(valid, proj, 0.0)

                    sorted_proj = np.sort(proj, axis=2)
                    k = int(36 * 0.40)
                    scores = np.mean(sorted_proj[:, :, k:], axis=2)
                    idx = np.unravel_index(np.argmax(scores), scores.shape)
                    if scores[idx] > best_score:
                        best_score = float(scores[idx])
                        best_res = (float(x_cand[idx]), float(y_cand[idx]), float(r))
                return best_res

            # Coarse pass: 0.5px steps across [-max_shift, +max_shift] and [-max_r_shift, +max_r_shift]
            c_x, c_y, c_r = eval_grid(
                cx, cy, radius,
                np.arange(-max_shift, max_shift + 0.5, 0.5, dtype=np.float32),
                np.arange(-max_r_shift, max_r_shift + 0.5, 0.5, dtype=np.float32) if max_r_shift > 0 else np.array([0.0], dtype=np.float32)
            )
            # Fine pass: 0.05px steps for XY, 0.10px for R around coarse peak
            f_x, f_y, f_r = eval_grid(
                c_x, c_y, c_r,
                np.arange(-0.45, 0.46, 0.05, dtype=np.float32),
                np.arange(-0.40, 0.45, 0.10, dtype=np.float32) if max_r_shift > 0 else np.array([0.0], dtype=np.float32)
            )
            if return_radius:
                return round(f_x, 3), round(f_y, 3), round(f_r, 2)
            return round(f_x, 3), round(f_y, 3)
        except Exception:
            if return_radius:
                return cx, cy, radius
            return cx, cy

    def _rank_candidate_circles(
        self,
        gray_roi: np.ndarray,
        candidates: np.ndarray,
        frame_w: int,
        frame_h: int,
        roi_x0: int,
        roi_y0: int,
        d0: Optional[float] = None
    ) -> Optional[np.ndarray]:
        """
        Ranks candidate circles returned by Hough transform using omnidirectional robust
        trimmed-quantile radial gradient projection combined with a soft optical center distance prior.
        """
        if candidates is None or len(candidates) == 0:
            return None

        if d0 is None:
            d0 = max(140.0, min(frame_w, frame_h) * 0.22)

        h, w = gray_roi.shape[:2]
        gx = cv2.Sobel(gray_roi, cv2.CV_32F, 1, 0, ksize=3)
        gy = cv2.Sobel(gray_roi, cv2.CV_32F, 0, 1, ksize=3)
        angles = np.linspace(0, 2 * np.pi, 36, endpoint=False)
        cos_a = np.cos(angles).astype(np.float32)
        sin_a = np.sin(angles).astype(np.float32)

        scored = []
        center_x, center_y = frame_w / 2.0, frame_h / 2.0

        for c in candidates:
            cx, cy, r = float(c[0]), float(c[1]), float(c[2])
            px = cx + r * cos_a
            py = cy + r * sin_a
            valid = (px >= 1) & (px < w - 1) & (py >= 1) & (py < h - 1)
            if np.sum(valid) < 20:
                continue
            ix = np.round(px[valid]).astype(int)
            iy = np.round(py[valid]).astype(int)
            proj = np.abs(gx[iy, ix] * cos_a[valid] + gy[iy, ix] * sin_a[valid])
            
            # Trimmed quantile mean: take top 60% of radial projections
            sorted_proj = np.sort(proj)
            k = int(len(sorted_proj) * 0.40)
            arc_score = float(np.mean(sorted_proj[k:]))

            # Distance weighting relative to optical center
            global_cx = roi_x0 + cx
            global_cy = roi_y0 + cy
            dist = math.hypot(global_cx - center_x, global_cy - center_y)
            w_dist = 1.0 / (1.0 + (dist / d0) ** 2)
            total_score = arc_score * w_dist
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
        r_roi = min(int(min(w, h) * 0.38), 260)

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
        all_candidates = []
        if circles is not None and len(circles) > 0:
            all_candidates.extend(circles[0])

        proc_gray = gray
        is_dim = (gray.mean() < 90 or gray.std() < 35)
        if is_dim:
            # Low-light & dim-illumination adaptive enhancement via CLAHE
            clahe = cv2.createCLAHE(clipLimit=2.5, tileGridSize=(8, 8))
            proc_gray = clahe.apply(gray)
            bilateral_enh = cv2.bilateralFilter(proc_gray, 9, 75, 75)
            enh_circles = cv2.HoughCircles(
                bilateral_enh,
                cv2.HOUGH_GRADIENT,
                dp=1,
                minDist=8,
                param1=50,
                param2=14,
                minRadius=7,
                maxRadius=25
            )
            if enh_circles is not None and len(enh_circles) > 0:
                all_candidates.extend(enh_circles[0])

        if not all_candidates:
            return None

        # Rank candidate circles by upper-arc radial gradient contrast and distance prior
        best = self._rank_candidate_circles(proc_gray, np.array(all_candidates), w, h, x0, y0)
        if best is None:
            return None

        # Refine candidate within ROI using radial gradient symmetry (using proc_gray for sharp gradients under dim light)
        refine_img = proc_gray if is_dim else gray
        refined_x, refined_y, refined_r = self._refine_upper_arc_symmetry(
            refine_img, float(best[0]), float(best[1]), float(best[2]), max_shift=3.5, max_r_shift=3.0, return_radius=True
        )
        global_x = float(x0 + refined_x)
        global_y = float(y0 + refined_y)
        return global_x, global_y, float(refined_r)

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
