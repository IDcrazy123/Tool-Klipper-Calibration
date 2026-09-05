# DIRECTORY.md — Project Structure & Module Boundaries

This document details the file tree, roles, and modular boundaries of the **Tool-Klipper-Calibration** project.

---

## 🌳 Directory Tree

```
Tool-Klipper-Calibration/
├── .gitignore                   # Ignores temp files, virtualenv, caches & local references
├── README.md                    # English landing page with quickstart and architecture links
├── README.vi.md                 # Vietnamese landing page
│
├── agent/                       # Agent & Developer Documentation Suite
│   ├── AGENTS.md                # Master coordination & sitemap (English)
│   ├── AGENTS.vi.md             # Hướng dẫn điều phối trung tâm (Tiếng Việt)
│   ├── BACKUP.md                # Configuration persistence, backups & rollback protocol
│   ├── CHANGELOG.md             # Semantic version update history
│   ├── DECISIONS.md             # Architecture Decision Records (ADR)
│   ├── DIRECTORY.md             # Codebase tree & boundaries (this document)
│   ├── GIT_RULE.md              # Git rules: single main branch only, commit conventions
│   ├── KNOWN_ISSUES.md          # Hardware/software edge cases & troubleshooting
│   ├── LOGGING.md               # Standard error codes (ERR_xxx) & telemetry format
│   ├── PROJECT.md               # Mission, technical requirements & hardware specs
│   ├── PROMPTS.md               # Standard prompts and interaction templates
│   ├── SAFETY.md                # 3-tier safe navigation & hardware protection rules
│   ├── STYLE.md                 # Python/Macro coding standards & commenting rules
│   ├── TODO.md                  # Phased work breakdown structure (WBS) & progress
│   └── WORKFLOW.md              # Operational flow & Finite State Machine (FSM)
│
├── klippy/                      # Klipper Extension (Runs inside Klippy Python environment)
│   └── extras/
│       ├── __init__.py
│       ├── tool_calibrator.py   # Primary Klipper module orchestrator
│       ├── safe_navigator.py    # 3-tier safe staging kinematics controller
│       ├── z_backends/          # Multi-backend probing adapters
│       │   ├── __init__.py
│       │   ├── base_z.py        # Abstract Base Class for Z probing
│       │   ├── switch_backend.py# Mechanical switch probe adapter
│       │   └── cartographer_backend.py # Cartographer V4 touch probe adapter
│       └── config_manager.py    # Offset persistence & backup manager
│
├── server/                      # Background Vision Service (Port: 8090)
│   ├── tool_calibrator_server.py# HTTP Server (FastAPI / Waitress) entrypoint
│   ├── stream_grabber.py        # Frame puller from Crowsnest snapshot endpoint
│   ├── nozzle_detector.py       # 3-tier OpenCV cascade blob detector
│   ├── affine_transform.py      # Millimeter-per-pixel (mpp) & affine solver
│   ├── visual_debugger.py       # Video preview stream with crosshairs overlay
│   └── requirements.txt         # Server Python dependencies (OpenCV, NumPy)
│
├── macros/                      # User-facing G-Code Macros
│   ├── tool_calibrator_macros.cfg   # Core calibration macros (CALIBRATE_TOOL_OFFSETS)
│   └── safe_staging_macros.cfg      # Interactive position teaching macros
│
├── scripts/                     # Installation & System Administration
│   ├── install.sh               # Automated installer (venv, systemd, dependencies)
│   ├── uninstall.sh             # Service removal and cleanup
│   └── tool_calibrator.service  # Linux systemd service unit definition
│
└── References/                  # Local reference repositories (git-ignored)
    ├── Axiscope-cartographer-main/
    ├── TAMV-master/
    └── kTAMV-main/
```

---

## 📦 Architectural Boundaries

### 1. `klippy/extras/`
- Runs directly inside Klipper's cooperative reactor.
- **Rule:** Strictly forbidden from importing heavy libraries (`cv2`, `torch`, `scipy`). Communicates with `server/` strictly via non-blocking HTTP requests with 2.0s timeouts.

### 2. `server/`
- Independent daemon bound to localhost port 8090.
- **Rule:** Never touches printer stepper hardware or Klipper memory spaces directly. Exposes REST JSON endpoints (`/health`, `/detect_nozzle`, `/calibrate_mpp`, `/preview`).

### 3. `macros/`
- User configuration layer.
- **Rule:** Never embeds complex math inside Jinja2. Macros act purely as parameter wrappers calling Klipper module commands.
