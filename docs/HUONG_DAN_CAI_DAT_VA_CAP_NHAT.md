# HƯỚNG DẪN CÀI ĐẶT, TỰ ĐỘNG CẬP NHẬT VÀ GỠ BỎ
## Tool-Klipper-Calibration (Hệ Sinh Thái Klipper & Moonraker)

Tài liệu này cung cấp lộ trình chi tiết, chuẩn mực theo best practices của các dự án lớn trong hệ sinh thái Klipper (như Axiscope, kTAMV, Crowsnest) để cài đặt, cấu hình tự động cập nhật qua Moonraker Update Manager, và gỡ bỏ sạch sẽ **Tool-Klipper-Calibration**.

---

## 1. Tổng Quan Kiến Trúc Hệ Thống

Hệ thống hoạt động theo mô hình tích hợp 4 thành phần:

```mermaid
graph TD
    A[Klipper Extras: tool_calibrator] -->|HTTP REST API port 8090| B[Vision Daemon: tool_calibrator_server]
    B -->|Chụp ảnh Snapshot HTTP| C[Camera / Crowsnest / MJPEG Stream]
    D[Mainsail / Fluidd UI] -->|Update Manager & Service Control| E[Moonraker]
    E -->|Kiểm soát dịch vụ via moonraker.asvc| B
    E -->|Restart Service| A
```

1. **Vision Daemon (`tool_calibrator_server.py`)**:
   - Chạy nền dưới dạng `systemd` service (`tool_calibrator.service`) trên máy chủ Klipper (Raspberry Pi, CB1, Orange Pi, PC Linux).
   - Sử dụng môi trường ảo Python cô lập (`~/Tool-Klipper-Calibration/env`) với OpenCV Headless siêu nhẹ và WSGI Waitress production server tại cổng `8090`.
2. **Klipper Module Extras (`klippy/extras/`)**:
   - Được liên kết tượng trưng (symlink) trực tiếp từ thư mục mã nguồn vào `~/klipper/klippy/extras/`:
     - `tool_calibrator.py`: Điều phối quy trình căn chỉnh tự động.
     - `tool_calibrator_station.py`: Quản lý trạm căn chỉnh, tính toán ma trận Affine 2D/3D.
     - `safe_navigator.py`: Định tuyến di chuyển an toàn đa đầu in, tránh va chạm giá đỡ.
     - `config_manager.py`: Lưu trữ offset và thông số vào cấu hình Klipper tự động.
     - `z_backends/`: Hỗ trợ đa cảm biến Z (`switch_backend.py`, `cartographer_backend.py`,...).
3. **Moonraker ASVC & Update Manager**:
   - Quản trị viên cập nhật trực tiếp 1-click từ Mainsail / Fluidd.
   - Khai báo phân quyền trong `moonraker.asvc` để Moonraker được phép restart daemon khi cập nhật mà không bị lỗi PolicyKit.

---

## 2. Lộ Trình Cài Đặt Từng Bước (Installation)

### Bước 2.1: Truy cập Terminal qua SSH
Kết nối SSH vào máy in của bạn (sử dụng tài khoản thông thường như `pi`, `btt`, `sovol`, v.v. **Tuyệt đối không dùng root**):
```bash
ssh pi@<dia_chi_ip_may_in>
```

### Bước 2.2: Clone mã nguồn từ GitHub
Tải mã nguồn về thư mục người dùng (`~`):
```bash
cd ~
git clone https://github.com/IDcrazy123/Tool-Klipper-Calibration.git
```

### Bước 2.3: Chạy script cài đặt tự động
Di chuyển vào thư mục dự án và thực thi installer:
```bash
cd ~/Tool-Klipper-Calibration
./scripts/install.sh
# Hoặc chế độ user-service (rootless, không cần quyền sudo):
# ./scripts/install.sh --user-service

# Hoặc nếu máy in tổ chức cấu hình theo thư mục con (ví dụ: Printer-Setup):
# ./scripts/install.sh --user-service --config-subdir Printer-Setup
```

