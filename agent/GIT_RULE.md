# GIT_RULE.md — Git Strategy & Repository Management Rules

> [!NOTE]
> Bản tiếng Việt có sẵn tại: [GIT_RULE.vi.md](GIT_RULE.vi.md)

This document establishes the binding Git policy, commit standards, and update mechanisms for the **Tool-Klipper-Calibration** repository.

---

## 1. Remote Repository & Branching Policy

- **Remote Origin URL:** `https://github.com/IDcrazy123/Tool-Klipper-Calibration.git`
- **Strict Single-Branch Rule (Trunk-Based Development):**
  - All work takes place exclusively on the **`main`** branch.
  - **Branch creation is strictly prohibited** (`no develop`, `no feature/*`, `no bugfix/*` branches).
  - All feature additions, bugfixes, and documentation refinements commit directly to `main`.
  - Maintains a clean linear Git history, ensuring automated Moonraker pulls never fail due to branch divergence or detached HEAD states.

---

## 2. Commit Message Standards (Conventional Commits)

Every commit on `main` must follow this structure:

```text
<type>(<scope>): <concise description in English or Vietnamese>

[Optional body detailing the rationale and background]

Ref: #<task-id>
```

### Allowed `<type>` Values:
- `feat`: New feature or capability (e.g., `feat(vision): implement 3-tier cascade nozzle detector`)
- `fix`: Bug fix (e.g., `fix(klipper): prevent reactor timeout during camera sync`)
- `docs`: Documentation updates (e.g., `docs(git): define single main branch policy`)
- `refactor`: Code reorganization without altering observable behavior
- `test`: Adding or refining test scripts and mock data
- `chore`: Maintenance tasks (e.g., `.gitignore` or dependency updates)

---

## 3. Moonraker Auto-Update Integration

The repository is configured to integrate with Moonraker's Update Manager on user 3D printers:

```ini
[update_manager tool_klipper_calibration]
type: git_repo
path: ~/Tool-Klipper-Calibration
origin: https://github.com/IDcrazy123/Tool-Klipper-Calibration.git
primary_branch: main
is_system_service: True
managed_services: tool_calibrator_server
```

When users click "Update" in Mainsail or Fluidd, Moonraker fast-forwards `origin/main` and automatically restarts `tool_calibrator_server.service`.
