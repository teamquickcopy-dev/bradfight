#!/usr/bin/env python3
"""
build_sff.py - compile an SFF build manifest into an SFF v2.01 archive, headless.

Sprites are stored as 32-bit RGBA PNG (format 12), which Ikemen GO loads
natively. No palette reduction, no PCX, no Fighter Factory.

  * Each sprite is trimmed to its alpha bounding box and its axis shifted to
    match, so 512x512 canvases cost nothing in the final file (--no-trim to keep).
  * Pixel-identical frames (holds, repeated poses) are stored once and written
    as linked sprites; each link keeps its own axis.

Layout written (offsets as read by Ikemen GO's SffHeader.Read / readHeaderV2):
    512-byte header | sprite nodes (28 B each) | palette nodes (16 B each) | ldata

Example:
    python tools/build_sff.py characters/kid_k/04_build/kid_k.sffmanifest.txt
    python tools/build_sff.py manifest.txt -o engine/chars/kid_k/kid_k.sff
"""
from __future__ import annotations

import argparse
import hashlib
import io
import re
import struct
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image

HEADER_SIZE = 512
SPRITE_NODE = struct.Struct("<HHHHhhHBBIIHH")   # 28 bytes
PALETTE_NODE = struct.Struct("<HHHHII")          # 16 bytes
FMT_PNG32 = 12
LINE_RE = re.compile(r"^\s*(\d+)\s*,\s*(\d+)\s*,\s*(-?\d+)\s*,\s*(-?\d+)\s*,\s*(.+?)\s*$")


@dataclass
class Entry:
    group: int
    image: int
    axis_x: int
    axis_y: int
    path: Path
    width: int = 0
    height: int = 0
    data: bytes = b""          # 4-byte length prefix + PNG; empty for linked sprites
    link: int = 0


def parse_manifest(path: Path) -> list[Entry]:
    entries, section, seen = [], None, {}
    for n, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.split(";", 1)[0].strip()
        if not line:
            continue
        if line.startswith("["):
            section = line.strip("[]").strip().lower()
            continue
        if section != "sprites":
            continue
        m = LINE_RE.match(line)
        if not m:
            raise SystemExit(f"{path}:{n}: expected 'group, image, axis_x, axis_y, path'")
        g, i, ax, ay = (int(m.group(k)) for k in range(1, 5))
        if g > 65535 or i > 65535:
            raise SystemExit(f"{path}:{n}: group/image must be 0..65535")
        if not (-32768 <= ax <= 32767 and -32768 <= ay <= 32767):
            raise SystemExit(f"{path}:{n}: axis out of int16 range")
        if (g, i) in seen:
            raise SystemExit(f"{path}:{n}: sprite {g},{i} already defined on line {seen[(g, i)]}")
        seen[(g, i)] = n
        p = Path(m.group(5))
        entries.append(Entry(g, i, ax, ay, p if p.is_absolute() else (path.parent / p).resolve()))
    if not entries:
        raise SystemExit(f"{path}: no sprites in [Sprites] section")
    return entries


def encode(entry: Entry, trim: bool, compress: int) -> bytes:
    """Load, optionally trim, return the raw RGBA bytes used for dedupe; fills entry.data."""
    with Image.open(entry.path) as im:
        im = im.convert("RGBA")
    if trim:
        alpha = np.asarray(im)[..., 3]
        ys, xs = np.nonzero(alpha)
        if xs.size:
            l, t, r, b = int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1
        else:
            l, t, r, b = 0, 0, 1, 1          # fully transparent frame: keep a 1x1 placeholder
        im = im.crop((l, t, r, b))
        entry.axis_x -= l
        entry.axis_y -= t
    if im.width > 65535 or im.height > 65535:
        raise SystemExit(f"{entry.path}: image too large for SFF")
    entry.width, entry.height = im.size
    raw = im.tobytes()
    buf = io.BytesIO()
    im.save(buf, format="PNG", compress_level=compress)
    # SFF v2 compressed formats are prefixed with the decoded size (Ikemen seeks past it).
    entry.data = struct.pack("<I", len(raw)) + buf.getvalue()
    return raw


