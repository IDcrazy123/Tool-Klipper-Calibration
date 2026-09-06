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
for arg in "$@"; do
    case "${arg}" in
        --user-service)
            SERVICE_MODE="user"
            ;;
        --system-service)
            SERVICE_MODE="system"
            ;;
        --help|-h)
            echo "Usage: ./scripts/install.sh [--system-service | --user-service]"
            echo "  --system-service : Install system-wide systemd unit in /etc/systemd/system (default, requires sudo)"
            echo "  --user-service   : Install user-level systemd unit in ~/.config/systemd/user (rootless)"
            exit 0
            ;;
        *)
            echo -e "${YELLOW}[!] Unknown option: ${arg}${NC}"
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
MANIFEST_FILE="${REPO_DIR}/.install_manifest.txt"

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

echo -e "${GREEN}[+] Repo directory:       ${REPO_DIR}${NC}"
echo -e "${GREEN}[+] Klipper directory:    ${KLIPPER_DIR}${NC}"
echo -e "${GREEN}[+] Config directory:     ${CONFIG_DIR}${NC}"
echo -e "${GREEN}[+] Python virtualenv:    ${VENV_DIR}${NC}"
echo -e "${GREEN}[+] Service mode:         ${SERVICE_MODE}${NC}"

# Setup Transactional Rollback
rm -f "${MANIFEST_FILE}"
touch "${MANIFEST_FILE}"

cleanup_on_error() {
    local exit_code=$?
    echo -e "\n${RED}[ERR] Quá trình cài đặt bị gián đoạn (Exit code: ${exit_code})! Đang hoàn tác...${NC}"
    if [ -f "${MANIFEST_FILE}" ]; then
        while IFS= read -r line || [ -n "${line}" ]; do
            key="$(echo "${line}" | cut -d'=' -f1)"
            val="$(echo "${line}" | cut -d'=' -f2-)"
            case "${key}" in
                SYMLINK)
                    [ -L "${val}" ] && rm -f "${val}"
                    ;;
                FILE)
                    [ -f "${val}" ] && rm -f "${val}"
                    ;;
                BACKUP)
                    src="$(echo "${val}" | cut -d':' -f1)"
                    dst="$(echo "${val}" | cut -d':' -f2)"
                    [ -f "${src}" ] && cp -f "${src}" "${dst}"
                    ;;
            esac
        done < "${MANIFEST_FILE}"
        rm -f "${MANIFEST_FILE}"
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
"${VENV_DIR}/bin/pip" install -r "${REPO_DIR}/server/requirements.txt"

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
    echo "SYMLINK=${TARGET}" >> "${MANIFEST_FILE}"
    echo -e "${GREEN}    Linked ${file} -> ${KLIPPY_EXTRAS}/${NC}"
done

for zb_file in "__init__.py" "base_z.py" "switch_backend.py" "cartographer_backend.py"; do
    TARGET="${KLIPPY_EXTRAS}/z_backends/${zb_file}"
    SOURCE="${REPO_DIR}/klippy/extras/z_backends/${zb_file}"
    ln -sf "${SOURCE}" "${TARGET}"
    echo "SYMLINK=${TARGET}" >> "${MANIFEST_FILE}"
    echo -e "${GREEN}    Linked z_backends/${zb_file} -> ${KLIPPY_EXTRAS}/z_backends/${NC}"
done

# Setup Macro Bundle under printer config directory
MACRO_DIR="${CONFIG_DIR}/tool_calibrator"
mkdir -p "${MACRO_DIR}"

for macro_file in "tool_calibrator_macros.cfg" "safe_staging_macros.cfg"; do
    TARGET="${MACRO_DIR}/${macro_file}"
    SOURCE="${REPO_DIR}/macros/${macro_file}"
    ln -sf "${SOURCE}" "${TARGET}"
    echo "SYMLINK=${TARGET}" >> "${MANIFEST_FILE}"
    echo -e "${GREEN}    Linked ${macro_file} -> ${MACRO_DIR}/${NC}"
done

