# KNOWN_ISSUES.vi.md — Tổng Hợp Lỗi Đã Biết & Giải Pháp Khắc Phục

> [!NOTE]
> English version available at: [KNOWN_ISSUES.md](KNOWN_ISSUES.md)

Tài liệu này tổng hợp các đặc thù phần cứng, hiện tượng quang học dị thường và các tình huống biên thực tế trên máy in Toolchanger, đi kèm các biện pháp xử lý đã được kiểm chứng.

---

## 1. Dị Thường Quang Học & Thị Giác Máy (Computer Vision)

### Vấn đề 1.1: Loá sáng & Phản quang trên đầu phun đồng bóng (Brass Nozzle)
- **Hiện tượng:** Bộ dò blob tìm sai tâm hoặc báo lỗi độ tròn $< 0.5$.
- **Nguyên nhân:** Đèn LED chiếu trực diện tạo các điểm loá sáng có cường độ cực cao trên mặt đồng bóng, làm mất đường viền lỗ nozzle.
- **Biện pháp khắc phục:**
  1. Tán xạ ánh sáng: Lắp vòng tán sáng in 3D bằng nhựa PETG mờ lên cụm đèn LED.
  2. Bù trừ phần mềm: Pipeline xử lý ảnh tự động kích hoạt bộ lọc Otsu thích ứng và phép toán đóng hình thái học (morphological closing).
  3. Giảm sáng: Hạ độ sáng LED bằng lệnh macro (`SET_PIN PIN=cam_led VALUE=0.35`).

### Vấn đề 1.2: Cặn nhựa bám dính quanh đầu vòi (Burnt PETG / ABS)
- **Hiện tượng:** Hình dạng đầu phun bị méo hoặc không tròn; kích hoạt lỗi `ERR_CV_202`.
- **Nguyên nhân:** Các vệt nhựa cháy bám dính ở chóp vòi làm biến dạng hình bao ngoài.
- **Biện pháp khắc phục:**
  1. Thêm hook gạt chùi đầu phun (nozzle brush wipe) trước khi tiếp cận camera trong `before_pickup_gcode`.
  2. Thuật toán tự động hạ xuống Tầng 2 (Relaxed) và Tầng 3 (Super-Relaxed), tập trung tìm vòng tròn đen sâu bên trong lỗ phun thay vì viền ngoài vòi.

### Vấn đề 1.3: Trôi nhiệt camera & Đọng sương mặt kính
- **Hiện tượng:** Đo liên tục sau 2 tiếng thấy toạ độ XY bị trôi lệch dần $0.05\text{mm}$.
- **Nguyên nhân:** Nhiệt lượng từ bàn nhiệt làm nóng gá camera, gây giãn nở cơ khí. Đầu phun nóng dừng lâu trên mặt kính camera lạnh gây đọng sương/mờ thấu kính.
- **Biện pháp khắc phục:**
  1. Tuyệt đối không đỗ vòi phun đứng yên trên mặt kính camera.
  2. Kiểm tra nhiệt độ trước khi đo: Bắt buộc vòi phun phải nguội $\le 100^\circ\text{C}$ trước khi vào trạm camera.

---

## 2. Đặc Thù Cơ Khí & Chuyển Động Động Học

### Vấn đề 2.1: Độ rơ của ngàm khoá Tool (Toolhead Latch Backlash)
- **Hiện tượng:** Đo lặp lại cùng một đầu in nhiều lần nhưng cho kết quả lệch nhau ($> 0.03\text{mm}$).
- **Nguyên nhân:** Lực kéo của servo khoá chưa đủ chặt hoặc các viên bi kinematic coupling trên carriage bị mòn/lỏng.
- **Biện pháp khắc phục:**
  1. Kiểm tra và siết chặt các ốc bắt bi định vị cơ khí.
  2. Cấu hình macro nhả và nạp lại tool 1 lần để đầu in ăn khớp hoàn toàn vào ngàm trước khi đo.

### Vấn đề 2.2: Độ nhạy nhiệt độ của Cartographer Touch
- **Hiện tượng:** Kết quả probe Z của Cartographer bị lệch giữa lúc buồng in nguội và buồng in nóng.
- **Nguyên nhân:** Độ tự cảm của cuộn dây Eddy Current thay đổi theo nhiệt độ môi trường nếu chưa được ngâm nhiệt (heat soak) đều.
- **Biện pháp khắc phục:**
  1. Thực hiện hiệu chuẩn ở cùng một mức nhiệt độ bàn/buồng in ổn định, ngâm nhiệt máy ít nhất 10 phút trước khi đo.
  2. Bật bảng bù trừ nhiệt độ trong touch-model của Cartographer.
