# DECISIONS.vi.md — Bản Ghi Quyết Định Kiến Trúc (ADR)

> [!NOTE]
> English version available at: [DECISIONS.md](DECISIONS.md)

Tài liệu này lưu trữ các Quyết định Kiến trúc Cốt lõi (Architecture Decision Records - ADR) của dự án **Tool-Klipper-Calibration**, phân tích cội nguồn kỹ thuật, các đánh đổi và bài học đúc kết từ 3 dự án tham khảo (`Axiscope`, `kTAMV`, `TAMV`).

---

## ADR-001: Kiến Trúc Phân Tách Độc Lập (Decoupled Client - Service)

### Bối cảnh
Tiến trình chính `klippy` của Klipper chạy trên một event loop đơn luồng với yêu cầu thời gian thực nghiêm ngặt. Nếu một tác vụ chiếm CPU quá $25\text{ms} - 50\text{ms}$, Klipper sẽ dừng khẩn cấp: `Timer too close` hoặc `MCU Shutdown`. Việc xử lý ảnh OpenCV và giải ma trận thường mất từ $100\text{ms} - 400\text{ms}$ trên chip Raspberry Pi.

### Quyết định
Tách hệ thống thành 2 tiến trình độc lập:
1. **Klipper Module (`klippy/extras/tool_calibrator.py`):** Chạy bên trong Klipper, chỉ điều phối động học máy in, quản lý state machine và gửi truy vấn HTTP dạng non-blocking.
2. **Vision Service (`server/tool_calibrator_server.py`):** Chạy dưới dạng systemd daemon độc lập trên cổng 8090, tận dụng các nhân CPU riêng để xử lý OpenCV và NumPy.

### Lý do & So sánh tham chiếu
- *Axiscope:* Chạy server web nhẹ, nhưng không có xử lý ảnh. Nếu đưa OpenCV vào Klippy sẽ gây sập Klipper.
- *kTAMV:* Đã chứng minh tính ổn định tuyệt đối khi tách server Waitress riêng. Dự án kế thừa và hiện đại hoá mô hình này.
- *Đánh đổi:* Phải quản lý thêm một systemd service, được giải quyết hoàn toàn qua script cài đặt tự động.

---

## ADR-002: Hỗ Trợ Đôi Z-Backend (Switch Cơ Khí & Cartographer Touch)

### Bối cảnh
Cộng đồng máy in Toolchanger sử dụng nhiều loại cảm biến Z khác nhau: công tắc cơ khí truyền thống (Sexbolt endstop) hoặc cảm biến từ trường Cartographer 3D (V4) chạm trực tiếp đầu vòi phun.

### Quyết định
Áp dụng Adapter Pattern hỗ trợ hai backend linh hoạt do người dùng chọn:
- `z_backend: switch` $\rightarrow$ Sử dụng wrapper probe đa trục từ `tools_calibrate`.
- `z_backend: cartographer` $\rightarrow$ Điều phối `CARTOGRAPHER_TOUCH_HOME` trên tool tham chiếu và `CARTOGRAPHER_TOUCH_PROBE` trên các tool tiếp theo, đọc độ lệch từ touch-model.

### Lý do lựa chọn
- Tương thích tối đa với cả hệ thống máy in đời cũ lẫn các dòng máy in Voron/Jubilee hiện đại nhất mà không làm phân mảnh mã nguồn.

---

## ADR-003: Điều Hướng Toạ Độ An Toàn 3 Tầng (Dynamic Safe Navigation)

### Bối cảnh
Cả kTAMV và Axiscope đều sử dụng toạ độ cố định và chạy thẳng (`G0 X... Y...`). Nếu đầu vòi phun đang ở độ cao thấp hơn thành bảo vệ camera hoặc vướng dock tool, máy sẽ bị va quệt nghiêm trọng.

### Quyết định
Thiết lập mô hình toạ độ 3 tầng (`Safe_Z` $\rightarrow$ `Safe_Approach` $\rightarrow$ `Target`) kết hợp macro tương tác `CALIBRATION_SET_SAFE_POS`.

### Lý do lựa chọn
- Triệt tiêu hoàn toàn nguy cơ va chạm cơ khí theo phương xiên. Cho phép người dùng lái máy bằng tay để "dạy" toạ độ phù hợp với từng khung máy in.

---

## ADR-004: Thuật Toán Nhận Diện Vòi Phun 3 Tầng Kế Thừa Từ TAMV

### Bối cảnh
Lỗ vòi phun thường gặp vấn đề: loá sáng kim loại, cặn nhựa cháy đen, hoặc làm từ nhiều vật liệu khác nhau (đồng, thép tôi, ruby) khiến bộ lọc SimpleBlobDetector đơn lẻ dễ nhận diện trượt.

### Quyết định
Kế thừa cấu trúc nhận diện 3 tầng từ **TAMV**:
- **Tầng 1 (Standard):** Ngưỡng độ tròn khắt khe ($0.8 - 1.0$) và diện tích chuẩn.
- **Tầng 2 (Relaxed):** Nới lỏng độ tròn ($0.6 - 1.0$) và mở rộng dải diện tích.
- **Tầng 3 (Super-Relaxed):** Bỏ qua bộ lọc màu, tự động lắc nhẹ đầu in ($0.1\text{mm}$) để tìm tâm.

### Lý do lựa chọn
- Nâng tỷ lệ nhận diện thành công từ $70\%$ lên $> 98\%$ trong điều kiện ánh sáng thực tế mà không cần nạp mô hình AI nặng tốn RAM của Raspberry Pi.

---

## ADR-005: Chính Sách Một Nhánh Duy Nhất `main`

### Bối cảnh
Người dùng cập nhật extension trên máy in qua Moonraker Update Manager. Cấu trúc nhiều branch phức tạp thường dẫn đến xung đột khi pull code tự động.

### Quyết định
Thực thi chính sách duy nhất một nhánh **`main`** (Trunk-Based Development). Toàn bộ tính năng mới, sửa lỗi và tài liệu đều commit trực tiếp lên `main`.

### Lý do lựa chọn
- Đảm bảo tính năng cập nhật 1-click của Moonraker luôn mượt mà, không bao giờ gặp lỗi phân nhánh hay detached HEAD.

---

## ADR-006: Lưu Trữ Cấu Hình Độc Lập Trong `tool_offsets.cfg`

### Bối cảnh
Ghi đè trực tiếp vào `printer.cfg` (như Axiscope) tiềm ẩn rủi ro làm hỏng toàn bộ cấu hình máy in nếu xảy ra mất điện hoặc lỗi cú pháp trong quá trình ghi.

### Quyết định
Lưu toạ độ bù trừ vào file riêng `tool_offsets.cfg` (được include trong `printer.cfg` qua `[include tool_offsets.cfg]`), đồng thời tự động sao lưu backup có timestamp trước mỗi lần ghi file.

### Lý do lựa chọn
- Bảo vệ an toàn tuyệt đối cấu hình máy in gốc và cung cấp khả năng rollback tức thì.
