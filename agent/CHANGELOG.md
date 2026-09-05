# CHANGELOG.md — Project Version Update History

All notable changes to the **Tool-Klipper-Calibration** project are documented in this file, adhering to the [Semantic Versioning (SemVer)](https://semver.org/) format.

---

## [Unreleased]
### Planned
- Phase 4: G-code macro suite and interactive position teaching commands.
- Phase 5: Hardware benchmarking and repeatable precision verification.
- Phase 6: Automated Linux installation scripts and Moonraker packaging.

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
