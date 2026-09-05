# CHANGELOG.md — Project Version Update History

All notable changes to the **Tool-Klipper-Calibration** project are documented in this file, adhering to the [Semantic Versioning (SemVer)](https://semver.org/) format.

---

## [Unreleased]
### Planned
- Phase 2: Background Vision Service implementation with 3-tier OpenCV cascade.
- Phase 3: Klipper Core Extension implementation with 3-tier safe navigation and dual Z backends.
- Phase 4: G-code macro suite and interactive position teaching commands.
- Phase 5: Hardware benchmarking and repeatable precision verification.
- Phase 6: Automated Linux installation scripts and Moonraker packaging.

---

## [0.1.0] - 2026-09-05
### Added
- Initial project architecture and complete documentation suite in `agent/` folder.
- Modular documentation structure across 14 specialized documents in `agent/`:
  - `AGENTS.md` (English) & `AGENTS.vi.md` (Tiếng Việt): Master orchestration and navigation index.
  - `PROJECT.md`: Project mission, hardware specs, and scope.
  - `WORKFLOW.md`: Operational workflow and finite state machine.
  - `SAFETY.md`: 3-tier safe navigation and hardware protection.
  - `DECISIONS.md`: Architecture Decision Records (ADRs).
  - `DIRECTORY.md`: Codebase tree and module boundaries.
  - `GIT_RULE.md`: Single `main` branch policy and commit standards.
  - `LOGGING.md`: Standardized error taxonomy (`ERR_xxx`) and telemetry format.
  - `KNOWN_ISSUES.md`: Hardware/software edge cases and solutions.
  - `BACKUP.md`: Configuration persistence, backups, and rollback.
  - `STYLE.md`: Python PEP 8, Jinja2 macros, and commenting rules.
  - `TODO.md`: Phased work breakdown structure (WBS) and progress tracking.
  - `PROMPTS.md`: Standard agent interaction templates.
  - `CHANGELOG.md`: Version update history.
- Root repository files: `README.md` (English), `README.vi.md` (Tiếng Việt), and `.gitignore`.
- Remote origin configuration: `https://github.com/IDcrazy123/Tool-Klipper-Calibration.git` on branch `main`.
