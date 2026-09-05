# AGENTS.vi.md — Hướng Dẫn Dành Cho AI Agent & Lập Trình Viên

> [!NOTE]
> English version available at: [AGENTS.md](AGENTS.md)

Chào mừng bạn đến với dự án **Tool-Klipper-Calibration**. Đây là tài liệu điều phối trung tâm bằng Tiếng Việt. Để duy trì tính nhất quán, module hóa và tránh quá tải ngữ cảnh (context window) khi dự án phình to, toàn bộ thông tin kiến trúc, quy chuẩn và quy trình được chia nhỏ thành các tệp tài liệu chuyên biệt trong thư mục `agent/`. Mọi Agent và Lập trình viên phải đọc hiểu các tài liệu liên quan trước khi thực hiện tác vụ.

---

## 🗺️ Bản Đồ Tài Liệu Chuyên Biệt

Toàn bộ tài liệu kỹ thuật chi tiết được quản lý bằng tiếng Anh trong các file chuyên môn dưới đây:

| Tài liệu chuyên môn | Mục đích & Trách nhiệm chính |
| :--- | :--- |
| [PROJECT.md](PROJECT.md) | Đặc tả dự án: Mục tiêu đo XY Camera, Z Switch/Cartographer, phần cứng |
| [WORKFLOW.md](WORKFLOW.md) | Chu trình vận hành: Sơ đồ máy trạng thái (FSM), quy trình đo T0, lặp đo T1..Tn, Dry-Run |
| [SAFETY.md](SAFETY.md) | Điều hướng an toàn: Toạ độ 3 tầng (`Safe_Z`, `Safe_Approach`, `Target`), chống va chạm |
| [DECISIONS.md](DECISIONS.md) | Quyết định kiến trúc (ADR): Phân tích 3 dự án tham khảo, giải thích lý do tách service |
| [DIRECTORY.md](DIRECTORY.md) | Sơ đồ cấu trúc cây thư mục: Phân chia ranh giới giữa `klippy/`, `server/`, `macros/` |
| [GIT_RULE.md](GIT_RULE.md) | Quy chuẩn Git: Chỉ làm việc trên duy nhất nhánh `main`, Moonraker update |
| [LOGGING.md](LOGGING.md) | Bảng mã lỗi chuẩn (`ERR_xxx`): Định dạng thông báo telemetry ra Console & Web UI |
| [KNOWN_ISSUES.md](KNOWN_ISSUES.md) | Lỗi thực tế đã biết: Loá sáng vòi phun, cặn nhựa bám, trôi nhiệt & cách khắc phục |
| [BACKUP.md](BACKUP.md) | Chiến lược lưu trữ: Cô lập file `tool_offsets.cfg`, backup timestamp & rollback |
| [STYLE.md](STYLE.md) | Tiêu chuẩn viết code: Chuẩn PEP 8, Type Hinting, viết Jinja2 macro & comment chuẩn |
| [TODO.md](TODO.md) | Bảng phân rã công việc (WBS): Checklist chi tiết 6 giai đoạn và tiến độ |
| [PROMPTS.md](PROMPTS.md) | Mẫu prompt chuẩn: Kịch bản giao việc cho Agent theo từng giai đoạn phát triển |
| [CHANGELOG.md](CHANGELOG.md) | Nhật ký cập nhật: Quản lý lịch sử phiên bản theo chuẩn SemVer |

---

## 🤖 Nguyên Tắc Hoạt Động Cốt Lõi Của Agent

1. **Tra cứu trước khi viết mã:** Luôn đọc kỹ tài liệu chuyên môn tương ứng trước khi tạo hoặc sửa mã nguồn.
2. **Tuân thủ phân chia trách nhiệm:**
   - Xử lý ảnh OpenCV & toán học nặng $\rightarrow$ Nằm ở Vision Service (`server/`).
   - Điều khiển động học, gcode macro, probe Z $\rightarrow$ Nằm ở Klipper Extension (`klippy/extras/`).
   - Giao diện câu lệnh người dùng $\rightarrow$ Nằm ở Macro config (`macros/`).
3. **Chính sách nhánh Git duy nhất:** Chỉ làm việc trên nhánh **`main`**, tuyệt đối không tạo nhánh phụ (xem [GIT_RULE.md](GIT_RULE.md)).
4. **An toàn cơ khí là trên hết:** Không bao giờ di chuyển vòi phun đường chéo cắt qua chướng ngại vật; luôn di chuyển qua các toạ độ an toàn (`Safe_Z`, `Safe_Approach`) (xem [SAFETY.md](SAFETY.md)).
5. **Cập nhật trạng thái:** Luôn cập nhật [TODO.md](TODO.md) và [CHANGELOG.md](CHANGELOG.md) sau mỗi task hoàn thành.
6. **Phòng tránh xung đột Klipper & Toolchanger:** Tuyệt đối không gọi lệnh `SAVE_CONFIG` (tránh phá hỏng cấu hình đa tool); luôn kéo ảnh từ Crowsnest Snapshot thay vì WebRTC; tái sử dụng probe wrapper nếu `[tools_calibrate]` đã được cấu hình (xem [KNOWN_ISSUES.md](KNOWN_ISSUES.md)).
