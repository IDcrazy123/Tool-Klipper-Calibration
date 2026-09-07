#!/usr/bin/env bash
# ============================================================================
# Tool-Klipper-Calibration: Automated Safe Uninstaller
# ============================================================================
set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m'

echo -e "${YELLOW}====================================================${NC}"
echo -e "${YELLOW}    Tool-Klipper-Calibration Safe Uninstaller       ${NC}"
echo -e "${YELLOW}====================================================${NC}"

# Parse optional arguments
KEEP_DATA=false
CONFIG_SUBDIR=""
PURGE_REPO=false
PURGE_CONFIG=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        --keep-data)
            KEEP_DATA=true
            shift
            ;;
        --purge-repo)
            PURGE_REPO=true
            shift
            ;;
        --purge-config)
            PURGE_CONFIG=true
            shift
            ;;
        --config-subdir)
            CONFIG_SUBDIR="$2"
            shift 2
            ;;
        --config-subdir=*)
            CONFIG_SUBDIR="${1#*=}"
            shift
            ;;
        --help|-h)
            echo "Usage: ./scripts/uninstall.sh [--keep-data] [--purge-repo] [--purge-config] [--config-subdir <subdir>]"
            echo "  --keep-data              : Preserves tool_offsets.cfg without archiving or removing"
            echo "  --purge-repo             : Removes cloned repository directory after uninstallation"
            echo "  --purge-config           : Safely creates a timestamped backup before purging TKC config files and archives"
            echo "  --config-subdir <subdir> : Machine-specific configuration subdirectory (e.g. Printer-Setup)"
            exit 0
            ;;
        *)
            echo -e "${YELLOW}[!] Unknown option: $1${NC}"
            shift
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
PRINTER_CFG="${CONFIG_DIR}/printer.cfg"

