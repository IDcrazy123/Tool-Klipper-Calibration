# PROMPTS.vi.md — Mẫu Prompt Tương Tác Chuẩn Cho Agent

> [!NOTE]
> English version available at: [PROMPTS.md](PROMPTS.md)

Tài liệu này cung cấp các mẫu prompt chuẩn hoá dành cho lập trình viên khi tương tác và giao việc cho AI Agent qua từng giai đoạn của dự án **Tool-Klipper-Calibration**.

---

## 1. Mẫu Prompt Theo Từng Giai Đoạn Phát Triển

### Prompt cho Giai đoạn 2: Xây dựng Vision Background Service
```text
Nhiệm vụ: Triển khai Giai đoạn 2 - Vision Background Service trong thư mục server/
Vui lòng đọc kỹ: agent/PROJECT.vi.md, agent/WORKFLOW.vi.md, agent/DECISIONS.vi.md (ADR-001, ADR-004), và agent/STYLE.vi.md.
Yêu cầu:
1. Viết stream_grabber.py kéo snapshot MJPEG từ Crowsnest.
2. Xây dựng nozzle_detector.py với bộ lọc 3 tầng (Standard, Relaxed, Super-Relaxed) kế thừa từ TAMV.
3. Xây dựng affine_transform.py tính toán mm/pixel theo chuyển động hình sao.
4. Mở các endpoint REST JSON trong tool_calibrator_server.py trên cổng 8090.
5. Tuân thủ quy tắc chỉ dùng nhánh main duy nhất và cập nhật agent/TODO.vi.md sau khi hoàn thành.
```

### Prompt cho Giai đoạn 3: Phát triển Klipper Extension
```text
Nhiệm vụ: Triển khai Giai đoạn 3 - Klipper Core Extension trong thư mục klippy/extras/
Vui lòng đọc kỹ: agent/SAFETY.vi.md, agent/DECISIONS.vi.md (ADR-002, ADR-003, ADR-006), và agent/LOGGING.vi.md.
Yêu cầu:
1. Viết tool_calibrator.py đăng ký các lệnh G-code.
2. Xây dựng safe_navigator.py kiểm soát toạ độ an toàn 3 tầng (Safe_Z, Safe_Approach, Target).
3. Viết z_backends/switch_backend.py và z_backends/cartographer_backend.py.
4. Viết config_manager.py ghi file nguyên tử và tự động sao lưu backup có timestamp.
5. Đảm bảo toàn bộ HTTP request sang cổng 8090 đều có timeout tối đa 2.0 giây.
```

### Prompt cho Giai đoạn 4: Xây dựng Bộ Macro G-Code
```text
Nhiệm vụ: Triển khai Giai đoạn 4 - Bộ Macro G-Code trong thư mục macros/
Vui lòng đọc kỹ: agent/WORKFLOW.vi.md và agent/STYLE.vi.md (Mục 3).
Yêu cầu:
1. Viết macro CALIBRATE_TOOL_OFFSETS hỗ trợ tham số chọn tool và chế độ DRY_RUN.
2. Viết macro CALIBRATION_SET_SAFE_POS để dạy toạ độ an toàn tương tác.
3. Tích hợp chuỗi hook khởi động, kết thúc và đổi tool.
4. Thêm macro CALIBRATION_ROLLBACK_OFFSETS phục hồi khẩn cấp.
```

---

## 2. Mẫu Prompt Sửa Lỗi & Tối Ưu

### Prompt điều tra và sửa lỗi (Bug Investigation)
```text
Nhiệm vụ: Điều tra sự cố <MÔ_TẢ_LỖI>
Vui lòng tham khảo: agent/KNOWN_ISSUES.vi.md, agent/LOGGING.vi.md, và kiểm tra mã nguồn.
Quy tắc:
1. Xác định chính xác nguyên nhân gốc rễ trước khi sửa mã.
2. Giải thích lý do kỹ thuật trong comment mã nguồn (Why, không What).
3. Tuân thủ chính sách một nhánh main duy nhất.
```
