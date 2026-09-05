# DIRECTORY.vi.md — Cấu Trúc Thư Mục & Ranh Giới Module

> [!NOTE]
> English version available at: [DIRECTORY.md](DIRECTORY.md)

Tài liệu này mô tả toàn diện sơ đồ cây thư mục của dự án **Tool-Klipper-Calibration**, trách nhiệm của từng thành phần và ranh giới phân định giữa các module.

---

## 🌳 Sơ Đồ Cây Thư Mục

```
Tool-Klipper-Calibration/
├── .gitignore                   # Bỏ qua file tạm, virtualenv, logs, cache & thư mục tham khảo
├── README.md                    # Trang giới thiệu tiếng Anh & liên kết tài liệu
├── README.vi.md                 # Trang giới thiệu tiếng Việt
│
├── agent/                       # Bộ tài liệu kiến trúc & quản trị chuẩn của Agent
│   ├── AGENTS.md / .vi.md       # Điều phối trung tâm & bản đồ điều hướng
│   ├── BACKUP.md / .vi.md       # Quy chuẩn sao lưu cấu hình & rollback khẩn cấp
│   ├── CHANGELOG.md / .vi.md    # Nhật ký cập nhật phiên bản theo chuẩn SemVer
│   ├── DECISIONS.md / .vi.md    # Quyết định kiến trúc cốt lõi (ADR)
│   ├── DIRECTORY.md / .vi.md    # Cấu trúc thư mục & mã nguồn (tài liệu này)
│   ├── GIT_RULE.md / .vi.md     # Quy chuẩn Git: chỉ 1 nhánh main, không phân nhánh
│   ├── KNOWN_ISSUES.md / .vi.md # Tổng hợp lỗi đã biết, nguyên nhân gốc & giải pháp
│   ├── LOGGING.md / .vi.md      # Quy chuẩn mã lỗi, định dạng log & telemetry
│   ├── PROJECT.md / .vi.md      # Mục tiêu dự án, yêu cầu kỹ thuật & phần cứng
│   ├── PROMPTS.md / .vi.md      # Mẫu prompt tương tác chuẩn cho từng tác vụ
│   ├── SAFETY.md / .vi.md       # Cơ chế toạ độ an toàn 3 tầng & chống va chạm
│   ├── STYLE.md / .vi.md        # Quy chuẩn viết code Python, Macro & comment
│   ├── TODO.md / .vi.md         # Danh sách công việc theo Phase (WBS) & tiến độ
│   └── WORKFLOW.md / .vi.md     # Chu trình vận hành chi tiết & State Machine
│
├── klippy/                      # Klipper Core Extension (Chạy trong môi trường Klippy)
│   └── extras/
│       ├── __init__.py
│       ├── tool_calibrator.py   # Lớp điều phối trung tâm Klipper Module
│       ├── safe_navigator.py    # Logic toạ độ an toàn 3 tầng & bảo vệ va chạm
│       ├── z_backends/          # Các adapter cảm biến Z
│       │   ├── __init__.py
│       │   ├── base_z.py        # Interface trừu tượng cho Z probe
│       │   ├── switch_backend.py# Adapter cho công tắc cơ khí / switch endstop
│       │   └── cartographer_backend.py # Adapter cho Cartographer V4 Touch
│       └── config_manager.py    # Quản lý sao lưu & ghi toạ độ vào file cấu hình
│
├── server/                      # Background Vision Service (Chạy độc lập trên cổng 8090)
│   ├── tool_calibrator_server.py# HTTP Service (FastAPI / Waitress) tiếp nhận lệnh
│   ├── stream_grabber.py        # Kéo frame từ Crowsnest MJPEG snapshot stream
│   ├── nozzle_detector.py       # Thuật toán 3 tầng OpenCV (Standard/Relaxed/Super)
│   ├── affine_transform.py      # Tính toán ma trận chuyển đổi MPP (mm/pixel)
│   ├── visual_debugger.py       # Stream preview ảnh có vẽ overlay (tâm, crosshair)
│   └── requirements.txt         # Các thư viện Python cần thiết (opencv-python, numpy)
│
├── macros/                      # Bộ G-Code Macro cung cấp cho người dùng
│   ├── tool_calibrator_macros.cfg   # Các macro chính: CALIBRATE_TOOL_OFFSETS,...
│   └── safe_staging_macros.cfg      # Các macro tương tác lưu toạ độ an toàn
│
├── scripts/                     # Kịch bản cài đặt tự động & quản lý hệ thống
│   ├── install.sh               # Kịch bản cài đặt venv, dependencies, systemd service
│   ├── uninstall.sh             # Kịch bản gỡ bỏ dịch vụ và dọn dẹp hệ thống
│   └── tool_calibrator.service  # File cấu hình systemd service cho server xử lý ảnh
│
└── References/                  # Thư mục chứa 3 dự án tham khảo gốc (Lưu cục bộ)
    ├── Axiscope-cartographer-main/
    ├── TAMV-master/
    └── kTAMV-main/
```

---

## 📦 Phân Chia Trách Nhiệm Module

### 1. Phân vùng `klippy/extras/`
- Chạy trực tiếp trong môi trường Klipper reactor.
- **Quy tắc:** Tuyệt đối không import thư viện xử lý ảnh nặng (như `cv2`, `torch`). Chỉ quản lý động học máy in và gửi yêu cầu HTTP non-blocking sang `server/` với timeout tối đa 2.0 giây.

### 2. Phân vùng `server/`
- Chạy dưới dạng tiến trình độc lập (systemd daemon) lắng nghe trên cổng 8090.
- **Quy tắc:** Không bao giờ can thiệp trực tiếp vào phần cứng stepper hay vùng nhớ của Klipper. Cung cấp API REST JSON phục vụ tính toán thị giác máy.

### 3. Phân vùng `macros/`
- Tầng giao diện người dùng cấu hình bằng ngôn ngữ Jinja2.
- **Quy tắc:** Không nhúng các phép toán phức tạp vào macro. Macro chỉ đóng vai trò nhận tham số và gọi các hàm thực thi của Klipper module.
