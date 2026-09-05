# GIT_RULE.vi.md — Quy Chuẩn Quản Lý Git & Kho Lưu Trữ

> [!NOTE]
> English version available at: [GIT_RULE.md](GIT_RULE.md)

Tài liệu này thiết lập các quy định bắt buộc về quản trị Git, cấu trúc commit và cơ chế cập nhật tự động cho dự án **Tool-Klipper-Calibration**.

---

## 1. Địa Chỉ Kho Lưu Trữ & Chính Sách Nhánh

- **Địa chỉ kho lưu trữ từ xa (Remote Origin URL):**  
  `https://github.com/IDcrazy123/Tool-Klipper-Calibration.git`
- **Chính sách duy nhất một nhánh (Single-Branch Policy / Trunk-Based Development):**
  - Toàn bộ công việc phát triển diễn ra độc quyền trên nhánh **`main`**.
  - **Tuyệt đối không tạo nhánh phụ** (`không tạo nhánh develop`, `không tạo feature/*`, `không tạo bugfix/*`).
  - Mọi tính năng mới, bản vá lỗi, tinh chỉnh cấu hình hay cập nhật tài liệu đều được commit và đẩy thẳng lên nhánh `main`.
  - Duy trì lịch sử Git hoàn toàn tuyến tính, đảm bảo tính năng tự động cập nhật của Moonraker trên máy in không bao giờ bị lỗi xung đột nhánh hay mất đồng bộ (detached HEAD).

---

## 2. Quy Chuẩn Đặt Tên Commit (Conventional Commits)

Mọi commit trên nhánh `main` phải tuân theo cấu trúc sau:

```text
<type>(<scope>): <mô tả ngắn gọn bằng tiếng Việt hoặc tiếng Anh>

[Nội dung chi tiết giải thích nguyên nhân và bối cảnh thay đổi nếu cần]

Ref: #<task-id>
```

### Các giá trị `<type>` được phép:
- `feat`: Tính năng hoặc năng lực mới (ví dụ: `feat(vision): thêm bộ lọc nhận diện nozzle 3 tầng`)
- `fix`: Sửa lỗi phát sinh (ví dụ: `fix(klipper): khắc phục timeout reactor khi đồng bộ camera`)
- `docs`: Cập nhật tài liệu (ví dụ: `docs(git): quy định chính sách chỉ dùng nhánh main`)
- `refactor`: Tái cấu trúc mã nguồn nhưng không thay đổi hành vi bên ngoài
- `test`: Bổ sung hoặc hoàn thiện kịch bản kiểm thử
- `chore`: Các tác vụ bảo trì (ví dụ: cập nhật `.gitignore` hoặc dependencies)

---

## 3. Tích Hợp Cập Nhật Tự Động Qua Moonraker

Kho lưu trữ được thiết kế tương thích hoàn toàn với trình quản lý cập nhật Moonraker trên máy in:

```ini
[update_manager tool_klipper_calibration]
type: git_repo
path: ~/Tool-Klipper-Calibration
origin: https://github.com/IDcrazy123/Tool-Klipper-Calibration.git
primary_branch: main
is_system_service: True
managed_services: tool_calibrator_server
```

Khi người dùng nhấn nút "Update" trên giao diện Mainsail hoặc Fluidd, Moonraker sẽ tự động kéo nhánh `origin/main` mới nhất về máy in và khởi động lại dịch vụ `tool_calibrator_server.service`.
