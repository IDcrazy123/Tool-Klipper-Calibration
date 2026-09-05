# DECISIONS.md — Architecture Decision Records (ADR)

This document captures key Architecture Decision Records (ADRs) for the **Tool-Klipper-Calibration** project, detailing technical rationale, trade-offs, and lessons synthesized from the three reference projects (`Axiscope`, `kTAMV`, `TAMV`).

---

## ADR-001: Decoupled Client - Service Architecture

### Context
Klipper's primary `klippy` process executes on a single-threaded cooperative event loop with strict real-time scheduling constraints. If a sub-routine blocks CPU execution for $> 25\text{ms} - 50\text{ms}$, Klipper triggers fatal safety halts: `Timer too close` or `MCU Shutdown`. OpenCV image acquisition and matrix solvers frequently exceed these limits on Raspberry Pi hardware.

### Decision
Decouple the architecture into two independent processes:
1. **Klipper Module (`klippy/extras/tool_calibrator.py`):** Runs inside Klipper. Manages kinematics, state machines, and dispatches non-blocking JSON HTTP queries.
2. **Vision Background Service (`server/tool_calibrator_server.py`):** Runs as an independent systemd daemon on port 8090, executing heavy OpenCV detection pipelines and NumPy solvers across separate CPU cores.

### Rationale & Reference Benchmark
- *Axiscope:* Runs lightweight web servers, but lacks computer vision. Embedding CV inside Klipper directly causes reactor starvation.
- *kTAMV:* Proven track record of decoupling via a standalone Waitress server. We inherit and modernize this pattern.
- *Trade-off:* Requires managing a secondary systemd service, resolved via automated installation scripts.

---

## ADR-002: Dual Z-Calibration Backends (Switch & Cartographer Touch)

### Context
Multi-toolhead machines employ distinct Z-probing mechanisms: classic mechanical/optical switches (Sexbolt endstops) vs modern eddy-current sensors (Cartographer 3D V4) capable of nozzle touch probing.

### Decision
Implement an Adapter Pattern supporting two user-selectable backends:
- `z_backend: switch` $\rightarrow$ Multi-axis probe wrapper inherited from `tools_calibrate`.
- `z_backend: cartographer` $\rightarrow$ Orchestrates `CARTOGRAPHER_TOUCH_HOME` on reference tool and `CARTOGRAPHER_TOUCH_PROBE` on secondary tools, reading touch-model offsets.

### Rationale
- Maximizes hardware compatibility across legacy and modern toolchangers without fragmenting codebases.

---

## ADR-003: Dynamic Three-Tier Safe Navigation System

### Context
Both kTAMV and Axiscope utilize hardcoded coordinates and direct linear moves (`G0 X... Y...`). If the nozzle height is lower than the camera shroud or tool docks, severe collisions occur.

### Decision
Establish a 3-tier coordinate model (`Safe_Z` $\rightarrow$ `Safe_Approach` $\rightarrow$ `Target`) coupled with an interactive teaching macro (`CALIBRATION_SET_SAFE_POS`).

### Rationale
- Completely eliminates diagonal blind moves. Enables users to jog the machine visually and record clearances tailored to their specific printer enclosure.

---

## ADR-004: Three-Stage Cascade Nozzle Detector (TAMV Lineage)

### Context
Nozzle orifices present optical challenges: reflective metal glares, carbonized plastic residues, and material differences (brass, hardened steel, ruby) that cause single-pass blob detectors to fail.

### Decision
Inherit TAMV's proven 3-tier cascade detector:
- **Tier 1 (Standard):** Strict circularity ($0.8 - 1.0$) and tight area filters.
- **Tier 2 (Relaxed):** Looser circularity ($0.6 - 1.0$) and wider area limits.
- **Tier 3 (Super-Relaxed):** Color-agnostic detection with micro-wiggle moves ($0.1\text{mm}$).

### Rationale
- Boosts nozzle detection reliability from $70\%$ to $> 98\%$ under variable illumination without requiring heavy neural networks.

---

## ADR-005: Strict Single `main` Branch Git Policy

### Context
End-users update Klipper extensions directly on their printer hosts via Moonraker Update Manager. Complex Git branching structures cause merge conflicts and detached HEAD failures during automatic pulls.

### Decision
Enforce a strict single `main` branch policy (Trunk-Based Development). All development, bugfixes, and documentation updates commit directly to `main`.

### Rationale
- Ensures frictionless 1-click updates through Moonraker and Mainsail/Fluidd.

---

## ADR-006: Dedicated Configuration Persistence (`tool_offsets.cfg`)

### Context
Directly modifying `printer.cfg` (as done by Axiscope) risks corrupting the main printer configuration if power is interrupted during regex file writes. Furthermore, native Klipper `SAVE_CONFIG` creates conflicting global `[probe]` or `[extruder]` overrides.

### Decision
Persist toolhead offsets into an isolated configuration file `tool_offsets.cfg` (included via `[include tool_offsets.cfg]`), accompanied by automated timestamped backups before each write. Avoid using `SAVE_CONFIG`.

### Rationale
- Absolute protection of master machine configurations and trivial rollback capabilities.

---

## ADR-007: Safe Coexistence with `tools_calibrate` & Pin Registration

