# TODO.md — Work Breakdown Structure (WBS) & Progress Checklist

This checklist tracks execution progress across all phases of the **Tool-Klipper-Calibration** project.

---

## 📊 Phase Overview & Status

- [x] **Phase 1: Architecture, Documentation & Environment Setup** *(Completed)*
- [x] **Phase 2: Vision Background Service Implementation (`server/`)** *(Completed)*
- [x] **Phase 3: Klipper Core Extension Implementation (`klippy/extras/`)** *(Completed)*
- [x] **Phase 4: User Macro Suite & Hook Integration (`macros/`)** *(Completed)*
- [x] **Phase 5: Offline Synthetic Testing & Dry-Run Benchmarking** *(Completed)*
- [x] **Phase 6: Automated Packaging, Install Scripts & Release** *(Completed)*

---

## Detailed Task Checklist

### Phase 1: Architecture, Documentation & Git Setup
- [x] **Task 1.1:** Initialize Git repository on `main` branch with remote origin.
- [x] **Task 1.2:** Configure comprehensive `.gitignore` for Python, Klipper configs, and local references.
- [x] **Task 1.3:** Create modular, bilingual 14-document architecture in `agent/` folder.
- [x] **Task 1.4:** Create root `README.md` and `README.vi.md`.

### Phase 2: Vision Background Service (`server/`)
- [x] **Task 2.1:** Implement `stream_grabber.py` to acquire low-latency MJPEG snapshot frames from Crowsnest.
- [x] **Task 2.2:** Build `nozzle_detector.py` featuring TAMV-derived 3-tier cascade (Standard, Relaxed, Super-Relaxed).
- [x] **Task 2.3:** Implement `affine_transform.py` for star-pattern mm-per-pixel (`mpp`) calibration and matrix inversion.
- [x] **Task 2.4:** Build `visual_debugger.py` to stream live MJPEG previews with crosshairs and detected circles.
- [x] **Task 2.5:** Develop `tool_calibrator_server.py` exposing REST JSON endpoints on port 8090 (`/health`, `/detect_nozzle`, `/calibrate_mpp`, `/preview`).
- [x] **Task 2.6:** Implement in-memory frame cache buffer (`stream_grabber`), session lock dual-key compatibility, and `ERR_CV_204` frame bounds guard.

### Phase 3: Klipper Core Extension (`klippy/extras/`)
- [x] **Task 3.1:** Implement `tool_calibrator.py` core orchestrator class and G-code dispatcher.
- [x] **Task 3.2:** Develop `safe_navigator.py` implementing 3-tier waypoint transitions and motion boundaries.
- [x] **Task 3.3:** Build `z_backends/base_z.py` abstract interface.
- [x] **Task 3.4:** Implement `z_backends/switch_backend.py` supporting physical endstop probing.
- [x] **Task 3.5:** Implement `z_backends/cartographer_backend.py` supporting Cartographer V4 Touch Home & Touch Probe.
- [x] **Task 3.6:** Develop `config_manager.py` for atomic configuration persistence and timestamped backups.
- [x] **Task 3.7:** Implement Multi-frame Burst Sampling (`_sample_burst`) and Adaptive Wiggle Recovery (`_recover_with_wiggle`).
- [x] **Task 3.8:** Decouple XY optical calibration and Z probing calibration so unmeasured axes are never overwritten.
- [x] **Task 3.9:** Hardening: Safe lookup for tools_calibrate in switch_backend, eliminate kin-desync set_position, targeted backup rollback, and camera thermal check (`max_camera_temp`).


### Phase 4: Macro Suite & Hook Integration (`macros/`)
- [x] **Task 4.1:** Develop `CALIBRATE_TOOL_OFFSETS` user macro with tool selection and dry-run parameters.
- [x] **Task 4.2:** Develop interactive teaching macros `CALIBRATION_SET_SAFE_POS`.
- [x] **Task 4.3:** Integrate lifecycle hooks: `before_pickup_gcode`, `after_pickup_gcode`, `start_gcode`, `finish_gcode`.
- [x] **Task 4.4:** Implement rollback macro `CALIBRATION_ROLLBACK_OFFSETS`.
- [x] **Task 4.5:** Implement Interactive Navigation (`CALIBRATION_NAVIGATE`), Vision Testing (`TEST_NOZZLE_VISION`), and Single-Tool Centering (`CENTER_NOZZLE`).
- [x] **Task 4.6:** Author Complete 6-Step Operational Standard Operating Procedure (`docs/QUY_TRINH_VAN_HANH.md`).
- [x] **Task 4.7:** Implement decoupled macros `CALIBRATE_TOOLS_XY`, `CALIBRATE_TOOLS_Z`, `CALIBRATE_TOOL_XY`, `CALIBRATE_TOOL_Z`.

### Phase 5: Verification & Benchmarking
- [x] **Task 5.1:** Offline unit tests using synthetic nozzle sample images across diverse illumination profiles (14 unit/integration tests).
- [x] **Task 5.2:** Contactless dry-run validation (`DRY_RUN=1`) supported in macro suite without disk writes.
- [ ] **Task 5.3:** Physical machine hardware validation by user on live toolchanger rig.

### Phase 6: Packaging & Automated Deployment
- [x] **Task 6.1:** Write automated Linux installer script `scripts/install.sh` (virtualenv, dependencies, systemd unit).
- [x] **Task 6.2:** Create uninstaller script `scripts/uninstall.sh`.
- [x] **Task 6.3:** Verify Moonraker Update Manager integration snippet (`scripts/moonraker_update.cfg`).
