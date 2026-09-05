# AGENTS.md — AI Agent & Developer Master Guidelines

> [!NOTE]
> Bản tiếng Việt có sẵn tại: [AGENTS.vi.md](AGENTS.vi.md)

Welcome to the **Tool-Klipper-Calibration** project. This is the master orchestration document. To ensure consistency, strict modularity, and avoid context window saturation as the project expands, all architectural specifications, rules, and workflows are partitioned into specialized documents inside the `agent/` folder. All Agents and Developers must review the corresponding documentation prior to executing any task.

---

## 🗺️ Documentation Sitemap

Every document is available in English (`.md`) and Vietnamese (`.vi.md`):

| Document (.md) | Vietnamese (.vi.md) | Primary Purpose & Scope |
| :--- | :--- | :--- |
| [PROJECT.md](PROJECT.md) | [PROJECT.vi.md](PROJECT.vi.md) | Project mission, technical specs & hardware requirements |
| [WORKFLOW.md](WORKFLOW.md) | [WORKFLOW.vi.md](WORKFLOW.vi.md) | Operational workflow, calibration loop & finite state machine |
| [SAFETY.md](SAFETY.md) | [SAFETY.vi.md](SAFETY.vi.md) | 3-tier safe navigation, anti-collision rules & interactive teaching |
| [DECISIONS.md](DECISIONS.md) | [DECISIONS.vi.md](DECISIONS.vi.md) | Architecture Decision Records (ADR) & reference comparisons |
| [DIRECTORY.md](DIRECTORY.md) | [DIRECTORY.vi.md](DIRECTORY.vi.md) | Project directory tree, file responsibilities & module boundaries |
| [GIT_RULE.md](GIT_RULE.md) | [GIT_RULE.vi.md](GIT_RULE.vi.md) | Git strategy: single `main` branch only, no branching, Moonraker update |
| [LOGGING.md](LOGGING.md) | [LOGGING.vi.md](LOGGING.vi.md) | Standardized error taxonomy (`ERR_xxx`), logging format & telemetry |
| [KNOWN_ISSUES.md](KNOWN_ISSUES.md) | [KNOWN_ISSUES.vi.md](KNOWN_ISSUES.vi.md) | Known hardware/software edge cases, root causes & remedies |
| [BACKUP.md](BACKUP.md) | [BACKUP.vi.md](BACKUP.vi.md) | Configuration persistence, timestamped backups & emergency rollback |
| [STYLE.md](STYLE.md) | [STYLE.vi.md](STYLE.vi.md) | Coding style guides (Python PEP 8, Jinja2 macros) & commenting rules |
| [TODO.md](TODO.md) | [TODO.vi.md](TODO.vi.md) | Work breakdown structure (WBS), phased milestones & task checklist |
| [PROMPTS.md](PROMPTS.md) | [PROMPTS.vi.md](PROMPTS.vi.md) | Standard prompts and interaction templates for agent development |
| [CHANGELOG.md](CHANGELOG.md) | [CHANGELOG.vi.md](CHANGELOG.vi.md) | Version history following Semantic Versioning (SemVer) |

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
