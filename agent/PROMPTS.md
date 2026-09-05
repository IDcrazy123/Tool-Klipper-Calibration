# PROMPTS.md — Standard Agent Interaction Templates & Prompts

This document provides standardized prompt templates for human developers when instructing AI agents across different implementation phases of **Tool-Klipper-Calibration**.

---

## 1. Phase-Specific Implementation Prompts

### Prompt for Phase 2: Vision Background Service
```text
Task: Implement Phase 2 - Vision Background Service in server/
Please read agent/PROJECT.md, agent/WORKFLOW.md, agent/DECISIONS.md (ADR-001, ADR-004), and agent/STYLE.md.
Requirements:
1. Implement stream_grabber.py to pull MJPEG snapshots from Crowsnest.
2. Build nozzle_detector.py with the 3-tier cascade (Standard, Relaxed, Super-Relaxed) derived from TAMV.
3. Build affine_transform.py for star-pattern mm/pixel calculations.
4. Expose REST JSON endpoints in tool_calibrator_server.py on port 8090.
5. Adhere to single main branch git rules and update agent/TODO.md upon completion.
```

### Prompt for Phase 3: Klipper Core Extension
```text
Task: Implement Phase 3 - Klipper Extension in klippy/extras/
Please read agent/SAFETY.md, agent/DECISIONS.md (ADR-002, ADR-003, ADR-006), and agent/LOGGING.md.
Requirements:
1. Implement tool_calibrator.py with G-Code registrations.
2. Build safe_navigator.py enforcing 3-tier waypoints (Safe_Z, Safe_Approach, Target).
3. Implement z_backends/switch_backend.py and z_backends/cartographer_backend.py.
4. Implement config_manager.py with atomic file writing and timestamped backups.
5. Ensure non-blocking HTTP requests to port 8090 with 2.0s timeouts.
```

### Prompt for Phase 4: Macro Suite Development
```text
Task: Implement Phase 4 - User G-Code Macros in macros/
Please read agent/WORKFLOW.md and agent/STYLE.md (Section 3).
Requirements:
1. Build CALIBRATE_TOOL_OFFSETS macro supporting TARGET_TOOL and DRY_RUN options.
2. Build CALIBRATION_SET_SAFE_POS macro for interactive waypoint teaching.
3. Integrate start, finish, and toolchange hooks.
4. Add CALIBRATION_ROLLBACK_OFFSETS emergency restore macro.
```

---

## 2. Bug Fixing & Maintenance Prompts

### Bug Investigation Prompt
```text
Task: Investigate issue <ISSUE_DESCRIPTION>
Please check agent/KNOWN_ISSUES.md, agent/LOGGING.md, and inspect the codebase.
Rules:
1. Identify root cause before proposing edits.
2. Explain the rationale in code comments (Why, not What).
3. Ensure no regressions against the single-branch git policy.
```