**Bộ cài đặt sẽ tự động thực hiện:**
1. Kiểm tra môi trường hệ thống (ngăn ngừa chạy nhầm bằng `sudo`).
2. Kiểm tra các gói hệ thống cần thiết (`libgl1`, `libglib2.0-0`, `curl`) và khả năng tạo virtualenv.
3. Khởi tạo Python virtual environment độc lập tại `~/Tool-Klipper-Calibration/env`.
4. Cài đặt các thư viện xử lý ảnh: `opencv-python-headless`, `numpy`, `flask`, `waitress` với file constraints ổn định.
5. Tạo symlink các module Klipper extras vào `~/klipper/klippy/extras/` và symlink bộ macro vào thư mục cấu hình mục tiêu (`~/printer_data/config/[subdir/]tool_calibrator/`).
6. Khởi tạo sẵn tệp `tool_offsets.cfg` (ngăn lỗi `Include file does not exist` khi khởi động Klipper).
7. Cấu hình Moonraker (ASVC allowlist, khối `[update_manager tool_calibrator]`, hook tự động restart daemon khi update).
8. Đăng ký và kích hoạt dịch vụ `tool_calibrator.service` (hỗ trợ cả `--system-service` và `--user-service`).
9. Khởi động lại Klipper và Moonraker qua API hoặc systemd để nạp ngay các module mới.
10. Kiểm tra nghiêm ngặt phản hồi JSON từ endpoint `/health` trên cổng `8090`.

### Bước 2.4: Kiểm tra trạng thái dịch vụ sau khi cài
Kiểm tra xem Vision Server đã sẵn sàng nhận lệnh hay chưa:
```bash
curl -s http://127.0.0.1:8090/health
```
Kết quả mong đợi:
```json
{
  "status": "ok",
  "service": "tool_calibrator_server",
  "version": "v0.8.19",
  "commit": "6c721e5",
  "process_ready": true,
  "camera_ready": true,
  "scale_ready": false,
  "matrix_ready": false,
  "matrix_solved": false,
  "session_locked": false
}
```

Kiểm tra hoặc khởi động lại service:
```bash
# Đối với system service:
sudo systemctl status tool_calibrator.service
sudo systemctl restart tool_calibrator.service

# Đối với user service:
systemctl --user status tool_calibrator.service
systemctl --user restart tool_calibrator.service

# Hoặc dùng công cụ tích hợp tự nhận diện:
./scripts/restart_service.sh
```

---

## 3. Cấu Hình Klipper (`printer.cfg`)

### Bước 3.1: Nạp bộ Macros
> **Lưu ý quan trọng về đường dẫn Include trong Klipper:**
> Klipper phân giải đường dẫn `[include ...]` tương đối so với thư mục chứa file cấu hình (`~/printer_data/config/`) và **không hỗ trợ dấu ngã `~`**.
> Vì script cài đặt đã tự động tạo symlink vào `printer_data/config/tool_calibrator/`, bạn chỉ cần khai báo đường dẫn tương đối sau trong `printer.cfg`:

```ini
[include tool_calibrator/tool_calibrator_macros.cfg]
[include tool_calibrator/safe_staging_macros.cfg]
[include tool_offsets.cfg]
```
*(Hoặc dùng đường dẫn tuyệt đối đầy đủ: `[include /home/pi/Tool-Klipper-Calibration/macros/tool_calibrator_macros.cfg]`)*

### Bước 3.2: Khai báo cấu hình Trạm Căn Chỉnh
Thêm khối cấu hình `[tool_calibrator]` chuẩn vào `printer.cfg`:

```ini
[tool_calibrator]
service_url: http://127.0.0.1:8090
camera_stream_url: http://127.0.0.1:8080/?action=snapshot
offsets_config_path: ~/printer_data/config/tool_offsets.cfg
safe_z: 35.0
travel_speed: 12000
approach_speed: 3000
z_backend: switch    # Chọn 'switch' (công tắc cơ) hoặc 'cartographer' (chạm dò điện từ/quang)

# ----------------------------------------------------------------------------
# Tùy chọn nâng cao khi z_backend là switch (tự động kế thừa nếu dùng tools_calibrate):
# ----------------------------------------------------------------------------
# probing_speed: 3.0
# lift_speed: 5.0
# samples: 3
# samples_tolerance: 0.010
# samples_retract_dist: 2.0
# switch_pin: ^PG12  # Để trống nếu tools_calibrate đã khai báo pin này
```

