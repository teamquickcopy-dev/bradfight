#!/usr/bin/env python3
"""
make_stage_art.py - placeholder art + stage.json for a night city-street stage with sponsor billboards.

    python tools/make_stage_art.py stages/src/corner_store
    python tools/build_stage.py stages/src/corner_store/stage.json

Every PNG it writes is a stand-in: replace art/*.png with real paintings at the same sizes
(or change the sizes and "at" positions in stage.json), and ads/*.png with sponsor artwork
at any size (it is letterboxed into the billboard slot). Existing files are never overwritten,
so re-running only fills in what is missing.
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

R = random.Random(42)


def font(size: int):
    return ImageFont.load_default(size)


def sky(w=1024, h=900):
    im = Image.new("RGBA", (w, h))
    d = ImageDraw.Draw(im)
    for y in range(h):                              # deep blue to purple haze at the horizon
        t = y / h
        d.line([(0, y), (w, y)], fill=(int(10 + 60 * t ** 2), int(12 + 18 * t), int(38 + 50 * t), 255))
    for _ in range(140):
        x, y = R.randrange(w), R.randrange(int(h * 0.6))
        v = R.randrange(120, 255)
        d.point((x, y), fill=(v, v, v, 255))
    return im


def skyline(w, h, color, window, density, seed):
    rnd = random.Random(seed)
    im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    x = 0
    while x < w:
        bw, bh = rnd.randrange(90, 220), rnd.randrange(int(h * 0.35), h)
        bw = min(bw, w - x)                         # last building ends exactly at the tile edge
        d.rectangle([x, h - bh, x + bw - 1, h], fill=color)
        if rnd.random() < 0.3:
            d.rectangle([x + bw // 2 - 3, h - bh - 30, x + bw // 2 + 3, h - bh], fill=color)   # antenna
        for wy in range(h - bh + 14, h - 10, 22):
            for wx in range(x + 10, x + bw - 14, 18):
                if rnd.random() < density:
                    d.rectangle([wx, wy, wx + 7, wy + 10], fill=window)
        x += bw + rnd.randrange(0, 12)
    return im


def billboard_frame(w=560, h=340):
    im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    for px in (110, w - 130):                       # posts
        d.rectangle([px, 250, px + 20, h], fill=(38, 38, 46, 255))
    d.rectangle([20, 260, w - 20, 272], fill=(46, 46, 56, 255))                     # catwalk
    d.rounded_rectangle([20, 20, w - 20, 260], radius=6, fill=(28, 28, 34, 255), outline=(70, 70, 84, 255), width=4)
    for lx in range(70, w - 40, 140):               # lamp heads over the panel
        d.rectangle([lx, 6, lx + 30, 16], fill=(60, 60, 70, 255))
    return im


def billboard_glow(w=560, h=340):
    im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    for lx in range(70, w - 40, 140):
        d.polygon([(lx + 4, 16), (lx + 26, 16), (lx + 80, 250), (lx - 50, 250)], fill=(90, 80, 40, 120))
    return im.filter(ImageFilter.GaussianBlur(10))


def ad(text: str, sub: str, colors, w=1000, h=440):
    im = Image.new("RGBA", (w, h), colors[0])
    d = ImageDraw.Draw(im)
    d.rectangle([0, h - 90, w, h], fill=colors[1])
    d.text((w // 2, h // 2 - 40), text, fill=colors[2], font=font(96), anchor="mm")
    d.text((w // 2, h - 45), sub, fill=colors[0], font=font(40), anchor="mm")
    return im


def storefront(w=2600, h=280):
    im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    x, palette = 0, [(70, 44, 40), (52, 56, 70), (64, 58, 48), (44, 50, 44)]
    shops = []
    while x < w:
        sw = min(R.randrange(380, 560), w - x)
        d.rectangle([x, 0, x + sw - 1, h], fill=(*palette[len(shops) % 4], 255))
        for wy in (24,):                            # apartment windows upstairs
            for wx in range(x + 30, x + sw - 60, 90):
                lit = R.random() < 0.5
                d.rectangle([wx, wy, wx + 46, wy + 60], fill=(200, 170, 90, 255) if lit else (24, 26, 34, 255))
        d.rectangle([x + 20, 140, x + sw - 20, h], fill=(18, 20, 26, 255))          # shop window
        d.rectangle([x + 20, 140, x + sw - 20, h], outline=(90, 90, 100, 255), width=4)
        d.polygon([(x + 10, 110), (x + sw - 10, 110), (x + sw - 40, 140), (x + 40, 140)],
                  fill=(150, 30, 30, 255) if len(shops) % 2 == 0 else (30, 90, 120, 255))   # awning
        shops.append(x)
        x += sw
    return im, shops


def neon(on: bool, w=220, h=90):
    im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    color = (255, 60, 140, 255) if on else (70, 25, 45, 255)
    d.rounded_rectangle([6, 6, w - 6, h - 6], radius=14, outline=color, width=5)
    d.text((w // 2, h // 2), "OPEN", fill=color, font=font(52), anchor="mm")
    if on:
        glow = im.filter(ImageFilter.GaussianBlur(8))
        im = Image.alpha_composite(glow, im)
    return im


def crowd(frame: int, w=900, h=180):
    rnd = random.Random(7)
    im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    x = 10
    while x < w - 60:
        bob = (rnd.randrange(0, 6) if frame else 0)
        ph = rnd.randrange(115, 165)
        top = h - ph + bob
        c = (rnd.randrange(14, 30),) * 3 + (255,)
        d.ellipse([x + 12, top, x + 48, top + 36], fill=c)                       # head
        d.rounded_rectangle([x, top + 34, x + 60, h], radius=16, fill=c)          # body
        if frame and rnd.random() < 0.4:                                          # cheering arm
            d.line([(x + 50, top + 50), (x + 72, top - 10)], fill=c, width=12)
        x += rnd.randrange(54, 80)
    return im


def car(w=360, h=120):
    im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle([0, 40, w, 100], radius=18, fill=(140, 20, 30, 255))
    d.polygon([(70, 42), (110, 6), (250, 6), (300, 42)], fill=(120, 16, 26, 255))
    d.polygon([(120, 40), (140, 14), (190, 14), (190, 40)], fill=(40, 60, 80, 255))
    d.polygon([(200, 40), (200, 14), (245, 14), (280, 40)], fill=(40, 60, 80, 255))
    for cx in (70, w - 80):
        d.ellipse([cx - 26, 74, cx + 26, 120], fill=(15, 15, 15, 255))
    d.ellipse([4, 52, 26, 70], fill=(255, 240, 180, 255))                          # headlight (drives left)
    return im


def street(w=3200, h=200):
    im = Image.new("RGBA", (w, h), (40, 40, 46, 255))
    d = ImageDraw.Draw(im)
    d.rectangle([0, 0, w, 18], fill=(110, 110, 116, 255))                         # curb
    for x in range(0, w, 220):
        d.rectangle([x, 110, x + 120, 118], fill=(190, 170, 90, 255))              # lane dashes
    for _ in range(900):
        x, y = R.randrange(w), R.randrange(20, h)
        v = R.randrange(30, 60)
        d.point((x, y), fill=(v, v, v + 4, 255))
    return im


def lamp_post(h=760):
    im = Image.new("RGBA", (200, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rectangle([90, 40, 110, h], fill=(20, 22, 26, 255))
    d.rectangle([40, 30, 110, 46], fill=(20, 22, 26, 255))
    d.rectangle([30, 46, 70, 60], fill=(255, 230, 160, 255))
    return im


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("folder", type=Path)
    ap.add_argument("--name", default=None)
    args = ap.parse_args()
    root = args.folder
    (root / "art").mkdir(parents=True, exist_ok=True)
    (root / "ads").mkdir(exist_ok=True)

    def save(rel, img):
        p = root / rel
        if not p.exists():
            img.save(p)

    front, shops = storefront()
    save("art/sky.png", sky())
    save("art/skyline_far.png", skyline(1600, 520, (30, 30, 58, 255), (90, 90, 130, 255), 0.15, 1))
    save("art/skyline_near.png", skyline(1800, 560, (16, 16, 30, 255), (220, 190, 110, 255), 0.22, 2))
    save("art/billboard_frame.png", billboard_frame())
    save("art/billboard_glow.png", billboard_glow())
    save("art/storefront.png", front)
    save("art/neon_open_on.png", neon(True))
    save("art/neon_open_off.png", neon(False))
    save("art/crowd_0.png", crowd(0))
    save("art/crowd_1.png", crowd(1))
    save("art/car.png", car())
    save("art/street.png", street())
    save("art/lamp_post.png", lamp_post())
    save("ads/your_brand_here.png", ad("YOUR BRAND HERE", "sponsor this stage", [(250, 200, 40, 255), (20, 20, 20, 255), (20, 20, 20, 255)]))
    save("ads/sponsor_b.png", ad("SPONSOR SLOT B", "logo / promo art goes here", [(30, 200, 220, 255), (240, 240, 240, 255), (10, 30, 40, 255)]))
    save("ads/support.png", ad("SUPPORT THE DEV", "add your link + QR in stage.json", [(40, 200, 90, 255), (240, 240, 240, 255), (10, 40, 20, 255)]))

    cfg_path = root / "stage.json"
    if cfg_path.exists():
        print(f"{cfg_path} exists; art refreshed, config kept")
        return 0
    name = args.name or root.name
    bb_y = -280                                      # frame bottoms hide behind the storefront roofline
    cfg = {
        "name": name, "displayname": name.replace("_", " ").title(), "author": "",
        "localcoord": [1280, 720], "ground_y": 660,
        "camera": {"boundleft": -500, "boundright": 500, "boundhigh": -450},
        "players": {"p1startx": -280, "p2startx": 280, "leftbound": -1000, "rightbound": 1000},
        "music": "",
        "sponsors": {
            "main": {"size": [500, 220], "seconds": 8, "images": ["ads/your_brand_here.png", "ads/sponsor_b.png"]},
            "side": {"size": [500, 220], "seconds": 10, "images": ["ads/support.png"],
                     "qr": {"url": "", "caption": "KEEP THE RENT PAID - scan to support"}},
        },
        "elements": [
            {"id": "sky", "image": "art/sky.png", "anchor": "top", "at": [0, -720], "delta": [0.05, 0.05], "tile": [1, 0]},
            {"id": "skyline_far", "image": "art/skyline_far.png", "at": [0, -150], "delta": [0.2, 0.15], "tile": [1, 0]},
            {"id": "skyline_near", "image": "art/skyline_near.png", "at": [0, -110], "delta": [0.35, 0.3], "tile": [1, 0]},
            {"id": "billboard_main_frame", "image": "art/billboard_frame.png", "at": [-330, bb_y], "delta": [0.35, 0.3]},
            {"id": "billboard_main_ad", "sponsor": "main", "anchor": "center", "at": [-330, bb_y - 200], "delta": [0.35, 0.3]},
            {"id": "billboard_main_glow", "image": "art/billboard_glow.png", "at": [-330, bb_y], "delta": [0.35, 0.3], "trans": "add"},
            {"id": "billboard_side_frame", "image": "art/billboard_frame.png", "at": [350, bb_y + 30], "delta": [0.35, 0.3]},
            {"id": "billboard_side_ad", "sponsor": "side", "anchor": "center", "at": [350, bb_y - 170], "delta": [0.35, 0.3]},
            {"id": "billboard_side_glow", "image": "art/billboard_glow.png", "at": [350, bb_y + 30], "delta": [0.35, 0.3], "trans": "add"},
            {"id": "storefront", "image": "art/storefront.png", "at": [0, -60], "delta": [0.7, 0.7]},
            {"id": "neon_open", "anchor": "center", "at": [shops[1] + 200 - 1300, -205], "delta": [0.7, 0.7], "trans": "add",
             "frames": [{"image": "art/neon_open_on.png", "ticks": 150}, {"image": "art/neon_open_off.png", "ticks": 5},
                        {"image": "art/neon_open_on.png", "ticks": 8}, {"image": "art/neon_open_off.png", "ticks": 4}]},
            {"id": "crowd", "at": [0, -40], "delta": [0.8, 0.8],
             "frames": [{"image": "art/crowd_0.png", "ticks": 18}, {"image": "art/crowd_1.png", "ticks": 18}]},
            {"id": "traffic", "image": "art/car.png", "at": [900, -45], "delta": [0.85, 0.85],
             "velocity": [-9, 0], "tile": [1, 0], "tilespacing": [3200, 0]},
            {"id": "street", "image": "art/street.png", "anchor": "top", "at": [0, -50], "delta": [1, 1]},
            {"id": "lamp_post", "image": "art/lamp_post.png", "at": [-1080, 60], "delta": [1.25, 1.1], "layer": 1},
        ],
    }
    cfg_path.write_text(json.dumps(cfg, indent=2) + "\n")
    print(f"wrote {root}: art/, ads/, stage.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
