"""
Generates detailed README.md inside test_annotated_results from INDEX.csv.
"""

import csv
import os
import numpy as np

csv_path = 'test_annotated_results/INDEX.csv'
with open(csv_path, 'r', encoding='utf-8') as f:
    reader = csv.DictReader(f)
    rows = list(reader)

md = []
md.append('# KẾT QUẢ ĐÁNH GIÁ THỊ GIÁC: BỘ ẢNH QUÉT ĐỘ SÁNG 5 CÔNG CỤ (75/75 FRAMES)\n')
md.append('Thư mục này chứa toàn bộ các ảnh chụp thực tế đã được thuật toán thị giác tự động nhận diện và vẽ đánh dấu tâm, phục vụ việc kiểm tra và đánh giá trực quan.\n')
md.append('## 1. Cấu Trúc Thư Mục')
md.append('- **`zoomed_crops/`**: Chứa 75 ảnh cắt phóng to cận cảnh 4X (320x320 px) trực diện vào lỗ vòi phun Nozzle Orifice. Rất thuận tiện để duyệt nhanh bằng phím mũi tên trong trình xem ảnh Windows.')
md.append('- **`full_frames/`**: Chứa 75 ảnh toàn khung gốc (1280x720 px) kèm thanh thông số HUD trên cùng và khung phóng đại Inset 4X ở góc trên bên trái.')
md.append('- **`INDEX.csv`**: Bảng dữ liệu thô toạ độ tâm pixel và bán kính của toàn bộ 75 frame.\n')

md.append('## 2. Thứ Tự Sắp Xếp Đánh Số (01 -> 75)')
md.append('Các ảnh được đánh số thứ tự tuần tự theo quy chuẩn:')
md.append('1. Theo Toolhead: **T0 -> T1 -> T2 -> T3 -> T4**')
md.append('2. Theo Mức Độ Sáng: **L001 (Tối nhất) -> L004 -> L016 -> L064 -> L255 (Sáng nhất)**')
md.append('3. Theo Frame Chụp: **F01 -> F02 -> F03**\n')

md.append('## 3. Bảng Tổng Hợp Chi Tiết Toàn Bộ 75 Frame\n')
md.append('| STT | Tool | Mức Sáng | Frame | Toạ Độ Tâm U (px) | Toạ Độ Tâm V (px) | Bán Kính R (px) | File Ảnh Gốc |')
md.append('|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---|')

for r in rows:
    idx = int(r['index'])
    u = float(r['u'])
    v = float(r['v'])
    rad = float(r['radius'])
    t = r['tool']
    l = r['light']
    fr = r['frame']
    fn = r['filename']
    md.append(f'| **{idx:02d}** | `{t}` | `{l}` | `{fr}` | **{u:.2f}** | **{v:.2f}** | {rad:.2f} | `{fn}` |')

md.append('\n## 4. Bảng Tổng Hợp Độ Lặp Lại Của Từng Tool (Toàn Bộ Dải Sáng L001 -> L255)\n')
md.append('| Tool | Số Ảnh | U Trung Bình (px) | Độ Lệch Std U | V Trung Bình (px) | Độ Lệch Std V | Bán Kính R TB | Biên Độ U | Biên Độ V |')
md.append('|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|')

from collections import defaultdict
by_tool = defaultdict(list)
for r in rows:
    by_tool[r['tool']].append((float(r['u']), float(r['v']), float(r['radius'])))

tool_means = {}
for t, vals in sorted(by_tool.items()):
    us = [v[0] for v in vals]
    vs = [v[1] for v in vals]
    rs = [v[2] for v in vals]
    tool_means[t] = (np.mean(us), np.mean(vs))
    md.append(f'| **{t}** | {len(vals)} | **{np.mean(us):.2f}** | {np.std(us):.3f}px | **{np.mean(vs):.2f}** | {np.std(vs):.3f}px | {np.mean(rs):.2f}px | {max(us)-min(us):.2f}px | {max(vs)-min(vs):.2f}px |')

md.append('\n## 5. Bảng Độ Lệch Vật Lý Giữa Các Tool So Với T0 (Tỉ Lệ MPP = 0.0125 mm/px)\n')
t0_u, t0_v = tool_means['T0']
md.append(f'Điểm neo quang học T0: U = {t0_u:.2f}px, V = {t0_v:.2f}px\n')
md.append('| Công Cụ | Delta U (px) | Delta V (px) | Offset X (mm) | Offset Y (mm) | Mã G-Code Bù Trừ Klipper |')
md.append('|:---:|:---:|:---:|:---:|:---:|:---|')

mpp = 0.0125
for t in ['T1', 'T2', 'T3', 'T4']:
    tu, tv = tool_means[t]
    du = tu - t0_u
    dv = tv - t0_v
    dx = du * mpp
    dy = dv * mpp
    t_idx = t.replace('T', '')
    md.append(f'| **{t}** | {du:+.2f}px | {dv:+.2f}px | **{dx:+.4f}mm** | **{dy:+.4f}mm** | `G10 P{t_idx} X{dx:+.4f} Y{dy:+.4f}` |')

md_out = '\n'.join(md)
with open('test_annotated_results/README.md', 'w', encoding='utf-8') as f:
    f.write(md_out)

print('Saved test_annotated_results/README.md successfully!')
