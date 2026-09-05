# STYLE.md — Coding Standards & Commenting Guidelines

This document defines code style, static typing, and commenting conventions for the **Tool-Klipper-Calibration** codebase.

---

## 1. Python Code Standards (Klippy & Vision Service)

1. **PEP 8 Compliance:** 4 spaces indentation, no tabs. Maximum line length: 100 characters.
2. **Type Annotations:** All core functions, method signatures, and return values must include explicit Python type hints:
   ```python
   def compute_pixel_offset(
       origin_uv: tuple[float, float],
       detected_uv: tuple[float, float],
       mpp: float
   ) -> tuple[float, float]:
       """Calculates physical space offset (in mm) from camera image coordinates."""
       delta_u: float = detected_uv[0] - origin_uv[0]
       delta_v: float = detected_uv[1] - origin_uv[1]
       return (round(delta_u * mpp, 3), round(delta_v * mpp, 3))
   ```
3. **Defensive Error Handling:**
   - Never use naked `except Exception: pass`.
   - Always log caught exceptions with contextual metadata.
   - All network calls (HTTP requests to Vision Server) must enforce explicit timeouts ($\le 2.0\text{s}$).

---

## 2. Commenting Guidelines: Explain "Why", Not "What"

1. **Self-Documenting Code:** Write expressive variable and function names so comments do not restate obvious mechanics.
2. **Intent & Rationale:** Comments must explain non-obvious engineering decisions, hardware quirks, and mathematical constants:
   - *Good:* `# Lift Z vertically before lateral travel to clear the 22mm camera shroud`
   - *Bad:* `# Move Z axis up`
3. **Docstring Standard (Google / Sphinx Format):**
   ```python
   class SafeNavigator:
       """
       Manages 3-tier staging waypoints and safe kinematics transitions.

       Attributes:
           safe_z (float): Global clearance altitude in mm.
           approach_feedrate (float): Safe velocity limit during station entry.
       """
   ```

---

## 3. Klipper G-Code & Jinja2 Macro Conventions

1. **Safe Variable Defaults:** Always supply fallback defaults for Jinja parameter extraction:
   ```jinja
   {% set TARGET_TOOL = params.TOOL|default(0)|int %}
   {% set DRY_RUN = params.DRY_RUN|default(0)|int %}
   ```
2. **Action Telemetry:** Use `action_respond_info` rather than raw `M118` to ensure clean parsing across Mainsail and Fluidd frontends.
3. **State Checks Before Moves:** Every macro performing physical movement must verify axes are homed before issuing motion commands.
