#!/usr/bin/env python3
"""
build_char_def.py - wire a built .sff into the template fighter and emit a ready-to-load character.

    templates/fighter_base/   (fighter.def + .cmd/.cns/.air/.snd/... from a balanced template)
  + characters/<char>/04_build/<char>.sff
  = engine/chars/<char>/      <char>.def, <char>.sff, <char>.air, <char>.cns, <char>.cmd, ...

What it does:
  1. Copies the template folder (everything except its .def and its own .sff), renaming files
     whose stem matches the template .def (kfm.cmd -> <char>.cmd) and rewriting [Files] to match.
  2. Rewrites [Info] (name, displayname, author, versiondate) and points sprite= at the new .sff.
     palN= lines are dropped: .act palettes do nothing to 32-bit sprites.
     [Arcade] storyboards are commented out (they show the template's art) unless --keep-arcade.
  3. HD scale match (--scale auto): your sprites are usually far taller than the template's.
     Ikemen scales sprites AND hitboxes by the cns [Size] xscale/yscale, so the builder sets
     xscale/yscale = 1/R and multiplies every .air Clsn box and frame offset by R, where
     R = your standing sprite height / template standing sprite height (axis Y of sprite 0,0).
     In-game size, hitboxes, velocities and pushboxes then match the template exactly, and the
     art keeps its full resolution.
  4. Validates: every sprite the .air references must exist in the new .sff; portraits
     9000,0 / 9000,1 are required by the select screen; every file in [Files] must exist.
  5. Optionally registers the character in Ikemen's select.def (--select).

Example:
    python tools/build_char_def.py kid_k --sff characters/kid_k/04_build/kid_k.sff \
        --template templates/fighter_base --out engine/chars/kid_k \
        --displayname "Kid K" --author "Big Deals" --select engine/data/select.def
"""
from __future__ import annotations

import argparse
import datetime as dt
import re
import shutil
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from make_sff_manifest import air_references  # noqa: E402

SECTION_RE = re.compile(r"^\s*\[\s*([^\]]+?)\s*\]")
KV_RE = re.compile(r"^(\s*)([A-Za-z0-9_.]+)(\s*=\s*)([^;\r\n]*?)(\s*)(;.*)?$")
CLSN_RE = re.compile(r"^(\s*Clsn[12]\s*\[\s*\d+\s*\]\s*=\s*)(-?\d+)\s*,\s*(-?\d+)\s*,\s*(-?\d+)\s*,\s*(-?\d+)(.*)$",
                     re.IGNORECASE)
FRAME_RE = re.compile(r"^(\s*-?\d+\s*,\s*-?\d+\s*,\s*)(-?\d+)(\s*,\s*)(-?\d+)(\s*,\s*-?\d+.*)$")
FILE_KEYS = re.compile(r"^(cmd|cns|st\d*|stcommon|sprite|anim|sound|ai|movelist\d*|pal\d+|font\d+)$", re.IGNORECASE)


# --------------------------------------------------------------------------- SFF / ini helpers

def read_sff_sprites(path: Path) -> dict[tuple[int, int], tuple[int, int, int, int]]:
    """{(group, image): (w, h, axis_x, axis_y)} from an SFF v1 or v2 file (headers only, no decoding)."""
    b = path.read_bytes()
    if b[:12] != b"ElecbyteSpr\x00":
        raise SystemExit(f"{path}: not an SFF file")
    out: dict[tuple[int, int], tuple[int, int, int, int]] = {}
    if b[15] == 2:
        ofs, n = struct.unpack_from("<II", b, 36)
        for i in range(n):
            g, num, w, h, ax, ay = struct.unpack_from("<HHHHhh", b, ofs + 28 * i)
            out.setdefault((g, num), (w, h, ax, ay))
    elif b[15] == 1:
        _, n, ofs = struct.unpack_from("<III", b, 16)
        for _ in range(n):
            if ofs <= 0 or ofs + 32 > len(b):
                break
            nxt, length, ax, ay, g, num = struct.unpack_from("<IIhhHH", b, ofs)
            w = h = 0
            if length >= 128:                              # embedded PCX header: xmin, ymin, xmax, ymax
                x0, y0, x1, y1 = struct.unpack_from("<HHHH", b, ofs + 32 + 4)
                w, h = x1 - x0 + 1, y1 - y0 + 1
            out.setdefault((g, num), (w, h, ax, ay))
            ofs = nxt
    else:
        raise SystemExit(f"{path}: unsupported SFF version {b[15]}")
    return out


