# SAFETY.md — Safe Navigation & Hardware Protection

---

## 1. First Principles of Machine Safety

In multi-tool 3D printers, mechanical crashes can bend toolhead mounts, shatter optical camera lenses, sever sensor wiring, or ruin expensive Cartographer eddy-current probes. Therefore, the calibration engine adheres to strict kinematic rules:

1. **No Direct Diagonal Slicing:** The toolhead is strictly prohibited from making diagonal linear travel moves across the bed between tool docks and calibration stations unless the nozzle is verified to be on or above the designated safe clearance plane (`Safe_Z`).
2. **"Lift-First, Descend-Last" Rule:**
   - **Departing any station/dock:** Z axis lifts vertically to `Safe_Z` before lateral XY motion begins.
   - **Approaching any station:** Lateral XY motion brings the toolhead to a complete standstill at the `Safe_Approach` staging coordinate before the Z axis descends.
3. **Approach Speed Throttling:** Lateral feedrates within calibration station zones are strictly capped at $\le 30\text{ mm/s}$ to permit emergency intervention.

---

## 2. Three-Tier Staging Coordinate Model

Every measurement station (Camera Station or Z-Switch Station) is managed by three distinct spatial tiers:

```
                      [ Safe_Z Clearance Plane (e.g., Z = 35.0 mm) ]
                        ^                                       ^
                        | (1. Vertical Lift)                    | (5. Vertical Lift)
                        |                                       |
[ Dock / Prior Pos ] ---+                                       +---> [ Next Target ]
                             \
                              \ (2. Lateral XY Transit)
                               v
                       [ Safe Approach Point ] (Staging Waypoint outside shroud)
                                |
                                | (3. Controlled Z Descent)
                                v
                       [ Target Station ] <---> [ Focal/Probe Apex ]
                             (4. Slow Final Creep)
```

| Staging Tier | Technical Purpose & Constraint |
| :--- | :--- |
| **`Safe_Z`** | A horizontal plane elevated at least 15mm–25mm above the highest mechanical obstacle on the machine bed (camera housing, docks, clips). |
| **`Safe_Approach_XY`** | A holding waypoint offset 20mm–30mm outside the station perimeter, allowing safe vertical descent without risking clipping the camera shroud. |
| **`Target_XY_Z`** | The precise optical focal coordinate of the camera lens or the mechanical apex of the Z-switch pin. |

---

## 3. Interactive Safe Position Teaching

Users are never required to manually calculate or guess coordinates. Instead, interactive teaching macros record real machine positions directly:

### G-Code Command Syntax:
```gcode
CALIBRATION_SET_SAFE_POS STATION=<CAMERA|Z_SWITCH> TYPE=<APPROACH|TARGET|SAFE_Z>
```

### Teaching Workflow:
1. **Teaching Camera Staging Point:**
   - Jog the toolhead to an open clearance zone directly in front of the camera housing.
   - Execute: `CALIBRATION_SET_SAFE_POS STATION=CAMERA TYPE=APPROACH`
2. **Teaching Camera Optical Center:**
   - Jog the nozzle directly over the lens until the orifice circle appears on screen.
   - Execute: `CALIBRATION_SET_SAFE_POS STATION=CAMERA TYPE=TARGET`
3. **Teaching Z-Switch Staging & Apex:**
   - Jog nozzle directly above the switch pin and record: `CALIBRATION_SET_SAFE_POS STATION=Z_SWITCH TYPE=TARGET`
4. **Teaching Safe Clearance Height:**
   - Raise the toolhead to an unobstructed height above all hardware and execute: `CALIBRATION_SET_SAFE_POS STATION=CAMERA TYPE=SAFE_Z`

Coordinates are immediately stored in memory and committed to the configuration file.

---

## 4. Software Fail-Safes & Motion Bounds

1. **Frame Boundary Clamping:**
   - The Vision Server continuously cross-checks calculated pixel offsets against the camera image resolution.
   - If a computed correction vector points outside the frame boundaries ($X < 0$, $X > \text{Width}$, $Y < 0$, $Y > \text{Height}$), motion is instantly suppressed and `ERR_CV_204` is triggered.
2. **Iteration Clamping & Backlash Detection:**
   - The nozzle centering routine allows a maximum of 5 iterative moves per toolhead.
   - If positional convergence is not achieved within 5 attempts, execution aborts with a mechanical backlash warning.
3. **Emergency E-Stop Integration:**
   - Full compatibility with Klipper's emergency stop mechanism (`M112`). Motors immediately de-energize upon trigger.
