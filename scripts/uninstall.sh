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

# 0. Check user permissions (Do NOT run as root/sudo directly)
if [ "${EUID}" -eq 0 ]; then
    echo -e "${RED}[ERR] Vui lòng KHÔNG chạy script này bằng sudo hoặc root!${NC}"
    echo -e "${YELLOW}      Hãy chạy bằng tài khoản người dùng thông thường (ví dụ: pi, btt).${NC}"
    exit 1
fi

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
KLIPPER_DIR="${KLIPPER_DIR:-${HOME}/klipper}"
if [ ! -d "${KLIPPER_DIR}" ] && [ -t 0 ]; then
    echo -e "${YELLOW}[!] Thư mục Klipper mặc định (${KLIPPER_DIR}) không tồn tại.${NC}"
    read -rp "Nhập đường dẫn đến thư mục Klipper của bạn: " USER_KLIPPER_DIR
    if [ -n "${USER_KLIPPER_DIR}" ]; then
        KLIPPER_DIR="${USER_KLIPPER_DIR}"
    fi
fi
SERVICE_FILE="/etc/systemd/system/tool_calibrator.service"
ASVC_FILE="${HOME}/printer_data/moonraker.asvc"


CONFIG_DIR="${HOME}/printer_data/config"
if [ ! -d "${CONFIG_DIR}" ] && [ -d "${HOME}/klipper_config" ]; then
    CONFIG_DIR="${HOME}/klipper_config"
fi
MOONRAKER_CONF="${CONFIG_DIR}/moonraker.conf"

# 1. Stop and disable systemd service
echo -e "${BLUE}[1/5] Dừng và gỡ bỏ systemd service (tool_calibrator.service)...${NC}"
if systemctl is-active --quiet tool_calibrator.service 2>/dev/null; then
    sudo systemctl stop tool_calibrator.service
fi

if [ -f "${SERVICE_FILE}" ]; then
    sudo systemctl disable tool_calibrator.service 2>/dev/null || true
    sudo rm -f "${SERVICE_FILE}"
    sudo systemctl daemon-reload
    echo -e "${GREEN}[✔] Đã xóa file service hệ thống.${NC}"
fi

# 2. Remove symlinks in Klipper extras
echo -e "${BLUE}[2/5] Gỡ bỏ liên kết Klipper extras symlinks...${NC}"
KLIPPY_EXTRAS="${KLIPPER_DIR}/klippy/extras"
if [ -d "${KLIPPY_EXTRAS}" ]; then
    for file in "tool_calibrator.py" "tool_calibrator_station.py" "tool_offsets.py" "safe_navigator.py" "config_manager.py"; do
        if [ -L "${KLIPPY_EXTRAS}/${file}" ] || [ -f "${KLIPPY_EXTRAS}/${file}" ]; then
            rm -f "${KLIPPY_EXTRAS}/${file}"
            echo -e "${GREEN}    Đã gỡ ${file}${NC}"
        fi
    done
    if [ -d "${KLIPPY_EXTRAS}/z_backends" ]; then
        rm -rf "${KLIPPY_EXTRAS}/z_backends"
        echo -e "${GREEN}    Đã gỡ thư mục z_backends/${NC}"
    fi
fi

# 2b. Remove Macro Bundle directory
if [ -d "${CONFIG_DIR}/tool_calibrator" ]; then
    rm -rf "${CONFIG_DIR}/tool_calibrator"
    echo -e "${GREEN}[✔] Đã gỡ thư mục macro ${CONFIG_DIR}/tool_calibrator/${NC}"
fi

# 3. Remove from moonraker.asvc
echo -e "${BLUE}[3/5] Dọn dẹp danh sách dịch vụ Moonraker (moonraker.asvc)...${NC}"
if [ -f "${ASVC_FILE}" ]; then
    sed -i '/^tool_calibrator$/d' "${ASVC_FILE}"
    echo -e "${GREEN}[✔] Đã xóa tool_calibrator khỏi ${ASVC_FILE}${NC}"
fi

# 4. Remove update manager from moonraker.conf
if [ -f "${MOONRAKER_CONF}" ]; then
    if grep -q "\[update_manager tool_calibrator\]" "${MOONRAKER_CONF}"; then
        echo -e "${BLUE}[4/5] Gỡ cấu hình update_manager khỏi moonraker.conf...${NC}"
        TIMESTAMP="$(date +%Y%m%d_%H%M%S)"
        BACKUP_FILE="${MOONRAKER_CONF}.uninstall.bak_${TIMESTAMP}"
        cp "${MOONRAKER_CONF}" "${BACKUP_FILE}"
        # Xóa khối update_manager tool_calibrator bằng python an toàn
        python3 -c "
with open('${MOONRAKER_CONF}', 'r') as f:
    lines = f.readlines()
out = []
skip = False
for line in lines:
    if line.strip() == '[update_manager tool_calibrator]':
        skip = True
        continue
    if skip and line.startswith('['):
        skip = False
    if not skip:
        out.append(line)
with open('${MOONRAKER_CONF}', 'w') as f:
    f.writelines(out)
"
        echo -e "${GREEN}[✔] Đã dọn khối [update_manager tool_calibrator] trong moonraker.conf (Sao lưu: ${BACKUP_FILE}).${NC}"
    fi
fi

# 5. Clean virtualenv
echo -e "${BLUE}[5/5] Xóa môi trường ảo Python virtualenv (env/)...${NC}"
if [ -d "${REPO_DIR}/env" ]; then
    rm -rf "${REPO_DIR}/env"
    echo -e "${GREEN}[✔] Đã xóa thư mục env/${NC}"
fi

# Restart services
if systemctl is-active --quiet moonraker.service 2>/dev/null; then
    echo -e "${BLUE}[+] Khởi động lại Moonraker...${NC}"
    sudo systemctl restart moonraker.service || true
fi
if systemctl is-active --quiet klipper.service 2>/dev/null; then
    echo -e "${BLUE}[+] Khởi động lại Klipper...${NC}"
    sudo systemctl restart klipper.service || true
fi

echo -e "\n${GREEN}====================================================${NC}"
echo -e "${GREEN}    GỠ CÀI ĐẶT THÀNH CÔNG VÀ SẠCH SẼ!              ${NC}"
echo -e "${GREEN}====================================================${NC}"
echo -e "Lưu ý: Nếu có khai báo macro trong printer.cfg ([include tool_calibrator/tool_calibrator_macros.cfg]),"
echo -e "vui lòng xóa hoặc comment dòng đó và lưu lại file printer.cfg."
echo ""