def read_text(path: Path) -> str:
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1", errors="replace")


def ini_get(lines: list[str], section: str) -> dict[str, str]:
    vals, cur = {}, None
    for line in lines:
        m = SECTION_RE.match(line)
        if m:
            cur = m.group(1).strip().lower()
            continue
        if cur == section:
            kv = KV_RE.match(line)
            if kv:
                vals.setdefault(kv.group(2).lower(), kv.group(4).strip().strip('"'))
    return vals


def find_ci(folder: Path, rel: str) -> Path | None:
    """Case-insensitive lookup (MUGEN content is authored on Windows)."""
    p = folder / rel
    if p.exists():
        return p
    cur = folder
    for part in Path(rel.replace("\\", "/")).parts:
        if not cur.is_dir():
            return None
        hit = next((c for c in cur.iterdir() if c.name.lower() == part.lower()), None)
        if hit is None:
            return None
        cur = hit
    return cur


# --------------------------------------------------------------------------- transforms

def scale_air(text: str, r: float) -> tuple[str, int]:
    def s(v: str) -> str:
        return str(int(round(int(v) * r)))
    out, changed = [], 0
    for line in text.splitlines(keepends=True):
        body, nl = (line[:-2], "\r\n") if line.endswith("\r\n") else (line.rstrip("\n"), "\n" if line.endswith("\n") else "")
        code, sep, comment = body.partition(";")
        m = CLSN_RE.match(code)
        if m:
            code = f"{m.group(1)}{s(m.group(2))}, {s(m.group(3))}, {s(m.group(4))}, {s(m.group(5))}{m.group(6)}"
            changed += 1
        else:
            m = FRAME_RE.match(code)
            if m and (m.group(2) != "0" or m.group(4) != "0"):
                code = f"{m.group(1)}{s(m.group(2))}{m.group(3)}{s(m.group(4))}{m.group(5)}"
                changed += 1
        out.append(code + sep + comment + nl)
    return "".join(out), changed


def scale_cns_size(text: str, inv_r: float) -> tuple[str, float, float]:
    lines = text.splitlines(keepends=True)
    in_size, found, size_idx, old = False, set(), None, {"xscale": 1.0, "yscale": 1.0}
    for i, line in enumerate(lines):
        m = SECTION_RE.match(line)
        if m:
            if in_size:
                break
            in_size = m.group(1).strip().lower() == "size"
            size_idx = i if in_size else size_idx
            continue
        if not in_size:
            continue
        kv = KV_RE.match(line.rstrip("\r\n"))
        if kv and kv.group(2).lower() in ("xscale", "yscale"):
            key = kv.group(2).lower()
            try:
                old[key] = float(kv.group(4))
            except ValueError:
                pass
            new = f"{old[key] * inv_r:.5f}".rstrip("0").rstrip(".")
            nl = "\r\n" if line.endswith("\r\n") else "\n"
            lines[i] = f"{kv.group(1)}{kv.group(2)}{kv.group(3)}{new}{kv.group(5) or ' '}{kv.group(6) or ''}".rstrip() + nl
            found.add(key)
    if size_idx is None:
        raise SystemExit("template cns has no [Size] section")
    for key in ("yscale", "xscale"):
        if key not in found:
            lines.insert(size_idx + 1, f"{key} = {old[key] * inv_r:.5f}\n")
    return "".join(lines), old["xscale"] * inv_r, old["yscale"] * inv_r


