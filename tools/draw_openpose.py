#!/usr/bin/env python3
"""
draw_openpose.py - draw OpenPose BODY-18 keypoint JSONs as ControlNet OpenPose control images.

Uses the same limb order and colours as the OpenPose ControlNet preprocessor, so the images can be
fed straight into a ControlNet OpenPose node (no preprocessor needed).

    python tools/draw_openpose.py comfy/pose_maps/kid_k/openpose_json comfy/pose_maps/kid_k/openpose
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

from PIL import Image, ImageDraw

# 1-based keypoint pairs and colours, as in the ControlNet OpenPose annotator
LIMBS = [(2, 3), (2, 6), (3, 4), (4, 5), (6, 7), (7, 8), (2, 9), (9, 10), (10, 11), (2, 12), (12, 13),
         (13, 14), (2, 1), (1, 15), (15, 17), (1, 16), (16, 18)]
COLORS = [(255, 0, 0), (255, 85, 0), (255, 170, 0), (255, 255, 0), (170, 255, 0), (85, 255, 0), (0, 255, 0),
          (0, 255, 85), (0, 255, 170), (0, 255, 255), (0, 170, 255), (0, 85, 255), (0, 0, 255), (85, 0, 255),
          (170, 0, 255), (255, 0, 255), (255, 0, 170), (255, 0, 85)]


def draw(data: dict) -> Image.Image:
    w, h = data["canvas_width"], data["canvas_height"]
    scale = max(w, h) / 512.0
    stick, radius = 4 * scale, 4 * scale
    base = Image.new("RGB", (w, h), (0, 0, 0))
    for person in data.get("people", []):
        k = person["pose_keypoints_2d"]
        pts = [(k[i * 3], k[i * 3 + 1]) if k[i * 3 + 2] > 0 else None for i in range(18)]
        limbs = Image.new("RGB", (w, h), (0, 0, 0))
        ld = ImageDraw.Draw(limbs)
        for n, (a, b) in enumerate(LIMBS):
            pa, pb = pts[a - 1], pts[b - 1]
            if pa is None or pb is None:
                continue
            (x1, y1), (x2, y2) = pa, pb
            length = math.hypot(x2 - x1, y2 - y1)
            if length < 1:
                continue
            ang = math.atan2(y2 - y1, x2 - x1)
            cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
            poly = []
            for t in range(0, 360, 10):                       # ellipse along the limb, like cv2.ellipse2Poly
                r = math.radians(t)
                ex, ey = (length / 2) * math.cos(r), stick * math.sin(r)
                poly.append((cx + ex * math.cos(ang) - ey * math.sin(ang), cy + ex * math.sin(ang) + ey * math.cos(ang)))
            ld.polygon(poly, fill=COLORS[n])
        base = Image.blend(base, limbs, 0.6)
        d = ImageDraw.Draw(base)
        for i, p in enumerate(pts):
            if p is not None:
                d.ellipse((p[0] - radius, p[1] - radius, p[0] + radius, p[1] + radius), fill=COLORS[i])
    return base


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("json_dir", type=Path)
    ap.add_argument("out_dir", type=Path)
    args = ap.parse_args()
    files = sorted(args.json_dir.rglob("*.json"))
    if not files:
        print(f"error: no .json files under {args.json_dir}", file=sys.stderr)
        return 2
    for f in files:
        dst = args.out_dir / f.relative_to(args.json_dir).with_suffix(".png")
        dst.parent.mkdir(parents=True, exist_ok=True)
        draw(json.loads(f.read_text())).save(dst)
    print(f"drew {len(files)} pose images -> {args.out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
