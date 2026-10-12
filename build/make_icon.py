"""生成 GitDesk 应用图标（多尺寸 .ico）。"""

from __future__ import annotations

import os
import sys

from PIL import Image, ImageDraw

SCALE = 4
SIZE = 256
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "assets", "gitdesk.ico")


def lerp(a, b, t):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def draw_icon() -> Image.Image:
    s = SIZE * SCALE
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))

    # 圆角背景 + 渐变
    grad = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    gd = ImageDraw.Draw(grad)
    top = (58, 122, 226)
    bottom = (26, 74, 176)
    for y in range(s):
        gd.line([(0, y), (s, y)], fill=lerp(top, bottom, y / s) + (255,))

    mask = Image.new("L", (s, s), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        [int(s * 0.045), int(s * 0.045), int(s * 0.955), int(s * 0.955)],
        radius=int(s * 0.225),
        fill=255,
    )
    img.paste(grad, (0, 0), mask)

    # 顶部高光
    hl = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    ImageDraw.Draw(hl).rounded_rectangle(
        [int(s * 0.045), int(s * 0.045), int(s * 0.955), int(s * 0.52)],
        radius=int(s * 0.225),
        fill=(255, 255, 255, 30),
    )
    img.alpha_composite(hl, (0, 0), )

    d = ImageDraw.Draw(img)
    white = (255, 255, 255, 255)
    line_w = int(s * 0.052)

    def node(cx, cy, r=None):
        r = r or int(s * 0.062)
        d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=white)

    def conn(p1, p2):
        d.line([p1, p2], fill=white, width=line_w, joint="curve")

    def circle(cx, cy, r):
        d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=white, width=line_w)

    # 主干
    x0 = int(s * 0.375)
    conn((x0, int(s * 0.30)), (x0, int(s * 0.72)))
    # 分支线
    x1 = int(s * 0.640)
    yb = int(s * 0.335)
    conn((x0, int(s * 0.505)), (x1 - int(s * 0.09), int(s * 0.505)))
    conn((x1 - int(s * 0.09), int(s * 0.505)), (x1, yb + int(s * 0.055)))
    conn((x1, yb + int(s * 0.055)), (x1, int(s * 0.60)))

    node(x0, int(s * 0.30))
    node(x0, int(s * 0.72))
    circle(x1, yb, int(s * 0.070))
    circle(x1, int(s * 0.665), int(s * 0.070))

    return img.resize((SIZE, SIZE), Image.LANCZOS)


def main() -> int:
    icon = draw_icon()
    target = os.path.normpath(OUT)
    os.makedirs(os.path.dirname(target), exist_ok=True)
    icon.save(
        target,
        format="ICO",
        sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
    )
    png = os.path.join(os.path.dirname(target), "gitdesk.png")
    icon.save(png, format="PNG")
    print("已生成:", target, os.path.getsize(target), "bytes")
    print("已生成:", png, os.path.getsize(png), "bytes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