# Ensure tool_offsets.cfg placeholder exists to prevent Klipper startup include errors
OFFSETS_CFG="${CONFIG_DIR}/tool_offsets.cfg"
if [ ! -f "${OFFSETS_CFG}" ]; then
    echo -e "${CYAN}[+] Khởi tạo tệp cấu hình ban đầu: ${OFFSETS_CFG}...${NC}"
    cat > "${OFFSETS_CFG}" << 'EOF'
# Tool-Klipper-Calibration Offsets File
# Auto-generated by Tool-Klipper-Calibration
# Include this in printer.cfg via: [include tool_offsets.cfg]

# Calibrated station waypoints and tool offsets will be automatically written here.
EOF
    echo "FILE=${OFFSETS_CFG}" >> "${MANIFEST_FILE}"
    echo -e "${GREEN}[✔] Đã tạo file ${OFFSETS_CFG} (ngăn lỗi missing include khi Klipper khởi động).${NC}"
fi

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
        echo "BACKUP=${BACKUP_CONF}:${MOONRAKER_CONF}" >> "${MANIFEST_FILE}"

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

# Khởi động lại Moonraker và Klipper để nhận diện service và module mới
if systemctl is-active --quiet moonraker.service 2>/dev/null; then
    echo -e "${GREEN}[+] Khởi động lại Moonraker...${NC}"
    sudo systemctl restart moonraker.service || true
fi
if systemctl is-active --quiet klipper.service 2>/dev/null; then
    echo -e "${GREEN}[+] Khởi động lại Klipper để nạp các module extras...${NC}"
    sudo systemctl restart klipper.service || true
fi

# 7. Verify Service Health
echo -e "\n${BLUE}[6/6] Kiểm tra trạng thái Vision Server daemon...${NC}"
HEALTH_SUCCESS=false
VERSION_INFO=""
for i in {1..10}; do
    HEALTH_RESP=$(curl -sS --fail --max-time 3 http://127.0.0.1:8090/health 2>/dev/null || true)
    if [ -n "${HEALTH_RESP}" ]; then
        HEALTH_CHECK=$(python3 -c "
import sys, json
try:
    data = json.loads('''${HEALTH_RESP}''')
    if data.get('status') == 'ok' and data.get('service') == 'tool_calibrator_server':
        v = data.get('version', '')
        c = data.get('commit', '')
        print(f'OK {v} {c}'.strip())
        sys.exit(0)
except Exception:
    pass
sys.exit(1)
" 2>/dev/null || true)
        if [[ "${HEALTH_CHECK}" =~ ^OK ]]; then
            HEALTH_SUCCESS=true
            VERSION_INFO=$(echo "${HEALTH_CHECK}" | cut -d' ' -f2-)
            break
        fi
    fi
    sleep 1
done

if [ "${HEALTH_SUCCESS}" = true ]; then
    echo -e "${GREEN}[✔] Tool Calibrator Vision Daemon đang HOẠT ĐỘNG trên cổng http://127.0.0.1:8090 (${VERSION_INFO})${NC}"
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

# Clear manifest on clean success
rm -f "${MANIFEST_FILE}"
trap - ERR

echo -e "\n${GREEN}====================================================${NC}"
echo -e "${GREEN}    CÀI ĐẶT HOÀN TẤT THÀNH CÔNG!                     ${NC}"
echo -e "${GREEN}====================================================${NC}"
echo -e "${CYAN}Các bước tiếp theo trong printer.cfg:${NC}"
echo -e "1. Thêm include macro:"
echo -e "   ${YELLOW}[include tool_calibrator/tool_calibrator_macros.cfg]${NC}"
echo -e "   ${YELLOW}[include tool_calibrator/safe_staging_macros.cfg]${NC}"
echo -e "   ${YELLOW}[include tool_offsets.cfg]${NC}"
echo -e "2. Cấu hình khối [tool_calibrator] (tham khảo macros/sample_tool_calibrator.cfg)."
echo -e "3. Vào Mainsail/Fluidd -> Settings -> Update Manager để kiểm tra trạng thái cập nhật!"
echo ""
