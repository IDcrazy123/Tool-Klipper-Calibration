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

# 0. Check user permissions (Do NOT run as root/sudo directly)
if [ "${EUID}" -eq 0 ]; then
    echo -e "${RED}[ERR] Vui lòng KHÔNG chạy script này bằng sudo hoặc root!${NC}"
    echo -e "${YELLOW}      Hãy chạy bằng tài khoản người dùng thông thường (ví dụ: pi, btt).${NC}"
    echo -e "${YELLOW}      Script sẽ tự động yêu cầu quyền sudo khi cần thiết.${NC}"
    exit 1
fi

# 1. Resolve paths
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CURRENT_USER="$(id -un)"
VENV_DIR="${REPO_DIR}/env"

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

# 2. System dependencies (Python3, venv, libgl1 for OpenCV headless)
echo -e "\n${BLUE}[1/6] Kiểm tra các gói hệ thống (apt dependencies)...${NC}"
MISSING_PKGS=()
for pkg in python3 python3-pip python3-venv curl libgl1 libglib2.0-0; do
    if ! dpkg -s "${pkg}" >/dev/null 2>&1; then
        MISSING_PKGS+=("${pkg}")
    fi
done

if [ ${#MISSING_PKGS[@]} -gt 0 ]; then
    echo -e "${YELLOW}[+] Đang cài đặt các gói hệ thống còn thiếu: ${MISSING_PKGS[*]}${NC}"
    sudo apt-get update
    sudo apt-get install -y "${MISSING_PKGS[@]}"
else
    echo -e "${GREEN}[✔] Đầy đủ các gói hệ thống cần thiết.${NC}"
fi

# 3. Setup Virtualenv & Dependencies
echo -e "\n${BLUE}[2/6] Thiết lập Python Virtual Environment...${NC}"
if [ ! -d "${VENV_DIR}" ]; then
    python3 -m venv "${VENV_DIR}"
fi

echo -e "${GREEN}[+] Đang nâng cấp pip và cài đặt thư viện từ server/requirements.txt...${NC}"
"${VENV_DIR}/bin/pip" install --upgrade pip
"${VENV_DIR}/bin/pip" install -r "${REPO_DIR}/server/requirements.txt"

# 4. Link Klipper Extras
echo -e "\n${BLUE}[3/6] Tạo liên kết tượng trưng (Symlinks) vào Klipper extras...${NC}"
KLIPPY_EXTRAS="${KLIPPER_DIR}/klippy/extras"

mkdir -p "${KLIPPY_EXTRAS}/z_backends"

for file in "tool_calibrator.py" "tool_calibrator_station.py" "tool_offsets.py" "safe_navigator.py" "config_manager.py"; do
    TARGET="${KLIPPY_EXTRAS}/${file}"
    SOURCE="${REPO_DIR}/klippy/extras/${file}"
    ln -sf "${SOURCE}" "${TARGET}"
    echo -e "${GREEN}    Linked ${file} -> ${KLIPPY_EXTRAS}/${NC}"
done

for zb_file in "__init__.py" "base_z.py" "switch_backend.py" "cartographer_backend.py"; do
    TARGET="${KLIPPY_EXTRAS}/z_backends/${zb_file}"
    SOURCE="${REPO_DIR}/klippy/extras/z_backends/${zb_file}"
    ln -sf "${SOURCE}" "${TARGET}"
    echo -e "${GREEN}    Linked z_backends/${zb_file} -> ${KLIPPY_EXTRAS}/z_backends/${NC}"
done

# 4b. Setup Macro Bundle under printer config directory
echo -e "\n${BLUE}[+] Tạo thư mục Macro trong Klipper config (${CONFIG_DIR}/tool_calibrator)...${NC}"
MACRO_DIR="${CONFIG_DIR}/tool_calibrator"
mkdir -p "${MACRO_DIR}"

for macro_file in "tool_calibrator_macros.cfg" "safe_staging_macros.cfg"; do
    TARGET="${MACRO_DIR}/${macro_file}"
    SOURCE="${REPO_DIR}/macros/${macro_file}"
    ln -sf "${SOURCE}" "${TARGET}"
    echo -e "${GREEN}    Linked ${macro_file} -> ${MACRO_DIR}/${NC}"
done

# 5. Moonraker Allowed Services (ASVC)
echo -e "\n${BLUE}[4/6] Cấu hình quyền dịch vụ cho Moonraker (moonraker.asvc)...${NC}"
ASVC_FILE="${HOME}/printer_data/moonraker.asvc"
if [ -d "${HOME}/printer_data" ]; then
    if [ ! -f "${ASVC_FILE}" ]; then
        touch "${ASVC_FILE}"
    fi
    if ! grep -q "^tool_calibrator$" "${ASVC_FILE}" 2>/dev/null; then
        [ -s "${ASVC_FILE}" ] && echo "" >> "${ASVC_FILE}"
        echo "tool_calibrator" >> "${ASVC_FILE}"
        echo -e "${GREEN}[✔] Đã thêm 'tool_calibrator' vào ${ASVC_FILE}${NC}"
    else
        echo -e "${GREEN}[✔] 'tool_calibrator' đã có sẵn trong ${ASVC_FILE}${NC}"
    fi
fi

# Tích hợp moonraker.conf nếu có
MOONRAKER_CONF="${CONFIG_DIR}/moonraker.conf"
if [ -f "${MOONRAKER_CONF}" ]; then
    if ! grep -q "\[update_manager tool_calibrator\]" "${MOONRAKER_CONF}"; then
        echo -e "${CYAN}[+] Tự động thêm [update_manager tool_calibrator] vào ${MOONRAKER_CONF}...${NC}"
        TIMESTAMP="$(date +%Y%m%d_%H%M%S)"
        BACKUP_CONF="${MOONRAKER_CONF}.bak_${TIMESTAMP}"
        cp "${MOONRAKER_CONF}" "${BACKUP_CONF}"
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

        echo -e "${GREEN}[✔] Đã cấu hình Update Manager trong moonraker.conf! (Đã sao lưu: ${BACKUP_CONF})${NC}"
    else
        echo -e "${GREEN}[✔] Khối [update_manager tool_calibrator] đã tồn tại trong moonraker.conf.${NC}"
    fi
fi

# 6. Install and start systemd service
echo -e "\n${BLUE}[5/6] Cấu hình và kích hoạt systemd service...${NC}"
SERVICE_FILE="/etc/systemd/system/tool_calibrator.service"
TEMP_SERVICE="/tmp/tool_calibrator.service"

sed -e "s|%USER%|${CURRENT_USER}|g" \
    -e "s|%REPO_DIR%|${REPO_DIR}|g" \
    -e "s|%VENV_DIR%|${VENV_DIR}|g" \
    "${REPO_DIR}/scripts/tool_calibrator.service" > "${TEMP_SERVICE}"

sudo mv "${TEMP_SERVICE}" "${SERVICE_FILE}"
sudo chown root:root "${SERVICE_FILE}"
sudo chmod 644 "${SERVICE_FILE}"

echo -e "${GREEN}[+] Reloading systemd daemon và khởi động service...${NC}"
sudo systemctl daemon-reload
sudo systemctl enable tool_calibrator.service
sudo systemctl restart tool_calibrator.service

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
    sudo journalctl -u tool_calibrator.service -n 30 --no-pager || true
    echo -e "${RED}[!] Quá trình cài đặt bị gián đoạn. Vui lòng kiểm tra lỗi trước khi tiếp tục.${NC}"
    exit 1
fi

echo -e "\n${GREEN}====================================================${NC}"
echo -e "${GREEN}    CÀI ĐẶT HOÀN TẤT THÀNH CÔNG!                     ${NC}"
echo -e "${GREEN}====================================================${NC}"
echo -e "${CYAN}Các bước tiếp theo:${NC}"
echo -e "1. Thêm include macro vào file printer.cfg của bạn:"
echo -e "   ${YELLOW}[include tool_calibrator/tool_calibrator_macros.cfg]${NC}"
echo -e "2. Cấu hình khối [tool_calibrator] trong printer.cfg (tham khảo macros/sample_tool_calibrator.cfg)."
echo -e "3. Vào Mainsail/Fluidd -> Settings -> Update Manager để kiểm tra trạng thái cập nhật!"
echo ""
