# BACKUP.vi.md — Quy Chuẩn Sao Lưu Cấu Hình & Rollback Khẩn Cấp

> [!NOTE]
> English version available at: [BACKUP.md](BACKUP.md)

Tài liệu này quy định kiến trúc lưu trữ toạ độ, cơ chế tự động tạo bản sao lưu có timestamp và quy trình rollback khẩn cấp cho dự án **Tool-Klipper-Calibration**.

---

## 1. Kiến Trúc Cô Lập File Cấu Hình

Nhằm triệt tiêu nguy cơ làm hỏng file cấu hình máy in chính:
1. **Lưu trữ độc lập:** Toàn bộ toạ độ offset của các đầu phun được ghi vào một file cấu hình riêng biệt:  
   `~/printer_data/config/tool_offsets.cfg`
2. **Khai báo trong printer.cfg:** File `printer.cfg` chính chỉ cần thêm một dòng include duy nhất:  
   `[include tool_offsets.cfg]`
3. **Quy chuẩn định dạng:** Mỗi đầu phun được cập nhật các khoá offset chuẩn:
   ```ini
   [tool 1]
   gcode_x_offset: 0.142
   gcode_y_offset: -0.085
   gcode_z_offset: 0.310
   ```

---

## 2. Giao Thức Tự Động Sao Lưu Trước Khi Ghi

Trước khi lưu các toạ độ đo mới xuống ổ đĩa:
1. Trình quản lý cấu hình kiểm tra sự tồn tại của file `tool_offsets.cfg`.
2. Nếu file đã tồn tại, hệ thống tự động nhân bản một bản sao lưu có timestamp:
   ```text
   tool_offsets.cfg.calib_backup_YYYYMMDD_HHMMSS
   ```
   *Ví dụ:* `tool_offsets.cfg.calib_backup_20260905_075812`
3. **Cơ chế ghi file nguyên tử (Atomic Write):** Dữ liệu cấu hình mới được ghi vào một file tạm (`tool_offsets.cfg.tmp`), ép xả bộ đệm xuống đĩa (flush) rồi mới đổi tên thành `tool_offsets.cfg`. Đảm bảo an toàn 100% ngay cả khi mất điện đột ngột.
4. **Chính sách lưu trữ:** Hệ thống tự động lưu giữ 10 bản backup gần nhất và tự động dọn dẹp các bản cũ hơn để tiết kiệm dung lượng thẻ nhớ.

---

## 3. Quy Trình Rollback Khôi Phục Khẩn Cấp

### Tình huống: Kết quả đo bị sai lệch do vòi phun bẩn hoặc lỏng cơ khí
Nếu người dùng muốn hoàn tác toạ độ về thời điểm trước khi đo:

#### Cách 1: Sử dụng Macro trên Klipper Console
Gõ lệnh khôi phục:
```gcode
CALIBRATION_ROLLBACK_OFFSETS
```
Hệ thống sẽ tự động khôi phục bản backup gần nhất (`*.calib_backup_*`) và yêu cầu người dùng chạy `FIRMWARE_RESTART`.

#### Cách 2: Khôi phục thủ công qua Terminal SSH
Đăng nhập SSH vào máy in và thực hiện:
```bash
cd ~/printer_data/config
ls -lt tool_offsets.cfg.calib_backup_*
# Xác định bản backup mong muốn, sau đó ghi đè:
cp tool_offsets.cfg.calib_backup_20260905_075812 tool_offsets.cfg
```
Nhấn `FIRMWARE_RESTART` trên Mainsail hoặc Fluidd.
