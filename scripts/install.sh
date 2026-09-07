#!/usr/bin/env bash
# ============================================================================
# Tool-Klipper-Calibration: Automated Linux / Raspberry Pi Installer
# ============================================================================
set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m'

echo -e "${BLUE}====================================================${NC}"
echo -e "${BLUE}    Tool-Klipper-Calibration Automated Installer    ${NC}"
echo -e "${BLUE}====================================================${NC}"

# Parse optional arguments
SERVICE_MODE="system"
CONFIG_SUBDIR=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --user-service)
            SERVICE_MODE="user"
            shift
            ;;
        --system-service)
            SERVICE_MODE="system"
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
            echo "Usage: ./scripts/install.sh [--system-service | --user-service] [--config-subdir <subdir>]"
            echo "  --system-service         : Install system-wide systemd unit in /etc/systemd/system (default, requires sudo)"
            echo "  --user-service           : Install user-level systemd unit in ~/.config/systemd/user (rootless)"
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
    echo -e "${YELLOW}      Script sẽ tự động yêu cầu quyền sudo khi cần thiết.${NC}"
    exit 1
fi

# 1. Resolve paths
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CURRENT_USER="$(id -un)"
VENV_DIR="${REPO_DIR}/env"
JOURNAL_FILE="${REPO_DIR}/.install_manifest.txt"

KLIPPER_DIR="${HOME}/klipper"
if [ ! -d "${KLIPPER_DIR}" ]; then
    echo -e "${YELLOW}[!] Thư mục Klipper mặc định (${KLIPPER_DIR}) không tồn tại.${NC}"
    read -rp "Nhập đường dẫn đến thư mục Klipper của bạn: " USER_KLIPPER_DIR
    KLIPPER_DIR="${USER_KLIPPER_DIR}"
fi

if [ ! -d "${KLIPPER_DIR}/klippy/extras" ]; then
    echo -e "${RED}[ERR] Thư mục Klipper không hợp lệ: ${KLIPPER_DIR}/klippy/extras không tồn tại.${NC}"
    exit 1
fi

# Detect Klipper / Moonraker config directory
CONFIG_DIR="${HOME}/printer_data/config"
if [ ! -d "${CONFIG_DIR}" ]; then
    if [ -d "${HOME}/klipper_config" ]; then
        CONFIG_DIR="${HOME}/klipper_config"
    fi
fi

