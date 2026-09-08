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
KEEP_DATA=true
CONFIG_SUBDIR=""
PURGE_REPO=false
PURGE_CONFIG=false
PURGE_BACKUPS=false
PURGE_ALL=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        --purge-all|--clean-all|--clean|-a)
            PURGE_ALL=true
            PURGE_REPO=true
            PURGE_CONFIG=true
            PURGE_BACKUPS=true
            KEEP_DATA=false
            shift
            ;;
        --keep-data)
            KEEP_DATA=true
            shift
            ;;
        --no-keep-data|--purge-data)
            KEEP_DATA=false
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
        --purge-backups)
            PURGE_BACKUPS=true
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
            echo "Usage: ./scripts/uninstall.sh [--purge-all] [--clean] [--purge-repo] [--purge-config] [--purge-backups] [--keep-data] [--config-subdir <subdir>]"
            echo "  --purge-all, --clean, -a : Xóa sạch toàn bộ (repo git clone, file config, thư mục backup cũ để cài mới)"
            echo "  --purge-repo             : Xóa hoàn toàn thư mục clone git sau khi gỡ để sẵn sàng git clone lại"
            echo "  --purge-config           : Gỡ bỏ toàn bộ file cấu hình TKC khỏi thư mục config máy in"
            echo "  --purge-backups          : Xóa sạch các file và thư mục backup cũ (tkc_*, calib_backup_*, archived_*)"
            echo "  --keep-data              : Bảo tồn tool_offsets.cfg mà không lưu trữ hay xóa"
            echo "  --config-subdir <subdir> : Thư mục con cấu hình máy in (ví dụ: Printer-Setup)"
            exit 0
            ;;
        *)
            echo -e "${YELLOW}[!] Unknown option: $1${NC}"
            shift
            ;;
    esac
done

# Interactive mode: if run in terminal without explicit flags, prompt user for clean uninstallation
if [ -t 0 ] && [ "${PURGE_ALL}" = false ] && [ "${KEEP_DATA}" = false ] && [ "${PURGE_REPO}" = false ] && [ "${PURGE_CONFIG}" = false ] && [ "${PURGE_BACKUPS}" = false ]; then
    echo -e "\n${CYAN}====================================================${NC}"
    echo -e "${CYAN}    TÙY CHỌN DỌN DẸP SẠCH SẼ (CLEAN UNINSTALL)      ${NC}"
    echo -e "${CYAN}====================================================${NC}"
    echo -e "Do cấu trúc khai báo đã được tinh gọn (gom vào 1 file duy nhất),"
    echo -e "khuyến nghị gỡ sạch sẽ các bản backup và repo cũ để cài lại bản mới nhất.\n"

    read -rp "1. Bạn có muốn xóa sạch thư mục sao lưu (backup & archive) cũ (config_backups/tkc_*, *.calib_backup_*)? [Y/n]: " ans_bk
    if [[ ! "${ans_bk}" =~ ^[Nn] ]]; then
        PURGE_BACKUPS=true
    fi

    read -rp "2. Bạn có muốn gỡ bỏ hoàn toàn file cấu hình TKC cũ trong thư mục config máy in? [Y/n]: " ans_cfg
    if [[ ! "${ans_cfg}" =~ ^[Nn] ]]; then
        PURGE_CONFIG=true
    fi

    read -rp "3. Bạn có muốn xóa sạch thư mục mã nguồn git clone sau khi gỡ để sẵn sàng 'git clone' mới? [Y/n]: " ans_repo
    if [[ ! "${ans_repo}" =~ ^[Nn] ]]; then
        PURGE_REPO=true
    fi
    echo ""
fi

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

