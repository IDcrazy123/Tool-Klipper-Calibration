# BACKUP.md — Configuration Persistence & Rollback Strategy

> [!NOTE]
> Bản tiếng Việt có sẵn tại: [BACKUP.vi.md](BACKUP.vi.md)

This document establishes configuration persistence protocols, automated backup naming conventions, and emergency rollback procedures for **Tool-Klipper-Calibration**.

---

## 1. File Isolation Architecture

To prevent catastrophic configuration corruption:
1. **Isolated Offset Storage:** All calibrated tool offsets are written to a dedicated configuration file:  
   `~/printer_data/config/tool_offsets.cfg`
2. **Master Inclusion:** The primary `printer.cfg` only requires a single include directive:  
   `[include tool_offsets.cfg]`
3. **Format Standard:** Each tool section contains explicitly generated offset keys:
   ```ini
   [tool 1]
   gcode_x_offset: 0.142
   gcode_y_offset: -0.085
   gcode_z_offset: 0.310
   ```

---

## 2. Automated Pre-Write Backup Protocol

Before committing new offset values to disk:
1. The configuration manager checks if `tool_offsets.cfg` exists.
2. If present, it creates a timestamped copy:
   ```text
   tool_offsets.cfg.calib_backup_YYYYMMDD_HHMMSS
   ```
   *Example:* `tool_offsets.cfg.calib_backup_20260905_075812`
3. **Atomic File Replacement:** New configuration data is written to a temporary scratch file (`tool_offsets.cfg.tmp`) and flushed to disk before being renamed to `tool_offsets.cfg`. This ensures zero data loss even during sudden power outages.
4. **Backup Retention:** The system retains the last 10 historical calibration backups, automatically purging older backups to conserve disk space.

---

## 3. Emergency Rollback Procedures

### Scenario: Undesirable Offset Calibration
If a calibration run was performed with a dirty nozzle or loose toolhead resulting in bad prints:

#### Method 1: Via Klipper Console / Macro
Execute the rollback command:
```gcode
CALIBRATION_ROLLBACK_OFFSETS
```
The system restores the immediate previous backup (`*.calib_backup_*`) and prompts for a `FIRMWARE_RESTART`.

#### Method 2: Manual Shell Restoration
Connect via SSH and run:
```bash
cd ~/printer_data/config
ls -lt tool_offsets.cfg.calib_backup_*
# Identify the desired backup timestamp, then copy over:
cp tool_offsets.cfg.calib_backup_20260905_075812 tool_offsets.cfg
```
Issue `FIRMWARE_RESTART` in Mainsail/Fluidd.
