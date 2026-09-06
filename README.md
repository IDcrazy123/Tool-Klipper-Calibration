# Tool-Klipper-Calibration

> [!NOTE]
> Bản tiếng Việt có sẵn tại: [README.vi.md](README.vi.md)

An automated spatial calibration engine for multi-toolhead 3D printers running Klipper Toolchanger, featuring Computer Vision XY alignment and multi-backend Z probing (Mechanical Switch & Cartographer Touch V4).

---

## 🎯 Core Project Objectives
- **Automated Vision-Based XY Alignment:** Detects nozzle orifice centers using an upward-facing camera with sub-pixel affine transformation solvers.
- **Dual Z-Offset Backends:** Supports classic **Physical Switch Endstops** and modern **Cartographer Touch Probes** (V4).
- **Dynamic Safe Staging Navigation:** Replaces rigid travel moves with a 3-tier safe waypoint model (`Safe_Z`, `Safe_Approach`, `Target`) and interactive jogging commands to prevent mechanical collisions.
- **Comprehensive Diagnostic Telemetry:** Emits standardized error codes (`ERR_xxx`) and actionable recovery advice directly to the Klipper console and Moonraker web UIs.
- **1-Click Full Automation:** Calibrates all toolheads sequentially and commits offsets to isolated config files via a single macro trigger.

---

## 📖 Architecture & Operational Workflow Guides
- 📘 **[Standard Operating Procedure & Macro Guide (Vietnamese)](docs/QUY_TRINH_VAN_HANH.md)** *(Comprehensive Step-by-Step SOP)*
- 🏛️ **[agent/AGENTS.md](agent/AGENTS.md)** *(Engineering blueprints & specifications)* | **[agent/AGENTS.vi.md](agent/AGENTS.vi.md)** *(Tiếng Việt)*

---

## 🛠️ Lineage & Reference Benchmarks
Built upon proven concepts synthesized and improved from three stable open-source projects:
1. **Axiscope-cartographer:** Multi-backend Z probing workflows and toolchanger lifecycle hooks.
2. **kTAMV:** Decoupled Klipper Client / Vision Daemon architecture preventing reactor timeouts (`Timer too close`).
3. **TAMV:** Multi-stage cascade OpenCV blob detection pipeline for high-reliability orifice tracking.

---

## 🚀 Git Strategy & Moonraker Auto-Updates
- **Remote Repository:** `https://github.com/IDcrazy123/Tool-Klipper-Calibration.git`
- **Branch Policy:** Strict single **`main`** branch (Trunk-based development). No side branches.
- Fully compatible with Moonraker Update Manager for 1-click updates in Mainsail and Fluidd.
