# PROJECT.md — Project Mission & Technical Specifications

> [!NOTE]
> Bản tiếng Việt có sẵn tại: [PROJECT.vi.md](PROJECT.vi.md)

---

## 1. Executive Summary
- **Project Name:** Tool-Klipper-Calibration
- **Mission:** Fully automated, high-precision calibration of spatial offsets (XY and Z axes) between multiple toolheads in Klipper Toolchanger 3D printing systems.
- **Application Target:** Multi-toolhead 3D printers (StealthBurner Toolchanger, Jubilee, DXL, E3D Toolchanger, Voron Tap/Toolchanger) powered by Klipper firmware.

---

## 2. Core Objectives & Scope

### 2.1. Automated Machine-Vision XY Calibration
- Utilizes an upward-facing macro camera mounted to the bed or frame.
- Automatically detects the nozzle orifice circle center via OpenCV algorithms.
- Iteratively centers the nozzle over the optical axis, calculates the millimeters-per-pixel (`mpp`) scale factor, and computes a 2D affine / perspective transformation matrix.
- Measures relative offsets between the designated Reference Toolhead (default `T0`) and all secondary toolheads (`T1`, `T2`, ... `Tn`).

### 2.2. Flexible Multi-Backend Z Calibration
- **Backend 1 — Physical Endstop / Switch:** Toolheads mechanically depress a precision pin or microswitch (inheriting multi-axis probing logic from `tools_calibrate`).
- **Backend 2 — Cartographer Touch Probe (V4):** Utilizes Cartographer 3D eddy current touch mode. Performs `CARTOGRAPHER_TOUCH_HOME` on the reference tool and `CARTOGRAPHER_TOUCH_PROBE` on secondary tools, reading the touch-model offset to compute relative Z heights.

### 2.3. Dynamic Safe Staging Navigation
- Replaces rigid, hardcoded travel coordinates with a flexible 3-tier safe waypoint model (`Safe_Z`, `Safe_Approach`, `Target`).
- Enables users to "teach" and store safe waypoints via interactive jogging commands (`CALIBRATION_SET_SAFE_POS`).
- Enforces vertical clearance before any lateral travel between tool docks and calibration stations.

### 2.4. Comprehensive Diagnostic & Telemetry System
- Defines a standardized error code taxonomy (`ERR_PRE_xxx`, `ERR_CAM_xxx`, `ERR_CV_xxx`, `ERR_Z_xxx`, `ERR_CFG_xxx`).
- Delivers real-time status updates and actionable recovery advice directly to the Klipper console and Moonraker web UIs (Mainsail/Fluidd).

### 2.5. Full 1-Click End-to-End Automation
- Once safe staging points are configured, triggering a single macro (e.g., `CALIBRATE_TOOL_OFFSETS`) executes the entire sequence: homing verification, reference tool alignment, secondary tool measurement loops, offset persistence, and safe parking.

---

## 3. Hardware & Infrastructure Specifications

| Component | Specification & Requirement |
| :--- | :--- |
| **Camera Module** | Upward-facing USB microscope or Raspberry Pi camera, macro focus range 15mm–40mm |
| **Lighting Unit** | 5V White Ring LED or 4x 45° LED array creating sharp circular orifice contrast |
| **Video Stream** | Crowsnest MJPEG snapshot endpoint (e.g., `http://localhost/webcam2/snapshot?max_delay=0`) |
| **Z Sensor 1** | Physical switch endstop (Sexbolt / Omron microswitch) mounted to the bed frame |
| **Z Sensor 2** | Cartographer 3D (V4) supporting toolhead nozzle touch probing |
| **Compute Host** | Raspberry Pi 3B+/4B/5 or BTT CB1 running Linux, Python 3.9+, OpenCV, NumPy |
| **Firmware** | Klipper with Toolchanger module active (`toolchanger.py`) |