> **Ghi chú về camera_stream_url:**
> Hệ thống hỗ trợ cả dạng Snapshot (`?action=snapshot`, `/snapshot`, `/snapshot.jpg`) và Stream. Bộ giải mã sẽ tự động chuẩn hóa URL để lấy ảnh tức thời mà không gây trễ hình.
> Tọa độ trạm camera và trạm switch sẽ được tự động học và lưu vĩnh viễn vào `tool_offsets.cfg` thông qua các lệnh 1-Click `AUTO_TEACH_CAMERA` và `AUTO_TEACH_SWITCH`.

Sau khi sửa xong, nhấn **Save & Restart** Klipper.

---

## 4. Cấu Hình & Sử Dụng Moonraker Update Manager

### Bước 4.1: Khối cấu hình trong `moonraker.conf`
Script `install.sh` đã tự động thêm khối này. Nếu bạn muốn kiểm tra hoặc cấu hình thủ công, mở file `~/printer_data/config/moonraker.conf`:

```ini
[update_manager tool_calibrator]
type: git_repo
path: ~/Tool-Klipper-Calibration
origin: https://github.com/IDcrazy123/Tool-Klipper-Calibration.git
primary_branch: main
virtualenv: ~/Tool-Klipper-Calibration/env
requirements: server/requirements.txt
is_system_service: True
managed_services:
    tool_calibrator
    klipper
info_tags:
    desc=Tool-Klipper-Calibration Automated Vision & Z Alignment
```

### Bước 4.2: Tầm quan trọng của `moonraker.asvc`
Moonraker yêu cầu mọi dịch vụ bên thứ ba do người dùng quản lý phải nằm trong danh sách trắng (allowlist).
File `~/printer_data/moonraker.asvc` phải có dòng:
```text
tool_calibrator
```
*Nhờ đó, người dùng có thể Restart hoặc Stop dịch vụ Vision Server trực tiếp trên bảng điều khiển Service của Mainsail/Fluidd.*

### Bước 4.3: Cách Cập Nhật Bằng 1-Click
Khi có phiên bản mới trên GitHub:
1. Mở giao diện web **Mainsail** hoặc **Fluidd**.
2. Vào mục **Settings (Cài đặt)** -> **Update Manager (Quản lý cập nhật)**.
3. Bạn sẽ thấy mục `tool_calibrator` hiển thị trạng thái commit.
4. Nhấn nút **Update** (hoặc **Update All**):
   - Moonraker sẽ tự động `git pull` mã nguồn mới nhất.
   - Tự động kích hoạt virtualenv cập nhật thư viện python nếu `server/requirements.txt` có thay đổi.
   - Tự động khởi động lại `tool_calibrator.service` và `klipper.service`.

---

## 5. Quy Trình Gỡ Bỏ Sạch Sẽ (Clean Uninstallation)

Nếu bạn không còn nhu cầu sử dụng và muốn đưa máy in về trạng thái ban đầu:

### Bước 5.1: Chạy script gỡ cài đặt
```bash
cd ~/Tool-Klipper-Calibration
./scripts/uninstall.sh
# Hoặc nếu muốn giữ lại file cấu hình tool_offsets.cfg:
# ./scripts/uninstall.sh --keep-data

# Hoặc nếu máy in sử dụng thư mục con cấu hình:
# ./scripts/uninstall.sh --config-subdir Printer-Setup
```

