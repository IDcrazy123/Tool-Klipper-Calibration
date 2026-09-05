# SAFETY.vi.md — Điều Hướng An Toàn & Bảo Vệ Phần Cứng

> [!NOTE]
> English version available at: [SAFETY.md](SAFETY.md)

---

## 1. Nguyên Tắc An Toàn Cơ Khí Bất Biến

Trong máy in 3D đa công cụ (Toolchanger), va chạm cơ khí có thể làm cong gá giữ đầu in, vỡ thấu kính quang học của camera, đứt dây tín hiệu hoặc làm hỏng cảm biến Cartographer đắt tiền. Do đó, hệ thống tuân thủ nghiêm ngặt các quy tắc chuyển động sau:

1. **Tuyệt đối không cắt chéo đường thẳng:** Nghiêm cấm đầu in di chuyển chéo thẳng hàng giữa khay giữ tool (dock) và các trạm đo lường trừ khi vòi phun đã được xác nhận nằm trên hoặc cao hơn mặt phẳng an toàn (`Safe_Z`).
2. **Quy tắc "Lên trước - Xuống sau":**
   - **Khi rời trạm hoặc dock:** Trục Z phải nâng thẳng đứng lên `Safe_Z` trước khi trục XY bắt đầu dịch chuyển ngang.
   - **Khi tiến vào trạm:** Trục XY phải di chuyển và dừng hẳn tại điểm chờ tiếp cận (`Safe_Approach`) trước khi trục Z được phép hạ xuống.
3. **Khống chế tốc độ tiếp cận trạm:** Tốc độ di chuyển ngang trong khu vực trạm đo bị giới hạn nghiêm ngặt $\le 30\text{ mm/s}$ để người vận hành kịp nhấn dừng khẩn cấp nếu có sự cố.

---

## 2. Mô Hình Toạ Độ An Toàn 3 Tầng

Mỗi trạm đo lường (Trạm Camera hoặc Trạm Switch Z) được quản lý thông qua 3 tầng toạ độ độc lập:

```
                      [ Mặt phẳng an toàn Safe_Z (ví dụ: Z = 35.0 mm) ]
                        ^                                               ^
                        | (1. Nâng Z thẳng đứng)                        | (5. Nâng Z thẳng đứng)
                        |                                               |
[ Dock / Điểm cũ ] -----+                                               +---> [ Trạm tiếp theo ]
                             \
                              \ (2. Dịch chuyển ngang XY)
                               v
                       [ Safe Approach Point ] (Điểm chờ tiếp cận ngoài vỏ che)
                                |
                                | (3. Hạ Z có kiểm soát)
                                v
                       [ Target Station ] <---> [ Tiêu cự / Đỉnh chốt Switch ]
                             (4. Trườn chậm vào tâm)
```

| Tầng toạ độ | Mục đích & Ràng buộc kỹ thuật |
| :--- | :--- |
| **`Safe_Z`** | Mặt phẳng ngang cao hơn ít nhất 15mm–25mm so với vật cản cơ khí cao nhất trên bàn in (vỏ bọc camera, dock kẹp tool, kẹp bàn). |
| **`Safe_Approach_XY`** | Điểm dừng chờ cách tâm trạm đo 20mm–30mm, cho phép trục Z hạ xuống độ cao quan sát mà không có nguy cơ quệt vào thành vỏ camera. |
| **`Target_XY_Z`** | Toạ độ tiêu cự ống kính quang học camera hoặc đỉnh cơ học của chốt công tắc Z. |

---

## 3. Lệnh Dạy Toạ Độ Tương Tác (Interactive Teaching)

Người dùng không bao giờ phải tự tính toán hoặc đoán toạ độ để gõ tay vào cấu hình. Thay vào đó, macro tương tác sẽ ghi nhận trực tiếp vị trí thực tế của máy:

### Cú pháp lệnh G-Code:
```gcode
CALIBRATION_SET_SAFE_POS STATION=<CAMERA|Z_SWITCH> TYPE=<APPROACH|TARGET|SAFE_Z>
```

### Quy trình dạy toạ độ thực tế:
1. **Dạy điểm chờ Camera:**
   - Lái đầu in (jog) đến vùng thoáng đãng ngay phía trước cụm vỏ camera.
   - Gọi lệnh: `CALIBRATION_SET_SAFE_POS STATION=CAMERA TYPE=APPROACH`
2. **Dạy tâm tiêu cự Camera:**
   - Jog vòi phun vào chính giữa ống kính camera cho đến khi vòng tròn lỗ nozzle hiện rõ trên màn hình.
   - Gọi lệnh: `CALIBRATION_SET_SAFE_POS STATION=CAMERA TYPE=TARGET`
3. **Dạy điểm chạm Switch Z:**
   - Jog vòi phun ngay phía trên đỉnh chốt bấm công tắc và gọi lệnh: `CALIBRATION_SET_SAFE_POS STATION=Z_SWITCH TYPE=TARGET`
4. **Dạy độ cao an toàn tuyệt đối:**
   - Nâng đầu in lên độ cao vượt qua toàn bộ chướng ngại vật và gọi lệnh: `CALIBRATION_SET_SAFE_POS STATION=CAMERA TYPE=SAFE_Z`

Các toạ độ này được lưu ngay vào bộ nhớ máy và ghi vào file cấu hình `tool_offsets.cfg`.

---

## 4. Cơ Chế Bảo Vệ Phần Mềm & Giới Hạn Hành Trình

1. **Khống chế biên khung hình (Frame Boundary Clamping):**
   - Vision Server liên tục đối chiếu toạ độ pixel tính toán được với kích thước khung hình ảnh camera.
   - Nếu vector di chuyển chỉ ra ngoài khung hình ($X < 0$, $X > \text{Width}$, $Y < 0$, $Y > \text{Height}$), lệnh di chuyển bị chặn ngay lập tức và báo lỗi `ERR_CV_204`.
2. **Khống chế số bước lặp & Cảnh báo độ rơ cơ khí:**
   - Vòng lặp vi chỉnh căn tâm đầu phun chỉ cho phép tối đa 5 lần lặp.
   - Nếu sau 5 lần vi chỉnh mà sai số vẫn không hội tụ trong ngưỡng $\le 0.02\text{ mm}$, hệ thống sẽ dừng an toàn và cảnh báo trục truyền động có độ rơ (backlash).
3. **Tương thích hoàn toàn với lệnh dừng khẩn cấp E-Stop:**
   - Tích hợp sâu với lệnh dừng khẩn cấp `M112` của Klipper. Ngay khi kích hoạt, toàn bộ stepper lập tức ngắt điện, bảo toàn tuyệt đối cho phần cứng.
