"""生成 FlashFind 图标（放大镜）。仅在开发时运行：python tools/make_icon.py"""

from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path(__file__).resolve().parent.parent / "assets" / "icon.ico"
SIZES = [256, 128, 64, 48, 32, 16]
SS = 4  # 超采样倍数，用于抗锯齿


def draw(size: int) -> Image.Image:
    s = size * SS
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    # 圆角背景
    d.rounded_rectangle([0, 0, s - 1, s - 1], radius=int(s * 0.22), fill=(47, 111, 235, 255))

    # 放大镜：白色圆环 + 手柄
    cx = cy = s * 0.44
    r_out = s * 0.26
    r_in = s * 0.17
    d.ellipse([cx - r_out, cy - r_out, cx + r_out, cy + r_out], fill=(255, 255, 255, 255))
    d.ellipse([cx - r_in, cy - r_in, cx + r_in, cy + r_in], fill=(47, 111, 235, 255))

    # 手柄
    x1 = cx + r_in * 0.72
    y1 = cy + r_in * 0.72
    x2 = s * 0.80
    y2 = s * 0.80
    w = max(int(s * 0.075), 1)
    d.line([(x1, y1), (x2, y2)], fill=(255, 255, 255, 255), width=w)
    return img.resize((size, size), Image.LANCZOS)


def main():
    OUT.parent.mkdir(parents=True, exist_ok=True)
    base = draw(256)
    base.save(OUT, format="ICO", sizes=[(n, n) for n in SIZES])
    print(f"图标已生成: {OUT}  ({OUT.stat().st_size/1024:.1f} KB)")


if __name__ == "__main__":
    main()