if [ -z "${CONFIG_SUBDIR}" ] && [ ! -f "${PERSISTENT_MANIFEST}" ]; then
    # Auto-discover manifest in subdirectories of CONFIG_DIR with canonical path verification
    while IFS= read -r found_manifest; do
        if [ -f "${found_manifest}" ]; then
            real_found="$(python3 -c "import os, sys; print(os.path.realpath(sys.argv[1]))" "${found_manifest}" 2>/dev/null || realpath "${found_manifest}" 2>/dev/null || echo "${found_manifest}")"
            real_config="$(python3 -c "import os, sys; print(os.path.realpath(sys.argv[1]))" "${CONFIG_DIR}" 2>/dev/null || realpath "${CONFIG_DIR}" 2>/dev/null || echo "${CONFIG_DIR}")"
            if [[ "${real_found}" == "${real_config}/"* ]]; then
                PERSISTENT_MANIFEST="${found_manifest}"
                echo -e "${GREEN}[+] Tự động phát hiện manifest trong thư mục con: ${PERSISTENT_MANIFEST}${NC}"
                break
            fi
        fi
    done < <(find "${CONFIG_DIR}" -maxdepth 3 -name ".tool_calibrator_manifest.json" 2>/dev/null || true)
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

MACRO_DIR="${TARGET_CONFIG_DIR}/tool_calibrator"
ROOT_MACRO_DIR="${CONFIG_DIR}/tool_calibrator"
OFFSETS_CFG="${MACRO_DIR}/tool_offsets.cfg"
ROOT_OFFSETS_CFG="${ROOT_MACRO_DIR}/tool_offsets.cfg"

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

# Remove Macro Symlinks safely (NEVER rm -rf, and preserve tool_offsets.cfg)
clean_macro_dir() {
    local dir="$1"
    if [ -d "${dir}" ]; then
        for mf in "tool_calibrator.cfg" "macros.cfg" "tool_calibrator_macros.cfg" "safe_staging_macros.cfg" "sample_tool_calibrator.cfg"; do
            if [ -L "${dir}/${mf}" ]; then
                rm -f "${dir}/${mf}"
                echo -e "${GREEN}    Đã gỡ symlink ${dir}/${mf}${NC}"
            elif [ "${PURGE_CONFIG}" = true ] && [ -f "${dir}/${mf}" ]; then
                rm -f "${dir}/${mf}"
                echo -e "${GREEN}    Đã xóa ${dir}/${mf}${NC}"
            fi
        done
        # If tool_offsets.cfg is a symlink, remove the symlink only
        if [ -L "${dir}/tool_offsets.cfg" ]; then
            rm -f "${dir}/tool_offsets.cfg"
            echo -e "${GREEN}    Đã gỡ symlink ${dir}/tool_offsets.cfg${NC}"
        fi
        # Only remove directory if it is completely empty
        rmdir "${dir}" 2>/dev/null && echo -e "${GREEN}[✔] Đã dọn thư mục rỗng ${dir}/${NC}" || true
    fi
}

clean_macro_dir "${MACRO_DIR}"
if [ "${MACRO_DIR}" != "${ROOT_MACRO_DIR}" ]; then
    clean_macro_dir "${ROOT_MACRO_DIR}"
fi

# Clean root tool_calibrator.cfg and tool_offsets.cfg symlinks if they exist
for root_sym in "${TARGET_CONFIG_DIR}/tool_calibrator.cfg" "${CONFIG_DIR}/tool_calibrator.cfg" "${TARGET_CONFIG_DIR}/tool_offsets.cfg" "${CONFIG_DIR}/tool_offsets.cfg"; do
    if [ -L "${root_sym}" ]; then
        rm -f "${root_sym}"
        echo -e "${GREEN}    Đã gỡ symlink ${root_sym}${NC}"
    fi
done

TKC_BACKUP_DIR="${MACRO_DIR}/backups"

# Consolidate legacy backup folders strictly into ${MACRO_DIR}/backups/
for legacy_bk in "${CONFIG_DIR}/tool_calibrator_backups" "${TARGET_CONFIG_DIR}/tool_calibrator_backups"; do
    if [ -d "${legacy_bk}" ]; then
        if [ "${PURGE_BACKUPS}" = false ]; then
            mkdir -p "${TKC_BACKUP_DIR}"
            cp -rn "${legacy_bk}"/* "${TKC_BACKUP_DIR}/" 2>/dev/null || true
        fi
        rm -rf "${legacy_bk}"
    fi
done

# Consolidate any loose printer.cfg or system backup files from root config directory into system_configs/
if [ "${PURGE_BACKUPS}" = false ]; then
    SYSTEM_BK_DIR="${TKC_BACKUP_DIR}/system_configs"
    for loose_bak in "${CONFIG_DIR}"/printer.cfg.tkc_bak_* "${TARGET_CONFIG_DIR}"/printer.cfg.tkc_bak_* "${CONFIG_DIR}"/printer.cfg.uninstall.bak_* "${TARGET_CONFIG_DIR}"/printer.cfg.uninstall.bak_*; do
        if [ -f "${loose_bak}" ] && [ ! -L "${loose_bak}" ]; then
            mkdir -p "${SYSTEM_BK_DIR}"
            mv -f "${loose_bak}" "${SYSTEM_BK_DIR}/" 2>/dev/null || true
            echo -e "${GREEN}[✔] Đã chuyển bản sao lưu bên ngoài (${loose_bak##*/}) vào ${SYSTEM_BK_DIR}/${NC}"
        fi
    done
