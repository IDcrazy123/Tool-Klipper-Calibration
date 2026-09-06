#!/usr/bin/env bash
# ============================================================================
# Tool-Klipper-Calibration: Service Restart & Diagnostic Tool
# ============================================================================
set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

echo -e "${BLUE}====================================================${NC}"
echo -e "${BLUE}    Tool-Klipper-Calibration Service Controller     ${NC}"
echo -e "${BLUE}====================================================${NC}"

ACTION="${1:-restart}"

restart_user() {
    echo -e "${GREEN}[+] Restarting user service: systemctl --user restart tool_calibrator.service${NC}"
    systemctl --user daemon-reload || true
    systemctl --user restart tool_calibrator.service
    systemctl --user status tool_calibrator.service --no-pager || true
}

restart_system() {
    echo -e "${GREEN}[+] Restarting system service: sudo systemctl restart tool_calibrator.service${NC}"
    sudo systemctl daemon-reload || true
    sudo systemctl restart tool_calibrator.service
    sudo systemctl status tool_calibrator.service --no-pager || true
}

if systemctl --user is-enabled tool_calibrator.service >/dev/null 2>&1 || systemctl --user is-active tool_calibrator.service >/dev/null 2>&1; then
    echo -e "${GREEN}[✔] Detected USER-LEVEL service mode.${NC}"
    restart_user
elif systemctl is-enabled tool_calibrator.service >/dev/null 2>&1 || systemctl is-active tool_calibrator.service >/dev/null 2>&1; then
    echo -e "${GREEN}[✔] Detected SYSTEM-LEVEL service mode.${NC}"
    restart_system
else
    echo -e "${YELLOW}[!] Neither user nor system tool_calibrator.service was found active.${NC}"
    echo -e "${YELLOW}    Attempting user service restart first...${NC}"
    restart_user
fi

echo -e "\n${BLUE}[+] Verifying daemon health on http://127.0.0.1:8090/health...${NC}"
sleep 1
HEALTH=$(curl -sS --max-time 3 http://127.0.0.1:8090/health 2>/dev/null || true)
if [ -n "${HEALTH}" ]; then
    echo -e "${GREEN}[✔] Daemon response:${NC}"
    echo "${HEALTH}"
else
    echo -e "${RED}[ERR] Daemon is not answering on http://127.0.0.1:8090/health${NC}"
fi
