# PROJECT.vi.md — Mục Tiêu & Đặc Tả Kỹ Thuật Dự Án

> [!NOTE]
> English version available at: [PROJECT.md](PROJECT.md)

---

## 1. Tổng Quan Dự Án
- **Tên dự án:** Tool-Klipper-Calibration
- **Sứ mệnh:** Tự động hoá toàn diện, chính xác cao quy trình đo đạc và bù trừ sai số không gian (trục XY và Z) giữa các đầu phun (toolheads) trong hệ thống máy in 3D Klipper Toolchanger.
- **Phạm vi áp dụng:** Các dòng máy in 3D đa công cụ (StealthBurner Toolchanger, Jubilee, DXL, E3D Toolchanger, Voron Tap/Toolchanger) chạy firmware Klipper.

---

## 2. Mục Tiêu Cốt Lõi & Phạm Vi Tính Năng

### 2.1. Đo độ lệch XY tự động bằng Machine Vision
- Sử dụng camera macro hướng lên trên, gắn cố định vào khung máy hoặc bàn in.
- Tự động nhận diện tâm lỗ vòi phun (nozzle orifice circle) qua các thuật toán OpenCV.
- Lặp lại bước dịch chuyển đưa vòi phun về trục quang học trung tâm, tính toán tỷ lệ `mm/pixel` (`mpp`) và ma trận chuyển đổi không gian thực (Affine / Perspective).
- Đo độ lệch tương đối giữa Đầu phun Tham chiếu (mặc định `T0`) và các đầu phun thứ cấp (`T1`, `T2`, ... `Tn`).

### 2.2. Đo độ lệch Z đa hạ tầng linh hoạt
- **Backend 1 — Công tắc cơ khí / Switch Endstop:** Đầu phun chạm cơ khí vào một chốt kim loại hoặc microswitch (kế thừa logic probe đa trục từ `tools_calibrate`).
- **Backend 2 — Cảm biến Cartographer Touch Probe (V4):** Tận dụng công nghệ cảm ứng từ trường Cartographer 3D. Chạy `CARTOGRAPHER_TOUCH_HOME` trên đầu in tham chiếu và `CARTOGRAPHER_TOUCH_PROBE` trên các đầu in còn lại, đọc độ lệch `touch_model` để tính toán chênh lệch cao độ Z.

### 2.3. Điều hướng toạ độ an toàn động (Dynamic Safe Staging Navigation)
- Thay thế các toạ độ cố định cứng nhắc bằng mô hình toạ độ an toàn 3 tầng (`Safe_Z`, `Safe_Approach`, `Target`).
- Cho phép người dùng trực tiếp "dạy" và lưu toạ độ an toàn bằng thao tác lái máy (jog) qua lệnh `CALIBRATION_SET_SAFE_POS`.
- Bắt buộc nâng trục Z vượt qua cao độ an toàn trước khi di chuyển ngang giữa khay giữ tool (dock) và trạm đo.

### 2.4. Hệ thống chẩn đoán lỗi & Telemetry toàn diện
- Xây dựng bảng phân loại mã lỗi chuẩn (`ERR_PRE_xxx`, `ERR_CAM_xxx`, `ERR_CV_xxx`, `ERR_Z_xxx`, `ERR_CFG_xxx`).
- Đưa ra thông báo trạng thái trực quan kèm giải pháp xử lý thực tế trực tiếp lên Klipper Console và giao diện Web UI (Mainsail/Fluidd).

### 2.5. Tự động hoá hoàn toàn 1-chạm (1-Click Automation)
- Sau khi thiết lập toạ độ an toàn, người dùng chỉ cần gọi 1 lệnh macro duy nhất (ví dụ: `CALIBRATE_TOOL_OFFSETS`), hệ thống sẽ tự động thực hiện: kiểm tra an toàn, căn chỉnh vòi tham chiếu, lặp đo các vòi còn lại, lưu cấu hình và đưa máy về vị trí đỗ an toàn.

---

## 3. Thông Số Phần Cứng & Yêu Cầu Hạ Tầng

| Thành phần phần cứng | Thông số & Yêu cầu kỹ thuật |
| :--- | :--- |
| **Camera Module** | Kính hiển vi USB hoặc Raspberry Pi Camera hướng lên, cự ly lấy nét gần 15mm–40mm |
| **Hệ thống đèn LED** | Vòng LED trắng (Ring LED) 5V hoặc cụm 4 LED góc 45° tạo viền phản quang rõ nét cho lỗ vòi phun |
| **Luồng Video** | Endpoint snapshot MJPEG từ Crowsnest (ví dụ: `http://localhost/webcam2/snapshot?max_delay=0`) |
| **Cảm biến Z 1** | Công tắc cơ khí hành trình (Sexbolt / Omron microswitch) gắn cố định cạnh bàn in |
| **Cảm biến Z 2** | Cartographer 3D (V4) hỗ trợ chế độ chạm vòi phun (nozzle touch probing) |
| **Máy tính chủ (SBC)** | Raspberry Pi 3B+/4B/5 hoặc BTT CB1 chạy Linux, Python 3.9+, OpenCV, NumPy |
| **Firmware** | Klipper đã kích hoạt module Toolchanger (`toolchanger.py`) |
