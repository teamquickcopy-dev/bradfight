#!/usr/bin/env python3
"""
make_sff_manifest.py - turn an aligned sprite tree into an SFF build manifest.

Reads   characters/<char>/03_aligned/<group>_<label>/*.png   (+ alignment.json for the axis)
Writes  characters/<char>/04_build/<char>.sffmanifest.txt

Each animation folder becomes one sprite group; frames are numbered 0..n-1 in
natural sort order (or by the trailing number in the filename with
--number-from filename, which keeps numbers stable if you delete a frame).

Manifest format (plain text, one sprite per line, paths relative to the manifest):

    [Sprites]
    ; group, image, axis_x, axis_y, path
    0, 0, 256, 460, ../03_aligned/0_stand/0000.png

Compile it with tools/build_sff.py. Pass --air <template>.air to list every
sprite the template animations reference that your art does not provide yet;
missing sprites are invisible in-game, not an error, so check before shipping.

Example:
    python tools/make_sff_manifest.py characters/kid_k --air templates/fighter_base/fighter.air \
        --extra characters/kid_k/portraits
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

IMAGE_EXTS = {".png"}
SEQ_DIR_RE = re.compile(r"^(\d+)(?:[_\-\s].*)?$")
AIR_FRAME_RE = re.compile(r"^\s*(-?\d+)\s*,\s*(-?\d+)\s*,\s*-?\d+\s*,\s*-?\d+\s*,\s*-?\d+")
AIR_ACTION_RE = re.compile(r"^\s*\[\s*begin\s+action\s+(-?\d+)\s*\]", re.IGNORECASE)


def natural_key(p: Path):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", p.name)]


def trailing_number(p: Path) -> int | None:
    m = re.findall(r"\d+", p.stem)
    return int(m[-1]) if m else None


def collect(root: Path, number_from: str, axis: tuple[int, int], rel_base: Path,
            sprites: dict[tuple[int, int], tuple[int, int, str]]) -> None:
    for d in sorted((d for d in root.iterdir() if d.is_dir()), key=natural_key):
        m = SEQ_DIR_RE.match(d.name)
        if not m:
            print(f"  ! skipping '{d.name}': folder name must start with a group number")
            continue
        group = int(m.group(1))
        if not 0 <= group <= 65535:
            print(f"  ! skipping '{d.name}': group must be 0..65535")
            continue
        files = sorted((f for f in d.iterdir() if f.suffix.lower() in IMAGE_EXTS), key=natural_key)
        for idx, f in enumerate(files):
            num = idx
            if number_from == "filename":
                num = trailing_number(f)
                if num is None:
                    print(f"  ! {d.name}/{f.name}: no number in filename, skipped")
                    continue
            key = (group, num)
            if key in sprites:
                print(f"  ! duplicate sprite {group},{num}: {f} overrides {sprites[key][2]}")
            sprites[key] = (axis[0], axis[1], os.path.relpath(f, rel_base).replace(os.sep, "/"))


def air_references(air_path: Path) -> dict[tuple[int, int], list[int]]:
    """{(group, image): [action numbers that use it]} for every frame line in an .air file."""
    refs: dict[tuple[int, int], list[int]] = {}
    action = None
    for raw in air_path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.split(";", 1)[0]
        m = AIR_ACTION_RE.match(line)
        if m:
            action = int(m.group(1))
            continue
        if action is None or line.strip().lower().startswith(("clsn", "loopstart", "interpolate")):
            continue
        m = AIR_FRAME_RE.match(line)
        if m:
            g, i = int(m.group(1)), int(m.group(2))
            if g >= 0 and i >= 0:   # -1 = intentionally blank frame
                refs.setdefault((g, i), []).append(action)
    return refs


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("char_dir", type=Path)
    ap.add_argument("--aligned", default="03_aligned")
    ap.add_argument("--out", type=Path, help="manifest path (default <char>/04_build/<char>.sffmanifest.txt)")
    ap.add_argument("--axis", type=int, nargs=2, metavar=("X", "Y"),
                    help="override the axis from alignment.json")
    ap.add_argument("--number-from", choices=("index", "filename"), default="index")
    ap.add_argument("--extra", type=Path, action="append", default=[],
                    help="extra <group>_<label> tree NOT ground-aligned (portraits, effects); axis 0,0. Repeatable.")
    ap.add_argument("--air", type=Path, help="template .air file to cross-check coverage against")
    ap.add_argument("--strict", action="store_true", help="exit 1 if the .air check finds missing sprites")
    args = ap.parse_args()

    char_dir = args.char_dir.resolve()
    aligned = char_dir / args.aligned
    if not aligned.is_dir():
        print(f"error: {aligned} not found (run align_sprites.py first)", file=sys.stderr)
        return 2

    meta_path = aligned / "alignment.json"
    meta = json.loads(meta_path.read_text()) if meta_path.exists() else {}
    if args.axis:
        axis = tuple(args.axis)
    elif "axis" in meta:
        axis = tuple(meta["axis"])
    else:
        print("error: no alignment.json; pass --axis X Y", file=sys.stderr)
        return 2

    out = (args.out or char_dir / "04_build" / f"{char_dir.name}.sffmanifest.txt").resolve()
    out.parent.mkdir(parents=True, exist_ok=True)

    sprites: dict[tuple[int, int], tuple[int, int, str]] = {}
    collect(aligned, args.number_from, axis, out.parent, sprites)
    for extra in args.extra:
        collect(extra.resolve(), args.number_from, (0, 0), out.parent, sprites)
    if not sprites:
        print("error: no sprites found", file=sys.stderr)
        return 2

    lines = [
        "; SFF build manifest - generated by make_sff_manifest.py, edit by hand if needed",
        "; compile: python tools/build_sff.py <this file>",
        "; paths are relative to this file",
        "[Info]",
        f"name = {char_dir.name}",
        f"canvas = {', '.join(map(str, meta.get('canvas', ['?', '?'])))}",
        f"axis = {axis[0]}, {axis[1]}",
        "",
        "[Sprites]",
        "; group, image, axis_x, axis_y, path",
    ]
    prev_group = None
    for (g, i) in sorted(sprites):
        if prev_group is not None and g != prev_group:
            lines.append("")
        ax, ay, path = sprites[(g, i)]
        lines.append(f"{g}, {i}, {ax}, {ay}, {path}")
        prev_group = g
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")

    groups = sorted({g for g, _ in sprites})
    print(f"wrote {out}  ({len(sprites)} sprites, {len(groups)} groups)")

    if args.air:
        refs = air_references(args.air)
        missing = sorted(k for k in refs if k not in sprites)
        unused = sorted({g for g, _ in sprites} - {g for g, _ in refs})
        print(f"[air] {args.air.name}: {len(refs)} sprites referenced, {len(missing)} missing")
        by_group: dict[int, list[int]] = {}
        for g, i in missing:
            by_group.setdefault(g, []).append(i)
        for g, imgs in by_group.items():
            actions = sorted({a for i in imgs for a in refs[(g, i)]})
            print(f"  missing group {g}: images {imgs}  (used by action {', '.join(map(str, actions))})")
        if unused:
            print(f"  note: groups not referenced by the .air: {unused}")
        if missing and args.strict:
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