fi

# 3. Safely comment out TKC includes in printer.cfg to prevent Klipper startup crash
echo -e "\n${BLUE}[3/5] Bảo vệ cấu hình Klipper (printer.cfg)...${NC}"
if [ -f "${PRINTER_CFG}" ]; then
    if [ "${PURGE_BACKUPS}" = false ]; then
        TIMESTAMP="$(date +%Y%m%d_%H%M%S)"
        SYSTEM_BK_DIR="${TKC_BACKUP_DIR}/system_configs"
        mkdir -p "${SYSTEM_BK_DIR}"
        BACKUP_PRINTER="${SYSTEM_BK_DIR}/printer.cfg.uninstall.bak_${TIMESTAMP}"
        cp "${PRINTER_CFG}" "${BACKUP_PRINTER}"
    fi
    python3 -c "
import re
try:
    with open('${PRINTER_CFG}', 'r') as f:
        content = f.read()
    # Safely comment out any include lines pointing to tool_offsets.cfg, tool_calibrator macros, safe_staging, sample_tool, etc.
    pattern = r'(?m)^([ \t]*\[include [^]]*(?:tool[-_]offsets\.cfg|tool[-_]calibrator[^\n]*\.cfg|safe[-_]staging[^\n]*\.cfg|sample[-_]tool[^\n]*\.cfg|tool[-_]calibrator/macros\.cfg)\])'
    updated, count = re.subn(pattern, r'# \1 # disabled by TKC uninstaller', content)
    if count > 0:
        with open('${PRINTER_CFG}', 'w') as f:
            f.write(updated)
        print(f'OK {count}')
except Exception as ex:
    print(f'ERR {ex}')
" | while read -r line; do
        if [[ "${line}" =~ ^OK ]]; then
            echo -e "${GREEN}[✔] Đã tự động vô hiệu hóa các dòng include TKC trong printer.cfg.${NC}"
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
        if [ "${PURGE_BACKUPS}" = false ]; then
            TIMESTAMP="$(date +%Y%m%d_%H%M%S)"
            SYSTEM_BK_DIR="${TKC_BACKUP_DIR}/system_configs"
            mkdir -p "${SYSTEM_BK_DIR}"
            BACKUP_FILE="${SYSTEM_BK_DIR}/moonraker.conf.uninstall.bak_${TIMESTAMP}"
            cp "${MOONRAKER_CONF}" "${BACKUP_FILE}"
        fi
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
        echo -e "${GREEN}[✔] Đã dọn khối [update_manager tool_calibrator] và chú thích liên quan trong moonraker.conf.${NC}"
    fi
fi