**Script sẽ tự động:**
1. Dừng và vô hiệu hóa `tool_calibrator.service` (tự động phát hiện cả chế độ system và user).
2. Xóa file service unit (`/etc/systemd/system/tool_calibrator.service` hoặc `~/.config/systemd/user/...`).
3. Gỡ bỏ an toàn các symlinks do TKC tạo trong `~/klipper/klippy/extras/` (tuyệt đối không xóa nhầm tệp của bên thứ ba).
4. Gỡ bỏ symlink macros mà không dùng `rm -rf` (chỉ xóa symlink macros của TKC và dọn thư mục rỗng).
5. Tự động tìm và comment an toàn các dòng `[include ...tool_offsets.cfg]` trong `printer.cfg` (có lưu bản sao lưu timestamp) để ngăn lỗi Klipper không thể khởi động do thiếu tệp include.
6. Lưu trữ tệp cấu hình `tool_offsets.cfg` thành bản sao lưu timestamp `.archived_YYYYMMDD_HHMMSS` (trừ khi bật `--keep-data`).
7. Xóa `tool_calibrator` khỏi `~/printer_data/moonraker.asvc`.
8. Gỡ bỏ khối `[update_manager tool_calibrator]` khỏi `moonraker.conf` (có lưu bản backup timestamp).
9. Xóa môi trường ảo `~/Tool-Klipper-Calibration/env`.
10. Tự động gửi lệnh khởi động lại Klipper và Moonraker qua API hoặc systemd.

### Bước 5.2: Bảng kiểm tra tàn dư sau gỡ cài đặt (Residue Checklist)

| Mục kiểm tra | Chế độ System Service | Chế độ User Service | Trạng thái sau gỡ |
|---|---|---|---|
| Dịch vụ chạy nền | `/etc/systemd/system/tool_calibrator.service` | `~/.config/systemd/user/tool_calibrator.service` | Đã xóa, cổng 8090 đóng |
| Klipper Extras | `~/klipper/klippy/extras/tool_calibrator*.py` | `~/klipper/klippy/extras/tool_calibrator*.py` | Đã gỡ symlinks |
| Macros | `[config_dir]/tool_calibrator/` | `[config_dir]/tool_calibrator/` | Đã xóa symlinks |
| Moonraker ASVC | `~/printer_data/moonraker.asvc` | N/A | Đã xóa dòng `tool_calibrator` |
| Update Manager | Khối `[update_manager tool_calibrator]` | Khối `[update_manager tool_calibrator]` | Đã dọn sạch |
| Includes trong `printer.cfg` | Dòng `[include ...tool_offsets.cfg]` | Dòng `[include ...tool_offsets.cfg]` | Đã tự động comment `#` |
| Dữ liệu đã lưu | `tool_offsets.cfg` | `tool_offsets.cfg` | Đã chuyển thành `.archived_*` |

---

## 6. Xử Lý Sự Cố Thường Gặp (Troubleshooting)

### 1. Vision Server không chạy / Lỗi cổng 8090
- **Hiện tượng:** Lệnh `curl http://127.0.0.1:8090/health` báo `Connection refused`.
- **Cách xử lý:**
  Kiểm tra log chi tiết:
  ```bash
  journalctl -u tool_calibrator.service -n 50 --no-pager
  ```
  Nếu trùng cổng 8090 với một dịch vụ khác, bạn có thể chỉnh tham số `--port <cổng_mới>` trong file `/etc/systemd/system/tool_calibrator.service`, sau đó chạy `sudo systemctl daemon-reload && sudo systemctl restart tool_calibrator`. Đồng thời sửa `server_url` trong `printer.cfg`.

### 2. Moonraker báo "Permission Denied" khi cập nhật hoặc khởi động lại
- **Hiện tượng:** Nhấn Update hoặc restart trên Mainsail báo lỗi phân quyền policykit.
- **Cách xử lý:**
  Đảm bảo dòng `tool_calibrator` đã có trong file `~/printer_data/moonraker.asvc`:
  ```bash
  echo "tool_calibrator" >> ~/printer_data/moonraker.asvc
  sudo systemctl restart moonraker
  ```

### 3. Klipper báo lỗi "Unknown pin" hoặc "Section not found"
- **Hiện tượng:** Klipper dừng khẩn cấp khi khởi động.
- **Cách xử lý:**
  - Kiểm tra xem các symlink đã có trong `~/klipper/klippy/extras/` hay chưa:
    ```bash
    ls -l ~/klipper/klippy/extras/tool_calibrator*
    ```
  - Khởi động lại Klipper: `sudo systemctl restart klipper`.
