# CHANGELOG.md — Project Version Update History

All notable changes to the **Tool-Klipper-Calibration** project are documented in this file, adhering to the [Semantic Versioning (SemVer)](https://semver.org/) format.

---

## [Unreleased]
### Planned
- Complete live unattended multi-tool offset application with physical print validation.

## [0.8.19] - 2026-09-06
### Fixed
- **[P1] Daemon Typing Imports & Dirty Status Telemetry**:
  - Added `Tuple, Optional, List` to typing imports in `server/tool_calibrator_server.py`, resolving `NameError: name 'Tuple' is not defined` when starting the daemon on Python 3.9 host platforms.
  - Enhanced `_get_git_commit()` to detect dirty working trees via `git status --porcelain` and append `-dirty`.
- **[P1] Out-of-Band Abort Control (Moonraker Mutex Bypass)**:
  - Registered Klipper webhook endpoints `tool_calibrator/abort` and `tool_calibrator/status` via `webhooks.register_endpoint`.
  - Added POST `/abort_calibration` endpoint on the vision server (port 8090).
  - Enables instant cancellation without waiting for Moonraker/Klipper's synchronous G-code mutex.
  - Integrated `_check_cancellation()` directly inside the `_center_nozzle` servoing loop and multi-frame burst sampling.
- **[P1] Physical Station Carriage XY Compensation Sign Correction**:
  - Fixed coordinate sign inversion in `safe_navigator.py::approach_switch` and `tool_calibrator.py::_execute_z_calibration` (Cartographer probe path).
  - Secondary tool nozzles now properly displace the machine carriage by $\vec{P}_{\text{carriage}} = \vec{P}_{\text{station}} + \vec{\Delta}_{\text{tool}}$, eliminating the 1.730 mm positioning discrepancy.
- **[P2] Deterministic Session Lock Lifecycle & Motion Preflight Guard**:
  - In `cmd_CALIBRATE_CAMERA_SCALE`, lock acquisition failures immediately abort before any physical motion is commanded.
  - In `cmd_CALIBRATE_TOOL_OFFSETS`, unified session acquisition, health verification, and tool changes under a single `try...finally` block with unique run tokens (`klipper_<timestamp>_<pid>`).
  - Added client-requested `timeout_seconds` lease support in the daemon.
- **[P2] Separation of Camera Scale Fit and Station Validation Outcomes**:
  - If post-fit optical centering fails in `cmd_CALIBRATE_CAMERA_SCALE`, the system reports partial completion (`WARNING: Scale fitted, but centering failed`), does not overwrite camera station target coordinates, and preserves previously validated waypoints.
- **[P2] Calibrated Physical Burst Spread Limit**:
  - Removed arbitrary `max(6.0, ...)` pixel floor that permitted 5.0px (0.115mm) noise to pass undetected at 0.023 mm/px.
  - Configured `self.physical_spread_limit_mm = 0.08` with minimal quantization floor (2.5px), rejecting 5.0px dispersion bursts lacking majority consensus.
- **[P2] Telemetry Disambiguation & Live Elapsed Duration**:
  - Expanded `run_record` with `phase` (`IDLE`, `INITIALIZING`, `CHANGING_TOOL`, `CALIBRATING_TOOL`, `CENTERING_NOZZLE`, `PROBING_Z`, `RESTORING_REFERENCE`, `COMPLETED`, `CANCELLED`, `FAILED`).
  - Separated `calibrating_tool` from `physical_tool`, properly reflecting physical tool restoration to T0 upon cycle completion.
  - Added real-time `elapsed_sec` calculation in `get_status()`.
- **G-Code Output Backpressure Mitigation**:
  - Streamlined routine centering step console responses to single-line format (`Err X... Y... | Move X... Y...`), preventing G-code file descriptor buffer overflow (`BlockingIOError: [Errno 11] Resource temporarily unavailable`).

## [0.8.18] - 2026-09-06
### Fixed
- **[Critical Safety] Elimination of Uncalibrated Fallback Blind Motion**:
  - Removed assumption-based fallback motion in `server/affine_transform.py`. Physical trial proved machine axes can run completely opposite to default assumptions ($du/dX = -44.0$ px/mm, $dv/dY = -42.95$ px/mm).
  - Calling `/calculate_offset` or `/calculate_tool_delta` without a solved transformation matrix now returns `ERR_CV_203` (HTTP 400), enforcing calibration via `CALIBRATE_CAMERA_SCALE` before physical toolhead motion.
  - Removed premature `_center_nozzle()` from `cmd_CALIBRATE_CAMERA_SCALE` prior to star-pattern displacements; added post-calibration centering with the newly solved matrix.
- **[Accuracy & Geometry] Optical Center Targeting Invariance**:
  - In `calculate_offset_detail`, visual servoing corrections now compute machine displacement directly to the camera optical center using $\vec{\Delta}_{\text{to\_center}} = \mathbf{M}\vec{v}(0, 0) - \mathbf{M}\vec{v}(nx, ny) = -\mathbf{J} \begin{bmatrix}nx \\ ny\end{bmatrix}$.
  - Completely cancels out the affine translation intercept matrix column $\mathbf{M}_{[:, 2]}$ (the approach baseline coordinates), making visual centering purely dependent on deviation from the lens optical axis.
- **[Convergence & Reliability] Dynamic Centering Iteration Budgeting**:
  - Addressed T2 centering abort at Step 5 (`ERR_CV_202`) where 0.55 damping on $E_0 = 0.865$ mm left $0.865 \times (0.45)^5 = 0.01596\text{ mm} > 0.015\text{ mm}$.
  - Implemented dynamic budget allocation on Step 1: $N_{\text{needed}} = \lceil \frac{\ln(\text{tol}/E_0)}{\ln(1 - 0.55)} \rceil + 2$ (bounded between default 8 and 15 steps), guaranteeing convergence without relaxing precision tolerances.
  - Increased default `max_centering_iterations` from 5 to 8 steps (config upper bound raised to 20).
  - Added final verification step after loop exhaustion before declaring failure.
- **[Noise & Dispersion] Calibrated Physical Spread Threshold & Minimum Valid Frames**:
  - Upgraded `_sample_burst()` in `klippy/extras/tool_calibrator.py` to enforce minimum valid frame count $N_{\text{min}} = \max(2, (N_{\text{total}} // 2) + 1)$ (or 1 for single-shot).
  - Replaced arbitrary pixel spread with a calibrated physical spread limit $\max(6.0\text{ px}, 0.08\text{ mm} / \text{MPP})$, rejecting transient mechanical vibration or lighting flares.
- **[Telemetry & Safety] Explicit Run Record State Machine & Tool State Guard**:
  - Introduced `run_record` tracking `run_id`, `state` (`IDLE`, `RUNNING`, `SUCCESS`, `FAILED`), `active_tool`, `start_time`, `end_time`, `duration_sec`, and `valid`.
  - Aborted/failed runs immediately set `valid = False` and preserve previous cached offsets marked as "Stale (Prior Completed Run)" rather than presenting incomplete cycle data as current results.
  - On failure, outputs warning indicating the exact active tool (e.g. T2) so operators can inspect physical dock state before issuing tool commands.
- **[Control & Usability] Calibration Abort & Dry Run Clarity**:
  - Registered `CALIBRATION_ABORT` and `ABORT_CALIBRATION` commands to cleanly halt multi-tool calibration at the next safe tool transition waypoint without crashing Klipper.
  - Added descriptive Dry-Run banner detailing that optical centering is performed while physical Z touch, disk writes, and runtime offset applications are skipped.
- **[Diagnostics] Git Commit Hash in Health Telemetry**:
  - Exposed current short git commit hash in `/health` API and `TOOL_CALIBRATOR_STATUS` console output to distinguish daemon releases across in-place restarts.

## [0.8.17] - 2026-09-06
### Fixed
- **[P1] Elimination of Duplicate G-Code Macros**:
  - Removed duplicate G-code macro definitions (`AUTO_TEACH_CAMERA`, `AUTO_TEACH_SWITCH`, `CENTER_NOZZLE`, `TEST_NOZZLE_VISION`) from `macros/safe_staging_macros.cfg`, establishing single-point ownership in `klippy/extras/tool_calibrator.py` and resolving Klipper startup crash (`already registered`).
- **[P1] Dynamic Runtime Application of Tool Offsets**:
  - Updated `klippy/extras/tool_offsets.py` and `klippy/extras/tool_calibrator.py` to listen for `klippy:ready` and apply calibrated offsets to toolchanger runtime (`tool.py` / `SET_TOOL_OFFSET`). Added `cmd_APPLY_TOOL_OFFSETS` command for manual runtime re-application.
- **[P1] Carriage XY Compensation During Z Probing**:
  - In `tool_calibrator.py` and `safe_navigator.py`, secondary tools approaching the physical Z switch or touch sensor now offset the machine carriage by `(ref_x - tool_x, ref_y - tool_y)`, ensuring all nozzles contact the exact physical switch pin center.
- **[P1] Active Tool Dynamic Thermal Guarding**:
  - Replaced hardcoded `reference_tool` checks in `tool_calibrator.py` with dynamic active tool resolution (`_get_active_tool_no()`), verifying the active tool's extruder temperature. Extended thermal checks to `CALIBRATION_NAVIGATE (STATION=CAMERA)`, `CALIBRATION_TEACH_STATION`, and `CALIBRATE_CAMERA_SCALE`.
- **[P1] Switch Backend Pin Config Parameter Normalization**:
  - Implemented `PinAdapterConfig` proxy in `klippy/extras/z_backends/switch_backend.py` to map `switch_pin` to `pin` for upstream `tools_calibrate.py` wrapper, raising an immediate `config.error` instead of silently swallowing exceptions.
- **[P2] Tool Auto-Discovery Hardening**:
  - Prioritized toolchanger configured tool lists in `_get_known_printer_tools()`, preventing phantom tool creation from extruder indices (e.g. T5 using extruder1 no longer discovers non-existent T1).
- **[P2] Strict Majority Consensus Burst Filtering**:
  - Upgraded `_sample_burst()` in `tool_calibrator.py` to require strict majority consensus (`> total // 2` and `>= 2`) when sample dispersion exceeds 15px, calculating median coordinates strictly within the inlier cluster.
- **[P2] Session Lock Ownership & Mutating Endpoint Protection**:
  - Enforced session token verification in `server/tool_calibrator_server.py` across `/set_camera*`, `/set_mpp`, `/calibrate_mpp`, `/solve_matrix`, and `/set_matrix`. Disallowed empty session tokens in `/release_lock` and separated API tokens (`X-API-Token`) from session tokens (`X-Session-Token`).
- **[P2] Vision Health Matrix Sync Consistency**:
  - Added `has_matrix` field to `/health` endpoint matching `matrix_solved`, preventing Klipper client from overwriting active calibration with stale files.
- **[P2] Frame Cache Invalidation on Stream Error**:
  - In `server/stream_grabber.py`, explicitly cleared `self._cached_frame = None` across all error branches and HTTP 503/404 responses.
- **[P2] Atomic Configuration Persistence & Single Backup**:
  - Refactored `klippy/extras/config_manager.py` to build the entire configuration file update in memory and write atomically with a single timestamped backup per session.
- **[P2] Robust Pre-Calibration Exception Cleanup**:
  - Enclosed `start_gcode` and initial `move_to_safe_z` in `tool_calibrator.py` within `try...finally` block to ensure session locks are released and state is reset on early failure.
- **[P2] Script Path Interpolation & Custom Paths**:
  - In `scripts/install.sh`, unquoted heredoc `<< EOF` for Moonraker configuration to interpolate actual repository and venv paths. In `scripts/uninstall.sh`, added support for `$KLIPPER_DIR` environment variable and interactive prompt fallback.
- **Dynamic Vision Confidence & False Positive Rejection**:
  - Implemented dynamic confidence in `server/nozzle_detector.py` measuring inner orifice vs. outer rim contrast and radial gradient continuity, while filtering noise and blank frames using spatial Laplacian variance.

## [0.8.16] - 2026-09-06
### Fixed
- **Auto-Teach & Staging Alias Registration**:
  - Registered G-code convenience commands directly in Python in [klippy/extras/tool_calibrator.py](file:///d:/Desktop/Tool-Klipper-Calibration/klippy/extras/tool_calibrator.py): `AUTO_TEACH_CAMERA`, `AUTO_TEACH_SWITCH`, `CENTER_NOZZLE`, `TEST_NOZZLE_VISION`. Eliminates `Unknown command:"AUTO_TEACH_CAMERA"` error when auxiliary macro files are not explicitly included.
- **Uncalibrated Centering Fallback ("Chicken-and-Egg" Bug Fix)**:
  - Added `default_mpp = 0.040` (mm/pixel) in `TransformationSolver` in [server/affine_transform.py](file:///d:/Desktop/Tool-Klipper-Calibration/server/affine_transform.py). Allows coarse visual servoing centering during `AUTO_TEACH_CAMERA` and `CENTER_NOZZLE` on fresh machines before `CALIBRATE_CAMERA_SCALE` is executed, eliminating HTTP 400 Bad Request aborts.
- **Resolved Section Header Collision (`[tool T*]` -> `[tool_offsets]`)**:
  - Transferred tool offset persistence in [klippy/extras/config_manager.py](file:///d:/Desktop/Tool-Klipper-Calibration/klippy/extras/config_manager.py) to unified `[tool_offsets]` section with variables `t{n}_x`, `t{n}_y`, `t{n}_z`, preventing Klipper boot crash caused by duplicate `[tool T*]` headers from tool definition files.
  - Implemented [klippy/extras/tool_offsets.py](file:///d:/Desktop/Tool-Klipper-Calibration/klippy/extras/tool_offsets.py) to register and validate `[tool_offsets]` in Klipper.
  - Updated [scripts/install.sh](file:///d:/Desktop/Tool-Klipper-Calibration/scripts/install.sh) and [scripts/uninstall.sh](file:///d:/Desktop/Tool-Klipper-Calibration/scripts/uninstall.sh) to link `tool_offsets.py`.
- **Command Name Collision Prevention (`CALIBRATION_STATUS`)**:
  - Renamed status command to `TOOL_CALIBRATOR_STATUS` with short alias `TKC_STATUS`. Legacy `CALIBRATION_STATUS` is registered conditionally only if not pre-registered by other macros/probes, preventing startup crash.
- **Crowsnest WebRTC / camera-streamer Endpoint Compatibility**:
  - Preserved image URLs ending with `.jpg`/`.jpeg` in [server/stream_grabber.py](file:///d:/Desktop/Tool-Klipper-Calibration/server/stream_grabber.py).
  - Added automatic 404 fallback probe to `/snapshot.jpg` with auto-switching when Crowsnest v4 WebRTC camera-streamer is detected.
- **Daemon Direct Script Execution**:
  - Added `try...except (ImportError, ValueError)` import wrappers in [server/tool_calibrator_server.py](file:///d:/Desktop/Tool-Klipper-Calibration/server/tool_calibrator_server.py), allowing direct invocation by systemd unit or CLI without package context errors.


## [0.8.15] - 2026-09-06
### Fixed
- **Switch Backend Probe Safety & Kinematics**:
  - Replaced unsupported `config.has_section()` call in [klippy/extras/z_backends/switch_backend.py](file:///d:/Desktop/Tool-Klipper-Calibration/klippy/extras/z_backends/switch_backend.py) with safe `printer.lookup_object("tools_calibrate", None)` lookup.
  - Eliminated redundant `toolhead.set_position(start_pos)` after probing retract moves, preventing kinematic stepper coordinate desynchronization and bed mesh corruptions.
- **Config Manager Targeted Rollback**:
  - Upgraded `rollback()` in [klippy/extras/config_manager.py](file:///d:/Desktop/Tool-Klipper-Calibration/klippy/extras/config_manager.py) to support restoring specific timestamped backup files (`target_backup`) by full path or filename, complete with cross-validation.
  - Wired `BACKUP` parameter in `CALIBRATION_ROLLBACK_OFFSETS` macro handler in [klippy/extras/tool_calibrator.py](file:///d:/Desktop/Tool-Klipper-Calibration/klippy/extras/tool_calibrator.py).
- **Vision Service Session Lock & Concurrency**:
  - Standardized session lock response and request keys (`session_id` and `session_token`) across `/acquire_lock` and `/release_lock` in [server/tool_calibrator_server.py](file:///d:/Desktop/Tool-Klipper-Calibration/server/tool_calibrator_server.py) and [klippy/extras/tool_calibrator.py](file:///d:/Desktop/Tool-Klipper-Calibration/klippy/extras/tool_calibrator.py), resolving HTTP 403 lock starvation.
- **Sub-pixel Out-of-Bound Frame Safety (`ERR_CV_204`)**:
  - Added coordinate boundary checks in `calculate_offset_detail()` in [server/affine_transform.py](file:///d:/Desktop/Tool-Klipper-Calibration/server/affine_transform.py). Coordinates landing outside image bounds cleanly return `ERR_CV_204` instead of causing unhandled runtime errors.
  - Handled `ERR_CV_204` in `/calculate_offset` endpoint in [server/tool_calibrator_server.py](file:///d:/Desktop/Tool-Klipper-Calibration/server/tool_calibrator_server.py).
- **Adaptive Circle Search Bounds for Nozzle Detection**:
  - Replaced fixed radius constraints `[7.0, 26.0]` in `_refine_upper_arc_symmetry` in [server/nozzle_detector.py](file:///d:/Desktop/Tool-Klipper-Calibration/server/nozzle_detector.py) with adaptive bounds `[max(3.0, base_radius - 6.0), base_radius + 6.0]`, supporting larger macro nozzles (0.8mm-1.2mm) and fine nozzles without clipping.
- **Stream Grabber In-Memory Caching & Performance**:
  - Implemented 80ms in-memory cache buffer with thread locks in [server/stream_grabber.py](file:///d:/Desktop/Tool-Klipper-Calibration/server/stream_grabber.py) to eliminate duplicate network calls and JPEG decode thrashing during visual servoing burst captures.
- **Thermal Safety & Safe Altitude Enforcement**:
  - Enforced vertical lift to `Safe_Z` prior to any initial toolchange in `cmd_CALIBRATE_TOOL_OFFSETS` in [klippy/extras/tool_calibrator.py](file:///d:/Desktop/Tool-Klipper-Calibration/klippy/extras/tool_calibrator.py), complying with Safety Rules 1 & 2 ("Lift-First, Descend-Last").
  - Added configurable `max_camera_temp` (default 100°C) with pre-flight heater checks across all optical calibration routines (`CALIBRATE_TOOL_OFFSETS`, `CALIBRATE_TOOL_XY`, `CALIBRATE_ALL_TOOLS`), preventing thermal damage to camera sensors (`ERR_PRE_002`).
- **Web UI Stream Auto-Recovery**:
  - Upgraded [server/templates/index.html](file:///d:/Desktop/Tool-Klipper-Calibration/server/templates/index.html) with auto-reconnecting MJPEG stream handler upon timeout/disconnect.


## [0.8.14] - 2026-09-06
### Fixed
- **Camera Sync Endpoint Compatibility**: Added `@app.route("/set_camera_url", methods=["POST"])` endpoint alias in [server/tool_calibrator_server.py](file:///d:/Desktop/Tool-Klipper-Calibration/server/tool_calibrator_server.py) to resolve 404 error during pre-flight camera URL synchronization from Klipper.
- **Stream URL Auto-Conversion**: Upgraded `_normalize_url` in [server/stream_grabber.py](file:///d:/Desktop/Tool-Klipper-Calibration/server/stream_grabber.py) to automatically convert continuous video stream URLs (`action=stream` and `/stream`) into static snapshot endpoints (`action=snapshot` and `/snapshot`), preventing HTTP request hangs.
- **Station Matrix Telemetry**: Added `matrix_tx` and `matrix_ty` config parsing and exported the full affine matrix in `get_status()` of [klippy/extras/tool_calibrator_station.py](file:///d:/Desktop/Tool-Klipper-Calibration/klippy/extras/tool_calibrator_station.py).
- **Deployment Scripts**: Added `__init__.py` linking in [scripts/install.sh](file:///d:/Desktop/Tool-Klipper-Calibration/scripts/install.sh) and included `tool_calibrator_station.py` in [scripts/uninstall.sh](file:///d:/Desktop/Tool-Klipper-Calibration/scripts/uninstall.sh).

### Added
- **Randomized 88-Image Camera Algorithm Benchmark**:
  - Added [scripts/test_camera_shuffled_88.py](file:///d:/Desktop/Tool-Klipper-Calibration/scripts/test_camera_shuffled_88.py) evaluating 75 sweep frames from `Picture Screenshot` and 13 real camera frames from `tests/sample_images`.
  - 100% detection rate (88/88) and 100% order invariance verification (0.000000px discrepancy).
  - Generated full-frame annotations with 4X Inset PiP and 4X zoomed crops in `test_annotated_results/`.
- Expanded test suite to **68/68 unit tests** (100% pass rate).

## [0.8.13] - 2026-09-06
### Added
- **Decoupled XY and Z Calibration for Non-Homogeneous Hardware:**
  - Decoupled `tool_offsets` dictionary in [klippy/extras/tool_calibrator.py](file:///d:/Desktop/Tool-Klipper-Calibration/klippy/extras/tool_calibrator.py) so that unmeasured axes are never initialized or overwritten with zero (`0.000`).
  - Added dedicated XY-only and Z-only macros in [macros/tool_calibrator_macros.cfg](file:///d:/Desktop/Tool-Klipper-Calibration/macros/tool_calibrator_macros.cfg):
    - `CALIBRATE_TOOLS_XY`: Runs optical XY calibration on all tools (or filtered `TOOLS`), preserving existing Z offsets 100%.
    - `CALIBRATE_TOOLS_Z`: Runs Z probing calibration on all tools (or filtered `TOOLS`), preserving existing XY offsets 100%.
    - `CALIBRATE_TOOL_XY`: Runs optical XY calibration on a specific single tool (`TOOL=x`).
    - `CALIBRATE_TOOL_Z`: Runs Z probing calibration on a specific single tool (`TOOL=x`).
  - Conditioned pre-flight Vision Service ping check on `calibrate_xy`, allowing users without a camera or vision service running to perform Z-only probe calibration seamlessly.
  - Enhanced [klippy/extras/config_manager.py](file:///d:/Desktop/Tool-Klipper-Calibration/klippy/extras/config_manager.py) to match existing configuration keys robustly across colon (`:`) and equals (`=`) delimiters with arbitrary whitespace.
- **Unit Tests & Documentation:**
  - Added unit tests `test_calibrate_xy_only_preserves_existing_z` and `test_calibrate_z_only_preserves_existing_xy` in [tests/test_calibration_cycle.py](file:///d:/Desktop/Tool-Klipper-Calibration/tests/test_calibration_cycle.py).
  - Updated [docs/QUY_TRINH_VAN_HANH.md](file:///d:/Desktop/Tool-Klipper-Calibration/docs/QUY_TRINH_VAN_HANH.md) with separate workflows for XY-only, Z-only, and joint calibrations.
  - Test suite passing at **55/55 tests** (100%).
- **Interactive Navigation & Inspection Commands in Klipper Extension:**
  - Added `CALIBRATION_NAVIGATE STATION=CAMERA|SWITCH|DEPART` for safe 3-tier transit between optical station, probe station, and safe altitude.
  - Added `CALIBRATION_CENTER_NOZZLE` for on-demand visual servoing centering on the active toolhead.
  - Added `CALIBRATION_TEST_VISION` to generate instant live inspection reports (UV center, radius, confidence, dispersion, algorithm tier) without moving the toolhead.
- **Enhanced User Macro Suite (`macros/`):**
  - Upgraded `GOTO_CAMERA_TARGET` to execute physical safe 3-tier waypoint approach to the camera station.
  - Added `GOTO_SWITCH_TARGET` and `LEAVE_CALIBRATION_STATION` for complete interactive staging control.
  - Added `CENTER_NOZZLE` and `TEST_NOZZLE_VISION` user macros for quick diagnostic checks.
  - Upgraded `CALIBRATE_ALL_TOOLS` and `CALIBRATE_TOOL` with all tuning parameters (`SAMPLES`, `WIGGLE`, `ORDER`, `COMPENSATE_FOCAL_Z`, `CLEAN_NOZZLE`, `DRY_RUN`).
- **Comprehensive 6-Step Operational Workflow Guide:**
  - Authored [docs/QUY_TRINH_VAN_HANH.md](file:///d:/Desktop/Tool-Klipper-Calibration/docs/QUY_TRINH_VAN_HANH.md): Complete Standard Operating Procedure (SOP) from hardware setup, 1-click auto-teaching, vision testing, camera scale calibration, dry-run, to full multi-tool calibration.
  - Linked guide into root [README.vi.md](file:///d:/Desktop/Tool-Klipper-Calibration/README.vi.md) and [README.md](file:///d:/Desktop/Tool-Klipper-Calibration/README.md).
- **Unit & Integration Test Suite:**
  - Added 3 unit tests in [tests/test_calibration_cycle.py](file:///d:/Desktop/Tool-Klipper-Calibration/tests/test_calibration_cycle.py) verifying navigation, active tool centering, and test vision report outputs.
  - Total test suite expanded to **53 passing tests** with 100% success rate.

## [0.8.11] - 2026-09-06
### Added
- **Multi-frame Burst Sampling in Visual Servoing Loop:**
  - Implemented `_sample_burst()` in [klippy/extras/tool_calibrator.py](file:///d:/Desktop/Tool-Klipper-Calibration/klippy/extras/tool_calibrator.py).
  - Enforces physical settling pause `max(0.12s, sample_delay)` before initial capture to allow mechanical ringing and camera MJPEG buffers to clear.
  - Collects $N$ settled frames (default `centering_samples = 3`, configurable 1–7) separated by `sample_delay` (default $0.08\text{s}$).
  - Uses median filtering across $(U, V, R)$ to eliminate frame outliers caused by mechanical vibrations, streamer buffering latency, or single-frame exposure spikes.
  - Computes and logs burst consensus telemetry: `burst_count`, `burst_total`, and sub-pixel `spread_px` (with high-dispersion diagnostic alert if `spread > 15px`).
  - Integrated `_sample_burst` into both nozzle centering and star-pattern camera scale calibration (`CALIBRATE_CAMERA_SCALE`).
- **Adaptive Wiggle Recovery Routine:**
  - Implemented `_recover_with_wiggle()` in [klippy/extras/tool_calibrator.py](file:///d:/Desktop/Tool-Klipper-Calibration/klippy/extras/tool_calibrator.py).
  - When the nozzle is temporarily lost during servoing (e.g. blind spots or specular glare), automatically executes 4 micro-moves around the anchor position ($\pm \text{wiggle\_distance}$, default $0.1\text{mm}$) to break the reflection angle before aborting.
  - Added configurable `wiggle_distance` parameter in `[tool_calibrator]` configuration.
  - Validates all micro-moves against printer frame boundaries via `SafeNavigator.validate_coordinate_safety`.
  - Automatically recovers optical lock or safely reverts toolhead to anchor coordinates if all attempts are exhausted.
  - Adds G-Code command parameters `SAMPLES` and `WIGGLE` to `CALIBRATE_TOOL_OFFSETS` for runtime overrides.
- **Unit & Integration Test Suite:**
  - Added 5 new unit tests in [tests/test_calibration_cycle.py](file:///d:/Desktop/Tool-Klipper-Calibration/tests/test_calibration_cycle.py) verifying median calculation, outlier filtering, partial burst drops, successful adaptive wiggle recovery, anchor position reset on exhaustion, and wiggle disable flags.
  - Test suite expanded to **50 passing tests** with 100% success rate.

## [0.8.10] - 2026-09-05
### Fixed
- Joint 3D $(x, y, r)$ Continuous Radial Symmetry Optimization (Images 16, 21, 22, 26, 27, 29, 32, 37, 40, 44):
  - **Root Cause Identified:** Previously, `_refine_upper_arc_symmetry()` kept the radius strictly fixed to the discrete integer Hough radius (`radius = float(best[2])`), optimizing only $(x, y)$ in 2D. In cases where Hough detected an outer chamfer ($R=24.4\text{px}$) instead of the true orifice ($R=21.8\text{px}$), or where glare expanded/contracted the circle by $1-2\text{px}$, forcing a circle of fixed incorrect radius caused $(x, y)$ to slide off-center towards an asymmetric edge/flare to fit the larger circumference.
  - **Algorithmic Solution:**
    - Upgraded `_refine_upper_arc_symmetry()` in [server/nozzle_detector.py](file:///d:/Desktop/Tool-Klipper-Calibration/server/nozzle_detector.py) to perform a **joint 3D continuous parameter search over $(cx, cy, r)$**.
    - Coarse pass: evaluates $dx \in [-3.5, +3.5\text{px}]$, $dy \in [-3.5, +3.5\text{px}]$, $dr \in [-3.0, +3.0\text{px}]$ in $0.5\text{px}$ steps.
    - Fine pass: evaluates $dx, dy$ in $0.05\text{px}$ steps and $dr$ in $0.10\text{px}$ steps around the coarse peak.
    - Vectorized with NumPy bilinear gradient interpolation, executing in under $15\text{ms}$ per frame.
  - **Results on User-Reported Frames:**
    - **T1 frames (21, 22, 26, 27, 29):** Previously locked onto outer chamfer ($U \approx 731.90, R=24.4\text{px}$), now all converge accurately to the true orifice center ($U \approx 734.50, V \approx 323.55, R = 21.8\text{px}$). Standard deviation of U in T1 dropped from $1.35\text{px}$ to **$0.369\text{px}$** (amplitude reduced from $3.80\text{px}$ to $1.20\text{px}$).
    - **T2 frames (32, 37, 40, 44):** Eliminated asymmetric glint bias ($R=23.0\text{px}$ on frame 40, $R=20.8\text{px}$ on frame 44), all converging to the true orifice ($U \approx 736.45, V \approx 324.55, R = 21.8\text{px}$). Standard deviation of U in T2 dropped from $0.70\text{px}$ to **$0.478\text{px}$**.
  - **Visual Verification:**
    - Regenerated all 75 frames in `test_annotated_results/zoomed_crops/` and `test_annotated_results/full_frames/`.
    - Every green circle envelopes the true orifice rim with uniform margins and centered crosshairs.

---

## [0.8.9] - 2026-09-05
### Fixed
- Low-Light Hough Candidate Starvation & False Edge Lock (Image 32 & Image 61):
  - **Root Cause Identified:** Under dim illumination (L001, mean luminance < 60), the initial Hough transform pass on raw bilateral-filtered luminance produced 3-4 weak spurious edge/chamfer candidate circles. Because `len(circles) > 0`, the fallback condition `if circles is None or len(circles) == 0:` bypassed the CLAHE enhancement pipeline. Consequently, the true orifice candidate was absent from the candidate pool, forcing ranking and sub-pixel refinement to converge onto shadow/chamfer boundaries (Image 32 shifted left by ~5px to $U=732.40, R=16.6\text{px}$; Image 61 shifted down by ~3.7px to $V=325.95, R=24.4\text{px}$).
  - **Algorithmic Fix:**
    - Upgraded `detect_curvature_circle()` in `server/nozzle_detector.py` to unconditionally pool candidate circles from both baseline bilateral filtering and CLAHE-enhanced contrast whenever illumination is dim (`gray.mean() < 90 or gray.std() < 35`).
    - Sub-pixel radial gradient refinement (`_refine_upper_arc_symmetry`) dynamically uses the enhanced `proc_gray` contrast map under low-light conditions, ensuring sharp Sobel gradients and robust edge locking.
  - **Result Verification:**
    - **Image 32 (`T2_L001_F02`):** Corrected from $(U=732.40, V=326.05, R=16.6\text{px})$ to $(U=737.10, V=325.40, R=20.4\text{px})$, perfectly concentric with T2 cluster ($U \approx 736.72, V \approx 324.70, \Delta < 0.7\text{px}$).
    - **Image 61 (`T4_L001_F01`):** Corrected from $(U=732.95, V=325.95, R=24.4\text{px})$ to $(U=735.50, V=321.95, R=21.8\text{px})$, perfectly concentric with T4 cluster ($V \approx 322.74, \Delta < 0.8\text{px}$).
- Deterministic Order Invariance & Randomized Shuffle Verification:
  - Added randomized order testing across the entire 75-frame dataset (`Picture Screenshot`), verifying that processing images in random sequence yields identical sub-pixel coordinates ($\Delta U = 0.000000\text{px}, \Delta V = 0.000000\text{px}$) with zero state leakage or sequence hysteresis.
  - Added unit test `test_picture_screenshot_shuffled_order_invariance` in `tests/test_vision.py`.
  - Regenerated all 75 annotated full frames, 4X zoomed crops, `INDEX.csv`, and `README.md` in `test_annotated_results/`.
  - Unit test suite expanded to **45 passing tests**.

---

## [0.8.8] - 2026-09-05
### Added
- Omnidirectional 360-Degree Trimmed-Quantile Radial Gradient Contrast Scoring:
  - Upgraded `_rank_candidate_circles()` and `_refine_upper_arc_symmetry()` in `server/nozzle_detector.py`: replaces single upper-arc evaluation with full 360-degree radial gradient projection (36 angular samples) and a robust 40% trimmed quantile filter.
  - Automatically drops any directional shadow, flat-land specular glint, or conical reflection flare quadrant (up to 40% of the circle perimeter), evaluating only the true circular orifice boundary across the remaining 60% (> 215 degrees) of continuous circular arc.
  - Eliminates all directional lighting sensitivity, operating seamlessly under ring lights, top lights, bottom lights, or asymmetric oblique LED spotlights.
- Comprehensive Verification Across 5 Tools & 5 Illumination Levels (75/75 Benchmark Images):
  - Benchmarked against the full sweep dataset in `Picture Screenshot` across all 5 toolheads (`T0`, `T1`, `T2`, `T3`, `T4`) and 5 brightness levels (`L001` dimmest, `L004`, `L016`, `L064`, `L255` maximum brightness).
  - Achieved **100.0% detection rate (75/75 frames)** with **0 outliers**.
  - Verified sub-pixel optical repeatability within each lighting level ($\sigma \approx 0.02 - 0.05\text{px}$) and across the entire 2-order-of-magnitude dynamic range ($\sigma_U \le 1.3\text{px}, \sigma_V \le 0.9\text{px}$).
  - Calculated calibrated inter-tool machine offsets relative to T0:
    - T1: $\Delta X = -0.0340\text{mm}, \Delta Y = -0.0532\text{mm}$ (`G10 P1 X-0.0340 Y-0.0532`)
    - T2: $\Delta X = -0.0022\text{mm}, \Delta Y = -0.0371\text{mm}$ (`G10 P2 X-0.0022 Y-0.0371`)
    - T3: $\Delta X = -0.0102\text{mm}, \Delta Y = -0.0209\text{mm}$ (`G10 P3 X-0.0102 Y-0.0209`)
    - T4: $\Delta X = -0.0382\text{mm}, \Delta Y = -0.0596\text{mm}$ (`G10 P4 X-0.0382 Y-0.0596`)
- Test Suite Expansion:
  - Added `test_picture_screenshot_sweep_dataset` in `tests/test_vision.py`.
  - Automated test suite expanded to **44 passing tests**.

---

## [0.8.7] - 2026-09-05
### Added
- Continuous Sub-Pixel Bilinear Gradient Sampling & Two-Stage Symmetry Refinement:
  - Added `_sample_bilinear_vec()` in `server/nozzle_detector.py`: performs vectorized continuous 2D bilinear interpolation over Sobel spatial gradients `gx, gy`, completely removing the 0.5px quantization noise caused by nearest-neighbor integer discretization (`np.round()`).
  - Upgraded `_refine_upper_arc_symmetry()` with a 2-stage coarse-to-fine vectorized search: evaluates 0.5px steps across $[-4.0, +4.0\text{px}]$, followed by 0.05px steps across $[-0.45, +0.45\text{px}]$ around the coarse peak.
  - Accelerated execution time by 15x (from 40ms to ~2.5ms per inspection frame) while providing $< 0.05\text{px}$ optical resolution.
- Multi-Tool Delta Offset Calculation Engine (`TransformationSolver` & REST API):
  - Added `calculate_tool_delta(reference_uv, target_uv)` in `server/affine_transform.py`: calculates exact physical machine XY offset between toolheads (T0 vs T1..n) directly in physical millimetres, supporting both 1st/2nd-order transformation matrices and linear MPP scale fallbacks without servo damping attenuation.
  - Added `/calculate_tool_delta` endpoint in `server/tool_calibrator_server.py`: returns pixel deltas $(\Delta U, \Delta V)$, physical machine deltas $(\Delta X, \Delta Y)$, ready-to-run G-code commands (`G10 P<tool> X... Y...`), and Klipper configuration snippets (`[tool <tool>] gcode_x_offset: ...`).
- Web Dashboard Interactive Multi-Tool Delta Calculator Widget:
  - Added dedicated Multi-Tool Delta Offset Calculator card in `server/templates/index.html`.
  - Added 1-click coordinate capture buttons ("Set Ref from Last Detect", "Set Tgt from Last Detect") to allow users to visually inspect and calibrate multi-toolchanger offsets interactively from their browser.
  - Bumped dashboard and server version badge to `v0.8.7 Sub-Pixel`.
- Test Suite Expansion:
  - Added `test_calculate_tool_delta`, `test_subpixel_refinement_precision`, and `test_calculate_tool_delta_endpoint` in `tests/test_vision.py`.
  - Automated test suite expanded to 43 passing tests.

---

## [0.8.6] - 2026-09-05
### Added
- Dual 1st-Order Affine & 2nd-Order Polynomial Transformation Solver:
  - Upgraded `solve_matrix` in `server/affine_transform.py`: automatically performs 1st-order affine fit ($[x, y, 1]$ basis) for 3 to 5 calibration points (such as the 5-point star-pattern displacement in `CALIBRATE_CAMERA_SCALE`), and 2nd-order polynomial fit ($[x^2, y^2, xy, x, y, 1]$) for $\ge 6$ points.
  - Updated `calculate_offset` with dynamic matrix rank dispatch, ensuring smooth sub-pixel visual servoing under both transformation models.
  - Added missing `set_mpp()` setter in `TransformationSolver` to support live `/set_mpp` REST synchronization.
- Resolution-Adaptive ROI & Distance Prior Scaling:
  - Upgraded `detect_curvature_circle()` in `server/nozzle_detector.py`: scaled central ROI radius adaptively (`r_roi = min(int(min(w, h) * 0.38), 260)`), accommodating larger initial toolhead offsets on 720p/1080p camera streams.
  - Scaled candidate distance prior $d_0$ dynamically with sensor resolution ($d_0 = \max(140.0, \min(w, h) \times 0.22)$).
- Codebase Logic Verification & Namespace Protection:
  - Fixed missing `Dict` and `Any` typing imports in `klippy/extras/safe_navigator.py` and `config_manager.py`.
  - Initialized and synchronized in-memory `calibrated_mpp` state on `ToolCalibrator`.
  - Renamed wrapper macro in `macros/tool_calibrator_macros.cfg` to `CALIBRATE_CAMERA` to prevent recursion and command collision with registered Python G-code `CALIBRATE_CAMERA_SCALE`.
  - Added automated unit tests `test_solve_matrix_affine_star_pattern` and `test_set_mpp` in `tests/test_vision.py`.
  - Expanded automated test suite to 40 passing unit & integration tests.

---

## [0.8.5] - 2026-09-05
### Added
- Hough Candidate Radial Gradient Contrast Ranking & Distance Discrimination:
  - Added `_rank_candidate_circles()` in `server/nozzle_detector.py`: scores all candidate circles returned by `cv2.HoughCircles` using upper-arc radial gradient projection combined with a soft distance prior from the optical center.
  - Resolved candidate ambiguity between the true circular nozzle orifice/flat ring ($R \approx 21-23\text{px}$) and internal specular reflections or false flares ($R \approx 13\text{px}$) that occur inside the orifice bore.
  - Verified 100% detection repeatability across all 12 real-world VoronBed camera-ring captures (`CAMRING_L001`, `CAMRING_L002`, `CAMRING_L004`): achieves sub-half-pixel repeatability ($\sigma_X = 0.57\text{px}, \sigma_Y = 0.48\text{px}, \sigma_R = 0.58\text{px}$) at coordinates $(737.42 \pm 0.57, 328.79 \pm 0.48)$.
  - Added regression unit test `test_candidate_circle_ranking_discrimination` in `tests/test_vision.py`.
  - Expanded automated test suite to 38 passing unit & integration tests.

---

## [0.8.4] - 2026-09-05
### Added
- Optical Inspection Lighting Lifecycle & Sensor Blooming Protection:
  - Integrated Lighting Manager in `klippy/extras/tool_calibrator.py`: automatically illuminates camera ring lighting (`camera_pin` / `camera_led_brightness` / `_CALIBRATION_CAMERA_LED_ON`) upon entering camera station, and shuts it off upon departure.
  - Automatic toolhead nozzle LED suppression (`_CALIBRATION_NOZZLE_LED_OFF`): automatically turns off downward-facing toolhead nozzle LEDs (StealthBurner, DragonBurner, etc.) during optical inspection, preventing intense direct light from saturating the upward-facing camera sensor (sensor blooming/flare trap). Restores toolhead LEDs (`_CALIBRATION_NOZZLE_LED_ON`) immediately upon departure.
  - Added default customizable macro hooks in `macros/tool_calibrator_macros.cfg`.
- Low-Light & Dim-Illumination Adaptive Contrast Enhancement (CLAHE):
  - Upgraded `detect_curvature_circle` in `server/nozzle_detector.py`: when central ROI has low luminance (< 90) or low contrast, applies adaptive histogram equalization (CLAHE) prior to circular gradient accumulation.
  - Guarantees sub-pixel detection reliability even under dim ring lighting or low-glare settings.
- Expanded automated test suite to 37 passing unit & integration tests (`test_inspection_lighting_lifecycle`).

---

## [0.8.3] - 2026-09-05
### Added
- Cartographer Touch V4 Relative Delta Z Backend & Official Specification Alignment:
  - Researched and integrated official Cartographer 3D source code (`scanner.py` v4.4.0) and documentation specifications into `CartographerBackend`.
  - Dynamic command resolution: automatically detects registered Klipper commands (`CARTOGRAPHER_TOUCH`, `SCANNER_TOUCH`, `CARTOGRAPHER_TOUCH_HOME`) matching the active sensor naming (`sensor: cartographer` vs `sensor: scanner`).
  - Added official Cartographer parameter forwarding: supports `carto_touch_speed` (`SPEED=...`), `carto_touch_tolerance` (`TOLERANCE=...`), and `carto_touch_retries` (`RETRIES=...`).
  - Aligned probing location priority: checks `bed_mesh` `zero_reference_position` first, defaulting to exact geometric bed center $((X_{min} + X_{max})/2, (Y_{min} + Y_{max})/2)$ to cancel bed tilt/mesh distortion.
  - Developed and verified mathematically sound multi-tool relative delta Z-offset calculation: $\Delta Z = Z_n - Z_{ref}$.
  - Corrected offset sign convention: longer nozzles ($Z_n > Z_{ref}$) yield positive offsets ($+\Delta Z$) to prevent bed collisions, while shorter nozzles ($Z_n < Z_{ref}$) yield negative offsets ($-\Delta Z$).
  - Implemented thermal safety pre-check (`ERR_PRE_002`): queries toolhead extruder temperature prior to touch probing and aborts if temperature exceeds `carto_max_touch_temp` (default $150^\circ\text{C}$ / `scanner_touch_max_temp`), safeguarding PEI bed sheets against melting or puncture.
  - Added automatic safe liftoff retraction (`carto_retract_z = 5.0mm`) executed at $15\text{mm/s}$ immediately after every touch contact to protect nozzle tips and bed coating during toolchanger transit.
  - Expanded test suite in `tests/test_z_backends.py` to 10 dedicated tests covering command resolution, parameter injection, and `zero_reference_position`, bringing total passing automated tests to 36.

---

## [0.8.2] - 2026-09-05
### Added
- Interactive Vision Dashboard & Benchmark Dataset Explorer:
  - Overhauled `server/templates/index.html` with modern Rich Aesthetics: Inter typography, glassmorphism, dynamic Zoom 1.0X/2.0X/3.5X controls, and visual detection result HUD with sub-pixel coordinates $(X, Y, R)$ and confidence meter.
  - Added REST endpoints `GET /api/samples` and `POST /api/test_sample` in `server/tool_calibrator_server.py` allowing instant 1-click testing of live camera or benchmark datasets directly in the web UI.
  - Expanded `scripts/generate_sample_dataset.py` with realistic optical simulators (`sim_conical_glare_flare.jpg`, `sim_ruby_gemstone.jpg`) generating conical glare flares and translucent ruby gemstone structures for continuous regression testing.
  - Enhanced visual servoing `_center_nozzle()` in `klippy/extras/tool_calibrator.py` with automatic retry after mechanical settling to prevent transient motion blur aborts, and added real-time optical tier reporting (`Tier 0 Curvature`, `Tier 1 Standard`, etc.) to Klipper console responses.
  - Added automated unit test `test_sample_api_endpoints` in `tests/test_vision.py`, expanding test coverage to 26 automated unit tests.

---

## [0.8.1] - 2026-09-05
### Added
- Radial Edge-Curvature Invariance & Upper-Arc Symmetry Refinement:
  - Added Tier 0 / Primary detector in `server/nozzle_detector.py` combining bilateral edge filtering with `cv2.HoughCircles` and sub-pixel radial gradient symmetry optimization.
  - Implemented upper-arc gradient projection (`_refine_upper_arc_symmetry` from $150^\circ$ to $30^\circ$ elevation), completely eliminating downward center pull caused by specular flare traps on conical flanks.
  - Resolved 4 critical real-world failure modes inherent to kTAMV's binary blob moments ($m_{10}/m_{00}, m_{01}/m_{00}$): downward conical glare flares, lateral shadow/reflection asymmetry, silicone sock aperture misidentification, and translucent Ruby/Sapphire gemstone inversion.
  - Updated 13 verified benchmark annotations with sub-pixel HUD badges and 3.5X micro-magnification in `tests/annotated_results/`.
  - Maintained 100% pass rate across 25 automated unit and integration tests.

---

## [0.8.0] - 2026-09-05
### Added
- Generic Multi-Toolchanger Architecture Discovery:
  - Added `_discover_tools()` dynamically detecting toolheads across all Klipper setups: `[toolchanger]` (viesturs), `[tool 0..n]` objects, `[gcode_macro T0..n]`, or explicit config `tools: 0, 1, 2, 3`.
  - Zero hardcoded assumptions: works out-of-the-box on Jubilee, StealthChanger, TapChanger, DXL, and custom macro-driven toolheads.
- Flexible Calibration Sequencing & Optical Focal Plane Compensation:
  - Added `ORDER` parameter to `CALIBRATE_TOOL_OFFSETS` supporting `XY_FIRST` (default) and `Z_FIRST`.
  - Added `COMPENSATE_FOCAL_Z` support: when Z is probed first, automatically compensates camera focal height for tools with different physical hotend/nozzle lengths so all nozzles sit at the exact optical focal plane.
- Non-Intrusive, Zero-Dependency Nozzle Cleaning:
  - Nozzle cleaning made strictly optional (`CLEAN_NOZZLE=0` by default).
  - Safe inspection: checks for `clean_nozzle_gcode` or `_CLEAN_NOZZLE` macro presence before execution; never raises errors or crashes on printers without cleaning hardware.
  - `_CLEAN_NOZZLE` macro defaults to a silent no-op.
- Real-World High-Contrast & Dim Illumination Vision Benchmarks:
  - Added user real-world Voron Stealth Changer camera frames (`nozzle_dim_lighting_eval1.jpg`, `nozzle_high_glare_eval2.jpg`) into regression fixtures.
  - Added 13 real-world multi-nozzle screenshots (`Screenshot 2026-09-05 *.png`) capturing 5 distinct toolheads into automated test suite.
  - 100% detection rate across all 13 captures at Tier 1 Combo 1 with 1.0 confidence.
- Selective Tool Calibration:
  - `CALIBRATE_TOOL_OFFSETS` now supports `TOOLS` parameter (e.g. `TOOLS=1` or `TOOLS=1,2`) with automatic reference tool sequencing.
- Configuration Robustness & Unit Normalization:
  - Automatic feedrate conversion in `SafeNavigator`: detects speeds configured in mm/min (`> 500`) and converts to mm/s, preventing supersonic velocity crashes.
  - Dual configuration key aliases supported seamlessly (`service_url` / `server_url`, `offsets_config_path` / `offset_config_path`, `camera_target_x` / `camera_x`, `switch_target_x` / `zswitch_x_pos`).
- Multi-Threshold Median Slice Optimization in `server/nozzle_detector.py`:
  - Adjusted `minArea` from 250/180 down to 45/35/25 pixels to detect micro-orifices (< 0.4mm nozzle holes) under intense specular cone reflections.
  - Reordered cascades to evaluate Grayscale + Median multi-threshold sweep first, achieving Tier 1 lock on shiny brass nozzles without glare artifacts.
  - Expanded test suite to 25 automated unit and integration tests.

---

## [0.7.0] - 2026-09-05
### Added
- Automated Star-Pattern Camera Scale & Matrix Calibration:
  - New G-code command `CALIBRATE_CAMERA_SCALE [DISTANCE=1.0]` in `klippy/extras/tool_calibrator.py`.
  - Automated 4-direction orthogonal displacement ($\pm X, \pm Y$) measuring real-time pixel shift vs physical travel.
  - Automatically queries `/calibrate_mpp` and `/solve_matrix` on the vision server.
  - Atomically saves the solved `mpp` scale directly into `tool_offsets.cfg` (`[tool_calibrator_station camera]`).
  - New REST endpoint `/set_mpp` on `tool_calibrator_server.py` to synchronize calibrated scale.
  - User macro `CALIBRATE_CAMERA_SCALE` added to `macros/tool_calibrator_macros.cfg`.
  - Integration test `test_calibrate_camera_scale_star_pattern` in `tests/test_calibration_cycle.py` (22 total passing tests).

---

## [0.6.0] - 2026-09-05
### Added
- Interactive Vision Monitor & Diagnostics Web Dashboard:
  - Responsive dark-theme dashboard on route `/` embedded into `server/tool_calibrator_server.py` via `server/templates/index.html`.
  - Live video stream HUD with crosshairs, optical center markers, and stream refresh.
  - Real-time telemetry monitoring (scale MPP, affine matrix solved state, active snapshot URL).
  - 1-Click diagnostic nozzle detection tool calling REST API directly from the browser.
- Offline Vision Benchmark Dataset & Testing:
  - Dataset generator in `scripts/generate_sample_dataset.py`.
  - Benchmark sample images in `tests/sample_images/` covering perfect center, dual-axis offsets, dim illumination, optical flare/glare, and debris contamination.
  - Automated regression test in `tests/test_vision.py` asserting 100% cascade detection across all sample images (21 total passing unit & integration tests).

---

## [0.5.0] - 2026-09-05
### Added
- Fully Automated Station Staging & Zero-Manual-Config Workflow:
  - Vector-based automatic approach waypoint derivation in `SafeNavigator`: automatically projects an inbound trajectory from the station towards the bed center, eliminating all manual calculation.
  - 1-Click Interactive Teaching (`CALIBRATION_TEACH_STATION`): supports `AUTO_CENTER=1` (sub-millimeter visual servoing) and `AUTO_TOUCH=1` (automatic contact Z probing).
  - Auto-Persistence via `ConfigManager.save_section()`: stores station coordinates directly in `tool_offsets.cfg` (`[tool_calibrator_station ...]`), removing the need to edit `printer.cfg` manually.
  - Auto-Inheritance from `[tools_calibrate]`: detects existing switch endstop pin locations (`pin_loc_x`, `pin_loc_y`, `pin_loc_z`) without duplicate user configuration.
  - New 1-Click macros: `AUTO_TEACH_CAMERA` and `AUTO_TEACH_SWITCH`.
  - Expanded test suite to 17 automated tests covering vector derivation, section persistence, and 1-click station teaching.

---

## [0.4.0] - 2026-09-05
### Added
- G-Code Macro Suite in `macros/`:
  - `tool_calibrator_macros.cfg`: `CALIBRATE_ALL_TOOLS`, `CALIBRATE_TOOL`, `CALIBRATION_ROLLBACK`, with pre/post flight lifecycle hooks.
  - `safe_staging_macros.cfg`: Interactive position teaching (`TEACH_CAMERA_SAFE_Z`, `TEACH_CAMERA_APPROACH`, `TEACH_CAMERA_TARGET`, `TEACH_SWITCH_APPROACH`, `TEACH_SWITCH_TARGET`).
  - `sample_tool_calibrator.cfg`: Complete sample configuration ready to include in `printer.cfg`.
- Automated Packaging & Installer Scripts in `scripts/`:
  - `install.sh`: Automated Raspberry Pi / Debian Linux installer with venv creation, dependency installation, Klipper extras linking, and systemd service startup.
  - `uninstall.sh`: Clean service teardown and file cleanup.
  - `tool_calibrator.service`: Systemd service unit definition.
  - `moonraker_update.cfg`: Moonraker Update Manager integration block for Mainsail / Fluidd.
- Expanded Test Suite:
  - `tests/test_safe_navigator.py`: 3-tier safe waypointing step transitions and boundary checks.
  - `tests/test_calibration_cycle.py`: End-to-end full calibration sequence integration test (14 total passing tests).

---

## [0.3.0] - 2026-09-05
### Added
- Core Klipper extension in `klippy/extras/`:
  - `safe_navigator.py`: 3-tier safe waypointing kinematics controller (`Safe_Z`, `Safe_Approach`, `Target`).
  - `config_manager.py`: Atomic persistence with isolated `tool_offsets.cfg` and rolling timestamped backups.
  - `z_backends/base_z.py`: Abstract Z probe interface.
  - `z_backends/switch_backend.py`: Physical switch endstop probing with `tools_calibrate` conflict prevention.
  - `z_backends/cartographer_backend.py`: Cartographer V4 Touch Home & Touch Probe backend.
  - `tool_calibrator.py`: Primary Klipper module orchestrating G-codes (`CALIBRATE_TOOL_OFFSETS`, `CALIBRATION_SET_SAFE_POS`, `CALIBRATION_ROLLBACK_OFFSETS`, `CALIBRATION_STATUS`).
- Unit tests in `tests/test_klipper_logic.py` verifying atomic persistence, formatting, and rollback.

---

## [0.2.0] - 2026-09-05
### Added
- External machine vision background service in `server/`:
  - `stream_grabber.py`: Snapshot acquisition from Crowsnest/camera-streamer with HTTP timeout protection.
  - `nozzle_detector.py`: 3-tier cascade (`Standard`, `Relaxed`, `Super-Relaxed`) with dual preprocessors (Gamma + YUV Adaptive Gaussian, Triangle threshold).
  - `affine_transform.py`: Star-pattern mm-per-pixel (`mpp`) calibration and 2nd-order polynomial matrix inversion with 0.55 damping.
  - `visual_debugger.py`: Thread-safe MJPEG streamer and dynamic HUD overlay for web preview.
  - `tool_calibrator_server.py`: Flask + Waitress HTTP daemon running on port 8090.
- Unit tests in `tests/test_vision.py` with synthetic circular nozzle images.

---

## [0.1.0] - 2026-09-05
### Added
- Initial project architecture and complete documentation suite in `agent/` folder.
- Modular documentation structure across 14 specialized documents in `agent/`:
  - `AGENTS.md` (English) & `AGENTS.vi.md` (Tiếng Việt): Master orchestration and navigation index.
  - `PROJECT.md`: Project mission, hardware specs, and scope.
  - `WORKFLOW.md`: Operational workflow and finite state machine.
  - `SAFETY.md`: 3-tier safe navigation and hardware protection.
  - `DECISIONS.md`: Architecture Decision Records (ADRs).
  - `DIRECTORY.md`: Codebase tree and module boundaries.
  - `GIT_RULE.md`: Single `main` branch policy and commit standards.
  - `LOGGING.md`: Standardized error taxonomy (`ERR_xxx`) and telemetry format.
  - `KNOWN_ISSUES.md`: Hardware/software edge cases and solutions.
  - `BACKUP.md`: Configuration persistence, backups, and rollback.
  - `STYLE.md`: Python PEP 8, Jinja2 macros, and commenting rules.
  - `TODO.md`: Phased work breakdown structure (WBS) and progress tracking.
  - `PROMPTS.md`: Standard agent interaction templates.
  - `CHANGELOG.md`: Version update history.
- Root repository files: `README.md` (English), `README.vi.md` (Tiếng Việt), and `.gitignore`.
- Remote origin configuration: `https://github.com/IDcrazy123/Tool-Klipper-Calibration.git` on branch `main`.
