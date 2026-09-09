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


def is_command_registered(gcode, cmd_name: str) -> bool:
    """Checks whether cmd_name is registered in Klipper gcode dispatcher handlers."""
    if gcode is None:
        return False
    cmd_upper = cmd_name.strip().split()[0].upper()
    registered = set()
    for attr in ("ready_gcode_handlers", "base_gcode_handlers", "gcode_handlers", "commands"):
        handlers = getattr(gcode, attr, None)
        if handlers and isinstance(handlers, dict):
            registered.update(k.upper() for k in handlers.keys())
        elif handlers and isinstance(handlers, (set, list, tuple)):
            registered.update(k.upper() for k in handlers)
    return cmd_upper in registered


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
            opt_lower = opt.lower()
            if re.match(r"^t\d+_[xyz]$", opt_lower):
                try:
                    val = config.getfloat(opt)
                    if val is not None:
                        self.offsets[opt_lower] = val
                except Exception as e:
                    err_fn = getattr(config, "error", None)
                    if callable(err_fn):
                        raise err_fn(f"Invalid float value for [{self.name}] option '{opt}': {e}")
                    raise
            else:
                try:
                    val = config.getfloat(opt, None)
                    if val is not None:
                        self.offsets[opt_lower] = val
                except Exception:
                    pass

        logger.info(f"Loaded [tool_offsets] parameters: {self.offsets}")

        # Register gcode command
        gcode = self.printer.lookup_object("gcode")
        gcode.register_command("APPLY_TOOL_OFFSETS", self.cmd_APPLY_TOOL_OFFSETS,
                               desc="Applies calibrated offsets to active toolchanger tools")

        # Register ready event to apply on startup
        self.printer.register_event_handler("klippy:ready", self._handle_ready)

    def _handle_ready(self) -> None:
        """Klippy ready hook to push configured offsets into active toolchanger tools."""
        self.apply_tool_offsets()

    def get_tool_offsets(self, tool_number: int) -> Dict[str, Optional[float]]:
        """Returns the dictionary of offsets (x, y, z) for the given tool."""
        return {
            "x": self.offsets.get(f"t{tool_number}_x"),
            "y": self.offsets.get(f"t{tool_number}_y"),
            "z": self.offsets.get(f"t{tool_number}_z"),
        }

    def get_all_tool_offsets(self) -> Dict[int, Dict[str, Optional[float]]]:
        """Groups offsets by tool number."""
        tools: Dict[int, Dict[str, Optional[float]]] = {}
        for key, val in self.offsets.items():
            m = re.match(r"^t(\d+)_([xyz])$", key)
            if m:
                t_num = int(m.group(1))
                axis = m.group(2)
                if t_num not in tools:
                    tools[t_num] = {"x": None, "y": None, "z": None}
                tools[t_num][axis] = val
        return tools

    def apply_tool_offsets(self, target_tool: Optional[int] = None) -> Dict[int, Dict[str, Optional[float]]]:
        """
        Pushes loaded offsets into Klipper's toolchanger tool objects.
        If target_tool is provided, only that specific tool's offsets are applied.
        """
        all_tools = self.get_all_tool_offsets()
        tc = self.printer.lookup_object("toolchanger", None)
        gcode = self.printer.lookup_object("gcode", None)

        applied = {}

        for t_num, axes in all_tools.items():
            if target_tool is not None and t_num != target_tool:
                continue

            x = axes.get("x")
            y = axes.get("y")
            z = axes.get("z")
            requested_axes = {axis for axis, val in [("x", x), ("y", y), ("z", z)] if val is not None}
            if not requested_axes:
                continue

            applied_axes = set()

            # 1. Look for Tool object in printer: [tool 1], [tool T1], etc.
            tool_obj = None
            for lookup_name in [f"tool {t_num}", f"tool T{t_num}", f"tool t{t_num}"]:
                tool_obj = self.printer.lookup_object(lookup_name, None)
                if tool_obj is not None:
                    break

            if tool_obj is None and tc is not None:
                tc_tools = getattr(tc, "tools", None)
                if isinstance(tc_tools, dict):
                    tool_obj = tc_tools.get(t_num) or tc_tools.get(f"T{t_num}")

            if tool_obj is not None:
                if hasattr(tool_obj, "set_offset") and callable(tool_obj.set_offset):
                    try:
                        tool_obj.set_offset(x=x, y=y, z=z)
                        applied_axes.update(requested_axes)
                    except Exception as e:
                        logger.warning(f"Failed to call set_offset on tool {t_num}: {e}")
                if hasattr(tool_obj, "gcode_x_offset") and x is not None:
                    tool_obj.gcode_x_offset = x
                    applied_axes.add("x")
                if hasattr(tool_obj, "gcode_y_offset") and y is not None:
                    tool_obj.gcode_y_offset = y
                    applied_axes.add("y")
                if hasattr(tool_obj, "gcode_z_offset") and z is not None:
                    tool_obj.gcode_z_offset = z
                    applied_axes.add("z")

            # 2. If toolchanger has set_tool_offset method
            if tc is not None and hasattr(tc, "set_tool_offset") and callable(tc.set_tool_offset):
                missing_from_tc = requested_axes - applied_axes
                if missing_from_tc:
                    try:
                        tc.set_tool_offset(t_num, x=x, y=y, z=z)
                        applied_axes.update(requested_axes)
                    except Exception as e:
                        logger.debug(f"tc.set_tool_offset exception for T{t_num}: {e}")

            # 3. Call SET_TOOL_PARAMETER (upstream viesturz standard) or fallback to SET_TOOL_OFFSET if registered
            if gcode is not None:
                if is_command_registered(gcode, "SET_TOOL_PARAMETER"):
                    for axis_name, axis_key, axis_val in [("gcode_x_offset", "x", x), ("gcode_y_offset", "y", y), ("gcode_z_offset", "z", z)]:
                        if axis_key in requested_axes and axis_val is not None:
                            try:
                                gcode.run_script_from_command(
                                    f"SET_TOOL_PARAMETER T={t_num} PARAMETER={axis_name} VALUE={axis_val:.6f}"
                                )
                                applied_axes.add(axis_key)
                            except Exception as e:
                                logger.warning(f"Failed to run SET_TOOL_PARAMETER for T{t_num} {axis_name}: {e}")
                elif is_command_registered(gcode, "SET_TOOL_OFFSET"):
                    cmd_parts = [f"SET_TOOL_OFFSET TOOL={t_num}"]
                    if "x" in requested_axes and x is not None:
                        cmd_parts.append(f"X={x:.4f}")
                    if "y" in requested_axes and y is not None:
                        cmd_parts.append(f"Y={y:.4f}")
                    if "z" in requested_axes and z is not None:
                        cmd_parts.append(f"Z={z:.4f}")
                    cmd_str = " ".join(cmd_parts)
                    try:
                        gcode.run_script_from_command(cmd_str)
                        applied_axes.update(requested_axes)
                    except Exception as e:
                        logger.warning(f"Failed to run script '{cmd_str}': {e}")

            if applied_axes >= requested_axes:
                applied[t_num] = axes
                logger.info(f"Applied offsets for Tool {t_num}: X={x} Y={y} Z={z}")
            else:
                logger.warning(
                    f"Could not fully apply offsets for Tool {t_num}: requested={requested_axes}, "
                    f"applied={applied_axes}. No corresponding Tool object, toolchanger method, or G-Code handler succeeded."
                )

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

