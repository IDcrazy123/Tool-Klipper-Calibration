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
Directly modifying `printer.cfg` (as done by Axiscope) risks corrupting the main printer configuration if power is interrupted during regex file writes.

### Decision
Persist toolhead offsets into an isolated configuration file `tool_offsets.cfg` (included via `[include tool_offsets.cfg]`), accompanied by automated timestamped backups before each write.

### Rationale
- Absolute protection of master machine configurations and trivial rollback capabilities.
