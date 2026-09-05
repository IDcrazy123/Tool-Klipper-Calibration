# CHANGELOG.md — Project Version Update History

All notable changes to the **Tool-Klipper-Calibration** project are documented in this file, adhering to the [Semantic Versioning (SemVer)](https://semver.org/) format.

---

## [Unreleased]
### Planned
- Physical hardware validation and user benchmarking telemetry on multi-tool rig.

---

## [0.8.2] - 2026-09-05
### Added
- Interactive Vision Dashboard & Benchmark Dataset Explorer:
  - Overhauled `server/templates/index.html` with modern Rich Aesthetics: Inter typography, glassmorphism, dynamic Zoom 1.0X/2.0X/3.5X controls, and visual detection result HUD with sub-pixel coordinates $(X, Y, R)$ and confidence meter.
  - Added REST endpoints `GET /api/samples` and `POST /api/test_sample` in `server/tool_calibrator_server.py` allowing instant 1-click testing of live camera or benchmark datasets directly in the web UI.
  - Expanded `scripts/generate_sample_dataset.py` with realistic optical simulators (`sim_conical_glare_flare.jpg`, `sim_ruby_gemstone.jpg`) generating conical glare flares and translucent ruby gemstone structures for continuous regression testing.
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
