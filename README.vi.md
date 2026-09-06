# Tool-Klipper-Calibration

> [!NOTE]
> English version available at: [README.md](README.md)

Hệ thống tự động cân chỉnh độ lệch không gian XY (Thị giác máy tính - Computer Vision) và Z (Công tắc cơ khí / Cartographer Touch V4) cho máy in 3D đa công cụ chạy Klipper Toolchanger.

---

## 🎯 Mục Tiêu Cốt Lõi Của Dự Án
- **Đo độ lệch XY tự động bằng Camera:** Nhận diện tâm lỗ vòi phun qua camera hướng lên và tính toán ma trận dịch chuyển với độ chính xác cao.
- **Đo độ lệch Z đa hạ tầng linh hoạt:** Hỗ trợ cả **Công tắc hành trình cơ khí (Physical Switch)** lẫn **Đầu dò cảm ứng Cartographer Touch** (V4).
- **Điều hướng toạ độ an toàn động (Dynamic Safe Navigation):** Mô hình toạ độ 3 tầng (`Safe_Z`, `Safe_Approach`, `Target`) kết hợp lệnh dạy toạ độ bằng tay giúp loại bỏ 100% nguy cơ va quệt cơ khí.
- **Hệ thống chẩn đoán lỗi & Telemetry toàn diện:** Bảng mã lỗi chuẩn (`ERR_xxx`) hiển thị trực tiếp nguyên nhân và giải pháp xử lý lên Klipper Console và giao diện Web UI.
- **Tự động hoá hoàn toàn 1-chạm:** Hiệu chuẩn toàn bộ các đầu in và lưu toạ độ vào cấu hình chỉ với 1 lệnh macro duy nhất.

---

## 📖 Bộ Tài Liệu Kiến Trúc & Hướng Dẫn Vận Hành
- 🚀 **[Hướng Dẫn Cài Đặt, Tự Động Cập Nhật & Gỡ Bỏ](docs/HUONG_DAN_CAI_DAT_VA_CAP_NHAT.md)** *(Hướng dẫn chi tiết từ A-Z với Moonraker Update Manager)*
- 📘 **[Quy trình Vận hành Chuẩn & Danh mục Macro](docs/QUY_TRINH_VAN_HANH.md)** *(Hướng dẫn 6 bước từ A-Z)*
- 🏛️ **[agent/AGENTS.vi.md](agent/AGENTS.vi.md)** *(Quy chuẩn kiến trúc, kỹ thuật & quản trị dự án)* | **[agent/AGENTS.md](agent/AGENTS.md)** *(English)*

---

## ⚡ Cài Đặt Nhanh Vào Máy In (Quick Install)

Chạy 3 lệnh sau qua SSH bằng tài khoản thường (`pi`, `btt`,... **không dùng sudo**):
```bash
cd ~
git clone https://github.com/IDcrazy123/Tool-Klipper-Calibration.git
cd ~/Tool-Klipper-Calibration && ./scripts/install.sh
```

---

## 🛠️ Công Nghệ Kế Thừa & Cải Tiến
Dự án được tổng hợp và nâng cấp từ 3 dự án mã nguồn mở uy tín trong thư mục `References/`:
1. **Axiscope-cartographer:** Quy trình probe Z đa hạ tầng và quản lý chuỗi hook toolchanger.
2. **kTAMV:** Kiến trúc phân tách Client - Server độc lập, chống tràn thời gian reactor Klipper (`Timer too close`).
3. **TAMV:** Bộ lọc xử lý ảnh 3 tầng (Multi-stage OpenCV Blob Detection) nhận diện vòi phun chính xác trong điều kiện ánh sáng phức tạp.

---

## 🚀 Quản Lý Git & Cập Nhật Tự Động Moonraker
- **Kho lưu trữ từ xa:** `https://github.com/IDcrazy123/Tool-Klipper-Calibration.git`
- **Chính sách nhánh:** Duy nhất một nhánh **`main`** (Trunk-based development), tuyệt đối không phân nhánh.
- Tương thích hoàn hảo với Moonraker Update Manager để cập nhật 1-click trên giao diện Mainsail và Fluidd (xem [Hướng Dẫn Cập Nhật Moonraker](docs/HUONG_DAN_CAI_DAT_VA_CAP_NHAT.md)).