def rewrite_def(lines: list[str], info: dict[str, str], files: dict[str, str | None], keep_arcade: bool) -> str:
    out, cur, seen = [], None, set()

    def flush_missing(section: str) -> None:
        wanted = info if section == "info" else files if section == "files" else {}
        for k, v in wanted.items():
            if (section, k) not in seen and v is not None:
                out.append(f"{k} = {v}\n")
                seen.add((section, k))

    for line in lines:
        m = SECTION_RE.match(line)
        if m:
            if cur in ("info", "files"):
                while out and not out[-1].strip():
                    out.pop()
                flush_missing(cur)
                out.append("\n")
            cur = m.group(1).strip().lower()
            out.append(line if line.endswith("\n") else line + "\n")
            continue
        kv = KV_RE.match(line.rstrip("\r\n"))
        table = info if cur == "info" else files if cur == "files" else None
        if kv and table is not None and kv.group(2).lower() in table:
            key = kv.group(2).lower()
            seen.add((cur, key))
            if table[key] is None:
                continue                      # drop the line
            comment = f" {kv.group(6)}" if kv.group(6) else ""
            out.append(f"{kv.group(1)}{kv.group(2)}{kv.group(3)}{table[key]}{comment}\n")
            continue
        if cur == "arcade" and kv and not keep_arcade:
            out.append(f";{line.rstrip()}   ; disabled by build_char_def.py (template storyboard)\n")
            continue
        out.append(line if line.endswith("\n") else line + "\n")
    if cur in ("info", "files"):
        flush_missing(cur)
    return "".join(out)


def register_select(select: Path, entry: str) -> str:
    text = read_text(select)
    lines = text.splitlines(keepends=True)
    if any(re.match(rf"^\s*{re.escape(entry)}\s*(,|;|$)", l.rstrip("\r\n"), re.IGNORECASE) for l in lines):
        return "already registered"
    start = next((i for i, l in enumerate(lines) if SECTION_RE.match(l)
                  and SECTION_RE.match(l).group(1).strip().lower() == "characters"), None)
    if start is None:
        raise SystemExit(f"{select}: no [Characters] section")
    end = next((i for i in range(start + 1, len(lines)) if SECTION_RE.match(lines[i])), len(lines))
    insert_at = end
    while insert_at > start + 1 and not lines[insert_at - 1].strip():
        insert_at -= 1
    if insert_at > 0 and not lines[insert_at - 1].endswith("\n"):
        lines[insert_at - 1] += "\n"
    lines.insert(insert_at, f"{entry}\n")
    select.write_text("".join(lines), encoding="utf-8")
    return f"added to {select}"


