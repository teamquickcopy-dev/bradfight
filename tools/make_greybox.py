#!/usr/bin/env python3
"""
make_greybox.py - placeholder "renders" for every sprite the template needs, so the full build
(align -> sff -> char) can be run and loaded in Ikemen before any real art exists.

Each template sprite is tinted into a flat-coloured greybox (shading kept, colours gone) and
placed on a render-sized canvas with the template's root at --source-root, plus a few pixels of
random drift, the way ControlNet output wanders. Output goes where ComfyUI renders would:

    characters/<char>/01_raw/<group>_<label>/<image>.png      (folders from make_shotlist.py)
    characters/<char>/portraits/9000_portraits/0000.png, 0001.png

Reads SFF v2 files with PNG sprites (format 10/11/12), e.g. Ikemen GO's chars/kfm720.

Example:
    python tools/make_greybox.py characters/fighter01 --template-sff templates/fighter_base/kfm720.sff
    NUMBER_FROM=filename ./build_character.sh fighter01 --source-root 512 600 --skip-rembg
"""
from __future__ import annotations

import argparse
import io
import random
import re
import struct
import sys
from pathlib import Path

import numpy as np
from PIL import Image

SEQ_DIR_RE = re.compile(r"^(\d+)(?:[_\-\s].*)?$")


def read_sff_v2(path: Path):
    """Yield (group, image, axis_x, axis_y, RGBA ndarray) for every sprite."""
    b = path.read_bytes()
    if b[:12] != b"ElecbyteSpr\x00" or b[15] != 2:
        raise SystemExit(f"{path}: need an SFF v2 file")
    spr_ofs, n, pal_ofs, npal, lofs, _llen, tofs = struct.unpack_from("<7I", b, 36)
    pals = []
    for i in range(npal):
        _g, _n, cols, link, ofs, size = struct.unpack_from("<HHHHII", b, pal_ofs + 16 * i)
        if size == 0:
            pals.append(pals[link])
            continue
        p = np.frombuffer(b, np.uint8, size, lofs + ofs).reshape(-1, 4).copy()
        p[:, 3] = 255
        p[0, 3] = 0                                   # index 0 is transparent in MUGEN
        pals.append(np.vstack([p, np.zeros((256 - len(p), 4), np.uint8)]) if len(p) < 256 else p[:256])
    cache: list[np.ndarray] = []
    for i in range(n):
        g, num, w, h, ax, ay, link, fmt, depth, ofs, size, palidx, flags = \
            struct.unpack_from("<HHHHhhHBBIIHH", b, spr_ofs + 28 * i)
        if size == 0:
            rgba = cache[link]
        else:
            start = (tofs if flags & 1 else lofs) + ofs
            if fmt not in (10, 11, 12):
                raise SystemExit(f"{path}: sprite {g},{num} uses format {fmt}; only PNG formats are supported")
            im = Image.open(io.BytesIO(b[start + 4:start + size]))
            if fmt == 10:
                rgba = pals[min(palidx, len(pals) - 1)][np.asarray(im)]
            else:
                rgba = np.asarray(im.convert("RGBA"))
        cache.append(rgba)
        yield g, num, ax, ay, rgba


def greybox(rgba: np.ndarray, tint: tuple[int, int, int]) -> np.ndarray:
    lum = rgba[..., :3].astype(np.float32) @ np.array([0.299, 0.587, 0.114], np.float32)
    shade = 0.35 + 0.65 * (lum / 255.0)
    out = np.zeros_like(rgba)
    out[..., :3] = np.clip(shade[..., None] * np.array(tint, np.float32), 0, 255).astype(np.uint8)
    out[..., 3] = rgba[..., 3]
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("char_dir", type=Path)
    ap.add_argument("--template-sff", type=Path, required=True)
    ap.add_argument("--render-size", type=int, nargs=2, default=(1024, 1024), metavar=("W", "H"))
    ap.add_argument("--source-root", type=int, nargs=2, default=(512, 600), metavar=("X", "Y"),
                    help="pixel where the template axis lands in each render (pass the same to align_sprites)")
    ap.add_argument("--drift", type=int, default=3, help="max random drift in px per frame (0 = exact)")
    ap.add_argument("--tint", default="230,140,60", help="R,G,B greybox colour")
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()

    rnd = random.Random(args.seed)
    tint = tuple(int(v) for v in args.tint.split(","))
    char_dir = args.char_dir.resolve()
    raw = char_dir / "01_raw"
    folders = {int(SEQ_DIR_RE.match(d.name).group(1)): d for d in raw.glob("*")
               if d.is_dir() and SEQ_DIR_RE.match(d.name)} if raw.exists() else {}
    rw, rh = args.render_size
    sx, sy = args.source_root
    count = clipped = 0
    for g, num, ax, ay, rgba in read_sff_v2(args.template_sff):
        if g == 9000:
            dst = char_dir / "portraits" / "9000_portraits" / f"{num:04d}.png"
            dst.parent.mkdir(parents=True, exist_ok=True)
            Image.fromarray(greybox(rgba, tint), "RGBA").save(dst)
            continue
        folder = folders.setdefault(g, raw / f"{g}_greybox")
        folder.mkdir(parents=True, exist_ok=True)
        canvas = Image.new("RGBA", (rw, rh), (0, 0, 0, 0))
        dx = rnd.randint(-args.drift, args.drift) if args.drift else 0
        dy = rnd.randint(-args.drift, args.drift) if args.drift else 0
        x, y = sx - ax + dx, sy - ay + dy
        h, w = rgba.shape[:2]
        if x < 0 or y < 0 or x + w > rw or y + h > rh:
            clipped += 1
        canvas.paste(Image.fromarray(greybox(rgba, tint), "RGBA"), (x, y))
        canvas.save(folder / f"{num:04d}.png")
        count += 1
    print(f"wrote {count} greybox renders under {raw}" + (f" ({clipped} clipped: enlarge --render-size)" if clipped else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
