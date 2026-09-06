"""
Tool Offsets Configuration Module for Tool-Klipper-Calibration.

Registers the [tool_offsets] section in Klipper config so tool_offsets.cfg
can be safely included into printer.cfg without causing Klipper's
'Section ... is not a valid config section' error or colliding with
pre-existing [tool T*] headers.

Applies offsets dynamically to toolchanger tools on startup (klippy:ready)
and provides the APPLY_TOOL_OFFSETS gcode command.
"""

from typing import Dict, Any, Optional
import re
import logging

logger = logging.getLogger("tool_calibrator.tool_offsets")


class ToolOffsets:
    """
    Holds persistent calibrated tool offsets (e.g. t1_x, t1_y, t1_z)
    loaded from tool_offsets.cfg and applies them to toolchanger tools at runtime.
    """

    def __init__(self, config) -> None:
        self.printer = config.get_printer()
        self.name = config.get_name()  # "tool_offsets"
        self.offsets: Dict[str, float] = {}

        # Read all configured options in [tool_offsets]
        for opt in config.get_prefix_options(""):
            try:
                val = config.getfloat(opt, None)
                if val is not None:
                    self.offsets[opt.lower()] = val
            except Exception:
                pass

        logger.info(f"Loaded [tool_offsets] parameters: {self.offsets}")

        # Register ready event to apply offsets to tools on startup
        self.printer.register_event_handler("klippy:ready", self._handle_ready)

        # Register APPLY_TOOL_OFFSETS command
        self.gcode = self.printer.lookup_object("gcode", None)
        if self.gcode is not None:
            self.gcode.register_command(
                "APPLY_TOOL_OFFSETS",
                self.cmd_APPLY_TOOL_OFFSETS,
                desc="Apply calibrated offsets to toolchanger tools at runtime",
            )

    def _handle_ready(self) -> None:
        """Called when Klipper enters ready state: apply offsets to tools."""
        try:
            self.apply_tool_offsets()
        except Exception as e:
            logger.warning(f"Error applying tool offsets on ready: {e}")

    def parse_tool_offsets(self) -> Dict[int, Dict[str, float]]:
        """Parses self.offsets into structured {tool_num: {'x': float, 'y': float, 'z': float}}."""
        tools: Dict[int, Dict[str, float]] = {}
        pattern = re.compile(r"^t(\d+)_(x|y|z)$", re.IGNORECASE)
        for key, val in self.offsets.items():
            m = pattern.match(key)
            if m:
                tool_num = int(m.group(1))
                axis = m.group(2).lower()
                if tool_num not in tools:
                    tools[tool_num] = {}
                tools[tool_num][axis] = float(val)
        return tools

    def apply_tool_offsets(self, target_tool: Optional[int] = None) -> Dict[int, Dict[str, float]]:
        """
        Applies offsets to Klipper tool objects and toolchanger runtime.
        Returns the dictionary of applied tools and their offsets.
        """
        parsed = self.parse_tool_offsets()
        applied: Dict[int, Dict[str, float]] = {}
        tc = self.printer.lookup_object("toolchanger", None)
        gcode = self.gcode or self.printer.lookup_object("gcode", None)

        for t_num, axes in parsed.items():
            if target_tool is not None and t_num != target_tool:
                continue

            x = axes.get("x")
            y = axes.get("y")
            z = axes.get("z")

            # 1. Look for Tool object in printer: [tool 1], [tool T1], etc.
            tool_obj = None
            for lookup_name in [f"tool {t_num}", f"tool T{t_num}", f"tool t{t_num}"]:
                tool_obj = self.printer.lookup_object(lookup_name, None)
                if tool_obj is not None:
                    break

            if tool_obj is None and tc is not None:
                # Check inside toolchanger.tools dict
                tc_tools = getattr(tc, "tools", None)
                if isinstance(tc_tools, dict):
                    tool_obj = tc_tools.get(t_num) or tc_tools.get(f"T{t_num}")

            if tool_obj is not None:
                if hasattr(tool_obj, "set_offset") and callable(tool_obj.set_offset):
                    try:
                        tool_obj.set_offset(x=x, y=y, z=z)
                    except Exception as e:
                        logger.warning(f"Failed to call set_offset on tool {t_num}: {e}")
                if hasattr(tool_obj, "gcode_x_offset") and x is not None:
                    tool_obj.gcode_x_offset = x
                if hasattr(tool_obj, "gcode_y_offset") and y is not None:
                    tool_obj.gcode_y_offset = y
                if hasattr(tool_obj, "gcode_z_offset") and z is not None:
                    tool_obj.gcode_z_offset = z

            # 2. If toolchanger has set_tool_offset method
            if tc is not None and hasattr(tc, "set_tool_offset") and callable(tc.set_tool_offset):
                try:
                    tc.set_tool_offset(t_num, x=x, y=y, z=z)
                except Exception as e:
                    logger.debug(f"tc.set_tool_offset exception for T{t_num}: {e}")

            # 3. Call SET_TOOL_OFFSET command if registered in gcode
            if gcode is not None and hasattr(gcode, "commands") and "SET_TOOL_OFFSET" in gcode.commands:
                cmd_parts = [f"SET_TOOL_OFFSET TOOL={t_num}"]
                if x is not None:
                    cmd_parts.append(f"X={x:.4f}")
                if y is not None:
                    cmd_parts.append(f"Y={y:.4f}")
                if z is not None:
                    cmd_parts.append(f"Z={z:.4f}")
                cmd_str = " ".join(cmd_parts)
                try:
                    gcode.run_script_from_command(cmd_str)
                except Exception as e:
                    logger.warning(f"Failed to run script '{cmd_str}': {e}")

            applied[t_num] = axes
            logger.info(f"Applied offsets for Tool {t_num}: X={x} Y={y} Z={z}")

        return applied

    def set_results_and_apply(self, results: Dict[int, Dict[str, float]]) -> None:
        """Updates internal offsets from calibration results and applies them immediately."""
        for t_num, axes in results.items():
            for ax, val in axes.items():
                self.offsets[f"t{t_num}_{ax.lower()}"] = float(val)
        self.apply_tool_offsets()

    def cmd_APPLY_TOOL_OFFSETS(self, gcmd) -> None:
        """G-Code command to reload and apply offsets to toolchanger."""
        target_tool = gcmd.get_int("TOOL", None)
        applied = self.apply_tool_offsets(target_tool)
        if not applied:
            gcmd.respond_info("[tool_offsets] No offsets found to apply.")
            return

        for t_num, axes in applied.items():
            parts = [f"{k.upper()}={v:+.4f}mm" for k, v in axes.items()]
            gcmd.respond_info(f"[tool_offsets] Applied T{t_num}: {' '.join(parts)}")

    def get_status(self, eventtime: Optional[float] = None) -> Dict[str, Any]:
        """Provides state for Klipper macros and web UI query."""
        return dict(self.offsets)


def load_config(config):
    return ToolOffsets(config)

