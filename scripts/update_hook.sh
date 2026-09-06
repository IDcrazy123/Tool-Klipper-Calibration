#!/usr/bin/env bash
# ============================================================================
# Tool-Klipper-Calibration: Post-Update Hook for Moonraker Update Manager
# ============================================================================
# Automatically called by Moonraker after git pull & pip requirements update
# to restart tool_calibrator daemon in either user-service or system-service mode.
# ============================================================================
set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

echo -e "${BLUE}[tool_calibrator] Executing post-update service restart hook...${NC}"

# Check user-level service
if systemctl --user is-enabled tool_calibrator.service >/dev/null 2>&1 || systemctl --user is-active tool_calibrator.service >/dev/null 2>&1; then
    echo -e "${GREEN}[+] Restarting user-level tool_calibrator.service...${NC}"
    systemctl --user daemon-reload || true
    systemctl --user restart tool_calibrator.service
    echo -e "${GREEN}[✔] User service restarted successfully.${NC}"
    exit 0
fi

# Check system-level service
if systemctl is-enabled tool_calibrator.service >/dev/null 2>&1 || systemctl is-active tool_calibrator.service >/dev/null 2>&1; then
    echo -e "${GREEN}[+] Restarting system-level tool_calibrator.service...${NC}"
    if sudo -n true 2>/dev/null; then
        sudo systemctl daemon-reload || true
        sudo systemctl restart tool_calibrator.service
        echo -e "${GREEN}[✔] System service restarted successfully.${NC}"
    else
        echo -e "${YELLOW}[!] Sudo required to restart system-level service. Attempting systemctl restart...${NC}"
        sudo systemctl restart tool_calibrator.service || true
    fi
    exit 0
fi

echo -e "${YELLOW}[!] No active tool_calibrator service detected.${NC}"
exit 0
