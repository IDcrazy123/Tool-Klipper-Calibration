"""
Affine and Non-Linear Transformation Solver for Tool-Klipper-Calibration.

Calculates millimeters-per-pixel (mpp) scale factors and solves a second-order polynomial
transformation matrix with visual-servoing damping to compensate for optical barrel distortion.
"""

from typing import List, Tuple, Optional
import logging
import math
import numpy as np

logger = logging.getLogger("tool_calibrator.affine_transform")


class TransformationSolver:
    """
    Solves spatial coordinate mapping from camera pixel coordinates to 3D printer physical XY space.
    """

    def __init__(self, damping_factor: float = 0.55, default_mpp: float = 0.040) -> None:
        self.damping_factor = damping_factor
        self.default_mpp = default_mpp
        self.transform_matrix: Optional[np.ndarray] = None
        self.mpp: Optional[float] = None
        self.frame_center: Tuple[float, float] = (320.0, 240.0)

    def set_frame_center(self, cx: float, cy: float) -> None:
        """Sets the reference optical image center."""
        self.frame_center = (cx, cy)

    def calculate_average_mpp(self, calibration_samples: List[Tuple[float, float]]) -> float:
        """
        Calculates average mm-per-pixel from displacement samples:
        calibration_samples: List of (commanded_distance_mm, measured_displacement_px)
        """
        if not calibration_samples:
            raise ValueError("No calibration samples provided for MPP calculation")

        mpp_values = []
        for dist_mm, dist_px in calibration_samples:
            if dist_px <= 0:
                continue
            mpp_values.append(dist_mm / dist_px)

        if not mpp_values:
            raise ValueError("Invalid samples: measured pixel displacement must be greater than zero")

        if len(mpp_values) >= 3:
            med = float(np.median(mpp_values))
            filtered = [v for v in mpp_values if abs(v - med) / med <= 0.35]
            if filtered:
                mpp_values = filtered

        self.mpp = float(np.mean(mpp_values))
        logger.info(f"Calibrated average MPP: {self.mpp:.5f} mm/pixel from {len(mpp_values)} samples")
        return self.mpp

    def set_mpp(self, mpp: float) -> None:
        """Sets the calibrated millimeters-per-pixel scale factor."""
        try:
            val = float(mpp)
        except (ValueError, TypeError):
            raise ValueError(f"MPP must be a valid float, got {mpp}")
        if not math.isfinite(val) or val <= 0:
            raise ValueError(f"MPP must be a finite positive number, got {mpp}")
        self.mpp = val
        logger.info(f"Updated MPP scale to: {self.mpp:.6f} mm/pixel")

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
        required_rank = 6 if n >= 6 else 3
        if rank < required_rank:
            raise ValueError(f"Degenerate calibration points: rank {rank} < required {required_rank}")
        if s is not None and len(s) > 0 and s[-1] > 0:
            cond = float(s[0] / s[-1])
            if cond > 1e4:
                raise ValueError(f"Ill-conditioned calibration points (condition number: {cond:.1f})")

        self.transform_matrix = solution.T
        order_desc = "2nd-order polynomial" if n >= 6 else "1st-order affine"
        logger.info(f"Solved {order_desc} transform matrix (rank={rank}). Shape: {self.transform_matrix.shape}")
        return True

    def get_matrix(self) -> Optional[List[List[float]]]:
        """Returns the serialized transformation matrix as nested list."""
        if self.transform_matrix is None:
            return None
        return self.transform_matrix.tolist()

    def set_matrix(self, matrix_data: List[List[float]]) -> None:
        """Loads a pre-computed transformation matrix from serialized list."""
        try:
            arr = np.array(matrix_data, dtype=np.float64)
        except Exception as e:
            raise ValueError(f"Invalid matrix data: {e}")
        if arr.ndim != 2 or arr.shape[0] != 2 or arr.shape[1] not in (3, 6):
            raise ValueError(f"Invalid transform matrix shape {arr.shape}, expected (2, 3) or (2, 6)")
        if not np.all(np.isfinite(arr)):
            raise ValueError("Transform matrix contains non-finite values (NaN or Inf)")
        self.transform_matrix = arr
        logger.info(f"Loaded transform matrix with shape: {self.transform_matrix.shape}")

    def calculate_offset_detail(
        self, detected_uv: Tuple[float, float]
    ) -> Tuple[Tuple[float, float], Tuple[float, float]]:
        """
        Computes both the damped visual-servoing step and raw physical error.
        Returns:
            ((damped_x, damped_y), (raw_error_x, raw_error_y))
        """
        u, v = float(detected_uv[0]), float(detected_uv[1])
        cx, cy = self.frame_center
        frame_w = cx * 2.0
        frame_h = cy * 2.0
        if frame_w > 0 and frame_h > 0:
            if u < 0 or u > frame_w or v < 0 or v > frame_h:
                raise ValueError(
                    f"[ERR_CV_204] Detected nozzle center ({u:.1f}, {v:.1f}) exceeds camera frame boundaries [0..{frame_w:.0f}, 0..{frame_h:.0f}]"
                )

        nx, ny = self.normalize_coords((u, v))

        if self.transform_matrix is not None:
            if self.transform_matrix.shape[1] == 6:
                v = np.array([nx**2, ny**2, nx * ny, nx, ny, 1.0])
            else:
                v = np.array([nx, ny, 1.0])
            real_displacement = self.transform_matrix @ v
            raw_x = -1.0 * float(real_displacement[0])
            raw_y = -1.0 * float(real_displacement[1])
            damped_x = self.damping_factor * raw_x
            damped_y = self.damping_factor * raw_y
            if not (math.isfinite(raw_x) and math.isfinite(raw_y)):
                raise RuntimeError("Calculated offset resulted in non-finite values")
            return (
                (round(damped_x, 4), round(damped_y, 4)),
                (round(raw_x, 4), round(raw_y, 4))
            )

        if self.mpp is not None:
            cx, cy = self.frame_center
            du = detected_uv[0] - cx
            dv = detected_uv[1] - cy
            raw_x = -1.0 * du * self.mpp
            raw_y = -1.0 * dv * self.mpp
            damped_x = self.damping_factor * raw_x
            damped_y = self.damping_factor * raw_y
            if not (math.isfinite(raw_x) and math.isfinite(raw_y)):
                raise RuntimeError("Calculated offset resulted in non-finite values")
            return (
                (round(damped_x, 4), round(damped_y, 4)),
                (round(raw_x, 4), round(raw_y, 4))
            )

        # Fallback to default_mpp for coarse visual servoing if camera scale has not been calibrated yet
        mpp = self.default_mpp
        cx, cy = self.frame_center
        du = detected_uv[0] - cx
        dv = detected_uv[1] - cy
        raw_x = -1.0 * du * mpp
        raw_y = -1.0 * dv * mpp
        damped_x = self.damping_factor * raw_x
        damped_y = self.damping_factor * raw_y
        if not (math.isfinite(raw_x) and math.isfinite(raw_y)):
            raise RuntimeError("Calculated offset resulted in non-finite values")
        logger.warning(
            f"Using uncalibrated fallback default MPP ({mpp:.4f} mm/px) for offset calculation. "
            f"Run CALIBRATE_CAMERA_SCALE to calibrate high-precision scale and matrix."
        )
        return (
            (round(damped_x, 4), round(damped_y, 4)),
            (round(raw_x, 4), round(raw_y, 4))
        )

    def calculate_offset(self, detected_uv: Tuple[float, float]) -> Tuple[float, float]:
        """
        Computes physical XY correction move required to align the nozzle with optical center.
        """
        return self.calculate_offset_detail(detected_uv)[0]

    def calculate_tool_delta(
        self, reference_uv: Tuple[float, float], target_uv: Tuple[float, float]
    ) -> Tuple[float, float]:
        """
        Calculates the physical machine XY offset of a target toolhead relative to a reference toolhead.
        Klipper requires opposite sign of physical nozzle carriage displacement so carriage shifts appropriately:
            gcode_offset = -1.0 * (physical_target - physical_ref)

        Args:
            reference_uv: Detected center of reference tool (e.g. T0) in pixels (u, v).
            target_uv: Detected center of target tool (e.g. T1) in pixels (u, v).

        Returns:
            Tuple[float, float]: (delta_x_mm, delta_y_mm) physical offset for Klipper gcode_offset.
        """
        nx_ref, ny_ref = self.normalize_coords(reference_uv)
        nx_tgt, ny_tgt = self.normalize_coords(target_uv)

        if self.transform_matrix is not None:
            if self.transform_matrix.shape[1] == 6:
                v_ref = np.array([nx_ref**2, ny_ref**2, nx_ref * ny_ref, nx_ref, ny_ref, 1.0])
                v_tgt = np.array([nx_tgt**2, ny_tgt**2, nx_tgt * ny_tgt, nx_tgt, ny_tgt, 1.0])
            else:
                v_ref = np.array([nx_ref, ny_ref, 1.0])
                v_tgt = np.array([nx_tgt, ny_tgt, 1.0])

            real_ref = self.transform_matrix @ v_ref
            real_tgt = self.transform_matrix @ v_tgt
            delta_xy = -1.0 * (real_tgt - real_ref)
            delta_x, delta_y = float(delta_xy[0]), float(delta_xy[1])
            if not (math.isfinite(delta_x) and math.isfinite(delta_y)):
                raise RuntimeError("Calculated tool delta resulted in non-finite values")
            return (round(delta_x, 4), round(delta_y, 4))

        if self.mpp is not None:
            du = target_uv[0] - reference_uv[0]
            dv = target_uv[1] - reference_uv[1]
            delta_x = -1.0 * du * self.mpp
            delta_y = -1.0 * dv * self.mpp
            if not (math.isfinite(delta_x) and math.isfinite(delta_y)):
                raise RuntimeError("Calculated tool delta resulted in non-finite values")
            return (round(delta_x, 4), round(delta_y, 4))

        # Fallback to default_mpp for tool delta if uncalibrated
        mpp = self.default_mpp
        du = target_uv[0] - reference_uv[0]
        dv = target_uv[1] - reference_uv[1]
        delta_x = -1.0 * du * mpp
        delta_y = -1.0 * dv * mpp
        if not (math.isfinite(delta_x) and math.isfinite(delta_y)):
            raise RuntimeError("Calculated tool delta resulted in non-finite values")
        logger.warning(
            f"Using uncalibrated fallback default MPP ({mpp:.4f} mm/px) for tool delta calculation."
        )
        return (round(delta_x, 4), round(delta_y, 4))

