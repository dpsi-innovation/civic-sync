"""Regenerate CivicSync PWA icons: dark rounded tile + gold road-to-horizon
+ dashed centerline + satellite (matches the new inline SVG brand mark)."""
import math
from PIL import Image, ImageDraw

BG = (13, 20, 27, 255)          # --bg dark #0d141b
GOLD = (245, 166, 35, 255)      # --lime accent #f5a623
SS = 8
VB = 24.0
FILL_FRAC = 0.70                 # mark size relative to canvas


def canvas(px):
    big = px * SS
    img = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    s = big * FILL_FRAC / VB
    off = big / 2 - s * VB / 2

    def u(x, y):
        return (off + x * s, off + y * s)

    # ---- dark rounded tile ----
    d.rounded_rectangle([0, 0, big - 1, big - 1], radius=big * 0.22, fill=BG)

    lw = max(1, round(1.7 * s))          # default stroke width
    lwm = max(1, round(1.4 * s))         # thin strokes (horizon, connectors)
    lwc = max(1, round(2.1 * s))         # centerline

    # ---- road edges (open, converging) ----
    d.line([u(5.8, 22), u(10.9, 9.9)], fill=GOLD, width=lw)
    d.line([u(13.1, 9.9), u(18.2, 22)], fill=GOLD, width=lw)
    # ---- horizon (split so the road emerges through it) ----
    d.line([u(9, 9.9), u(11.2, 9.9)], fill=GOLD, width=lwm)
    d.line([u(12.8, 9.9), u(15, 9.9)], fill=GOLD, width=lwm)
    # ---- dashed centerline: y 12.7 -> 20.7, dash 2.7 gap 2.2 ----
    total = 20.7 - 12.7
    y = 12.7
    while y < 20.7:
        y2 = min(y + 2.7, 20.7)
        d.line([u(12, y), u(12, y2)], fill=GOLD, width=lwc)
        y += 2.7 + 2.2
    # round caps on road bottom + horizon ends
    for p in (u(5.8, 22), u(18.2, 22)):
        r = lw / 2
        d.ellipse([p[0] - r, p[1] - r, p[0] + r, p[1] + r], fill=GOLD)
    for p in (u(9, 9.9), u(11.2, 9.9), u(12.8, 9.9), u(15, 9.9)):
        r = lwm / 2
        d.ellipse([p[0] - r, p[1] - r, p[0] + r, p[1] + r], fill=GOLD)
    # apex caps
    for p in (u(10.9, 9.9), u(13.1, 9.9)):
        r = lw / 2
        d.ellipse([p[0] - r, p[1] - r, p[0] + r, p[1] + r], fill=GOLD)

    # ---- satellite ----
    def rect(x0, y0, x1, y1, w_):
        d.rounded_rectangle([u(x0, y0), u(x1, y1)], radius=max(2, int(s * 0.45)), outline=GOLD, width=w_)
        d.rounded_rectangle([u(x0 + w_*0.0, y0), u(x1, y1)], radius=max(2, int(s * 0.45)), fill=BG, outline=GOLD, width=w_)
    # body 11..13 x 1.9..3.9 ; panels 7.2..9.9 / 14.1..16.8 x 2.55..4.15
    rect(7.2, 2.55, 9.9, 4.15, lw)
    rect(14.1, 2.55, 16.8, 4.15, lw)
    rect(11.0, 1.9, 13.0, 3.9, lw)
    # connectors body<->panels
    d.line([u(9.9, 3.35), u(11.0, 3.35)], fill=GOLD, width=lwm)
    d.line([u(13.0, 3.35), u(14.1, 3.35)], fill=GOLD, width=lwm)

    return img.resize((px, px), Image.LANCZOS)


for size in (192, 512):
    img = canvas(size)
    img.save(rf"web\icons\icon-{size}.png")
    print("wrote", size)
