# LOGGING.md — Error Taxonomy & Telemetry Standards

This document establishes the standardized error classification, telemetry messaging format, and console feedback protocols for **Tool-Klipper-Calibration**.

---

## 1. Standardized Error Taxonomy

| Error Code | Category | Root Cause | Automatic Action & Actionable User Guidance |
| :--- | :--- | :--- | :--- |
| **`ERR_PRE_001`** | Pre-Flight | Printer axes not fully homed | Aborts execution immediately. Message: *"G28 homing required before calibration."* |
| **`ERR_PRE_002`** | Pre-Flight | Nozzle temperature $> 100^\circ\text{C}$ | Halts execution to prevent optical distortion and smoke. Message: *"Allow nozzle to cool $\le 100^\circ\text{C}$."* |
| **`ERR_PRE_003`** | Pre-Flight | No active tool engaged or toolchanger lock error | Halts sequence. Message: *"Check toolchanger carriage lock state."* |
| **`ERR_CAM_101`** | Vision | Vision Service offline (port 8090 unreachable) | Aborts sequence. Message: *"Vision daemon down. Run: sudo systemctl restart tool_calibrator."* |
| **`ERR_CAM_102`** | Vision | Crowsnest video stream timeout or bad URL | Warns user. Message: *"Verify camera snapshot URL in printer.cfg."* |
| **`ERR_CV_201`** | Algorithm | Nozzle orifice not found after search attempts | Executes micro-wiggle ($0.1\text{mm}$). If still failing: *"Clean nozzle tip or adjust LED ring brightness."* |
| **`ERR_CV_202`** | Algorithm | Circularity / area filter failure ($< 0.6$) | Warns user: *"Nozzle tip heavily encrusted with filament or out of focal plane."* |
| **`ERR_CV_203`** | Algorithm | Scale calibration failed ($> 25\%$ outlier variance) | Aborts calibration: *"Camera axis alignment skewed. Verify camera mounting rigidity."* |
| **`ERR_CV_204`** | Algorithm | Computed target outside image frame bounds | Movement suppressed: *"Calculated motion exceeds camera boundaries. Check lens alignment."* |
| **`ERR_Z_301`** | Z-Probe | Switch did not trigger within travel distance | Aborts probe move: *"Switch pin failed to trigger. Check wiring or target XY coordinate."* |
| **`ERR_Z_302`** | Z-Probe | Cartographer Touch returned null/invalid reading | Aborts probe: *"Cartographer Touch failed. Check CAN bus connection and touch model."* |
| **`ERR_Z_303`** | Z-Probe | Sample variance exceeds safety limit ($> 0.05\text{mm}$) | Retries probe sample once; if persistent: *"Mechanical play or loose tool dock detected."* |
| **`ERR_CFG_401`** | Storage | Unable to open or write to configuration file | Halts persistence: *"Failed writing tool_offsets.cfg. Check file permissions."* |

---

## 2. Console Telemetry Output Format

All diagnostic messages emitted to Klipper Console follow this structured format:

```text
// [TOOL_CALIB] [LEVEL] [TOOL] Message body
//   -> Context / Coordinates
//   -> Recommended Action
```

### Example 1: Operational Status
```text
// [TOOL_CALIB] [INFO] [T1] Approaching Camera Station (X:150.00, Y:300.00, Z:25.00)...
// [TOOL_CALIB] [INFO] [T1] Nozzle detected at UV (320.5, 240.1), Confidence: 98.4%, Tier: Standard
// [TOOL_CALIB] [INFO] [T1] Offset computed: X:+0.142 Y:-0.085 Z:+0.310
```

### Example 2: Error Notification
```text
// [TOOL_CALIB] [ERROR] [T2] [ERR_CV_201] Nozzle orifice not detected after 3 attempts!
//   -> Position: X:150.2 Y:300.1 Z:12.0
//   -> Action: Clean burnt plastic off the nozzle tip or adjust ring LED illumination.
//   -> Status: Aborting safely. Toolhead elevated to Safe_Z (25.00mm).
```
