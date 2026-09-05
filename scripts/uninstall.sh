#!/usr/bin/env bash
# ============================================================================
# Tool-Klipper-Calibration: Automated Uninstaller
# ============================================================================
set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

echo -e "${YELLOW}====================================================${NC}"
echo -e "${YELLOW}    Tool-Klipper-Calibration Uninstaller            ${NC}"
echo -e "${YELLOW}====================================================${NC}"

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
KLIPPER_DIR="${HOME}/klipper"
SERVICE_FILE="/etc/systemd/system/tool_calibrator.service"

# 1. Stop and disable systemd service
if systemctl is-active --quiet tool_calibrator.service 2>/dev/null; then
    echo -e "${BLUE}[1/4] Stopping tool_calibrator.service...${NC}"
    sudo systemctl stop tool_calibrator.service
fi

if [ -f "${SERVICE_FILE}" ]; then
    echo -e "${BLUE}[2/4] Removing systemd service unit...${NC}"
    sudo systemctl disable tool_calibrator.service 2>/dev/null || true
    sudo rm -f "${SERVICE_FILE}"
    sudo systemctl daemon-reload
fi

# 2. Remove symlinks in Klipper extras
echo -e "${BLUE}[3/4] Removing Klipper extras links...${NC}"
KLIPPY_EXTRAS="${KLIPPER_DIR}/klippy/extras"
if [ -d "${KLIPPY_EXTRAS}" ]; then
    for file in "tool_calibrator.py" "safe_navigator.py" "config_manager.py"; do
        rm -f "${KLIPPY_EXTRAS}/${file}"
    done
    rm -rf "${KLIPPY_EXTRAS}/z_backends"
fi

# 3. Clean virtualenv
echo -e "${BLUE}[4/4] Removing Python virtualenv...${NC}"
if [ -d "${REPO_DIR}/env" ]; then
    rm -rf "${REPO_DIR}/env"
fi

echo -e "\n${GREEN}====================================================${NC}"
echo -e "${GREEN}    Uninstall Complete. Please restart Klipper:     ${NC}"
echo -e "${GREEN}    sudo systemctl restart klipper                  ${NC}"
echo -e "${GREEN}====================================================${NC}"
