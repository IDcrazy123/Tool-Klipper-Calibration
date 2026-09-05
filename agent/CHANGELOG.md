# CHANGELOG.md — Project Version Update History

> [!NOTE]
> Bản tiếng Việt có sẵn tại: [CHANGELOG.vi.md](CHANGELOG.vi.md)

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
- Bilingual documentation structure (`.md` for English, `.vi.md` for Vietnamese) across 14 specialized documents:
  - `AGENTS.md` / `AGENTS.vi.md`: Master orchestration and navigation index.
  - `PROJECT.md` / `PROJECT.vi.md`: Project mission, hardware specs, and scope.
  - `WORKFLOW.md` / `WORKFLOW.vi.md`: Operational workflow and finite state machine.
  - `SAFETY.md` / `SAFETY.vi.md`: 3-tier safe navigation and hardware protection.
  - `DECISIONS.md` / `DECISIONS.vi.md`: Architecture Decision Records (ADRs).
  - `DIRECTORY.md` / `DIRECTORY.vi.md`: Codebase tree and module boundaries.
  - `GIT_RULE.md` / `GIT_RULE.vi.md`: Single `main` branch policy and commit standards.
  - `LOGGING.md` / `LOGGING.vi.md`: Standardized error taxonomy (`ERR_xxx`) and telemetry format.
  - `KNOWN_ISSUES.md` / `KNOWN_ISSUES.vi.md`: Hardware/software edge cases and solutions.
  - `BACKUP.md` / `BACKUP.vi.md`: Configuration persistence, backups, and rollback.
  - `STYLE.md` / `STYLE.vi.md`: Python PEP 8, Jinja2 macros, and commenting rules.
  - `TODO.md` / `TODO.vi.md`: Phased work breakdown structure (WBS) and progress tracking.
  - `PROMPTS.md` / `PROMPTS.vi.md`: Standard agent interaction templates.
  - `CHANGELOG.md` / `CHANGELOG.vi.md`: Version update history.
- Root repository files: `README.md`, `README.vi.md`, and `.gitignore`.
- Remote origin configuration: `https://github.com/IDcrazy123/Tool-Klipper-Calibration.git` on branch `main`.
