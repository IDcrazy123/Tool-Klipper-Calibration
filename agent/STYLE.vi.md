# STYLE.vi.md — Quy Chuẩn Viết Code & Hướng Dẫn Comment

> [!NOTE]
> English version available at: [STYLE.md](STYLE.md)

Tài liệu này quy định các tiêu chuẩn về phong cách viết mã nguồn Python, type hinting, quy tắc comment và chuẩn viết macro Jinja2 cho dự án **Tool-Klipper-Calibration**.

---

## 1. Tiêu Chuẩn Viết Mã Nguồn Python (Klippy & Vision Service)

1. **Tuân thủ chuẩn PEP 8:** Thụt dòng bằng 4 dấu cách (spaces), không dùng tab. Độ dài tối đa mỗi dòng: 100 ký tự.
2. **Khai báo kiểu dữ liệu (Type Annotations):** Toàn bộ hàm, tham số và giá trị trả về phải có Type Hinting rõ ràng:
   ```python
   def compute_pixel_offset(
       origin_uv: tuple[float, float],
       detected_uv: tuple[float, float],
       mpp: float
   ) -> tuple[float, float]:
       """Tính toán độ lệch toạ độ thực tế (mm) dựa trên toạ độ camera."""
       delta_u: float = detected_uv[0] - origin_uv[0]
       delta_v: float = detected_uv[1] - origin_uv[1]
       return (round(delta_u * mpp, 3), round(delta_v * mpp, 3))
   ```
3. **Lập trình phòng thủ (Defensive Error Handling):**
   - Tuyệt đối không sử dụng `except Exception: pass` bỏ qua lỗi âm thầm.
   - Luôn bắt đúng loại ngoại lệ và ghi log ngữ cảnh chi tiết.
   - Mọi truy vấn mạng HTTP sang Vision Server bắt buộc phải đặt timeout ($\le 2.0\text{ giây}$).

---

## 2. Quy Chuẩn Comment: Giải Thích "Tại Sao" (Why), Không Giải Thích "Làm Gì" (What)

1. **Mã nguồn tự giải thích:** Đặt tên hàm và biến rõ nghĩa để người đọc hiểu ngay hành động code thực hiện mà không cần đọc comment.
2. **Mục đích của Comment:** Comment chỉ dùng để giải thích lý do kỹ thuật, các hằng số toán học hoặc đặc thù cơ khí phần cứng:
   - *Chuẩn:* `# Nâng Z thẳng đứng trước khi chạy ngang để vượt qua thành vỏ camera cao 22mm`
   - *Sai:* `# Gọi lệnh nâng trục Z lên`
3. **Quy chuẩn Docstring (Google / Sphinx Format):**
   ```python
   class SafeNavigator:
       """
       Quản lý các điểm toạ độ an toàn 3 tầng và điều phối chuyển động chống va chạm.

       Attributes:
           safe_z (float): Cao độ an toàn tuyệt đối tính bằng mm.
           approach_feedrate (float): Tốc độ di chuyển giới hạn khi tiếp cận trạm đo.
       """
   ```

---

## 3. Quy Chuẩn Viết Macro Klipper (G-Code & Jinja2)

1. **Giá trị mặc định an toàn:** Luôn cung cấp tham số `default` khi lấy giá trị từ người dùng:
   ```jinja
   {% set TARGET_TOOL = params.TOOL|default(0)|int %}
   {% set DRY_RUN = params.DRY_RUN|default(0)|int %}
   ```
2. **Thông báo qua Web UI:** Ưu tiên dùng `action_respond_info` thay vì lệnh `M118` thô để đảm bảo hiển thị đẹp mắt trên Mainsail/Fluidd.
3. **Kiểm tra trạng thái máy trước khi di chuyển:** Mọi macro thực hiện di chuyển cơ khí bắt buộc phải kiểm tra cờ homing (`'xyz' in printer.toolhead.homed_axes`).