# --------------------------------------------------------------------------- main

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("char", help="character id: folder and file stem (no spaces)")
    ap.add_argument("--sff", type=Path, required=True, help="the .sff built by build_sff.py")
    ap.add_argument("--template", type=Path, default=Path("templates/fighter_base"),
                    help="template folder containing one .def (or a path to the .def)")
    ap.add_argument("--out", type=Path, help="output folder (default engine/chars/<char>)")
    ap.add_argument("--displayname", help="name shown in game (default: char id)")
    ap.add_argument("--author", default="")
    ap.add_argument("--scale", default="auto",
                    help="'auto' (match template sprite size), '1' (no change) or a number R = new/template size")
    ap.add_argument("--ref-sprite", default="0,0", help="sprite used to measure size for --scale auto")
    ap.add_argument("--scale-snap", type=float, default=0.015,
                    help="--scale auto: ratios this close to 1 are treated as exactly 1")
    ap.add_argument("--keep-arcade", action="store_true", help="keep the template's intro/ending storyboards")
    ap.add_argument("--select", type=Path, help="Ikemen data/select.def to register the character in")
    ap.add_argument("--force", action="store_true", help="overwrite an existing output folder")
    ap.add_argument("--strict", action="store_true", help="exit 1 if sprites or files are missing")
    args = ap.parse_args()

    if not re.fullmatch(r"[A-Za-z0-9_\-]+", args.char):
        print("error: char id must be letters, digits, _ or -", file=sys.stderr)
        return 2
    tpl = args.template.resolve()
    if tpl.is_dir():
        defs = sorted(tpl.glob("*.def"))
        defs = [d for d in defs if "[files]" in read_text(d).lower()]
        if len(defs) != 1:
            print(f"error: expected one character .def in {tpl}, found {[d.name for d in defs]}", file=sys.stderr)
            return 2
        tpl_def = defs[0]
    else:
        tpl_def, tpl = tpl, tpl.parent
    sff = args.sff.resolve()
    if not sff.exists():
        print(f"error: {sff} not found", file=sys.stderr)
        return 2
    out = (args.out or Path("engine/chars") / args.char).resolve()
    if out.exists() and any(out.iterdir()):
        if not args.force:
            print(f"error: {out} exists (use --force to rebuild)", file=sys.stderr)
            return 2
        ours = any(read_text(d).startswith("; ") and "generated by build_char_def.py" in read_text(d)[:200]
                   for d in out.glob("*.def"))
        if not ours:
            print(f"error: {out} was not generated by build_char_def.py; refusing to delete it", file=sys.stderr)
            return 2
        shutil.rmtree(out)
    out.mkdir(parents=True)

    def_lines = read_text(tpl_def).splitlines(keepends=True)
    tpl_files = ini_get(def_lines, "files")
    stem = tpl_def.stem.lower()
    problems = 0

    # ---- 1. copy template, renaming <template-stem>.* -> <char>.*
    tpl_sprite = find_ci(tpl, tpl_files["sprite"]) if tpl_files.get("sprite") else None
    renames: dict[str, str] = {}
    for src in sorted(tpl.rglob("*")):
        if src.is_dir() or src == tpl_def or (tpl_sprite and src == tpl_sprite) or src.name == ".gitkeep":
            continue
        rel = src.relative_to(tpl)
        name = rel.name
        if Path(name).stem.lower() == stem:
            name = args.char + Path(name).suffix.lower()
        dst_rel = rel.with_name(name)
        (out / dst_rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, out / dst_rel)
        renames[rel.as_posix().lower()] = dst_rel.as_posix()
    sprite_rel = f"{args.char}.sff"
    shutil.copy2(sff, out / sprite_rel)

    files: dict[str, str | None] = {}
    for key, val in tpl_files.items():
        if not FILE_KEYS.match(key):
            continue
        if re.fullmatch(r"pal\d+", key):
            files[key] = None
            continue
        hit = find_ci(tpl, val)
        new = renames.get(hit.relative_to(tpl).as_posix().lower()) if hit and tpl in hit.parents else None
        if key == "sprite":
            files[key] = sprite_rel
        elif new:
            files[key] = new
        elif key == "stcommon":
            files[key] = val                  # resolved from the engine's data/ folder
        else:
            print(f"  ! [Files] {key} = {val}: not in template, line dropped")
            files[key] = None
            problems += 1

    # ---- 2. HD scale match
    new_sprites = read_sff_sprites(out / sprite_rel)
    ref = tuple(int(v) for v in args.ref_sprite.split(","))
    if args.scale == "auto":
        if not tpl_sprite or not tpl_sprite.exists():
            print("error: --scale auto needs the template .sff; pass --scale R", file=sys.stderr)
            return 2
        tpl_sprites = read_sff_sprites(tpl_sprite)
        if ref not in tpl_sprites or ref not in new_sprites:
            print(f"error: reference sprite {ref} missing from {'template' if ref not in tpl_sprites else 'new'} "
                  f".sff; pass --ref-sprite or --scale", file=sys.stderr)
            return 2
        r = new_sprites[ref][3] / tpl_sprites[ref][3]
        print(f"[scale] standing height {new_sprites[ref][3]}px vs template {tpl_sprites[ref][3]}px -> R = {r:.4f}")
        if abs(r - 1.0) < args.scale_snap:
            print(f"[scale] within {args.scale_snap:.1%} of the template: treating as 1 (drift noise, not a size change)")
            r = 1.0
    else:
        r = float(args.scale)
    if r <= 0:
        print("error: scale must be > 0", file=sys.stderr)
        return 2
    if r < 0.95:
        print(f"  ! sprites are {1 / r:.2f}x UPSCALED in game (blurry). Re-run align_sprites.py with "
              f"its --scale multiplied by {1 / r:.2f} (bigger --canvas if it clips), or use a lower-res template.")

    anim_path = out / files["anim"] if files.get("anim") else None
    cns_path = out / files["cns"] if files.get("cns") else None
    if abs(r - 1.0) > 1e-3:
        if anim_path:
            txt, n = scale_air(read_text(anim_path), r)
            anim_path.write_text(txt, encoding="utf-8")
            print(f"[scale] {anim_path.name}: {n} Clsn boxes / frame offsets multiplied by {r:.4f}")
        if cns_path:
            txt, xs, ys = scale_cns_size(read_text(cns_path), 1.0 / r)
            cns_path.write_text(txt, encoding="utf-8")
            print(f"[scale] {cns_path.name}: [Size] xscale = {xs:.5f}, yscale = {ys:.5f}")

    # ---- 3. write the .def
    old_info = ini_get(def_lines, "info")
    info = {
        "name": f'"{args.char}"',
        "displayname": f'"{args.displayname or args.char}"',
        "author": f'"{args.author}"',
        "versiondate": dt.date.today().strftime("%m,%d,%Y"),
        "pal.defaults": None,
    }
    if "localcoord" not in old_info:
        print("  note: template .def has no localcoord; Ikemen assumes 320,240")
    def_text = rewrite_def(def_lines, info, files, args.keep_arcade)
    header = (f"; {args.char}.def - generated by build_char_def.py on {dt.date.today().isoformat()}\n"
              f"; template: {tpl_def.name}   sprite scale R = {r:.4f}\n")
    (out / f"{args.char}.def").write_text(header + def_text, encoding="utf-8")

    # ---- 4. validate
    for key, val in files.items():
        if val and key != "stcommon" and not (out / val).exists():
            print(f"  ! [Files] {key} = {val}: file missing")
            problems += 1
    if anim_path and anim_path.exists():
        refs = air_references(anim_path)
        missing = sorted(k for k in refs if k not in new_sprites)
        by_group: dict[int, list[int]] = {}
        for g, i in missing:
            by_group.setdefault(g, []).append(i)
        print(f"[air] {len(refs)} sprites referenced, {len(missing)} missing from {sprite_rel} "
              f"({len(by_group)} groups)")
        for g, imgs in list(by_group.items())[:40]:
            actions = sorted({a for i in imgs for a in refs[(g, i)]})
            shown = imgs if len(imgs) <= 12 else imgs[:12] + ["..."]
            print(f"  missing {g}: {shown}  (actions {', '.join(map(str, actions[:8]))}{' ...' if len(actions) > 8 else ''})")
        if len(by_group) > 40:
            print(f"  ... {len(by_group) - 40} more groups")
        problems += bool(missing)
    for portrait in ((9000, 0), (9000, 1)):
        if portrait not in new_sprites:
            print(f"  ! sprite {portrait[0]},{portrait[1]} missing: "
                  f"{'small' if portrait[1] == 0 else 'big'} select-screen portrait")
            problems += 1

    # ---- 5. register
    if args.select:
        print(f"[select] {register_select(args.select.resolve(), args.char)}")

    print(f"done: {out / (args.char + '.def')}  ({problems} issue(s))")
    return 1 if (problems and args.strict) else 0


if __name__ == "__main__":
    sys.exit(main())
