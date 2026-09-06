#!/usr/bin/env bash
# ============================================================================
# Tool-Klipper-Calibration: Automated Linux / Raspberry Pi Installer
# ============================================================================
set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

echo -e "${BLUE}====================================================${NC}"
echo -e "${BLUE}    Tool-Klipper-Calibration Automated Installer    ${NC}"
echo -e "${BLUE}====================================================${NC}"

# 1. Resolve paths
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CURRENT_USER="$(id -un)"
VENV_DIR="${REPO_DIR}/env"

KLIPPER_DIR="${HOME}/klipper"
if [ ! -d "${KLIPPER_DIR}" ]; then
    echo -e "${YELLOW}[!] Default Klipper directory ${KLIPPER_DIR} not found.${NC}"
    read -rp "Enter path to your klipper directory: " USER_KLIPPER_DIR
    KLIPPER_DIR="${USER_KLIPPER_DIR}"
fi

if [ ! -d "${KLIPPER_DIR}/klippy/extras" ]; then
    echo -e "${RED}[ERR] Invalid Klipper directory: ${KLIPPER_DIR}/klippy/extras does not exist.${NC}"
    exit 1
fi

echo -e "${GREEN}[+] Repo directory:    ${REPO_DIR}${NC}"
echo -e "${GREEN}[+] Klipper directory: ${KLIPPER_DIR}${NC}"
echo -e "${GREEN}[+] Target venv:       ${VENV_DIR}${NC}"

# 2. System dependencies (Python3, venv, libgl1 for OpenCV headless if needed)
echo -e "\n${BLUE}[1/5] Checking system packages...${NC}"
MISSING_PKGS=()
for pkg in python3 python3-pip python3-venv; do
    if ! dpkg -s "${pkg}" >/dev/null 2>&1; then
        MISSING_PKGS+=("${pkg}")
    fi
done

if [ ${#MISSING_PKGS[@]} -gt 0 ]; then
    echo -e "${YELLOW}[+] Installing missing system packages: ${MISSING_PKGS[*]}${NC}"
    sudo apt-get update
    sudo apt-get install -y "${MISSING_PKGS[@]}"
fi

# 3. Setup Virtualenv & Dependencies
echo -e "\n${BLUE}[2/5] Creating Python virtual environment...${NC}"
if [ ! -d "${VENV_DIR}" ]; then
    python3 -m venv "${VENV_DIR}"
fi

echo -e "${GREEN}[+] Installing Python requirements from server/requirements.txt...${NC}"
"${VENV_DIR}/bin/pip" install --upgrade pip
"${VENV_DIR}/bin/pip" install -r "${REPO_DIR}/server/requirements.txt"

# 4. Link Klipper Extras
echo -e "\n${BLUE}[3/5] Linking Klipper extension modules...${NC}"
KLIPPY_EXTRAS="${KLIPPER_DIR}/klippy/extras"

# Ensure z_backends directory exists in Klipper extras if linking submodules
mkdir -p "${KLIPPY_EXTRAS}/z_backends"

for file in "tool_calibrator.py" "tool_calibrator_station.py" "safe_navigator.py" "config_manager.py"; do
    TARGET="${KLIPPY_EXTRAS}/${file}"
    SOURCE="${REPO_DIR}/klippy/extras/${file}"
    if [ -L "${TARGET}" ] || [ -f "${TARGET}" ]; then
        rm -f "${TARGET}"
    fi
    ln -s "${SOURCE}" "${TARGET}"
    echo -e "${GREEN}    Linked ${file} -> ${KLIPPY_EXTRAS}/${NC}"
done

for zb_file in "base_z.py" "switch_backend.py" "cartographer_backend.py"; do
    TARGET="${KLIPPY_EXTRAS}/z_backends/${zb_file}"
    SOURCE="${REPO_DIR}/klippy/extras/z_backends/${zb_file}"
    if [ -L "${TARGET}" ] || [ -f "${TARGET}" ]; then
        rm -f "${TARGET}"
    fi
    ln -s "${SOURCE}" "${TARGET}"
    echo -e "${GREEN}    Linked z_backends/${zb_file} -> ${KLIPPY_EXTRAS}/z_backends/${NC}"
done

# 5. Install and start systemd service
echo -e "\n${BLUE}[4/5] Configuring systemd background service...${NC}"
SERVICE_FILE="/etc/systemd/system/tool_calibrator.service"
TEMP_SERVICE="/tmp/tool_calibrator.service"

sed -e "s|%USER%|${CURRENT_USER}|g" \
    -e "s|%REPO_DIR%|${REPO_DIR}|g" \
    -e "s|%VENV_DIR%|${VENV_DIR}|g" \
    "${REPO_DIR}/scripts/tool_calibrator.service" > "${TEMP_SERVICE}"

sudo mv "${TEMP_SERVICE}" "${SERVICE_FILE}"
sudo chown root:root "${SERVICE_FILE}"
sudo chmod 644 "${SERVICE_FILE}"

echo -e "${GREEN}[+] Reloading systemd and enabling service...${NC}"
sudo systemctl daemon-reload
sudo systemctl enable tool_calibrator.service
sudo systemctl restart tool_calibrator.service

# 6. Verify Service Health
echo -e "\n${BLUE}[5/5] Checking service status...${NC}"
sleep 2
if curl -s http://127.0.0.1:8090/health >/dev/null; then
    echo -e "${GREEN}[✔] Tool Calibrator Vision Daemon is RUNNING on http://127.0.0.1:8090${NC}"
else
    echo -e "${YELLOW}[!] Warning: Service started, but health endpoint is not yet responding.${NC}"
    echo -e "    Check logs with: journalctl -u tool_calibrator.service -n 50"
fi

echo -e "\n${GREEN}====================================================${NC}"
echo -e "${GREEN}    Installation Complete!                          ${NC}"
echo -e "${GREEN}====================================================${NC}"
echo -e "Next Steps:"
echo -e "1. Restart Klipper: sudo systemctl restart klipper"
echo -e "2. Add the update manager entry to your moonraker.conf (see scripts/moonraker_update.cfg)"
echo -e "3. Include macros in printer.cfg: [include macros/tool_calibrator_macros.cfg]"
