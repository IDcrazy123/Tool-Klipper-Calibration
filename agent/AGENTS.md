# AGENTS.md — AI Agent & Developer Master Guidelines

> [!NOTE]
> Bản tiếng Việt có sẵn tại: [AGENTS.vi.md](AGENTS.vi.md)

Welcome to the **Tool-Klipper-Calibration** project. This is the master orchestration document. To ensure consistency, strict modularity, and avoid context window saturation as the project expands, all architectural specifications, rules, and workflows are partitioned into specialized documents inside the `agent/` folder. All Agents and Developers must review the corresponding documentation prior to executing any task.

---

## 🗺️ Documentation Sitemap

| Specialized Document | Primary Purpose & Scope |
| :--- | :--- |
| [PROJECT.md](PROJECT.md) | Project mission, technical specifications & hardware requirements |
| [WORKFLOW.md](WORKFLOW.md) | Operational workflow, calibration loop & finite state machine (FSM) |
| [SAFETY.md](SAFETY.md) | 3-tier safe navigation, anti-collision rules & interactive teaching |
| [DECISIONS.md](DECISIONS.md) | Architecture Decision Records (ADR) & reference project benchmarks |
| [DIRECTORY.md](DIRECTORY.md) | Project directory tree, file responsibilities & module boundaries |
| [GIT_RULE.md](GIT_RULE.md) | Git strategy: single `main` branch only, no branching, Moonraker update |
| [LOGGING.md](LOGGING.md) | Standardized error taxonomy (`ERR_xxx`), logging format & telemetry |
| [KNOWN_ISSUES.md](KNOWN_ISSUES.md) | Known hardware/software edge cases, root causes & remedies |
| [BACKUP.md](BACKUP.md) | Configuration persistence, timestamped backups & emergency rollback |
| [STYLE.md](STYLE.md) | Coding style guides (Python PEP 8, Jinja2 macros) & commenting rules |
| [TODO.md](TODO.md) | Work breakdown structure (WBS), phased milestones & task checklist |
| [PROMPTS.md](PROMPTS.md) | Standard prompts and interaction templates for agent development |
| [CHANGELOG.md](CHANGELOG.md) | Version history following Semantic Versioning (SemVer) |

---

## 🤖 Agent Core Operational Rules

1. **Research First, Code Second:** Always read the corresponding specialized document before creating or modifying code.
2. **Strict Separation of Concerns:**
   - Heavy OpenCV image processing & mathematical calculations $\rightarrow$ Vision Service (`server/`).
   - Kinematics control, gcode macros, Z probing $\rightarrow$ Klipper Extension (`klippy/extras/`).
   - User command interfaces & custom hooks $\rightarrow$ Macro configuration files (`macros/`).
3. **Strict Single-Branch Git Policy:** Commit directly to **`main`** only; never create side branches (see [GIT_RULE.md](GIT_RULE.md)).
4. **Mechanical Safety First:** Never execute diagonal travel moves through obstacles; always transit through safe staging positions (`Safe_Z`, `Safe_Approach`) (see [SAFETY.md](SAFETY.md)).
5. **Continuous State Updates:** Always update [TODO.md](TODO.md) and [CHANGELOG.md](CHANGELOG.md) upon task completion.
