# KNOWN_ISSUES.md — Known Hardware/Software Edge Cases & Remedies

> [!NOTE]
> Bản tiếng Việt có sẵn tại: [KNOWN_ISSUES.vi.md](KNOWN_ISSUES.vi.md)

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