def build(entries: list[Entry], out: Path, trim: bool, dedupe: bool, compress: int) -> dict:
    entries.sort(key=lambda e: (e.group, e.image))
    first_by_hash: dict[bytes, int] = {}
    linked = 0
    for idx, e in enumerate(entries):
        if not e.path.exists():
            raise SystemExit(f"missing image for sprite {e.group},{e.image}: {e.path}")
        raw = encode(e, trim, compress)
        if dedupe:
            key = hashlib.blake2b(struct.pack("<HH", e.width, e.height) + raw, digest_size=16).digest()
            if key in first_by_hash:
                e.link, e.data = first_by_hash[key], b""
                linked += 1
            else:
                first_by_hash[key] = idx

    # One 256-colour palette (1,1). Unused by 32-bit sprites, but gives palette index 0 a target
    # so the char loads cleanly whatever the .def's pal1 / PaletteMax settings are.
    pal_data = bytes(c for v in range(256) for c in (v, v, v, 0))

    n_spr, n_pal = len(entries), 1
    spr_ofs = HEADER_SIZE
    pal_ofs = spr_ofs + n_spr * SPRITE_NODE.size
    ldata_ofs = pal_ofs + n_pal * PALETTE_NODE.size

    ldata = bytearray(pal_data)
    sprite_nodes = bytearray()
    for idx, e in enumerate(entries):
        if e.data:
            ofs, size, link = len(ldata), len(e.data), 0
            ldata += e.data
        else:
            ofs, size, link = 0, 0, e.link
        sprite_nodes += SPRITE_NODE.pack(e.group, e.image, e.width, e.height, e.axis_x, e.axis_y,
                                         link, FMT_PNG32, 32, ofs, size, 0, 0)  # flags 0 -> ldata
    palette_nodes = PALETTE_NODE.pack(1, 1, 256, 0, 0, len(pal_data))
    tdata_ofs = ldata_ofs + len(ldata)

    header = bytearray(HEADER_SIZE)
    header[0:12] = b"ElecbyteSpr\x00"
    header[12:16] = bytes((0, 1, 0, 2))            # verlo3, verlo2, verlo1, verhi -> v2.01
    struct.pack_into("<IIIIIIIII", header, 36,
                     spr_ofs, n_spr, pal_ofs, n_pal,
                     ldata_ofs, len(ldata), tdata_ofs, 0, 0)

    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(out.suffix + ".tmp")
    with tmp.open("wb") as fh:
        fh.write(header)
        fh.write(sprite_nodes)
        fh.write(palette_nodes)
        fh.write(ldata)
    tmp.replace(out)
    return {"sprites": n_spr, "linked": linked, "bytes": out.stat().st_size}


def replace_sprites(src: Path, out: Path, images: dict) -> dict:
    """Copy an existing SFF v2 with some sprites replaced or added.

    images: {(group, number): (PIL RGBA image, axis_x, axis_y)}. Every other sprite and palette is
    copied byte for byte (whatever its format), so screenpack files built by other tools stay intact.
    """
    b = src.read_bytes()
    if b[:12] != b"ElecbyteSpr\x00" or b[15] != 2:
        raise SystemExit(f"{src}: need an SFF v2 file")
    spr_ofs, n_spr, pal_ofs, n_pal, lofs, llen, tofs, tlen = struct.unpack_from("<8I", b, 36)
    nodes = [list(SPRITE_NODE.unpack_from(b, spr_ofs + i * SPRITE_NODE.size)) for i in range(n_spr)]
    pals = b[pal_ofs:pal_ofs + n_pal * PALETTE_NODE.size]
    ldata = bytearray(b[lofs:lofs + llen])
    tdata = b[tofs:tofs + tlen]
    index = {(nd[0], nd[1]): i for i, nd in enumerate(nodes)}
    replaced = added = 0
    for (g, n), (img, ax, ay) in sorted(images.items()):
        img = img.convert("RGBA")
        raw = img.tobytes()
        buf = io.BytesIO()
        img.save(buf, format="PNG", compress_level=9)
        data = struct.pack("<I", len(raw)) + buf.getvalue()
        node = [g, n, img.width, img.height, ax, ay, 0, FMT_PNG32, 32, len(ldata), len(data), 0, 0]
        ldata += data
        if (g, n) in index:
            nodes[index[(g, n)]] = node
            replaced += 1
        else:
            nodes.append(node)
            added += 1
    new_spr_ofs = HEADER_SIZE
    new_pal_ofs = new_spr_ofs + len(nodes) * SPRITE_NODE.size
    new_lofs = new_pal_ofs + len(pals)
    new_tofs = new_lofs + len(ldata)
    header = bytearray(b[:HEADER_SIZE])
    struct.pack_into("<8I", header, 36, new_spr_ofs, len(nodes), new_pal_ofs, n_pal,
                     new_lofs, len(ldata), new_tofs, len(tdata))
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(out.suffix + ".tmp")
    with tmp.open("wb") as fh:
        fh.write(header)
        for nd in nodes:
            fh.write(SPRITE_NODE.pack(*nd))
        fh.write(pals)
        fh.write(ldata)
        fh.write(tdata)
    tmp.replace(out)
    return {"replaced": replaced, "added": added, "sprites": len(nodes), "bytes": out.stat().st_size}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("manifest", type=Path)
    ap.add_argument("-o", "--out", type=Path, help="output .sff (default: next to the manifest, <name>.sff)")
    ap.add_argument("--no-trim", action="store_true", help="store full canvases instead of alpha-trimmed sprites")
    ap.add_argument("--no-dedupe", action="store_true", help="store identical frames separately")
    ap.add_argument("--compress", type=int, default=6, choices=range(10), metavar="0-9",
                    help="PNG zlib level (9 = smallest/slowest)")
    args = ap.parse_args()

    manifest = args.manifest.resolve()
    entries = parse_manifest(manifest)
    name = manifest.name.split(".")[0]
    out = (args.out or manifest.parent / f"{name}.sff").resolve()
    stats = build(entries, out, trim=not args.no_trim, dedupe=not args.no_dedupe, compress=args.compress)
    print(f"wrote {out}: {stats['sprites']} sprites ({stats['linked']} linked duplicates), "
          f"{stats['bytes'] / 1024:.0f} KiB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
