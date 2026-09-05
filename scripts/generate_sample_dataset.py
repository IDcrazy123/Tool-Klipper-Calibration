"""
Generate synthetic nozzle sample dataset for offline vision benchmarking and testing.
"""

import os
import cv2
import numpy as np


def generate_nozzle_image(
    filename: str,
    cx: int = 320,
    cy: int = 240,
    inner_r: int = 18,
    outer_r: int = 42,
    bg_brightness: int = 40,
    nozzle_brightness: int = 180,
    hole_brightness: int = 12,
    noise_sigma: float = 2.0,
    blur_ksize: int = 3,
    add_debris: bool = False
) -> None:
    h, w = 480, 640
    img = np.full((h, w, 3), bg_brightness, dtype=np.uint8)

    # Outer brass body
    cv2.circle(img, (cx, cy), outer_r, (nozzle_brightness, nozzle_brightness, nozzle_brightness), -1)

    # Inner orifice hole
    cv2.circle(img, (cx, cy), inner_r, (hole_brightness, hole_brightness, hole_brightness), -1)

    # Optional debris/filament speck near orifice
    if add_debris:
        cv2.circle(img, (cx + inner_r - 2, cy - inner_r + 4), 4, (120, 120, 120), -1)

    # Add Gaussian noise
    if noise_sigma > 0:
        noise = np.random.normal(0, noise_sigma, (h, w, 3)).astype(np.float32)
        noisy = np.clip(img.astype(np.float32) + noise, 0, 255).astype(np.uint8)
        img = noisy

    # Blur
    if blur_ksize > 1:
        img = cv2.GaussianBlur(img, (blur_ksize, blur_ksize), 1.0)

    cv2.imwrite(filename, img, [int(cv2.IMWRITE_JPEG_QUALITY), 95])
    print(f"Generated sample image: {filename}")


def main():
    out_dir = os.path.join(os.path.dirname(__file__), "..", "tests", "sample_images")
    os.makedirs(out_dir, exist_ok=True)

    # 1. Perfect centered
    generate_nozzle_image(
        os.path.join(out_dir, "nozzle_perfect_center.jpg"),
        cx=320, cy=240, inner_r=18, outer_r=42,
        bg_brightness=40, nozzle_brightness=190, hole_brightness=10
    )

    # 2. Offset top right
    generate_nozzle_image(
        os.path.join(out_dir, "nozzle_offset_top_right.jpg"),
        cx=345, cy=215, inner_r=18, outer_r=42,
        bg_brightness=45, nozzle_brightness=185, hole_brightness=12
    )

    # 3. Offset bottom left
    generate_nozzle_image(
        os.path.join(out_dir, "nozzle_offset_bottom_left.jpg"),
        cx=290, cy=265, inner_r=18, outer_r=42,
        bg_brightness=35, nozzle_brightness=180, hole_brightness=15
    )

    # 4. Dim lighting / low contrast
    generate_nozzle_image(
        os.path.join(out_dir, "nozzle_dim_lighting.jpg"),
        cx=318, cy=242, inner_r=17, outer_r=40,
        bg_brightness=25, nozzle_brightness=105, hole_brightness=8,
        noise_sigma=3.0
    )

    # 5. Glare / high backlight
    generate_nozzle_image(
        os.path.join(out_dir, "nozzle_glare_backlight.jpg"),
        cx=322, cy=238, inner_r=19, outer_r=44,
        bg_brightness=85, nozzle_brightness=240, hole_brightness=20,
        blur_ksize=5
    )

    # 6. Debris / filament oozing residue
    generate_nozzle_image(
        os.path.join(out_dir, "nozzle_slight_debris.jpg"),
        cx=315, cy=244, inner_r=18, outer_r=42,
        bg_brightness=40, nozzle_brightness=185, hole_brightness=12,
        add_debris=True
    )

    # 7. Conical specular glare flare simulation
    img_flare = np.full((480, 640, 3), 35, dtype=np.uint8)
    cx, cy = 320, 240
    # Outer conical body
    cv2.circle(img_flare, (cx, cy), 42, (180, 180, 180), -1)
    # Downward specular flare wedge
    flare_pts = np.array([[cx - 20, cy + 10], [cx + 20, cy + 10], [cx + 60, cy + 120], [cx - 60, cy + 120]], np.int32)
    cv2.fillPoly(img_flare, [flare_pts], (240, 240, 255))
    cv2.circle(img_flare, (cx, cy), 18, (12, 12, 12), -1)
    img_flare = cv2.GaussianBlur(img_flare, (5, 5), 1.5)
    cv2.imwrite(os.path.join(out_dir, "sim_conical_glare_flare.jpg"), img_flare)

    # 8. Ruby gemstone nozzle simulation
    img_ruby = np.full((480, 640, 3), 40, dtype=np.uint8)
    # Metallic outer face
    cv2.circle(img_ruby, (cx, cy), 42, (200, 200, 210), -1)
    # Ruby insert (reddish-pink)
    cv2.circle(img_ruby, (cx, cy), 22, (180, 50, 160), -1)
    # Central micro orifice
    cv2.circle(img_ruby, (cx, cy), 6, (10, 10, 10), -1)
    img_ruby = cv2.GaussianBlur(img_ruby, (3, 3), 1.0)
    cv2.imwrite(os.path.join(out_dir, "sim_ruby_gemstone.jpg"), img_ruby)

    print("Successfully populated realistic benchmark datasets.")


if __name__ == "__main__":
    main()