# Try to discover installation paths from persistent manifest
TARGET_CONFIG_DIR="${CONFIG_DIR}"
if [ -n "${CONFIG_SUBDIR}" ]; then
    CLEAN_SUBDIR="$(printf '%s' "${CONFIG_SUBDIR}" | tr -d '\r' | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')"
    if printf '%s' "${CLEAN_SUBDIR}" | grep -q '[[:cntrl:]]'; then
        echo -e "${RED}[ERR] Giá trị --config-subdir chứa ký tự điều khiển không hợp lệ!${NC}"
        exit 1
    fi
    if [[ "${CLEAN_SUBDIR}" == /* ]] || [[ "${CLEAN_SUBDIR}" =~ \.\. ]] || [[ "${CLEAN_SUBDIR}" =~ // ]]; then
        echo -e "${RED}[ERR] Giá trị --config-subdir không hợp lệ!${NC}"
        exit 1
    fi
    CONFIG_SUBDIR="${CLEAN_SUBDIR}"
    TARGET_CONFIG_DIR="${CONFIG_DIR}/${CONFIG_SUBDIR}"
fi

PERSISTENT_MANIFEST="${TARGET_CONFIG_DIR}/.tool_calibrator_manifest.json"
if [ ! -f "${PERSISTENT_MANIFEST}" ] && [ -f "${CONFIG_DIR}/.tool_calibrator_manifest.json" ]; then
    PERSISTENT_MANIFEST="${CONFIG_DIR}/.tool_calibrator_manifest.json"
fi

if [ -f "${PERSISTENT_MANIFEST}" ]; then
    echo -e "${GREEN}[+] Đã tìm thấy manifest cài đặt: ${PERSISTENT_MANIFEST}${NC}"
    DISCOVERED_SUBDIR=$(python3 -c "
import json
try:
    with open('${PERSISTENT_MANIFEST}') as f:
        d = json.load(f)
        print(d.get('config_subdir', ''))
except Exception:
    pass
" 2>/dev/null || true)
    if [ -n "${DISCOVERED_SUBDIR}" ] && [ -z "${CONFIG_SUBDIR}" ]; then
        CONFIG_SUBDIR="$(printf '%s' "${DISCOVERED_SUBDIR}" | tr -d '\r' | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')"
        TARGET_CONFIG_DIR="${CONFIG_DIR}/${CONFIG_SUBDIR}"
    fi
fi

OFFSETS_CFG="${TARGET_CONFIG_DIR}/tool_offsets.cfg"
ROOT_OFFSETS_CFG="${CONFIG_DIR}/tool_offsets.cfg"
MACRO_DIR="${TARGET_CONFIG_DIR}/tool_calibrator"
ROOT_MACRO_DIR="${CONFIG_DIR}/tool_calibrator"

# 1. Stop and disable systemd service (both system and user mode if found)
echo -e "\n${BLUE}[1/5] Dừng và gỡ bỏ systemd service...${NC}"

# Check user-level service
if systemctl --user is-active --quiet tool_calibrator.service 2>/dev/null; then
    echo -e "${GREEN}[+] Dừng user-level tool_calibrator.service...${NC}"
    systemctl --user stop tool_calibrator.service || true
fi
if [ -f "${USER_SERVICE_FILE}" ]; then
    systemctl --user disable tool_calibrator.service 2>/dev/null || true
    rm -f "${USER_SERVICE_FILE}"
    systemctl --user daemon-reload 2>/dev/null || true
    echo -e "${GREEN}[✔] Đã gỡ bỏ user service unit: ${USER_SERVICE_FILE}${NC}"
fi

# Check system-level service
if systemctl is-active --quiet tool_calibrator.service 2>/dev/null; then
    echo -e "${GREEN}[+] Dừng system-level tool_calibrator.service...${NC}"
    sudo systemctl stop tool_calibrator.service || true
fi
if [ -f "${SYSTEM_SERVICE_FILE}" ]; then
    sudo systemctl disable tool_calibrator.service 2>/dev/null || true
    sudo rm -f "${SYSTEM_SERVICE_FILE}"
    sudo systemctl daemon-reload 2>/dev/null || true
    echo -e "${GREEN}[✔] Đã gỡ bỏ system service unit: ${SYSTEM_SERVICE_FILE}${NC}"
fi

# 2. Remove symlinks in Klipper extras safely (only TKC symlinks)
echo -e "\n${BLUE}[2/5] Gỡ bỏ liên kết Klipper extras symlinks...${NC}"
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

# Remove Macro Symlinks safely (NEVER rm -rf)
clean_macro_dir() {
    local dir="$1"
    if [ -d "${dir}" ]; then
        for mf in "tool_calibrator.cfg" "macros.cfg" "tool_calibrator_macros.cfg" "safe_staging_macros.cfg" "sample_tool_calibrator.cfg"; do
            if [ -L "${dir}/${mf}" ] || [ -f "${dir}/${mf}" ]; then
                rm -f "${dir}/${mf}"
                echo -e "${GREEN}    Đã xóa ${dir}/${mf}${NC}"
            fi
        done
        # Only remove directory if it is completely empty
        rmdir "${dir}" 2>/dev/null && echo -e "${GREEN}[✔] Đã dọn thư mục rỗng ${dir}/${NC}" || true
    fi
}

clean_macro_dir "${MACRO_DIR}"
if [ "${MACRO_DIR}" != "${ROOT_MACRO_DIR}" ]; then
    clean_macro_dir "${ROOT_MACRO_DIR}"
fi

# Clean root tool_calibrator.cfg symlink if it exists
for root_sym in "${TARGET_CONFIG_DIR}/tool_calibrator.cfg" "${CONFIG_DIR}/tool_calibrator.cfg"; do
    if [ -L "${root_sym}" ]; then
        rm -f "${root_sym}"
        echo -e "${GREEN}    Đã gỡ symlink ${root_sym}${NC}"
    fi
done

# 3. Safely comment out TKC includes in printer.cfg to prevent Klipper startup crash
echo -e "\n${BLUE}[3/5] Bảo vệ cấu hình Klipper (printer.cfg)...${NC}"
if [ -f "${PRINTER_CFG}" ]; then
    TIMESTAMP="$(date +%Y%m%d_%H%M%S)"
    BACKUP_PRINTER="${PRINTER_CFG}.uninstall.bak_${TIMESTAMP}"
    cp "${PRINTER_CFG}" "${BACKUP_PRINTER}"
    python3 -c "
import re
try:
    with open('${PRINTER_CFG}', 'r') as f:
        content = f.read()
    # Safely comment out any include lines pointing to tool_offsets.cfg or tool_calibrator macros (supports hyphens and underscores)
    pattern = r'(?m)^([ \t]*\[include [^]]*(?:tool[-_]offsets\.cfg|tool[-_]calibrator[^\n]*\.cfg)\])'
    updated, count = re.subn(pattern, r'# \1 # disabled by TKC uninstaller', content)
    if count > 0:
        with open('${PRINTER_CFG}', 'w') as f:
            f.write(updated)
        print(f'OK {count}')
except Exception as ex:
    print(f'ERR {ex}')
" | while read -r line; do
        if [[ "${line}" =~ ^OK ]]; then
            echo -e "${GREEN}[✔] Đã tự động vô hiệu hóa các dòng include TKC trong printer.cfg (Sao lưu: ${BACKUP_PRINTER})${NC}"
        fi
    done
fi

# 4. Remove from moonraker.asvc & moonraker.conf
echo -e "\n${BLUE}[4/5] Dọn dẹp cấu hình Moonraker (moonraker.asvc & moonraker.conf)...${NC}"
if [ -f "${ASVC_FILE}" ]; then
    sed -i '/^tool_calibrator$/d' "${ASVC_FILE}"
    echo -e "${GREEN}[✔] Đã xóa tool_calibrator khỏi ${ASVC_FILE}${NC}"
fi

if [ -f "${MOONRAKER_CONF}" ]; then
    if grep -q "\[update_manager tool_calibrator\]" "${MOONRAKER_CONF}"; then
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
        while out and (out[-1].strip().startswith('#') and any(k in out[-1].lower() for k in ['tool_calibrator', 'tool-calibrator', 'tkc', 'calibrator'])):
            out.pop()
        if out and not out[-1].strip():
            out.pop()
        skip = True
        continue
    if skip and line.startswith('['):
        skip = False
    if not skip:
        out.append(line)
with open('${MOONRAKER_CONF}', 'w') as f:
    f.writelines(out)
"
        echo -e "${GREEN}[✔] Đã dọn khối [update_manager tool_calibrator] và chú thích liên quan trong moonraker.conf (Sao lưu: ${BACKUP_FILE}).${NC}"
    fi
fi

if [ "${PURGE_CONFIG}" = true ]; then
    TIMESTAMP="$(date +%Y%m%d_%H%M%S)"
    PURGE_BACKUP_DIR="${CONFIG_DIR}/config_backups/tkc_purge_${TIMESTAMP}"
    mkdir -p "${PURGE_BACKUP_DIR}"
    echo -e "\n${YELLOW}[!] Đang thực hiện dọn dẹp triệt để config (--purge-config)...${NC}"
    echo -e "${GREEN}[+] Tự động tạo bản sao lưu toàn bộ trước khi dọn tại: ${PURGE_BACKUP_DIR}${NC}"
    for dir in "${TARGET_CONFIG_DIR}" "${CONFIG_DIR}"; do
        [ -d "${dir}" ] || continue
        for pattern in "tool_offsets.cfg*" "tool_calibrator.cfg*" "tool-calibrator.cfg*" "sample_tool_calibrator.cfg*"; do
            for f in "${dir}"/${pattern}; do
                if [ -f "${f}" ]; then
                    cp -a "${f}" "${PURGE_BACKUP_DIR}/"
                    rm -f "${f}"
                    echo -e "${GREEN}    Đã sao lưu và gỡ bỏ: ${f}${NC}"
                fi
            done
        done
    done
else
    # Archive tool_offsets.cfg safely
    archive_offsets() {
        local target="$1"
        if [ -f "${target}" ]; then
            if [ "${KEEP_DATA}" = false ]; then
                TIMESTAMP="$(date +%Y%m%d_%H%M%S)"
                ARCHIVE_CFG="${target}.archived_${TIMESTAMP}"
                mv "${target}" "${ARCHIVE_CFG}"
                echo -e "${GREEN}[✔] Đã di chuyển và lưu trữ ${target} -> ${ARCHIVE_CFG}${NC}"
            else
                echo -e "${YELLOW}[+] Bảo tồn ${target} (--keep-data enabled).${NC}"
            fi
        fi
    }

    archive_offsets "${OFFSETS_CFG}"
    if [ "${OFFSETS_CFG}" != "${ROOT_OFFSETS_CFG}" ]; then
        archive_offsets "${ROOT_OFFSETS_CFG}"
    fi
fi

# Remove persistent manifest
[ -f "${PERSISTENT_MANIFEST}" ] && rm -f "${PERSISTENT_MANIFEST}"

# 5. Clean virtualenv & restart services
echo -e "\n${BLUE}[5/5] Xóa môi trường ảo Python virtualenv (env/)...${NC}"
if [ -d "${REPO_DIR}/env" ]; then
    rm -rf "${REPO_DIR}/env"
    echo -e "${GREEN}[✔] Đã xóa thư mục env/${NC}"
fi

echo -e "\n${BLUE}[+] Đang khởi động lại Klipper và Moonraker để nạp trạng thái sạch...${NC}"
# Attempt Moonraker API restart first
if curl -sS --fail --max-time 3 -X POST "http://127.0.0.1:7125/machine/services/restart?service=klipper" >/dev/null 2>&1; then
    echo -e "${GREEN}[✔] Klipper restart triggered via Moonraker API.${NC}"
elif sudo -n true 2>/dev/null; then
    sudo systemctl restart klipper.service || true
fi

if curl -sS --fail --max-time 3 -X POST "http://127.0.0.1:7125/machine/services/restart?service=moonraker" >/dev/null 2>&1; then
    echo -e "${GREEN}[✔] Moonraker restart triggered via Moonraker API.${NC}"
elif sudo -n true 2>/dev/null; then
    sudo systemctl restart moonraker.service || true
fi

# Report remaining backups/archives and retained configs
echo -e "\n${CYAN}[i] Báo cáo chi tiết các file cấu hình và dữ liệu bảo tồn:${NC}"
RETAINED_ITEMS=$(python3 -c "
import os, glob
dirs = list(dict.fromkeys(['${TARGET_CONFIG_DIR}', '${CONFIG_DIR}']))
patterns = [
    'tool_calibrator.cfg*', 'tool-calibrator.cfg*', 'tool_offsets.cfg*',
    'printer.cfg.uninstall.bak_*', 'moonraker.conf.uninstall.bak_*'
]
found = []
for d in dirs:
    if os.path.isdir(d):
        for p in patterns:
            for f in glob.glob(os.path.join(d, p)):
                if os.path.isfile(f):
                    found.append(f)
for f in sorted(set(found)):
    print(f)
" 2>/dev/null || true)

if [ -n "${RETAINED_ITEMS}" ]; then
    echo -e "${CYAN}    Các file cấu hình/dữ liệu máy in được bảo tồn an toàn:${NC}"
    echo "${RETAINED_ITEMS}" | while read -r arc; do
        [ -n "${arc}" ] && echo -e "      - ${arc}"
    done
    if [ "${PURGE_CONFIG}" = false ]; then
        echo -e "${YELLOW}    (Lưu ý: TKC mặc định bảo tồn các file cấu hình máy in. Để gỡ bỏ hoàn toàn kèm backup tự động, sử dụng --purge-config)${NC}"
    fi
else
    echo -e "${GREEN}    Không còn file cấu hình hoặc archive TKC nào trong thư mục config.${NC}"
fi

if [ "${PURGE_REPO}" = true ]; then
    echo -e "\n${YELLOW}[!] Yêu cầu gỡ bỏ toàn bộ thư mục repository (--purge-repo)...${NC}"
    if [ -d "${REPO_DIR}" ]; then
        rm -rf "${REPO_DIR}"
        echo -e "${GREEN}[✔] Đã xóa repository: ${REPO_DIR}${NC}"
    fi
fi

echo -e "\n${GREEN}====================================================${NC}"
echo -e "${GREEN}    GỠ CÀI ĐẶT THÀNH CÔNG VÀ AN TOÀN!              ${NC}"
echo -e "${GREEN}====================================================${NC}"
echo -e "Lưu ý cuối cùng:"
echo -e "1. Các dòng include TKC trong printer.cfg đã được comment an toàn với bản sao lưu."
echo -e "2. Khối [tool_calibrator] (nếu có) có thể được comment hoặc xóa khỏi printer.cfg."
echo -e "3. Giao diện Klipper sẽ hoạt động bình thường sau khi khởi động lại."
echo ""
