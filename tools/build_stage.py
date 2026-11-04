#!/usr/bin/env python3
"""
build_stage.py - stage.json + PNG layers -> Ikemen GO stage (.def + .sff) with parallax, animated
elements and rotating sponsor billboards. Renders a preview and checks for gaps at the camera limits.

    python tools/build_stage.py stages/src/corner_store/stage.json [--select engine/data/select.def]
        [--home-stage-for fighter01]

Writes engine/stages/<name>.def, engine/stages/<name>.sff and stages/src/<name>/preview.png.

Coordinates (1280x720 local space): "at": [x, y] puts the element's anchor point x px from the screen
centre and y px from the floor line (negative = up), as seen with the camera centred. "anchor" picks
which point of the image that is: "bottom" (default, bottom-centre), "top" or "center".

Element keys (the list is drawn back to front):
  image | frames [{"image", "ticks"}] | sponsor "<slot>"   one picture, an animation, or an ad slot
  delta [dx, dy]      parallax: 1 moves with the fighters, 0.1 is far away (default 1, 1)
  tile [x, y], tilespacing [x, y]     repeat (1 = forever) e.g. sky, traffic
  velocity [vx, vy]   px per tick (traffic, clouds)        sin_y [amplitude, period_ticks]   bobbing
  trans "add" | "addalpha" (+ alpha [src, dst]) | "sub"     neon glow, light beams
  layer 0 (behind fighters, default) | 1 (in front)         fill true: must cover the screen (gap check)

Sponsors: "sponsors": {"<slot>": {"size": [w, h], "seconds": 8, "images": ["ads/acme.png", ...],
          "qr": {"url": "https://...", "caption": "KEEP THE RENT PAID"}}}
Each image is fitted into the slot (letterboxed, never stretched) and the slot cycles through them.
Swap a sponsor = replace the PNG and rebuild; no positions to touch.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import shutil
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_sff import Entry, build  # noqa: E402

TICKS_PER_SEC = 60
DEFAULTS = {
    "localcoord": [1280, 720], "ground_y": 660,
    "camera": {"boundleft": -500, "boundright": 500, "boundhigh": -450, "boundlow": 0,
               "verticalfollow": 0.85, "tension": 200, "floortension": 200},
    "players": {"p1startx": -280, "p2startx": 280, "leftbound": -1000, "rightbound": 1000, "screen_margin": 60},
}


# --------------------------------------------------------------------------- images

def fit_into(img: Image.Image, size: tuple[int, int], bg: tuple[int, int, int, int]) -> Image.Image:
    """Letterbox img into size (keeps aspect ratio: sponsor logos are never distorted)."""
    w, h = size
    s = min(w / img.width, h / img.height)
    im = img.resize((max(1, round(img.width * s)), max(1, round(img.height * s))), Image.Resampling.LANCZOS)
    out = Image.new("RGBA", size, bg)
    out.alpha_composite(im, ((w - im.width) // 2, (h - im.height) // 2))
    return out


def qr_panel(url: str, caption: str, size: tuple[int, int]) -> Image.Image:
    import qrcode
    q = qrcode.QRCode(border=2, error_correction=qrcode.constants.ERROR_CORRECT_M)
    q.add_data(url)
    q.make(fit=True)
    code = q.make_image(fill_color="black", back_color="white").convert("RGBA")
    w, h = size
    panel = Image.new("RGBA", size, (255, 255, 255, 255))
    side = min(h - 16, int(w * 0.45))
    panel.alpha_composite(code.resize((side, side), Image.Resampling.NEAREST), (8, (h - side) // 2))
    d = ImageDraw.Draw(panel)
    text_w = w - side - 24
    words, lines, cur = caption.split(), [], ""
    font_size = max(14, h // 6)
    font = ImageFont.load_default(font_size)
    for word in words:                               # simple word wrap
        trial = f"{cur} {word}".strip()
        if d.textlength(trial, font=font) > text_w and cur:
            lines.append(cur)
            cur = word
        else:
            cur = trial
    lines.append(cur)
    y = (h - len(lines) * (font_size + 6)) // 2
    for line in lines:
        d.text((side + 16, y), line, fill=(20, 20, 20, 255), font=font)
        y += font_size + 6
    return panel


# --------------------------------------------------------------------------- build

def anchor_axis(img: Image.Image, anchor: str) -> tuple[int, int]:
    return {"bottom": (img.width // 2, img.height), "top": (img.width // 2, 0),
            "center": (img.width // 2, img.height // 2)}[anchor]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("config", type=Path)
    ap.add_argument("--out", type=Path, default=Path("engine/stages"))
    ap.add_argument("--select", type=Path, help="Ikemen data/select.def: add to [ExtraStages]")
    ap.add_argument("--home-stage-for", help="make this the home stage of a character in select.def")
    ap.add_argument("--fighter-sff", type=Path, help="a character .sff drawn into the preview for scale")
    args = ap.parse_args()

    cfg_path = args.config.resolve()
    src = cfg_path.parent
    cfg = json.loads(cfg_path.read_text())
    name = cfg["name"]
    if not re.fullmatch(r"[A-Za-z0-9_\-]+", name):
        raise SystemExit("name must be letters, digits, _ or -")
    W, H = cfg.get("localcoord", DEFAULTS["localcoord"])
    ground = cfg.get("ground_y", DEFAULTS["ground_y"])
    cam = {**DEFAULTS["camera"], **cfg.get("camera", {})}
    pl = {**DEFAULTS["players"], **cfg.get("players", {})}
    work = src / "build"
    if work.exists():
        shutil.rmtree(work)
    work.mkdir()

    entries: list[Entry] = []
    sprite_imgs: dict[tuple[int, int], tuple[Image.Image, int, int]] = {}   # for the preview
    actions: list[str] = []
    bgs: list[str] = []
    elements = []                                                            # resolved, for the preview

    def add_sprite(group: int, num: int, img: Image.Image, axis: tuple[int, int]) -> None:
        p = work / f"{group}_{num}.png"
        img.save(p)
        entries.append(Entry(group, num, axis[0], axis[1], p))
        sprite_imgs[(group, num)] = (img, *axis)

    sponsors = cfg.get("sponsors", {})
    for idx, el in enumerate(cfg["elements"]):
        group = 100 + idx * 10
        anchor = el.get("anchor", "bottom")
        frames: list[tuple[Image.Image, int]] = []
        if "image" in el:
            frames = [(Image.open(src / el["image"]).convert("RGBA"), -1)]
        elif "frames" in el:
            frames = [(Image.open(src / f["image"]).convert("RGBA"), int(f["ticks"])) for f in el["frames"]]
        elif "sponsor" in el:
            slot = sponsors[el["sponsor"]]
            size = tuple(slot["size"])
            bg = tuple(slot.get("background", [12, 12, 16, 255]))
            ticks = int(slot.get("seconds", 8) * TICKS_PER_SEC)
            ads = [fit_into(Image.open(src / a).convert("RGBA"), size, bg) for a in slot.get("images", [])]
            if slot.get("qr", {}).get("url"):
                ads.append(qr_panel(slot["qr"]["url"], slot["qr"].get("caption", ""), size))
            if not ads:
                raise SystemExit(f"sponsor slot '{el['sponsor']}' has no images")
            frames = [(a, ticks if len(ads) > 1 else -1) for a in ads]
        else:
            raise SystemExit(f"element {idx} ({el.get('id')}): needs image, frames or sponsor")

        axis = anchor_axis(frames[0][0], anchor)
        for n, (img, _) in enumerate(frames):
            add_sprite(group, n, img, anchor_axis(img, anchor) if img.size != frames[0][0].size else axis)

        x, y = el.get("at", [0, 0])
        start = (int(x), int(ground + y))
        dx, dy = el.get("delta", [1, 1])
        lines = [f"[BG {el.get('id', idx)}]"]
        if len(frames) > 1:
            action = group
            actions.append(f"[Begin Action {action}]\n" +
                           "\n".join(f"{group},{n}, 0,0, {t}" for n, (_, t) in enumerate(frames)))
            lines += ["type = anim", f"actionno = {action}"]
        else:
            lines += ["type = normal", f"spriteno = {group}, 0"]
        lines += [f"layerno = {int(el.get('layer', 0))}", f"start = {start[0]}, {start[1]}", f"delta = {dx}, {dy}"]
        if el.get("tile"):
            lines.append(f"tile = {el['tile'][0]}, {el['tile'][1]}")
            if el.get("tilespacing"):
                lines.append(f"tilespacing = {el['tilespacing'][0]}, {el['tilespacing'][1]}")
        if el.get("velocity"):
            lines.append(f"velocity = {el['velocity'][0]}, {el['velocity'][1]}")
        if el.get("sin_y"):
            lines.append(f"sin.y = {el['sin_y'][0]}, {el['sin_y'][1]}")
        trans = el.get("trans", "none")
        lines.append(f"trans = {trans}")
        if trans == "addalpha":
            lines.append(f"alpha = {el.get('alpha', [256, 256])[0]}, {el.get('alpha', [256, 256])[1]}")
        lines.append("mask = 1")
        bgs.append("\n".join(lines))
        elements.append(dict(el=el, group=group, frames=frames, start=start, delta=(dx, dy), axis=axis))

    # ---- write .sff + .def
    args.out.mkdir(parents=True, exist_ok=True)
    sff_path = args.out / f"{name}.sff"
    stats = build(entries, sff_path.resolve(), trim=False, dedupe=True, compress=9)
    music = cfg.get("music", "")
    margin = pl["screen_margin"]
    def_text = f"""; {name}.def - generated by build_stage.py from {cfg_path.name}. Edit the JSON, not this file.
