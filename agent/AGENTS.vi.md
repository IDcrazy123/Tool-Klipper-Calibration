# AGENTS.vi.md — HƯỚNG DẪN AGENT & DEVELOPER

> [!NOTE]
> English version available at: [AGENTS.md](AGENTS.md)

Chào mừng bạn đến với dự án **Tool-Klipper-Calibration**. Đây là tài liệu điều phối trung tâm. Để duy trì tính nhất quán, module hóa và tránh quá tải ngữ cảnh (context window) khi dự án phình to, toàn bộ thông tin kiến trúc, quy chuẩn và quy trình được chia nhỏ thành các tệp tài liệu chuyên biệt trong thư mục `agent/`. Mọi Agent và Lập trình viên phải đọc hiểu các tài liệu liên quan trước khi thực hiện tác vụ.

---

## 🗺️ Bản đồ tài liệu dự án

Mỗi tài liệu đều có phiên bản tiếng Anh (`.md`) và tiếng Việt (`.vi.md`):

| Tài liệu (.vi.md) | Bản tiếng Anh (.md) | Mục đích & Trách nhiệm chính |
| :--- | :--- | :--- |
| [PROJECT.vi.md](PROJECT.vi.md) | [PROJECT.md](PROJECT.md) | Mục tiêu dự án, yêu cầu kỹ thuật & phần cứng |
| [WORKFLOW.vi.md](WORKFLOW.vi.md) | [WORKFLOW.md](WORKFLOW.md) | Chu trình hoạt động chi tiết & State Machine |
| [SAFETY.vi.md](SAFETY.vi.md) | [SAFETY.md](SAFETY.md) | Điều hướng toạ độ an toàn 3 tầng, chống va chạm |
| [DECISIONS.vi.md](DECISIONS.vi.md) | [DECISIONS.md](DECISIONS.md) | Bản ghi quyết định kiến trúc cốt lõi (ADR) |
| [DIRECTORY.vi.md](DIRECTORY.vi.md) | [DIRECTORY.md](DIRECTORY.md) | Cấu trúc cây thư mục & ranh giới module |
| [GIT_RULE.vi.md](GIT_RULE.vi.md) | [GIT_RULE.md](GIT_RULE.md) | Quy chuẩn Git: chỉ 1 nhánh `main`, không phân nhánh |
| [LOGGING.vi.md](LOGGING.vi.md) | [LOGGING.md](LOGGING.md) | Chuẩn mã lỗi (`ERR_xxx`), định dạng log & telemetry |
| [KNOWN_ISSUES.vi.md](KNOWN_ISSUES.vi.md) | [KNOWN_ISSUES.md](KNOWN_ISSUES.md) | Lỗi đã biết, nguyên nhân gốc & giải pháp |
| [BACKUP.vi.md](BACKUP.vi.md) | [BACKUP.md](BACKUP.md) | Chiến lược sao lưu cấu hình & rollback an toàn |
| [STYLE.vi.md](STYLE.vi.md) | [STYLE.md](STYLE.md) | Quy chuẩn viết code Python, Macro & comment |
| [TODO.vi.md](TODO.vi.md) | [TODO.md](TODO.md) | Danh sách công việc theo Phase (WBS) & tiến độ |
| [PROMPTS.vi.md](PROMPTS.vi.md) | [PROMPTS.md](PROMPTS.md) | Mẫu prompt tương tác chuẩn cho từng công đoạn |
| [CHANGELOG.vi.md](CHANGELOG.vi.md) | [CHANGELOG.md](CHANGELOG.md) | Nhật ký cập nhật phiên bản theo chuẩn SemVer |

---

## 🤖 Nguyên tắc hoạt động cốt lõi của Agent

1. **Tra cứu trước khi viết mã:** Luôn đọc kỹ tài liệu chuyên môn trước khi tạo hoặc sửa mã nguồn.
2. **Tuân thủ phân chia trách nhiệm:**
   - Xử lý ảnh OpenCV & toán học nặng $\rightarrow$ Nằm ở Vision Service (`server/`).
   - Điều khiển động học, gcode macro, probe Z $\rightarrow$ Nằm ở Klipper Extension (`klippy/extras/`).
   - Giao diện câu lệnh người dùng $\rightarrow$ Nằm ở Macro config (`macros/`).
3. **Chính sách nhánh Git duy nhất:** Chỉ làm việc trên nhánh **`main`**, tuyệt đối không tạo nhánh phụ (xem [GIT_RULE.vi.md](GIT_RULE.vi.md)).
4. **An toàn cơ khí là trên hết:** Không bao giờ di chuyển vòi phun đường chéo cắt qua chướng ngại vật; luôn di chuyển qua các toạ độ an toàn (`Safe_Z`, `Safe_Approach`) (xem [SAFETY.vi.md](SAFETY.vi.md)).
5. **Cập nhật trạng thái:** Luôn cập nhật [TODO.vi.md](TODO.vi.md) và [CHANGELOG.vi.md](CHANGELOG.vi.md) sau mỗi task hoàn thành.
