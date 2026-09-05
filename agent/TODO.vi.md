# TODO.vi.md — Danh Sách Công Việc (WBS) & Tiến Độ Dự Án

> [!NOTE]
> English version available at: [TODO.md](TODO.md)

Bảng checklist này theo dõi tiến độ thực hiện chi tiết xuyên suốt các giai đoạn của dự án **Tool-Klipper-Calibration**.

---

## 📊 Tổng Quan Các Giai Đoạn & Tiến Độ

- [x] **Giai đoạn 1: Chuẩn bị Kiến trúc, Hệ Thống Tài Liệu & Git** *(Đã hoàn thành)*
- [ ] **Giai đoạn 2: Xây dựng Dịch Vụ Xử Lý Thị Giác Máy (`server/`)**
- [ ] **Giai đoạn 3: Phát triển Klipper Core Extension (`klippy/extras/`)**
- [ ] **Giai đoạn 4: Xây dựng Bộ Macro G-Code & Hook Điều Khiển (`macros/`)**
- [ ] **Giai đoạn 5: Kiểm Thử Offline, Chạy Thử Dry-Run & Đánh Giá Sai Số**
- [ ] **Giai đoạn 6: Đóng Gói Cài Đặt Tự Động & Phát Hành Chính Thức**

---

## Danh Mục Công Việc Chi Tiết

### Giai đoạn 1: Kiến trúc, Tài liệu hoá & Thiết lập Git
- [x] **Task 1.1:** Khởi tạo Git repository trên nhánh duy nhất `main`, kết nối remote origin.
- [x] **Task 1.2:** Cấu hình file `.gitignore` tối ưu cho Python, Klipper và thư mục tham khảo cục bộ.
- [x] **Task 1.3:** Xây dựng bộ tài liệu kiến trúc song ngữ gồm 14 file chuyên biệt trong thư mục `agent/`.
- [x] **Task 1.4:** Tạo file `README.md` và `README.vi.md` tại thư mục gốc.

### Giai đoạn 2: Dịch vụ Xử lý Ảnh Nền (`server/`)
- [ ] **Task 2.1:** Viết `stream_grabber.py` kéo frame snapshot độ trễ thấp từ Crowsnest.
- [ ] **Task 2.2:** Xây dựng `nozzle_detector.py` với bộ lọc 3 tầng kế thừa từ TAMV (Standard, Relaxed, Super-Relaxed).
- [ ] **Task 2.3:** Phát triển `affine_transform.py` giải ma trận chuyển đổi tỷ lệ mm/pixel (`mpp`).
- [ ] **Task 2.4:** Viết `visual_debugger.py` xuất luồng preview MJPEG có vẽ tâm chữ thập và vòng tròn phát hiện.
- [ ] **Task 2.5:** Xây dựng server HTTP `tool_calibrator_server.py` cung cấp API REST JSON trên cổng 8090 (`/health`, `/detect_nozzle`, `/calibrate_mpp`, `/preview`).

### Giai đoạn 3: Klipper Core Extension (`klippy/extras/`)
- [ ] **Task 3.1:** Viết lớp điều phối trung tâm `tool_calibrator.py` và đăng ký các lệnh G-code.
- [ ] **Task 3.2:** Phát triển `safe_navigator.py` điều khiển toạ độ an toàn 3 tầng và chống va chạm.
- [ ] **Task 3.3:** Xây dựng interface trừu tượng `z_backends/base_z.py`.
- [ ] **Task 3.4:** Xây dựng `z_backends/switch_backend.py` hỗ trợ đo công tắc cơ khí switch endstop.
- [ ] **Task 3.5:** Xây dựng `z_backends/cartographer_backend.py` hỗ trợ Cartographer V4 Touch Home & Touch Probe.
- [ ] **Task 3.6:** Phát triển `config_manager.py` tự động ghi toạ độ nguyên tử và sao lưu backup có timestamp.

### Giai đoạn 4: Bộ Macro Người Dùng & Hook Tích Hợp (`macros/`)
- [ ] **Task 4.1:** Phát triển macro người dùng `CALIBRATE_TOOL_OFFSETS` hỗ trợ chọn tool và tham số dry-run.
- [ ] **Task 4.2:** Viết macro tương tác dạy toạ độ an toàn `CALIBRATION_SET_SAFE_POS`.
- [ ] **Task 4.3:** Tích hợp chuỗi hook vòng đời: `before_pickup_gcode`, `after_pickup_gcode`, `start_gcode`, `finish_gcode`.
- [ ] **Task 4.4:** Xây dựng macro hoàn tác khẩn cấp `CALIBRATION_ROLLBACK_OFFSETS`.

### Giai đoạn 5: Thử Nghiệm & Đánh Giá Độ Chính Xác
- [ ] **Task 5.1:** Chạy unit test giải thuật offline trên tập ảnh mẫu vòi phun trong các điều kiện sáng khác nhau.
- [ ] **Task 5.2:** Kiểm thử chuyển động không chạm thực tế (`DRY_RUN=1`) trên phần cứng máy in.
- [ ] **Task 5.3:** Đo kiểm định lặp lại 10 chu kỳ đạt $\sigma_{XY} \le 0.015\text{mm}$ và $\sigma_Z \le 0.008\text{mm}$.

### Giai đoạn 6: Kịch Bản Cài Đặt & Phát Hành
- [ ] **Task 6.1:** Viết kịch bản cài đặt tự động `scripts/install.sh` (tạo venv, cài thư viện, thiết lập systemd).
- [ ] **Task 6.2:** Viết kịch bản gỡ bỏ `scripts/uninstall.sh`.
- [ ] **Task 6.3:** Kiểm tra xác thực tính năng tự động cập nhật 1-click qua Moonraker.
