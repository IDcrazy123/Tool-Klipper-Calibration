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

# Parse optional arguments
KEEP_DATA=false
for arg in "$@"; do
    case "${arg}" in
        --keep-data)
            KEEP_DATA=true
            ;;
        --help|-h)
            echo "Usage: ./scripts/uninstall.sh [--keep-data]"
            echo "  --keep-data : Preserves tool_offsets.cfg without archiving or removing"
            exit 0
            ;;
    esac
done

# 0. Check user permissions (Do NOT run as root/sudo directly)
if [ "${EUID}" -eq 0 ]; then
    echo -e "${RED}[ERR] Vui lòng KHÔNG chạy script này bằng sudo hoặc root!${NC}"
    echo -e "${YELLOW}      Hãy chạy bằng tài khoản người dùng thông thường (ví dụ: pi, btt, voron).${NC}"
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

SYSTEM_SERVICE_FILE="/etc/systemd/system/tool_calibrator.service"
USER_SERVICE_FILE="${HOME}/.config/systemd/user/tool_calibrator.service"
ASVC_FILE="${HOME}/printer_data/moonraker.asvc"

CONFIG_DIR="${HOME}/printer_data/config"
if [ ! -d "${CONFIG_DIR}" ] && [ -d "${HOME}/klipper_config" ]; then
    CONFIG_DIR="${HOME}/klipper_config"
fi
MOONRAKER_CONF="${CONFIG_DIR}/moonraker.conf"
OFFSETS_CFG="${CONFIG_DIR}/tool_offsets.cfg"

# 1. Stop and disable systemd service (both system and user mode if found)
echo -e "${BLUE}[1/5] Dừng và gỡ bỏ systemd service...${NC}"

# Check user-level service
if systemctl --user is-active --quiet tool_calibrator.service 2>/dev/null; then
    systemctl --user stop tool_calibrator.service || true
fi
if [ -f "${USER_SERVICE_FILE}" ]; then
    systemctl --user disable tool_calibrator.service 2>/dev/null || true
    rm -f "${USER_SERVICE_FILE}"
    systemctl --user daemon-reload 2>/dev/null || true
    echo -e "${GREEN}[✔] Đã gỡ bỏ user service: ${USER_SERVICE_FILE}${NC}"
fi

# Check system-level service
if systemctl is-active --quiet tool_calibrator.service 2>/dev/null; then
    sudo systemctl stop tool_calibrator.service || true
fi
if [ -f "${SYSTEM_SERVICE_FILE}" ]; then
    sudo systemctl disable tool_calibrator.service 2>/dev/null || true
    sudo rm -f "${SYSTEM_SERVICE_FILE}"
    sudo systemctl daemon-reload 2>/dev/null || true
    echo -e "${GREEN}[✔] Đã gỡ bỏ system service: ${SYSTEM_SERVICE_FILE}${NC}"
fi

# 2. Remove symlinks in Klipper extras safely (only TKC symlinks)
echo -e "${BLUE}[2/5] Gỡ bỏ liên kết Klipper extras symlinks...${NC}"
KLIPPY_EXTRAS="${KLIPPER_DIR}/klippy/extras"
if [ -d "${KLIPPY_EXTRAS}" ]; then
    for file in "tool_calibrator.py" "tool_calibrator_station.py" "tool_offsets.py" "safe_navigator.py" "config_manager.py"; do
        target="${KLIPPY_EXTRAS}/${file}"
        if [ -L "${target}" ]; then
            rm -f "${target}"
            echo -e "${GREEN}    Đã gỡ symlink ${file}${NC}"
        fi
    done

    # Remove only TKC symlinks in z_backends
    if [ -d "${KLIPPY_EXTRAS}/z_backends" ]; then
        for zb in "__init__.py" "base_z.py" "switch_backend.py" "cartographer_backend.py"; do
            target="${KLIPPY_EXTRAS}/z_backends/${zb}"
            if [ -L "${target}" ]; then
                rm -f "${target}"
                echo -e "${GREEN}    Đã gỡ symlink z_backends/${zb}${NC}"
            fi
        done
        # Only remove directory if it is completely empty
        rmdir "${KLIPPY_EXTRAS}/z_backends" 2>/dev/null || true
    fi
fi

# Remove Macro Bundle directory
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

# Archive tool_offsets.cfg if desired
if [ -f "${OFFSETS_CFG}" ]; then
    if [ "${KEEP_DATA}" = false ]; then
        TIMESTAMP="$(date +%Y%m%d_%H%M%S)"
        ARCHIVE_CFG="${OFFSETS_CFG}.archived_${TIMESTAMP}"
        mv "${OFFSETS_CFG}" "${ARCHIVE_CFG}"
        echo -e "${GREEN}[✔] Đã lưu trữ ${OFFSETS_CFG} thành ${ARCHIVE_CFG}${NC}"
    else
        echo -e "${YELLOW}[+] Bảo tồn ${OFFSETS_CFG} (--keep-data enabled).${NC}"
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
echo -e "Lưu ý cuối cùng dành cho bạn:"
echo -e "1. Mở file printer.cfg và xóa (hoặc comment dấu #) các dòng sau:"
echo -e "   # [include tool_calibrator/tool_calibrator_macros.cfg]"
echo -e "   # [include tool_calibrator/safe_staging_macros.cfg]"
echo -e "   # [include tool_offsets.cfg]"
echo -e "   # [tool_calibrator]"
echo -e "2. Khởi động lại Klipper: FIRMWARE_RESTART"
echo ""
