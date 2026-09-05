"""
Affine and Non-Linear Transformation Solver for Tool-Klipper-Calibration.

Calculates millimeters-per-pixel (mpp) scale factors and solves a second-order polynomial
transformation matrix with visual-servoing damping to compensate for optical barrel distortion.
"""

from typing import List, Tuple, Optional
import logging
import numpy as np

logger = logging.getLogger("tool_calibrator.affine_transform")


class TransformationSolver:
    """
    Solves spatial coordinate mapping from camera pixel coordinates to 3D printer physical XY space.
    """

    def __init__(self, damping_factor: float = 0.55) -> None:
        self.damping_factor = damping_factor
        self.transform_matrix: Optional[np.ndarray] = None
        self.mpp: Optional[float] = None
        self.frame_center: Tuple[float, float] = (320.0, 240.0)

    def set_frame_center(self, cx: float, cy: float) -> None:
        """Sets the reference optical image center."""
        self.frame_center = (cx, cy)

    def calculate_average_mpp(self, calibration_samples: List[Tuple[float, float]]) -> float:
        """
        Computes robust average millimeters-per-pixel from a list of (distance_mm, distance_px).
        Filters out statistical outliers exceeding 20% deviation from the median.

        Args:
            calibration_samples: List of (traveled_mm, measured_pixels).

        Returns:
            float: Filtered average millimeters per pixel.
        """
        raw_mpp = []
        for dist_mm, dist_px in calibration_samples:
            if dist_px > 1.0:
                raw_mpp.append(dist_mm / dist_px)

        if not raw_mpp:
            raise ValueError("No valid calibration displacement samples provided.")

        median_mpp = float(np.median(raw_mpp))
        # Filter outliers with >20% deviation from median
        filtered = [val for val in raw_mpp if abs(val - median_mpp) <= (0.20 * median_mpp)]

        if not filtered:
            filtered = raw_mpp

        self.mpp = float(np.mean(filtered))
        logger.info(f"Calibrated MPP: {self.mpp:.5f} mm/pixel (from {len(filtered)}/{len(raw_mpp)} samples)")
        return self.mpp

    def set_mpp(self, mpp: float) -> None:
        """Sets the calibrated millimeters-per-pixel scale factor."""
        if mpp <= 0:
            raise ValueError(f"MPP must be positive, got {mpp}")
        self.mpp = float(mpp)
        logger.info(f"Updated MPP scale to: {self.mpp:.5f} mm/pixel")

    def normalize_coords(self, uv: Tuple[float, float]) -> Tuple[float, float]:
        """
        Normalizes pixel coordinates relative to the optical center in range [-1.0, 1.0].
        """
        cx, cy = self.frame_center
        nx = (uv[0] - cx) / cx if cx > 0 else 0.0
        ny = (uv[1] - cy) / cy if cy > 0 else 0.0
        return nx, ny

    def solve_matrix(self, calibration_points: List[Tuple[List[float], List[float]]]) -> bool:
        """
        Solves spatial mapping matrix between camera UV and machine XY.
        Supports 2nd-order polynomial fit for n >= 6, and 1st-order affine fit for 3 <= n < 6.

        Args:
            calibration_points: List of ([real_x, real_y], [pixel_u, pixel_v]) coordinates.

        Returns:
            bool: True if matrix was successfully solved.
        """
        n = len(calibration_points)
        if n < 3:
            raise ValueError(f"At least 3 calibration points required for transformation fit, got {n}.")

        real_coords = np.empty((n, 2))
        pixel_coords = np.empty((n, 2))

        for i, (real_pt, pixel_pt) in enumerate(calibration_points):
            real_coords[i] = real_pt
            # Normalize pixel coords
            nx, ny = self.normalize_coords((pixel_pt[0], pixel_pt[1]))
            pixel_coords[i] = [nx, ny]

        x, y = pixel_coords[:, 0], pixel_coords[:, 1]
        if n >= 6:
            # 2nd-order polynomial feature basis: [x^2, y^2, x*y, x, y, 1]
            A = np.vstack([x**2, y**2, x * y, x, y, np.ones(n)]).T
        else:
            # 1st-order affine feature basis: [x, y, 1] (ideal for 3 to 5 star-pattern points)
            A = np.vstack([x, y, np.ones(n)]).T

        # Solve least squares: A * M = real_coords
        solution, residuals, rank, s = np.linalg.lstsq(A, real_coords, rcond=None)
        self.transform_matrix = solution.T
        order_desc = "2nd-order polynomial" if n >= 6 else "1st-order affine"
        logger.info(f"Solved {order_desc} transform matrix (rank={rank}). Shape: {self.transform_matrix.shape}")
        return True

    def calculate_offset(self, detected_uv: Tuple[float, float]) -> Tuple[float, float]:
        """
        Computes physical XY correction move required to align the nozzle with optical center.

        Args:
            detected_uv: Current nozzle detection coordinates in pixels.

        Returns:
            Tuple[float, float]: (delta_x_mm, delta_y_mm) for printer toolhead move.
        """
        nx, ny = self.normalize_coords(detected_uv)

        if self.transform_matrix is not None:
            if self.transform_matrix.shape[1] == 6:
                # 2nd-order polynomial feature vector
                v = np.array([nx**2, ny**2, nx * ny, nx, ny, 1.0])
            else:
                # 1st-order affine feature vector
                v = np.array([nx, ny, 1.0])
            # Apply matrix and negative visual-servoing damping factor
            offset = -1.0 * (self.damping_factor * (self.transform_matrix @ v))
            return (round(float(offset[0]), 3), round(float(offset[1]), 3))

        # Fallback linear approximation using MPP if matrix not yet solved
        if self.mpp is not None:
            cx, cy = self.frame_center
            du = detected_uv[0] - cx
            dv = detected_uv[1] - cy
            offset_x = -1.0 * self.damping_factor * du * self.mpp
            offset_y = -1.0 * self.damping_factor * dv * self.mpp
            return (round(float(offset_x), 3), round(float(offset_y), 3))

        raise RuntimeError("Neither transformation matrix nor MPP scale factor has been calibrated.")