if [ "${PURGE_CONFIG}" = true ]; then
    echo -e "\n${YELLOW}[!] Đang thực hiện gỡ bỏ cấu hình TKC (--purge-config)...${NC}"
    if [ "${PURGE_BACKUPS}" = false ]; then
        TIMESTAMP="$(date +%Y%m%d_%H%M%S)"
        PURGE_BACKUP_DIR="${TKC_BACKUP_DIR}/archived_configs/purge_${TIMESTAMP}"
        mkdir -p "${PURGE_BACKUP_DIR}"
        echo -e "${GREEN}[+] Tự động tạo bản sao lưu trước khi dọn tại: ${PURGE_BACKUP_DIR}${NC}"
    fi

    for dir in "${TARGET_CONFIG_DIR}" "${CONFIG_DIR}"; do
        [ -d "${dir}" ] || continue
        for pattern in "tool_offsets.cfg*" "tool_calibrator.cfg*" "tool-calibrator.cfg*" "sample_tool_calibrator.cfg*" "safe_staging_macros.cfg*" "tool_calibrator_macros.cfg*"; do
            for f in "${dir}"/${pattern}; do
                if [ -f "${f}" ] || [ -L "${f}" ]; then
                    if [[ "${pattern}" == "tool_offsets.cfg"* ]] && [ "${KEEP_DATA}" = true ] && [ ! -L "${f}" ]; then
                        echo -e "${YELLOW}[+] Bảo tồn dữ liệu toạ độ: ${f} (bảo toàn dữ liệu mặc định/--keep-data).${NC}"
                        continue
                    fi
                    if [ "${PURGE_BACKUPS}" = false ] && [ -f "${f}" ] && [ ! -L "${f}" ]; then
                        cp -a "${f}" "${PURGE_BACKUP_DIR}/"
                    fi
                    rm -f "${f}"
                    echo -e "${GREEN}    Đã gỡ bỏ: ${f}${NC}"
                fi
            done
        done
    done

    # Remove macro dirs only if keep-data is false, or only if empty
    if [ "${KEEP_DATA}" = false ]; then
        rm -rf "${MACRO_DIR}" "${ROOT_MACRO_DIR}" 2>/dev/null || true
    else
        rmdir "${MACRO_DIR}" "${ROOT_MACRO_DIR}" 2>/dev/null || true
    fi
else
    # Archive or preserve tool_offsets.cfg safely
    archive_offsets() {
        local target="$1"
        if [ -f "${target}" ] && [ ! -L "${target}" ]; then
            if [ "${KEEP_DATA}" = false ]; then
                TIMESTAMP="$(date +%Y%m%d_%H%M%S)"
                ARCHIVE_BK_DIR="${TKC_BACKUP_DIR}/archived_configs"
                mkdir -p "${ARCHIVE_BK_DIR}"
                ARCHIVE_CFG="${ARCHIVE_BK_DIR}/tool_offsets.cfg.archived_${TIMESTAMP}"
                mv "${target}" "${ARCHIVE_CFG}"
                echo -e "${GREEN}[✔] Đã di chuyển và lưu trữ ${target} -> ${ARCHIVE_CFG}${NC}"
            else
                echo -e "${YELLOW}[+] Bảo tồn dữ liệu toạ độ: ${target} (bảo toàn dữ liệu mặc định/--keep-data).${NC}"
            fi
        fi
    }

    archive_offsets "${OFFSETS_CFG}"
    if [ "${OFFSETS_CFG}" != "${ROOT_OFFSETS_CFG}" ]; then
        archive_offsets "${ROOT_OFFSETS_CFG}"
    fi
    for legacy_off in "${TARGET_CONFIG_DIR}/tool_offsets.cfg" "${CONFIG_DIR}/tool_offsets.cfg"; do
        if [ "${legacy_off}" != "${OFFSETS_CFG}" ] && [ "${legacy_off}" != "${ROOT_OFFSETS_CFG}" ]; then
            archive_offsets "${legacy_off}"
        fi
    done
fi

# Remove persistent manifest
[ -f "${PERSISTENT_MANIFEST}" ] && rm -f "${PERSISTENT_MANIFEST}"
[ -f "${CONFIG_DIR}/.tool_calibrator_manifest.json" ] && rm -f "${CONFIG_DIR}/.tool_calibrator_manifest.json"

