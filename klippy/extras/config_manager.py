"""
Configuration Persistence & Backup Manager for Tool-Klipper-Calibration.

Safely saves calibrated tool offsets into an isolated tool_offsets.cfg file
using atomic temporary writes and automatic timestamped backups.
"""

from typing import Dict, List, Optional, Any
import datetime
import glob
import logging
import os
import re
import shutil

logger = logging.getLogger("tool_calibrator.config_manager")


class ConfigManagerException(Exception):
    """Raised when configuration operations fail."""
    pass


class ConfigManager:
    """
    Manages offset reading, atomic persistence, and historical rollbacks.
    """

    def __init__(self, config_file_path: str = "~/printer_data/config/tool_calibrator/tool_offsets.cfg") -> None:
        self.config_path = os.path.expanduser(config_file_path)
        self.max_backups = 10
        self.config_dir = os.path.dirname(self.config_path)
        os.makedirs(self.config_dir, exist_ok=True)
        # Dedicated consolidated backup directory strictly inside tool_calibrator:
        # <tool_calibrator_dir>/backups/calibration_offsets
        self.backup_dir = os.path.join(self.config_dir, "backups", "calibration_offsets")

    @staticmethod
    def _backup_sort_key(filepath: str) -> tuple:
        """
        Extracts timestamp from backup filename for chronological ordering across directories,
        falling back to file modification time if timestamp pattern is absent.
        """
        filename = os.path.basename(filepath)
        match = re.search(r'\.calib_backup_(\d{8}_\d{6}(?:_\d+)?)', filename)
        if match:
            return (0, match.group(1))
        try:
            return (1, os.path.getmtime(filepath))
        except OSError:
            return (2, filepath)

    def create_backup(self) -> Optional[str]:
        """
        Creates a timestamped backup copy of tool_offsets.cfg inside the consolidated
        backup directory (tool_calibrator/backups/calibration_offsets).
        Automatically rotates and purges backups older than max_backups.
        """
        if not os.path.exists(self.config_path):
            return None

        os.makedirs(self.backup_dir, exist_ok=True)
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        filename = os.path.basename(self.config_path)
        backup_filename = os.path.join(self.backup_dir, f"{filename}.calib_backup_{timestamp}")
        try:
            shutil.copy2(self.config_path, backup_filename)
            logger.info(f"Created configuration backup: {backup_filename}")
            self._rotate_backups()
            return backup_filename
        except Exception as ex:
            logger.error(f"Failed to create configuration backup: {ex}")
            raise ConfigManagerException(f"Backup creation failed: {ex}")

    def _get_all_backups(self) -> List[str]:
        """Discovers all available backup files across modern and legacy directories, sorted oldest first, newest last."""
        fn = os.path.basename(self.config_path)
        pattern_new = os.path.join(self.backup_dir, f"{fn}.calib_backup_*")
        pattern_legacy_tc = os.path.join(self.config_dir, "tool_calibrator_backups", "calibration_offsets", f"{fn}.calib_backup_*")
        pattern_legacy_parent = os.path.join(os.path.dirname(self.config_dir), "tool_calibrator_backups", "calibration_offsets", f"{fn}.calib_backup_*")
        pattern_legacy_flat = f"{self.config_path}.calib_backup_*"
        all_backups = glob.glob(pattern_new) + glob.glob(pattern_legacy_tc) + glob.glob(pattern_legacy_parent) + glob.glob(pattern_legacy_flat)
        return sorted(set(all_backups), key=self._backup_sort_key)

    def _rotate_backups(self) -> None:
        """Keeps the most recent N backups in backup dir and deletes older ones."""
        backups = self._get_all_backups()
        if len(backups) > self.max_backups:
            to_remove = backups[:-self.max_backups]
            for old_backup in to_remove:
                try:
                    os.remove(old_backup)
                    logger.debug(f"Purged old backup: {old_backup}")
                except OSError:
                    pass

    def load_section(self, section_name: str) -> Dict[str, str]:
        """
        Loads key-value pairs from a specific section in tool_offsets.cfg.
        Returns an empty dict if the section or file does not exist.
        """
        if not os.path.exists(self.config_path):
            return {}

        clean_section = section_name.strip("[]")
        target_header = f"[{clean_section}]"
        results: Dict[str, str] = {}
        in_section = False

        with open(self.config_path, "r", encoding="utf-8") as f:
            for raw_line in f:
                line = raw_line.strip()
                if line.startswith("[") and line.endswith("]"):
                    in_section = (line == target_header)
                    continue

                if in_section and line and not line.startswith("#"):
                    if ":" in line:
                        k, v = line.split(":", 1)
                        results[k.strip()] = v.strip()
                    elif "=" in line:
                        k, v = line.split("=", 1)
                        results[k.strip()] = v.strip()

        return results

    def _read_lines(self) -> List[str]:
        """Reads existing lines from config file or generates initial default header."""
        if os.path.exists(self.config_path):
            with open(self.config_path, "r", encoding="utf-8") as f:
                return f.readlines()
        return [
            "# Tool-Klipper-Calibration Offsets File\n",
            "# Auto-generated by Tool-Klipper-Calibration\n",
            "# Include this in printer.cfg via: [include tool_offsets.cfg]\n\n"
        ]

    def _update_lines_with_section(self, lines: List[str], section_name: str, values: Dict[str, Any]) -> List[str]:
        """Updates or appends a [section_name] within lines and returns the updated lines."""
        lines = list(lines)
        clean_section = section_name.strip("[]")
        target_header = f"[{clean_section}]"
        section_start = -1
        section_end = len(lines)

        m_tool = re.match(r"^tool\s+(?:t)?(\d+)$", clean_section, re.IGNORECASE)
        tool_pattern = re.compile(rf"^\[tool\s+(?:t)?{m_tool.group(1)}\]$", re.IGNORECASE) if m_tool else None

        for i, raw_line in enumerate(lines):
            line = raw_line.strip()
            if (tool_pattern and tool_pattern.match(line)) or line == target_header:
                section_start = i
                target_header = line
            elif section_start != -1 and line.startswith("[") and line.endswith("]"):
                section_end = i
                break

        formatted_values = {}
        high_precision_keys = {"mpp", "matrix_a", "matrix_b", "matrix_c", "matrix_d", "matrix_tx", "matrix_ty"}
        for k, v in values.items():
            if v is None:
                continue
            if isinstance(v, float):
                if k in high_precision_keys or "matrix" in k:
                    formatted_values[k] = f"{v:.6f}"
                else:
                    formatted_values[k] = f"{v:.4f}"
            else:
                formatted_values[k] = str(v)

        if not formatted_values:
            return lines

        if section_start != -1:
            existing_keys = set()
            for idx in range(section_start + 1, section_end):
                line = lines[idx].strip()
                if not line or line.startswith("#"):
                    continue
                delimiter = ":" if ":" in line else "=" if "=" in line else None
                if delimiter:
                    found_key = line.split(delimiter, 1)[0].strip()
                    if found_key in formatted_values:
                        lines[idx] = f"{found_key}: {formatted_values[found_key]}\n"
                        existing_keys.add(found_key)

            missing_keys = [k for k in formatted_values if k not in existing_keys]
            if missing_keys:
                insert_lines = [f"{k}: {formatted_values[k]}\n" for k in missing_keys]
                lines[section_end:section_end] = insert_lines
        else:
            new_block = [f"\n{target_header}\n"]
            for k, val_str in formatted_values.items():
                new_block.append(f"{k}: {val_str}\n")
            lines.extend(new_block)

        return lines

    def _write_lines_atomically(self, lines: List[str]) -> None:
        """Atomically writes lines to self.config_path via temporary file replacement."""
        os.makedirs(self.config_dir, exist_ok=True)
        tmp_path = f"{self.config_path}.tmp"
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                f.writelines(lines)
                f.flush()
                os.fsync(f.fileno())

            os.replace(tmp_path, self.config_path)
        except Exception as ex:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
            raise ConfigManagerException(f"Failed to atomically write config: {ex}")

    def save_section(self, section_name: str, values: Dict[str, Any], create_backup: bool = True) -> None:
        """
        Atomically updates or creates any [section_name] with key-value pairs.
        Ignores None values to prevent writing invalid config lines.
        """
        if create_backup:
            self.create_backup()

        lines = self._read_lines()
        updated_lines = self._update_lines_with_section(lines, section_name, values)
        self._write_lines_atomically(updated_lines)
        logger.info(f"Successfully committed section [{section_name.strip('[]')}] to {self.config_path}")

    def save_tool_offsets(self, tool_number: int, offsets: Dict[str, float]) -> None:
        """
        Atomically saves calibrated tool offsets into the unified [tool_offsets] section
        using variable format (e.g. t1_x, t1_y, t1_z) to avoid duplicate [tool T*]
        section collisions with toolchanger tool definition files.
        If a legacy [tool N] section is already present in the file, updates it in-place.
        """
        self.save_all_tool_offsets({tool_number: offsets})

    def save_all_tool_offsets(self, results: Dict[int, Dict[str, float]]) -> None:
        """
        Atomically updates all tool offsets into config in a single commit,
        creating exactly one backup for the whole session.
        """
        self.create_backup()

        lines = self._read_lines()
        content = "".join(lines)

        legacy_tools = set()
        for t_num in results:
            pattern = re.compile(rf"^\[tool\s+(?:t)?{t_num}\]", re.IGNORECASE | re.MULTILINE)
            if pattern.search(content):
                legacy_tools.add(t_num)

        modern_offsets = {}
        for t_num, offsets in sorted(results.items()):
            if t_num in legacy_tools:
                legacy_dict = {}
                for axis, val in offsets.items():
                    if val is not None:
                        legacy_dict[f"gcode_{axis.lower()}_offset"] = val
                lines = self._update_lines_with_section(lines, f"tool {t_num}", legacy_dict)
            else:
                for axis, val in offsets.items():
                    if val is not None:
                        modern_offsets[f"t{t_num}_{axis.lower()}"] = val

        if modern_offsets:
            lines = self._update_lines_with_section(lines, "tool_offsets", modern_offsets)

        self._write_lines_atomically(lines)
        logger.info(f"Successfully committed all tool offsets to {self.config_path} in single atomic transaction")


    def _get_valid_backup_roots(self) -> List[str]:
        """Returns list of allowed canonical directory roots for configuration backups."""
        roots = [
            self.backup_dir,
            os.path.join(self.config_dir, "tool_calibrator", "backups", "calibration_offsets"),
            os.path.join(self.config_dir, "tool_calibrator_backups", "calibration_offsets"),
            os.path.join(os.path.dirname(self.config_dir), "tool_calibrator_backups", "calibration_offsets"),
        ]
        return [os.path.realpath(r) for r in roots if r]

    def rollback(self, target_backup: Optional[str] = None) -> str:
        """
        Restores a specific timestamped backup copy or the newest available backup.
        Validates backup location strictly within backup roots, validates backup filename pattern,
        validates INI content structure, creates a mandatory pre-rollback backup, and atomically
        writes to config_path.
        """
        backups = self._get_all_backups()
        valid_roots = self._get_valid_backup_roots()
        fn = os.path.basename(self.config_path)

        if target_backup:
            candidate = os.path.expanduser(target_backup)
            if not os.path.isabs(candidate):
                found = None
                for root in valid_roots:
                    test_path = os.path.join(root, os.path.basename(candidate))
                    if os.path.exists(test_path):
                        found = test_path
                        break
                if found:
                    candidate = found

            if not os.path.exists(candidate):
                raise ConfigManagerException(f"Specified backup file not found: {target_backup}")

            chosen_backup = os.path.abspath(candidate)
            chosen_real = os.path.realpath(candidate)

            # Security check 1: backup file must strictly reside inside an allowed backup root
            is_allowed = False
            for root in valid_roots:
                try:
                    if os.path.commonpath([root, chosen_real]) == root:
                        is_allowed = True
                        break
                except (ValueError, OSError):
                    pass
            if not is_allowed:
                raise ConfigManagerException(f"Unauthorized backup file location outside backup roots: {target_backup}")

            # Security check 2: backup filename must match managed backup naming pattern
            candidate_fn = os.path.basename(chosen_real)
            if not re.match(rf"^{re.escape(fn)}\.calib_backup_.*$", candidate_fn):
                raise ConfigManagerException(
                    f"Invalid backup filename '{candidate_fn}'. Managed backups must match pattern '{fn}.calib_backup_*'"
                )
        else:
            if not backups:
                raise ConfigManagerException("No historical backups found to restore.")
            chosen_backup = backups[-1]

        # Validate content structure before applying
        try:
            with open(chosen_backup, "r", encoding="utf-8") as f:
                content = f.read()
            if "\x00" in content:
                raise ConfigManagerException("Backup file contains null bytes (binary corruption).")
            lines = [l + "\n" for l in content.splitlines()]
            if content and not lines:
                lines = [content]

            # Content structure verification: must contain valid section header or TKC header
            has_valid_structure = False
            for raw_l in lines:
                stripped = raw_l.strip()
                if not stripped:
                    continue
                if stripped.startswith("#") and "Tool-Klipper-Calibration" in stripped:
                    has_valid_structure = True
                    break
                if stripped.startswith("[") and stripped.endswith("]"):
                    has_valid_structure = True
                    break
                if "=" in stripped or ":" in stripped:
                    has_valid_structure = True
                    break
            if not has_valid_structure:
                raise ConfigManagerException(
                    f"Backup file '{chosen_backup}' does not contain valid Klipper configuration structure."
                )
        except Exception as e:
            if isinstance(e, ConfigManagerException):
                raise
            raise ConfigManagerException(f"Failed to read/validate backup file {chosen_backup}: {e}")

        # Create mandatory pre-rollback backup of current config if it exists
        if os.path.exists(self.config_path):
            try:
                self.create_backup()
            except Exception as e:
                raise ConfigManagerException(f"Cannot perform rollback: failed to create pre-rollback backup: {e}")

        # Atomic replacement with fsync
        self._write_lines_atomically(lines)
        logger.info(f"Restored configuration from: {chosen_backup}")
        return chosen_backup