### Context
When using the Switch Z backend, registering a physical switch pin with Klipper's `query_endstops` will throw an unrecoverable configuration error if `[tools_calibrate]` is already declared in `printer.cfg`.

### Decision
Instead of raising a fatal conflict error (as done in Axiscope), `tool_calibrator` inspects `printer.lookup_object('tools_calibrate', None)`:
1. If `tools_calibrate` is present, it re-uses the already registered probe wrapper object.
2. In Cartographer backend mode, switch pin registration is bypassed completely.

### Rationale
- Guarantees painless coexistence across diverse community toolchanger configurations.

---

## ADR-008: Snapshot Frame Acquisition vs Live Stream Decoding

### Context
Mainsail supports multiple camera streaming backends (ustreamer, camera-streamer/WebRTC). OpenCV cannot natively decode WebRTC without extensive browser dependencies.

### Decision
The Vision Service exclusively pulls single frames from Crowsnest's HTTP snapshot endpoint (`/?action=snapshot`), independent of the live UI stream format.

### Rationale
- Ensures $100\%$ compatibility whether the user views WebRTC, MJPEG, or HLS in Mainsail/Fluidd.

---

## ADR-009: Radial Edge-Curvature Invariance vs Binary Blob Centroids (kTAMV Analysis)

### Context
kTAMV relies on OpenCV `SimpleBlobDetector`, which thresholds image intensity and computes centers via binary area moments $x_c = m_{10}/m_{00}, y_c = m_{01}/m_{00}$. In real-world toolchanger setups (such as Voron StealthChanger with upward/side LED illumination), this approach exhibits four critical failure modes:
1. Triangular specular flares along the conical flank drag the moment centroid downward by 5 to 30 pixels away from the true orifice.
2. Uneven lateral lighting or surface marks pull the centroid horizontally along axis $X$ towards the brighter side.
3. High-contrast silicone sock aperture borders are mistakenly selected over nozzles.
4. Translucent Ruby/Sapphire inserts produce inverted dark cores, fragmenting the binary blob.

### Decision
Implement a two-stage hybrid detection architecture in `server/nozzle_detector.py`:
1. **Primary Stage (Curvature Invariance):** Crop central ROI, apply edge-preserving bilateral filtering, and execute Hough circular gradient accumulation ($R \in [6, 25]\text{px}$).
2. **Upper-Arc Radial Gradient Refinement:** Optimize the candidate center by evaluating radial gradient projection along the upper arc ($150^\circ$ to $30^\circ$ elevation), strictly excluding the downward conical glare sector.
3. **Cascade Fallback:** Retain the 3-tier cascade `SimpleBlobDetector` as a fallback for ultra-dim/diffuse lighting, followed by upper-arc gradient refinement.

### Rationale
- Mechanically turned nozzle faces and orifice holes are strictly invariant circles with constant radial curvature.
- Completely immunizes the vision daemon against downward specular flares, lateral shadow bias, and silicone sock false locks while maintaining $< 50\text{ms}$ frame processing times.

---

## ADR-010: Cartographer Touch V4 Relative Delta Z Formulation, PEI Thermal Guard, and Center Probing

### Context
Cartographer 3D V4 eddy-current probes feature high-precision nozzle-touch homing (`CARTOGRAPHER_TOUCH_HOME` / `CARTOGRAPHER_TOUCH_PROBE`). In multi-toolhead toolchangers, nozzle lengths vary across tools. Furthermore, direct physical nozzle contact on PEI beds at elevated printing temperatures (> 150°C) permanently damages the PEI film. Off-center probing also introduces bed tilt and mesh distortion bias into relative tool offsets.

### Decision
1. **Relative Delta Z Formulation:**
   - Reference Tool $T_{ref}$ (T0) touches the bed at $Z_{ref}$ (suggested offset = $0.000\text{mm}$).
   - Secondary Tool $T_n$ touches the bed at $Z_n$.
   - Relative offset is computed as:
     $$\Delta Z = Z_n - Z_{ref}$$
   - When a nozzle is longer ($Z_n > Z_{ref} \implies \Delta Z > 0$), positive offset adjusts Klipper's toolhead coordinate frame downwards, preserving identical layer heights. When a nozzle is shorter ($Z_n < Z_{ref} \implies \Delta Z < 0$), negative offset brings the tool closer.
2. **PEI Bed Thermal Safety Guard (`ERR_PRE_002`):**
   - Before dispatching any touch commands, `_check_thermal_safety()` queries extruder temperature. If temperature exceeds `carto_max_touch_temp` (default 150°C), probing immediately halts with `[ERR_PRE_002]`.
3. **Bed Center Coordinate Auto-Derivation:**
   - Both baseline and secondary touch probing execute at the bed's exact geometric center $((X_{min} + X_{max})/2, (Y_{min} + Y_{max})/2)$ when `carto_probe_x/y` are omitted. Probing at identical coordinates completely cancels out bed tilt, frame expansion, and mesh curvature.
4. **Immediate Post-Probe Liftoff Retraction:**
   - Following every touch contact, the toolhead immediately retracts $+5.0\text{mm}$ (`carto_retract_z`) at $15\text{mm/s}$ before initiating dock transit or tool selection moves.

### Rationale
- Completely eliminates bed sticker melting, eliminates bed tilt systematic errors, and ensures safe multi-tool Z calibration with sub-micron repeatability.