[Info]
name = "{name}"
displayname = "{cfg.get('displayname', name)}"
versiondate = 01,01,2026
mugenversion = 1.1
author = "{cfg.get('author', '')}"

[Camera]
startx = 0
starty = 0
boundleft = {cam['boundleft']}
boundright = {cam['boundright']}
boundhigh = {cam['boundhigh']}
boundlow = {cam['boundlow']}
verticalfollow = {cam['verticalfollow']}
floortension = {cam['floortension']}
tension = {cam['tension']}
overdrawhigh = 0
overdrawlow = 0
cuthigh = 0
cutlow = 0

[PlayerInfo]
p1startx = {pl['p1startx']}
p1starty = 0
p1facing = 1
p2startx = {pl['p2startx']}
p2starty = 0
p2facing = -1
leftbound = {pl['leftbound']}
rightbound = {pl['rightbound']}

[Bound]
screenleft = {margin}
screenright = {margin}

[StageInfo]
zoffset = {ground}
autoturn = 1
resetBG = 1
localcoord = {W}, {H}
xscale = 1
yscale = 1

[Shadow]
intensity = 110
color = 0,0,0
yscale = .25

[Reflection]
intensity = 0

[Music]
bgmusic = {music}
bgmvolume = 100

[BGdef]
spr = {name}.sff
debugbg = 0