TARGET_CONFIG_DIR="${CONFIG_DIR}"
if [ -n "${CONFIG_SUBDIR}" ]; then
    # Strip carriage return and leading/trailing whitespace
    CLEAN_SUBDIR="$(printf '%s' "${CONFIG_SUBDIR}" | tr -d '\r' | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')"

    # Check for control characters
    if printf '%s' "${CLEAN_SUBDIR}" | grep -q '[[:cntrl:]]'; then
        echo -e "${RED}[ERR] Giá trị --config-subdir chứa ký tự điều khiển không hợp lệ!${NC}"
        exit 1
    fi

    # Reject absolute path
    if [[ "${CLEAN_SUBDIR}" == /* ]]; then
        echo -e "${RED}[ERR] Giá trị --config-subdir không được là đường dẫn tuyệt đối (${CLEAN_SUBDIR})!${NC}"
        exit 1
    fi

    # Reject directory traversal
    if [[ "${CLEAN_SUBDIR}" =~ \.\. ]]; then
        echo -e "${RED}[ERR] Giá trị --config-subdir không được chứa đường dẫn duyệt ngược (..) (${CLEAN_SUBDIR})!${NC}"
        exit 1
    fi

    # Reject double slashes
    if [[ "${CLEAN_SUBDIR}" =~ // ]]; then
        echo -e "${RED}[ERR] Giá trị --config-subdir không được chứa dấu gạch chéo kép (//) (${CLEAN_SUBDIR})!${NC}"
        exit 1
    fi

    CONFIG_SUBDIR="${CLEAN_SUBDIR}"
    TARGET_CONFIG_DIR="${CONFIG_DIR}/${CONFIG_SUBDIR}"
fi

# Verify canonical resolved path stays strictly within CONFIG_DIR
REAL_CONFIG_DIR="$(python3 -c "import os, sys; print(os.path.realpath(sys.argv[1]))" "${CONFIG_DIR}" 2>/dev/null || realpath "${CONFIG_DIR}" 2>/dev/null || echo "${CONFIG_DIR}")"
REAL_TARGET_DIR="$(python3 -c "import os, sys; print(os.path.realpath(sys.argv[1]))" "${TARGET_CONFIG_DIR}" 2>/dev/null || realpath "${TARGET_CONFIG_DIR}" 2>/dev/null || echo "${TARGET_CONFIG_DIR}")"

if [[ "${REAL_TARGET_DIR}" != "${REAL_CONFIG_DIR}" && "${REAL_TARGET_DIR}" != "${REAL_CONFIG_DIR}/"* ]]; then
    echo -e "${RED}[ERR] Thư mục cấu hình mục tiêu (${REAL_TARGET_DIR}) nằm ngoài thư mục cấu hình Klipper (${REAL_CONFIG_DIR})!${NC}"
    exit 1
fi

mkdir -p "${TARGET_CONFIG_DIR}"

PERSISTENT_MANIFEST="${TARGET_CONFIG_DIR}/.tool_calibrator_manifest.json"

echo -e "${GREEN}[+] Repo directory:          ${REPO_DIR}${NC}"
echo -e "${GREEN}[+] Klipper directory:       ${KLIPPER_DIR}${NC}"
echo -e "${GREEN}[+] Config directory:        ${TARGET_CONFIG_DIR}${NC}"
echo -e "${GREEN}[+] Python virtualenv:       ${VENV_DIR}${NC}"
echo -e "${GREEN}[+] Service mode:            ${SERVICE_MODE}${NC}"

# Setup Transactional Rollback Journal
rm -f "${JOURNAL_FILE}"
touch "${JOURNAL_FILE}"

cleanup_on_error() {
    local exit_code=$?
    echo -e "\n${RED}[ERR] Quá trình cài đặt bị gián đoạn (Exit code: ${exit_code})! Đang hoàn tác theo thứ tự ngược lại...${NC}"
    if [ -f "${JOURNAL_FILE}" ]; then
        # Rollback in reverse order
        (tac "${JOURNAL_FILE}" 2>/dev/null || cat "${JOURNAL_FILE}") | while IFS= read -r line || [ -n "${line}" ]; do
            key="$(echo "${line}" | cut -d'=' -f1)"
            val="$(echo "${line}" | cut -d'=' -f2-)"
            case "${key}" in
                SYMLINK)
                    [ -L "${val}" ] && rm -f "${val}"
                    ;;
                FILE)
                    [ -f "${val}" ] && rm -f "${val}"
                    ;;
                DIR)
                    [ -d "${val}" ] && rmdir "${val}" 2>/dev/null || true
                    ;;
                BACKUP)
                    src="$(echo "${val}" | cut -d':' -f1)"
                    dst="$(echo "${val}" | cut -d':' -f2)"
                    [ -f "${src}" ] && cp -f "${src}" "${dst}"
                    ;;
            esac
        done
        rm -f "${JOURNAL_FILE}"
    fi
    echo -e "${YELLOW}[!] Đã hoàn tác các thay đổi tạm thời. Vui lòng kiểm tra lỗi trước khi thử lại.${NC}"
    exit "${exit_code}"
}
trap cleanup_on_error ERR

# 2. Preflight Check: System dependencies & Python
echo -e "\n${BLUE}[1/6] Kiểm tra môi trường hệ thống & Python...${NC}"

# Check Python 3
if ! command -v python3 >/dev/null 2>&1; then
    echo -e "${RED}[ERR] python3 không được tìm thấy trên hệ thống.${NC}"
    exit 1
fi

# Check if python3 -m venv works without installing extra packages
VENV_WORKS=false
TEST_VENV_DIR="/tmp/tkc_venv_test_$$"
if python3 -m venv "${TEST_VENV_DIR}" >/dev/null 2>&1; then
    if [ -f "${TEST_VENV_DIR}/bin/pip" ]; then
        VENV_WORKS=true
    fi
fi
rm -rf "${TEST_VENV_DIR}"

MISSING_PKGS=()
for pkg in curl libgl1 libglib2.0-0; do
    if ! dpkg -s "${pkg}" >/dev/null 2>&1; then
        MISSING_PKGS+=("${pkg}")
    fi
done

if [ "${VENV_WORKS}" = false ]; then
    if ! dpkg -s "python3-venv" >/dev/null 2>&1; then
        MISSING_PKGS+=("python3-venv")
    fi
fi

if [ ${#MISSING_PKGS[@]} -gt 0 ]; then
    echo -e "${YELLOW}[+] Đang cài đặt các gói hệ thống còn thiếu: ${MISSING_PKGS[*]}${NC}"
    if sudo -n true 2>/dev/null || [ -t 0 ]; then
        sudo apt-get update
        sudo apt-get install -y "${MISSING_PKGS[@]}"
    else
        echo -e "${RED}[ERR] Cần cài đặt: ${MISSING_PKGS[*]} nhưng không có quyền sudo tương tác.${NC}"
        echo -e "${YELLOW}      Vui lòng chạy lệnh sau bằng tài khoản có quyền sudo rồi thử lại:${NC}"
        echo -e "      sudo apt-get update && sudo apt-get install -y ${MISSING_PKGS[*]}"
        exit 1
    fi
else
    echo -e "${GREEN}[✔] Đầy đủ các gói hệ thống và thư viện đồ họa cần thiết.${NC}"
fi

# 3. Setup Virtualenv & Dependencies
echo -e "\n${BLUE}[2/6] Thiết lập Python Virtual Environment...${NC}"
if [ ! -d "${VENV_DIR}" ]; then
    python3 -m venv "${VENV_DIR}"
fi

echo -e "${GREEN}[+] Đang nâng cấp pip và cài đặt thư viện từ server/requirements.txt...${NC}"
"${VENV_DIR}/bin/pip" install --upgrade pip
if [ -f "${REPO_DIR}/server/constraints.txt" ]; then
    "${VENV_DIR}/bin/pip" install -r "${REPO_DIR}/server/requirements.txt" -c "${REPO_DIR}/server/constraints.txt" || "${VENV_DIR}/bin/pip" install -r "${REPO_DIR}/server/requirements.txt"
else
    "${VENV_DIR}/bin/pip" install -r "${REPO_DIR}/server/requirements.txt"
fi

# 4. Link Klipper Extras & Macro Bundle
echo -e "\n${BLUE}[3/6] Tạo liên kết tượng trưng (Symlinks) vào Klipper extras & macros...${NC}"
KLIPPY_EXTRAS="${KLIPPER_DIR}/klippy/extras"

# Safety: Handle legacy whole-directory symlink for z_backends
if [ -L "${KLIPPY_EXTRAS}/z_backends" ]; then
    rm -f "${KLIPPY_EXTRAS}/z_backends"
fi
mkdir -p "${KLIPPY_EXTRAS}/z_backends"

for file in "tool_calibrator.py" "tool_calibrator_station.py" "tool_offsets.py" "safe_navigator.py" "config_manager.py"; do
    TARGET="${KLIPPY_EXTRAS}/${file}"
    SOURCE="${REPO_DIR}/klippy/extras/${file}"
    ln -sf "${SOURCE}" "${TARGET}"
    echo "SYMLINK=${TARGET}" >> "${JOURNAL_FILE}"
    echo -e "${GREEN}    Linked ${file} -> ${KLIPPY_EXTRAS}/${NC}"
done

for zb_file in "__init__.py" "base_z.py" "switch_backend.py" "cartographer_backend.py"; do
    TARGET="${KLIPPY_EXTRAS}/z_backends/${zb_file}"
    SOURCE="${REPO_DIR}/klippy/extras/z_backends/${zb_file}"
    ln -sf "${SOURCE}" "${TARGET}"
    echo "SYMLINK=${TARGET}" >> "${JOURNAL_FILE}"
    echo -e "${GREEN}    Linked z_backends/${zb_file} -> ${KLIPPY_EXTRAS}/z_backends/${NC}"
done

# Setup Macro Bundle under target config directory
MACRO_DIR="${TARGET_CONFIG_DIR}/tool_calibrator"
mkdir -p "${MACRO_DIR}"
echo "DIR=${MACRO_DIR}" >> "${JOURNAL_FILE}"

for macro_file in "tool_calibrator.cfg" "tool_calibrator_macros.cfg" "safe_staging_macros.cfg" "macros.cfg"; do
    TARGET="${MACRO_DIR}/${macro_file}"
    SOURCE="${REPO_DIR}/macros/${macro_file}"
    ln -sf "${SOURCE}" "${TARGET}"
    echo "SYMLINK=${TARGET}" >> "${JOURNAL_FILE}"
    echo -e "${GREEN}    Linked ${macro_file} -> ${MACRO_DIR}/${NC}"
done

# Provide 1-file master entrypoint in root target config directory
if [ ! -f "${TARGET_CONFIG_DIR}/tool_calibrator.cfg" ] && [ ! -L "${TARGET_CONFIG_DIR}/tool_calibrator.cfg" ]; then
    ln -sf "${REPO_DIR}/macros/tool_calibrator.cfg" "${TARGET_CONFIG_DIR}/tool_calibrator.cfg"
    echo "SYMLINK=${TARGET_CONFIG_DIR}/tool_calibrator.cfg" >> "${JOURNAL_FILE}"
    echo -e "${GREEN}    Linked tool_calibrator.cfg -> ${TARGET_CONFIG_DIR}/tool_calibrator.cfg${NC}"
fi

# Ensure tool_offsets.cfg placeholder exists in both target and macro dir to prevent Klipper startup include errors
for off_dir in "${TARGET_CONFIG_DIR}" "${MACRO_DIR}"; do
    OFF_PATH="${off_dir}/tool_offsets.cfg"
    if [ ! -f "${OFF_PATH}" ]; then
        echo -e "${CYAN}[+] Khởi tạo tệp cấu hình ban đầu: ${OFF_PATH}...${NC}"
        cat > "${OFF_PATH}" << 'EOF'
# Tool-Klipper-Calibration Offsets File
# Auto-generated by Tool-Klipper-Calibration
# Calibrated station waypoints and tool offsets will be automatically written here.
EOF
        echo "FILE=${OFF_PATH}" >> "${JOURNAL_FILE}"
        echo -e "${GREEN}[✔] Đã tạo file ${OFF_PATH} (ngăn lỗi missing include khi Klipper khởi động).${NC}"
    fi
done

# 5. Moonraker Allowed Services (ASVC) & Update Manager
echo -e "\n${BLUE}[4/6] Cấu hình Moonraker (ASVC & Update Manager)...${NC}"

if [ "${SERVICE_MODE}" = "system" ]; then
    ASVC_FILE="${HOME}/printer_data/moonraker.asvc"
    if [ -d "${HOME}/printer_data" ]; then
        [ ! -f "${ASVC_FILE}" ] && touch "${ASVC_FILE}"
        if ! grep -q "^tool_calibrator$" "${ASVC_FILE}" 2>/dev/null; then
            [ -s "${ASVC_FILE}" ] && echo "" >> "${ASVC_FILE}"
            echo "tool_calibrator" >> "${ASVC_FILE}"
            echo -e "${GREEN}[✔] Đã thêm 'tool_calibrator' vào ${ASVC_FILE}${NC}"
        fi
    fi
fi

MOONRAKER_CONF="${CONFIG_DIR}/moonraker.conf"
if [ -f "${MOONRAKER_CONF}" ]; then
    if ! grep -q "\[update_manager tool_calibrator\]" "${MOONRAKER_CONF}"; then
        echo -e "${CYAN}[+] Tự động cấu hình [update_manager tool_calibrator] trong ${MOONRAKER_CONF}...${NC}"
        TIMESTAMP="$(date +%Y%m%d_%H%M%S)"
        BACKUP_CONF="${MOONRAKER_CONF}.bak_${TIMESTAMP}"
        cp "${MOONRAKER_CONF}" "${BACKUP_CONF}"
        echo "BACKUP=${BACKUP_CONF}:${MOONRAKER_CONF}" >> "${JOURNAL_FILE}"

        if [ "${SERVICE_MODE}" = "system" ]; then
            cat >> "${MOONRAKER_CONF}" << EOF

[update_manager tool_calibrator]
type: git_repo
path: ${REPO_DIR}
origin: https://github.com/IDcrazy123/Tool-Klipper-Calibration.git
primary_branch: main
virtualenv: ${VENV_DIR}
requirements: server/requirements.txt
is_system_service: True
managed_services:
    tool_calibrator
    klipper
info_tags:
    desc=Tool-Klipper-Calibration Automated Vision & Z Alignment
EOF
        else
            cat >> "${MOONRAKER_CONF}" << EOF

[update_manager tool_calibrator]
type: git_repo
path: ${REPO_DIR}
origin: https://github.com/IDcrazy123/Tool-Klipper-Calibration.git
primary_branch: main
virtualenv: ${VENV_DIR}
requirements: server/requirements.txt
install_script: scripts/update_hook.sh
is_system_service: False
managed_services:
    klipper
info_tags:
    desc=Tool-Klipper-Calibration Automated Vision & Z Alignment (User Service)
EOF
        fi
        echo -e "${GREEN}[✔] Đã cấu hình Update Manager trong moonraker.conf (Sao lưu: ${BACKUP_CONF})${NC}"
    else
        echo -e "${GREEN}[✔] Khối [update_manager tool_calibrator] đã tồn tại trong moonraker.conf.${NC}"
    fi
fi

# 6. Install and start systemd service
echo -e "\n${BLUE}[5/6] Cấu hình và kích hoạt systemd service (${SERVICE_MODE} mode)...${NC}"

if [ "${SERVICE_MODE}" = "system" ]; then
    SERVICE_FILE="/etc/systemd/system/tool_calibrator.service"
    TEMP_SERVICE="/tmp/tool_calibrator.service.$$"

    sed -e "s|%USER%|${CURRENT_USER}|g" \
        -e "s|%REPO_DIR%|${REPO_DIR}|g" \
        -e "s|%VENV_DIR%|${VENV_DIR}|g" \
        "${REPO_DIR}/scripts/tool_calibrator.service" > "${TEMP_SERVICE}"

    sudo mv "${TEMP_SERVICE}" "${SERVICE_FILE}"
    sudo chown root:root "${SERVICE_FILE}"
    sudo chmod 644 "${SERVICE_FILE}"

    echo -e "${GREEN}[+] Reloading systemd daemon và kích hoạt system service...${NC}"
    sudo systemctl daemon-reload
    sudo systemctl enable tool_calibrator.service
    sudo systemctl restart tool_calibrator.service
else
    USER_SYSTEMD_DIR="${HOME}/.config/systemd/user"
    mkdir -p "${USER_SYSTEMD_DIR}"
    USER_SERVICE_FILE="${USER_SYSTEMD_DIR}/tool_calibrator.service"

    sed -e "/User=%USER%/d" \
        -e "s|WantedBy=multi-user.target|WantedBy=default.target|g" \
        -e "s|%REPO_DIR%|${REPO_DIR}|g" \
        -e "s|%VENV_DIR%|${VENV_DIR}|g" \
        "${REPO_DIR}/scripts/tool_calibrator.service" > "${USER_SERVICE_FILE}"

    echo -e "${GREEN}[+] Reloading systemd user daemon và kích hoạt user service...${NC}"
    systemctl --user daemon-reload
    systemctl --user enable tool_calibrator.service
    systemctl --user restart tool_calibrator.service
fi

# Restart Moonraker and Klipper
RELOAD_SUCCESS=true
if [ "${SERVICE_MODE}" = "system" ]; then
    if systemctl is-active --quiet moonraker.service 2>/dev/null; then
        echo -e "${GREEN}[+] Khởi động lại Moonraker...${NC}"
        sudo systemctl restart moonraker.service || RELOAD_SUCCESS=false
    fi
    if systemctl is-active --quiet klipper.service 2>/dev/null; then
        echo -e "${GREEN}[+] Khởi động lại Klipper để nạp các module extras...${NC}"
        sudo systemctl restart klipper.service || RELOAD_SUCCESS=false
    fi
else
    echo -e "${CYAN}[+] Đang khởi động lại dịch vụ qua Moonraker API hoặc sudo...${NC}"
    API_SUCCESS=false
    if curl -sS --fail --max-time 3 -X POST "http://127.0.0.1:7125/machine/services/restart?service=klipper" >/dev/null 2>&1; then
        echo -e "${GREEN}[✔] Đã gửi yêu cầu khởi động lại Klipper qua Moonraker API.${NC}"
        API_SUCCESS=true
    fi
    if curl -sS --fail --max-time 3 -X POST "http://127.0.0.1:7125/machine/services/restart?service=moonraker" >/dev/null 2>&1; then
        echo -e "${GREEN}[✔] Đã gửi yêu cầu khởi động lại Moonraker qua Moonraker API.${NC}"
    fi

    if [ "${API_SUCCESS}" = false ]; then
        if sudo -n true 2>/dev/null; then
            sudo systemctl restart moonraker.service || true
            sudo systemctl restart klipper.service || true
        else
            RELOAD_SUCCESS=false
        fi
    fi
fi

# 7. Verify Service Health
echo -e "\n${BLUE}[6/6] Kiểm tra trạng thái Vision Server daemon...${NC}"
HEALTH_SUCCESS=false
HEALTH_OUTPUT=""

for i in {1..10}; do
    HEALTH_RESP=$(curl -sS --fail --max-time 3 http://127.0.0.1:8090/health 2>/dev/null || true)
    if [ -n "${HEALTH_RESP}" ]; then
        HEALTH_OUTPUT=$(python3 -c "
import sys, json
try:
    data = json.loads('''${HEALTH_RESP}''')
    if data.get('status') == 'ok' and data.get('service') == 'tool_calibrator_server':
        ver = data.get('version', 'unknown')
        cmt = data.get('commit', 'unknown')[:7]
        proc = 'READY' if data.get('process_ready', True) else 'NOT_READY'
        cam = 'CONNECTED' if data.get('camera_ready') else 'PENDING/OFFLINE'
        scale = 'SOLVED' if data.get('scale_ready') else 'NOT_SET'
        mat = 'LOADED' if data.get('matrix_ready') else 'NOT_SET'
        print(f'VER={ver}\nCMT={cmt}\nPROC={proc}\nCAM={cam}\nSCALE={scale}\nMAT={mat}')
        sys.exit(0)
except Exception as ex:
    pass
sys.exit(1)
" 2>/dev/null || true)
        if [ -n "${HEALTH_OUTPUT}" ]; then
            HEALTH_SUCCESS=true
            break
        fi
    fi
    sleep 1
done

if [ "${HEALTH_SUCCESS}" = true ]; then
    eval "${HEALTH_OUTPUT}"
    echo -e "${GREEN}[✔] Tool Calibrator Vision Daemon đang HOẠT ĐỘNG trên cổng http://127.0.0.1:8090${NC}"
    echo -e "${GREEN}    [Layer 1: Dịch vụ lõi] Phiên bản: ${VER} (Commit: ${CMT}) | Daemon: ${PROC} | Port 8090: LISTENING${NC}"
    echo -e "${CYAN}    [Layer 2: Dữ liệu quang học] Camera: ${CAM} | Scale MPP: ${SCALE} | Affine Matrix: ${MAT}${NC}"
    if [ "${SCALE}" = "NOT_SET" ] || [ "${MAT}" = "NOT_SET" ]; then
        echo -e "${YELLOW}    (Lưu ý: Camera/Scale/Matrix sẽ sẵn sàng sau khi cấu hình camera và chạy CALIBRATE_CAMERA_SCALE)${NC}"
    fi
else
    echo -e "${RED}[ERR] Service không khởi động được hoặc phản hồi /health không hợp lệ!${NC}"
    echo -e "${YELLOW}Log chi tiết từ journalctl:${NC}"
    if [ "${SERVICE_MODE}" = "system" ]; then
        sudo journalctl -u tool_calibrator.service -n 30 --no-pager || true
    else
        journalctl --user -u tool_calibrator.service -n 30 --no-pager || true
    fi
    exit 1
fi

# Write persistent manifest for safe uninstall and upgrade tracking
python3 -c "
import json, time
manifest = {
    'installed_at': time.strftime('%Y-%m-%d %H:%M:%S'),
    'service_mode': '${SERVICE_MODE}',
    'config_subdir': '${CONFIG_SUBDIR}',
    'target_config_dir': '${TARGET_CONFIG_DIR}',
    'macro_dir': '${MACRO_DIR}',
    'offsets_cfg': '${OFFSETS_CFG}',
    'version': '${HEALTH_OUTPUT}'
}
with open('${PERSISTENT_MANIFEST}', 'w') as f:
    json.dump(manifest, f, indent=2)
" 2>/dev/null || true

# Clear temporary rollback journal
rm -f "${JOURNAL_FILE}"
trap - ERR

if [ "${RELOAD_SUCCESS}" = false ]; then
    echo -e "\n${YELLOW}====================================================${NC}"
    echo -e "${YELLOW}    LƯU Ý: CẦN KHỞI ĐỘNG LẠI DỊCH VỤ THỦ CÔNG       ${NC}"
    echo -e "${YELLOW}====================================================${NC}"
    echo -e "${YELLOW}[!] Do script chạy chế độ user và không có quyền sudo trực tiếp,${NC}"
    echo -e "${YELLOW}    hãy khởi động lại dịch vụ trong Mainsail/Fluidd hoặc chạy:${NC}"
    echo -e "    - sudo systemctl restart moonraker.service"
    echo -e "    - sudo systemctl restart klipper.service"
fi

echo -e "\n${GREEN}====================================================${NC}"
echo -e "${GREEN}    CÀI ĐẶT HOÀN TẤT THÀNH CÔNG!                     ${NC}"
echo -e "${GREEN}====================================================${NC}"
echo -e "${CYAN}Thêm DUY NHẤT 1 DÒNG vào printer.cfg (Quản lý kiểu kTAMV):${NC}"
if [ -n "${CONFIG_SUBDIR}" ]; then
    echo -e "   ${YELLOW}[include ${CONFIG_SUBDIR}/tool_calibrator.cfg]${NC}"
    echo -e "   (Hoặc: ${YELLOW}[include ${CONFIG_SUBDIR}/tool_calibrator/tool_calibrator.cfg]${NC})"
else
    echo -e "   ${YELLOW}[include tool_calibrator.cfg]${NC}"
    echo -e "   (Hoặc: ${YELLOW}[include tool_calibrator/tool_calibrator.cfg]${NC})"
fi
echo ""
echo -e "${CYAN}Toàn bộ cấu hình [tool_calibrator], macros và toạ độ an toàn${NC}"
echo -e "${CYAN}được quản lý tập trung trong file duy nhất này!${NC}"
echo -e "Vào Mainsail/Fluidd -> Settings -> Update Manager để kiểm tra trạng thái cập nhật!"
echo ""
