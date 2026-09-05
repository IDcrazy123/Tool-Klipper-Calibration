# LOGGING.vi.md — Quy Chuẩn Mã Lỗi & Định Dạng Telemetry

> [!NOTE]
> English version available at: [LOGGING.md](LOGGING.md)

Tài liệu này quy định bảng phân loại mã lỗi chuẩn hoá, định dạng thông báo log và quy tắc phản hồi telemetry cho dự án **Tool-Klipper-Calibration**.

---

## 1. Bảng Phân Loại Mã Lỗi Chuẩn (Standardized Error Taxonomy)

| Mã lỗi | Nhóm lỗi | Nguyên nhân gốc rễ | Hành động tự động & Chỉ dẫn khắc phục |
| :--- | :--- | :--- | :--- |
| **`ERR_PRE_001`** | Tiền kiểm tra | Các trục máy in chưa được home hoàn toàn | Hủy lệnh lập tức. Thông báo: *"Cần chạy G28 home máy trước khi hiệu chuẩn."* |
| **`ERR_PRE_002`** | Tiền kiểm tra | Nhiệt độ đầu phun $> 100^\circ\text{C}$ | Dừng máy để chống biến dạng quang học. Thông báo: *"Chờ vòi phun nguội $\le 100^\circ\text{C}$."* |
| **`ERR_PRE_003`** | Tiền kiểm tra | Chưa kẹp tool hoặc cơ cấu khoá toolchanger bị lỗi | Dừng máy. Thông báo: *"Kiểm tra trạng thái kẹp/khoá của carridge toolchanger."* |
| **`ERR_CAM_101`** | Thị giác | Vision Service offline (không kết nối được cổng 8090) | Dừng máy. Thông báo: *"Daemon xử lý ảnh chưa chạy. Chạy: sudo systemctl restart tool_calibrator."* |
| **`ERR_CAM_102`** | Thị giác | Stream Crowsnest bị timeout hoặc URL không đúng | Cảnh báo: *"Kiểm tra lại URL snapshot webcam trong printer.cfg."* |
| **`ERR_CV_201`** | Giải thuật | Không tìm thấy lỗ nozzle sau các lần dò | Lắc nhẹ đầu in ($0.1\text{mm}$). Nếu vẫn lỗi: *"Lau sạch vòi phun hoặc chỉnh đèn LED."* |
| **`ERR_CV_202`** | Giải thuật | Độ tròn hoặc diện tích không đạt chuẩn ($< 0.6$) | Cảnh báo: *"Đầu vòi phun bị dính nhựa biến dạng hoặc lệch tiêu cự Z."* |
| **`ERR_CV_203`** | Giải thuật | Đo tỷ lệ mm/pixel thất bại ($> 25\%$ điểm đo bị lệch) | Hủy quy trình: *"Trục camera bị lệch nghiêng. Kiểm tra độ vững gá camera."* |
| **`ERR_CV_204`** | Giải thuật | Toạ độ tính toán vượt quá biên khung hình | Chặn di chuyển: *"Toạ độ tính toán nằm ngoài camera. Kiểm tra căn chỉnh ống kính."* |
| **`ERR_Z_301`** | Probe Z | Switch không kích hoạt trong hành trình đo | Hủy chuyển động probe: *"Công tắc Z không phản hồi. Kiểm tra dây tín hiệu hoặc toạ độ XY."* |
| **`ERR_Z_302`** | Probe Z | Cartographer Touch trả về giá trị null hoặc không hợp lệ | Hủy probe: *"Cartographer Touch thất bại. Kiểm tra kết nối CAN bus và touch model."* |
| **`ERR_Z_303`** | Probe Z | Sai số giữa các lần lấy mẫu quá lớn ($> 0.05\text{mm}$) | Thử lấy mẫu lại 1 lần; nếu vẫn lỗi: *"Phát hiện độ rơ cơ khí hoặc lỏng chân đế tool."* |
| **`ERR_CFG_401`** | Lưu trữ | Không thể mở hoặc ghi đè file cấu hình | Dừng quy trình lưu: *"Lỗi ghi file tool_offsets.cfg. Kiểm tra quyền ghi file."* |

---

## 2. Định Dạng Xuất Log Ra Klipper Console

Toàn bộ thông báo telemetry xuất ra Klipper Console đều tuân theo mẫu chuẩn:

```text
// [TOOL_CALIB] [CẤP_ĐỘ] [CÔNG_CỤ] Nội dung thông báo
//   -> Toạ độ / Bối cảnh thực tế
//   -> Hành động khuyến nghị cho người dùng
```

### Ví dụ 1: Thông báo trạng thái hoạt động bình thường
```text
// [TOOL_CALIB] [INFO] [T1] Đang tiếp cận Trạm Camera (X:150.00, Y:300.00, Z:25.00)...
// [TOOL_CALIB] [INFO] [T1] Đã phát hiện vòi phun tại UV (320.5, 240.1), Độ tin cậy: 98.4%, Tầng: Standard
// [TOOL_CALIB] [INFO] [T1] Đã tính xong Offset: X:+0.142 Y:-0.085 Z:+0.310
```

### Ví dụ 2: Thông báo sự cố và cảnh báo
```text
// [TOOL_CALIB] [ERROR] [T2] [ERR_CV_201] Không tìm thấy vòi phun sau 3 lần dò!
//   -> Vị trí máy: X:150.2 Y:300.1 Z:12.0
//   -> Hướng dẫn: Lau sạch cặn nhựa bám quanh đầu phun hoặc điều chỉnh độ sáng đèn LED.
//   -> Trạng thái: Dừng an toàn. Đầu in đã được nâng lên Safe_Z (25.00mm).
```
