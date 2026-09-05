# KNOWN_ISSUES.md — Known Hardware/Software Edge Cases & Remedies

This document catalogues known hardware quirks, optical anomalies, and environmental edge cases observed across toolchanger implementations, along with proven mitigation strategies.

---

## 1. Optical & Computer Vision Anomalies

### Issue 1.1: Harsh Glare / Specular Reflections on Brass Nozzles
- **Symptom:** The blob detector identifies erratic false centers or fails with circularity $< 0.5$.
- **Root Cause:** Direct on-axis LED lighting produces high-intensity specular hot spots on shiny brass surfaces, washing out the orifice contour.
- **Remedies:**
  1. Use diffuse lighting: Place a 3D-printed translucent white PETG ring diffuser over the LEDs.
  2. Software compensation: The vision pipeline applies adaptive Otsu thresholding with morphological closing before passing frames to the detector.
  3. Dimming: Lower LED PWM duty cycle via macro (`SET_PIN PIN=cam_led VALUE=0.35`).

### Issue 1.2: Encrusted Filament Residues (Burnt PETG / ABS)
- **Symptom:** Nozzle appears elliptical or asymmetrical; `ERR_CV_202` triggered.
- **Root Cause:** Hardened plastic blobs adhering to the nozzle tip alter the exterior silhouette.
- **Remedies:**
  1. Add a nozzle brush wipe macro hook before camera approach (`before_pickup_gcode`).
  2. The detector cascade drops into Tier 2 (Relaxed) and Tier 3 (Super-Relaxed) focusing strictly on the inner dark orifice hole rather than the outer nozzle perimeter.

### Issue 1.3: Camera Thermal Drift & Lens Fogging
- **Symptom:** Successive measurements over 2 hours show progressive 0.05mm XY drift.
- **Root Cause:** Heat rising from heated bed warms the camera lens mount, causing thermal expansion. Hot nozzles parked over cold lenses cause condensation.
- **Remedies:**
  1. Never park stationary nozzles directly over the camera lens.
  2. Enforce pre-flight temperature limit: Nozzles must cool to $\le 100^\circ\text{C}$ before camera entry.

---

## 2. Mechanical & Motion Quirks

### Issue 2.1: Toolhead Latch Backlash / Hysteresis
- **Symptom:** Re-measuring the same toolhead yields inconsistent offsets ($> 0.03\text{mm}$ variance).
- **Root Cause:** Insufficient locking servo tension or worn kinematic coupling balls on the toolchanger carriage.
- **Remedies:**
  1. Inspect and tighten kinematic ball mounts.
  2. Configure macro to dock and re-seat the toolhead once before measuring.

### Issue 2.2: Cartographer Touch Temperature Sensitivity
- **Symptom:** Cartographer touch results drift significantly between cold and hot chamber runs.
- **Root Cause:** Eddy current coil inductances fluctuate with ambient chamber temperature if not fully heat-soaked.
- **Remedies:**
  1. Calibrate tools at a consistent chamber/bed temperature or ensure 10-minute heat soak prior to calibration.
  2. Use Cartographer touch-model thermal compensation tables.

---

## 3. Klipper, Toolchanger & Mainsail Integration Pitfalls (Anti-Conflict Architecture)

### Issue 3.1: Hardware Pin & Endstop Collision with `[tools_calibrate]`
- **Symptom:** Klipper fails on startup with `MCU endstop already registered` or pin collision errors.
- **Root Cause:** If `[tools_calibrate]` is already active in `printer.cfg`, its probe wrapper registers the physical switch pin with `query_endstops`. Having a second module register the same pin causes fatal conflicts (which forced Axiscope to crash on detection).
- **Remedies:**
  1. In Cartographer backend mode: Never configure or register the switch pin at all.
  2. In Switch backend mode: Check if `printer.lookup_object('tools_calibrate', None)` exists. If present, reuse the registered probe object instead of double-registering the hardware pin.

### Issue 3.2: Klipper Reactor Starvation & `Timer too close`
- **Symptom:** Printer halts with `MCU 'mcu' shutdown: Missed scheduling deadline` or `Timer too close`.
- **Root Cause:** Any synchronous blocking call (like an HTTP query or `time.sleep()`) in Klippy's reactor thread delays stepper pulses.
- **Remedies:**
  1. All HTTP requests to port 8090 must use strict, sub-second socket timeouts (`timeout=1.0s`).
  2. Polling waits must yield time back to the reactor loop via `reactor.pause(reactor.monotonic() + delay)` rather than Python `time.sleep()`.

### Issue 3.3: Configuration Corruption from Native `SAVE_CONFIG`
- **Symptom:** Using native `SAVE_CONFIG` creates duplicate `[probe]` or `[extruder]` blocks at the bottom of `printer.cfg`, breaking per-tool offset mappings.
- **Root Cause:** Klipper's built-in `SAVE_CONFIG` is not designed for multi-toolhead architectures.
- **Remedies:**
  1. Strictly avoid calling Klipper's native `SAVE_CONFIG`.
  2. Save tool offsets solely into an included `tool_offsets.cfg` file using atomic writes and timestamped backups (see [BACKUP.md](BACKUP.md)).

### Issue 3.4: WebRTC Stream Incompatibility with OpenCV
- **Symptom:** Vision service fails to decode frames when Mainsail uses WebRTC mode (`camera-streamer`).
- **Root Cause:** OpenCV cannot directly parse raw WebRTC streams without a browser WebRTC peer connection.
- **Remedies:**
  1. Always configure the vision grabber with Crowsnest's Snapshot URL (`http://localhost/webcam2/?action=snapshot` or `/webcam/?action=snapshot`), which delivers uncompressed JPEG frames on-demand regardless of whether the live frontend stream is WebRTC or MJPEG.

### Issue 3.5: Non-Sequential Tool Indices & Unmounted State
- **Symptom:** `IndexError` or crashes when changing tools on custom setups.
- **Root Cause:** Toolchangers do not always number tools sequentially (e.g. `[0, 1, 4]`), and the carriage may start in an unmounted state (`tool_number == -1`).
- **Remedies:**
  1. Always iterate over `toolchanger.tool_numbers`, never `range(n)`.
  2. Check `toolchanger.tool_number != -1` before starting. If unmounted, pickup reference tool `T0` first.
