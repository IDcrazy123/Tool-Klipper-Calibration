# Tool-Klipper-Calibration

Hệ thống tự động cân chỉnh độ lệch XY (Computer Vision) và Z (Switch / Cartographer Touch) cho máy in 3D đa công cụ chạy Klipper (Toolchanger).

## 🎯 Mục tiêu dự án
- **Đo độ lệch XY tự động:** Nhận diện tâm lỗ vòi phun (nozzle orifice) qua camera hướng lên và tính toán ma trận dịch chuyển chính xác cao.
- **Đo độ lệch Z đa hạ tầng:** Hỗ trợ linh hoạt cả **Physical Switch / Endstop** lẫn **Cartographer Touch Probe** (V4).
- **Điều hướng an toàn động (Dynamic Safe Navigation):** Cho phép người dùng chỉ định/dạy vị trí an toàn trước khi đo thay vì toạ độ cố định cứng nhắc, loại bỏ nguy cơ va quệt phần cứng.
- **Hệ thống Macro chẩn đoán toàn diện:** Cung cấp mã lỗi chi tiết (`ERR_xxx`), trạng thái trực quan và hướng dẫn khắc phục ngay trên Klipper console.
- **Tự động hoá 1-chạm:** Thực hiện toàn bộ chu trình đo cho tất cả toolheads và lưu trực tiếp vào cấu hình chỉ với 1 lệnh macro.

---

## 📖 Tài liệu quản lý & Kiến trúc dự án
Toàn bộ quy chuẩn kiến trúc, phân rã công việc (WBS), chiến lược Git (duy nhất nhánh `main`), quy chuẩn viết code, cơ chế toạ độ an toàn và các điểm cải tiến được quy định chi tiết tại:
👉 **[agent.md](agent.md)**

---

## 🛠️ Công nghệ & Tham khảo
Dự án được kế thừa và phát triển cải tiến dựa trên 3 dự án mã nguồn mở uy tín:
1. **Axiscope-cartographer:** Cơ chế Z-Backend (Switch & Cartographer Touch) và quản lý toolchanger hook.
2. **kTAMV:** Kiến trúc vi dịch vụ Klipper Client - Vision Server độc lập, chống tràn thời gian reactor (`Timer too close`).
3. **TAMV:** Bộ lọc xử lý ảnh đa tầng (Multi-stage OpenCV Blob Detection) thích ứng linh hoạt với nhiều loại vòi phun.

---

## 🚀 Quản lý Git & Cập nhật
- **Kho lưu trữ:** `https://github.com/IDcrazy123/Tool-Klipper-Calibration.git`
- **Chính sách nhánh:** Duy nhất một nhánh `main` (Trunk-based development), không phân nhánh, đồng bộ trực tiếp với Moonraker Auto-Update Manager.
