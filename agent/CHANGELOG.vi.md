# CHANGELOG.vi.md — Lịch Sử Cập Nhật Phiên Bản Dự Án

> [!NOTE]
> English version available at: [CHANGELOG.md](CHANGELOG.md)

Toàn bộ các thay đổi đáng chú ý của dự án **Tool-Klipper-Calibration** được ghi chép tại tài liệu này, tuân thủ theo chuẩn [Định danh phiên bản ngữ nghĩa (Semantic Versioning - SemVer)](https://semver.org/).

---

## [Chưa phát hành / Unreleased]
### Kế hoạch tiếp theo
- Giai đoạn 2: Xây dựng dịch vụ xử lý ảnh nền Vision Background Service với bộ lọc 3 tầng OpenCV.
- Giai đoạn 3: Phát triển Klipper Core Extension với bộ điều hướng an toàn 3 tầng và hỗ trợ dual Z backend.
- Giai đoạn 4: Xây dựng bộ macro G-code và các lệnh dạy toạ độ an toàn tương tác.
- Giai đoạn 5: Thử nghiệm thực tế trên phần cứng máy in và kiểm định sai số lặp lại.
- Giai đoạn 6: Kịch bản cài đặt tự động trên Linux và tích hợp Moonraker.

---

## [0.1.0] - 2026-09-05
### Đã thêm
- Khởi tạo kiến trúc dự án và toàn bộ bộ tài liệu quản lý trong thư mục `agent/`.
- Cấu trúc tài liệu song ngữ hoàn chỉnh (`.md` cho tiếng Anh, `.vi.md` cho tiếng Việt) bao gồm 14 tài liệu chuyên biệt:
  - `AGENTS.md` / `AGENTS.vi.md`: Điều phối trung tâm và bản đồ điều hướng.
  - `PROJECT.md` / `PROJECT.vi.md`: Mục tiêu dự án, phần cứng và phạm vi tính năng.
  - `WORKFLOW.md` / `WORKFLOW.vi.md`: Chu trình vận hành và Finite State Machine.
  - `SAFETY.md` / `SAFETY.vi.md`: Toạ độ an toàn 3 tầng và bảo vệ chống va chạm.
  - `DECISIONS.md` / `DECISIONS.vi.md`: Bản ghi quyết định kiến trúc (ADR).
  - `DIRECTORY.md` / `DIRECTORY.vi.md`: Sơ đồ cây thư mục và ranh giới module.
  - `GIT_RULE.md` / `GIT_RULE.vi.md`: Quy chuẩn nhánh duy nhất `main` và commit chuẩn.
  - `LOGGING.md` / `LOGGING.vi.md`: Bảng mã lỗi (`ERR_xxx`) và định dạng telemetry.
  - `KNOWN_ISSUES.md` / `KNOWN_ISSUES.vi.md`: Các tình huống lỗi thực tế và cách xử lý.
  - `BACKUP.md` / `BACKUP.vi.md`: Chiến lược lưu trữ cấu hình, backup và rollback.
  - `STYLE.md` / `STYLE.vi.md`: Chuẩn viết code Python, Macro và quy tắc comment.
  - `TODO.md` / `TODO.vi.md`: Danh mục công việc WBS và bảng theo dõi tiến độ.
  - `PROMPTS.md` / `PROMPTS.vi.md`: Mẫu prompt chuẩn tương tác với Agent.
  - `CHANGELOG.md` / `CHANGELOG.vi.md`: Nhật ký cập nhật phiên bản dự án.
- Các file gốc kho lưu trữ: `README.md`, `README.vi.md`, và `.gitignore`.
- Cấu hình kho từ xa: `https://github.com/IDcrazy123/Tool-Klipper-Calibration.git` trên nhánh duy nhất `main`.