""" + "\n\n".join(actions) + ("\n\n" if actions else "") + "\n\n".join(bgs) + "\n"
    (args.out / f"{name}.def").write_text(def_text, encoding="utf-8")
    print(f"wrote {args.out / (name + '.def')} + {sff_path.name}: {len(elements)} elements, "
          f"{stats['sprites']} sprites, {stats['bytes'] / 1024:.0f} KiB")

    # ---- preview + gap check at the camera limits
    fighter = None
    if args.fighter_sff and args.fighter_sff.exists():
        from make_greybox import read_sff_v2
        for g, n, ax, ay, rgba in read_sff_v2(args.fighter_sff):
            if (g, n) == (0, 0):
                fighter = (Image.fromarray(rgba, "RGBA"), ax, ay)
                break
    views = [("centre", 0, 0, 0), ("left limit", cam["boundleft"], 0, 300), ("right limit", cam["boundright"], 0, 600),
             ("jump high", 0, cam["boundhigh"], 900)]
    shots, problems = [], 0
    for label, cx, cy, t in views:
        frame, gap = render(elements, W, H, cx, cy, t, fighter, ground, pl)
        if gap > 0.001:
            problems += 1
            print(f"  ! gap at camera {label} ({cx},{cy}): {gap:.1%} of the screen shows no background")
        shots.append((label, frame))
    sheet = Image.new("RGB", (W, H), (0, 0, 0))
    half = (W // 2, H // 2)
    for i, (label, frame) in enumerate(shots):
        tile = frame.convert("RGB").resize(half)
        ImageDraw.Draw(tile).text((10, 8), label, fill=(255, 255, 0), font=ImageFont.load_default(20))
        sheet.paste(tile, ((i % 2) * half[0], (i // 2) * half[1]))
    sheet.save(src / "preview.png")
    shots[0][1].convert("RGB").save(src / "preview_centre.png")      # full-size centre view
    print(f"preview: {src / 'preview.png'}" + ("" if problems else "  (no gaps at any camera limit)"))

    if args.select:
        register(args.select, name, args.home_stage_for)
    return 1 if problems else 0


def render(elements, W, H, camx, camy, t, fighter, ground, pl):
    """Approximates Ikemen's BG drawing: axis at start + screen centre - camera * delta (+ velocity, tile).
    Returns the frame and the fraction of the screen no opaque background layer covers."""
    canvas = Image.new("RGBA", (W, H), (0, 0, 0, 255))
    covered = np.zeros((H, W), bool)

    def layer_image(e) -> Image.Image:
        el, frames = e["el"], e["frames"]
        img = frames[0][0]
        if len(frames) > 1:                                   # animation frame at time t
            k = t % sum(max(1, f[1]) for f in frames)
            for f_img, ticks in frames:
                if k < max(1, ticks):
                    img = f_img
                    break
                k -= max(1, ticks)
        vx, vy = el.get("velocity", [0, 0])
        ox = W / 2 + e["start"][0] - camx * e["delta"][0] + vx * t - e["axis"][0]
        oy = e["start"][1] - camy * e["delta"][1] + vy * t - e["axis"][1]
        if el.get("sin_y"):
            oy += el["sin_y"][0] * math.sin(2 * math.pi * t / el["sin_y"][1])
        xs = [ox]
        if (el.get("tile") or [0])[0]:
            period = img.width + (el.get("tilespacing") or [0, 0])[0]
            first = ox - math.ceil((ox + img.width) / period) * period
            xs = [first + k * period for k in range(int((W - first) // period) + 2)]
        out = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        for x in xs:
            if -img.width < x < W and -img.height < oy < H:
                out.alpha_composite(img, (int(round(x)), int(round(oy))))
        return out

    def composite(e) -> None:
        nonlocal canvas
        img = layer_image(e)
        if e["el"].get("trans") in ("add", "addalpha"):
            base = np.asarray(canvas).astype(np.float32)
            add = np.asarray(img).astype(np.float32)
            base[..., :3] = np.clip(base[..., :3] + add[..., :3] * (add[..., 3:4] / 255.0), 0, 255)
            canvas = Image.fromarray(base.astype(np.uint8), "RGBA")
            return
        canvas.alpha_composite(img)
        if int(e["el"].get("layer", 0)) == 0 and not e["el"].get("velocity"):
            covered[:] |= np.asarray(img)[..., 3] > 0

    for e in elements:
        if int(e["el"].get("layer", 0)) == 0:
            composite(e)
    if fighter:                                           # two copies of the character, for scale
        img, ax, ay = fighter
        for px, flip in ((pl["p1startx"], False), (pl["p2startx"], True)):
            f = img.transpose(Image.Transpose.FLIP_LEFT_RIGHT) if flip else img
            fx = W / 2 + px - camx - ((f.width - ax) if flip else ax)
            canvas.alpha_composite(f, (int(fx), int(ground - camy - ay)))
    for e in elements:
        if int(e["el"].get("layer", 0)) == 1:
            composite(e)
    return canvas, 1.0 - covered.mean()


def register(select: Path, name: str, home_for: str | None) -> None:
    text = select.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines(keepends=True)
    entry = f"stages/{name}.def"
    if not any(l.strip().lower().startswith(entry.lower()) for l in lines):
        start = next(i for i, l in enumerate(lines) if l.strip().lower() == "[extrastages]")
        end = next((i for i in range(start + 1, len(lines)) if lines[i].lstrip().startswith("[")), len(lines))
        while end > start + 1 and not lines[end - 1].strip():
            end -= 1
        lines.insert(end, entry + "\n")
        print(f"[select] added {entry} to [ExtraStages]")
    if home_for:
        for i, l in enumerate(lines):
            if re.match(rf"^\s*{re.escape(home_for)}\s*(,|$)", l.split(";")[0]):
                parts = [p.strip() for p in l.split(";")[0].split(",")]
                parts = [parts[0], entry] + [p for p in parts[1:] if "=" in p]
                lines[i] = ", ".join(parts) + "\n"
                print(f"[select] {home_for} now fights on {entry} in arcade mode")
                break
        else:
            print(f"  ! {home_for} not found in {select}")
    select.write_text("".join(lines), encoding="utf-8")


if __name__ == "__main__":
    sys.exit(main())