# Clean backups if requested
if [ "${PURGE_BACKUPS}" = true ]; then
    echo -e "\n${BLUE}[+] Xóa sạch toàn bộ thư mục và file backup cũ (--purge-backups)...${NC}"
    # 1. Remove unified backup folder
    rm -rf "${MACRO_DIR}/backups" "${ROOT_MACRO_DIR}/backups" "${CONFIG_DIR}/tool_calibrator_backups" "${TARGET_CONFIG_DIR}/tool_calibrator_backups" 2>/dev/null || true

    # 2. Remove legacy backup folders: config_backups/tkc_* and config_backups/pre-tkc-*
    for bk_parent in "${CONFIG_DIR}/config_backups" "${TARGET_CONFIG_DIR}/config_backups"; do
        if [ -d "${bk_parent}" ]; then
            for bk_dir in "${bk_parent}"/tkc* "${bk_parent}"/pre-tkc-*; do
                if [ -d "${bk_dir}" ]; then
                    rm -rf "${bk_dir}"
                    echo -e "${GREEN}    Đã xóa thư mục sao lưu cũ: ${bk_dir}${NC}"
                fi
            done
            rmdir "${bk_parent}" 2>/dev/null || true
        fi
    done

    # 3. Remove loose legacy backup files
    for dir in "${TARGET_CONFIG_DIR}" "${CONFIG_DIR}"; do
        [ -d "${dir}" ] || continue
        for bk_pat in "tool_offsets.cfg.calib_backup_*" "tool_offsets.cfg.archived_*" "tool_offsets.cfg.bak_*" "*.uninstall.bak_*" "*.tkc_bak_*" "*.bak_[0-9]*"; do
            for f in "${dir}"/${bk_pat}; do
                if [ -f "${f}" ]; then
                    rm -f "${f}"
                    echo -e "${GREEN}    Đã xóa file sao lưu cũ: ${f}${NC}"
                fi
            done
        done
    done
    echo -e "${GREEN}[✔] Toàn bộ file và thư mục backup cũ đã được dọn sạch.${NC}"
fi

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
        echo -e "${YELLOW}    (Lưu ý: TKC mặc định bảo tồn các file cấu hình máy in. Để gỡ bỏ hoàn toàn, sử dụng --purge-config hoặc --purge-all)${NC}"
    fi
else
    echo -e "${GREEN}    Không còn file cấu hình hoặc archive TKC nào trong thư mục config.${NC}"
fi

if [ "${PURGE_REPO}" = true ]; then
    echo -e "\n${YELLOW}[!] Đang xóa sạch hoàn toàn thư mục repository clone từ git (--purge-repo)...${NC}"
    TARGET_REPO="${REPO_DIR}"
    cd "${HOME}"
    rm -rf "${TARGET_REPO}"
    echo -e "${GREEN}[✔] Đã xóa hoàn toàn thư mục git clone: ${TARGET_REPO}${NC}"
    echo -e "${GREEN}    Bây giờ bạn có thể thực hiện 'git clone' mới mà không bị lỗi 'already exists'!${NC}"
fi

echo -e "\n${GREEN}====================================================${NC}"
echo -e "${GREEN}    GỠ CÀI ĐẶT HOÀN TẤT THÀNH CÔNG VÀ SẠCH SẼ!      ${NC}"
echo -e "${GREEN}====================================================${NC}"
echo -e "Lưu ý quan trọng:"
echo -e "1. Các dòng include TKC trong printer.cfg đã được vô hiệu hóa an toàn."
echo -e "2. Giao diện Klipper/Moonraker đã được nạp lại trạng thái sạch."
echo ""
echo -e "${CYAN}====================================================${NC}"
echo -e "${CYAN}    HƯỚNG DẪN CÀI ĐẶT LẠI MỚI (CẤU TRÚC 1 FILE)     ${NC}"
echo -e "${CYAN}====================================================${NC}"
echo -e "Để cài đặt lại phiên bản mới nhất từ đầu:"
echo -e "  1. Chuyển về thư mục người dùng:  ${YELLOW}cd ~${NC}"
echo -e "  2. Clone mã nguồn mới nhất:       ${YELLOW}git clone https://github.com/IDcrazy123/Tool-Klipper-Calibration.git${NC}"
echo -e "  3. Chạy script cài đặt:           ${YELLOW}cd Tool-Klipper-Calibration && ./scripts/install.sh${NC}"
echo ""
echo -e "Sau khi cài đặt xong, thêm duy nhất 1 dòng sau vào printer.cfg:"
if [ -n "${CONFIG_SUBDIR}" ]; then
    echo -e "  ${GREEN}[include ${CONFIG_SUBDIR}/tool_calibrator/tool_calibrator.cfg]${NC}"
else
    echo -e "  ${GREEN}[include tool_calibrator/tool_calibrator.cfg]${NC}"
fi
echo ""
