#!/usr/bin/env python3
"""
build_brand.py - your game's own screenpack: title screen, select screen, startup splash, lifebar frame,
window title and icon. Built on Ikemen's bundled ikemen1 screenpack, so every menu keeps working.

    python tools/build_brand.py brand/brand.json [--engine engine]

From brand/brand.json (name, colours, logo/splash/icon PNGs, background art) it writes
engine/data/<slug>/ and points engine/save/config.ini at it ([Config] Motif, WindowTitle, WindowIcon).
Missing logo/splash/icon PNGs are generated once as placeholders in brand/; replace them with real art
(any size: they are fitted, never stretched) and rebuild.

What changes, by sprite in the ikemen1 screenpack (its layout code is untouched):
  0,0 / 0,2       title logo and its glow  -> your logo + a glow in the primary colour
  0,1 / 0,3       "GO" logo part + glow     -> removed
  100,x / 101,x   title / select sky + clouds -> your background art, tinted primary / secondary,
                                               skylines scrolling slowly where the clouds were
  logo.def        startup video            -> your splash (+ "Powered by Ikemen GO" if enabled)
  fight.sff 11,0  lifebar frame            -> tinted primary (health colours stay readable)
  [Title Info]    highlighted menu item    -> secondary colour
Writes brand/preview.png (title + select screens, approximate menu text) and is safe to re-run: it
always starts from the untouched engine/data/ikemen1 and engine/data/fight.* files.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import shutil
import struct
import sys
import io
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_sff import Entry, build, replace_sprites  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
FONT_CANDIDATES = ["/usr/share/fonts/opentype/inter/Inter-ExtraBold.otf", "/usr/share/fonts/opentype/inter/Inter-Bold.otf",
                   "/System/Library/Fonts/Supplemental/Arial Black.ttf", "C:/Windows/Fonts/ariblk.ttf",
                   "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"]


def font(size: int):
    for f in FONT_CANDIDATES:
        if Path(f).exists():
            return ImageFont.truetype(f, size)
    return ImageFont.load_default(size)


def fit(img: Image.Image, box: tuple[int, int]) -> Image.Image:
    s = min(box[0] / img.width, box[1] / img.height)
    return img.resize((max(1, round(img.width * s)), max(1, round(img.height * s))), Image.Resampling.LANCZOS)


def blank() -> tuple[Image.Image, int, int]:
    return Image.new("RGBA", (1, 1), (0, 0, 0, 0)), 0, 0


def tint(img: Image.Image, rgb, amount: float) -> Image.Image:
    a = np.asarray(img.convert("RGBA")).astype(np.float32)
    a[..., :3] = a[..., :3] * (1 - amount) + np.array(rgb, np.float32) * amount * (a[..., :3].mean(-1, keepdims=True) / 255 * 1.6)
    return Image.fromarray(np.clip(a, 0, 255).astype(np.uint8), "RGBA")


# --------------------------------------------------------------------------- placeholder art

def make_logo(name: str, c1, c2) -> Image.Image:
    f = font(260)
    probe = ImageDraw.Draw(Image.new("L", (1, 1)))
    l, t, r, b = probe.textbbox((0, 0), name, font=f)
    w, h = r - l + 120, b - t + 120
    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).text((60 - l, 60 - t), name, font=f, fill=255)
    shear = 0.18                                            # italic lean, reads as speed
    sw = int(w + h * shear)
    mask = mask.transform((sw, h), Image.Transform.AFFINE, (1, shear, -h * shear, 0, 1, 0), Image.Resampling.BICUBIC)
    grad = np.zeros((h, sw, 3), np.float32)
    t_ = np.linspace(0, 1, h)[:, None, None]
    grad[:] = np.array(c1, np.float32) * (1 - t_) + np.array(c2, np.float32) * t_
    fill = Image.fromarray(grad.astype(np.uint8), "RGB").convert("RGBA")
    fill.putalpha(mask)
    outline = mask.filter(ImageFilter.MaxFilter(17))
    logo = Image.new("RGBA", (sw, h), (0, 0, 0, 0))
    logo.paste((12, 10, 20, 255), (0, 0), outline)
    shine = Image.new("RGBA", (sw, h), (255, 255, 255, 0))  # top highlight
    hi = Image.fromarray((np.linspace(110, 0, h)[:, None] * np.ones((1, sw))).astype(np.uint8), "L")
    shine.putalpha(Image.fromarray(np.minimum(np.asarray(hi), np.asarray(mask)).astype(np.uint8)))
    logo.alpha_composite(fill)
    logo.alpha_composite(shine)
    return logo.crop(logo.getbbox())


def make_icon(name: str, c1, c2) -> Image.Image:
    s = 256
    grad = np.zeros((s, s, 3), np.float32)
    t = (np.add.outer(np.arange(s), np.arange(s)) / (2 * s))[..., None]
    grad[:] = np.array(c1, np.float32) * (1 - t) + np.array(c2, np.float32) * t
    bg = Image.fromarray(grad.astype(np.uint8), "RGB").convert("RGBA")
    mask = Image.new("L", (s, s), 0)
    ImageDraw.Draw(mask).rounded_rectangle([8, 8, s - 8, s - 8], radius=48, fill=255)
    icon = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    icon.paste(bg, (0, 0), mask)
    d = ImageDraw.Draw(icon)
    d.text((s // 2, s // 2 + 6), name[:1], font=font(190), fill=(12, 10, 20, 255), anchor="mm")
    return icon


# --------------------------------------------------------------------------- screen art

def background(sky_p: Path, far_p: Path, near_p: Path, color, dim: float) -> dict:
    """dim: 0-1 darkening so menus and the select grid stay readable on top."""
    sky = Image.open(sky_p).convert("RGBA").resize((1280, 960), Image.Resampling.LANCZOS)
    # glow at the horizon in the brand colour
    g = np.zeros((960, 1280, 4), np.float32)
    y = np.arange(960)[:, None]
    g[..., :3] = color
    g[..., 3] = np.clip(np.exp(-((y - 560) / 170.0) ** 2) * 150, 0, 255)
    sky.alpha_composite(Image.fromarray(g.astype(np.uint8), "RGBA"))
    out = {"sky": (_dim(sky, dim), 0, 0)}
    for key, path, bottom, amount in (("far", far_p, 770, 0.35), ("near", near_p, 720, 0.15)):
        layer = _dim(tint(Image.open(path).convert("RGBA"), color, amount), dim)
        canvas = Image.new("RGBA", (layer.width, 960), (0, 0, 0, 0))   # tile period = image width
        canvas.alpha_composite(layer, (0, bottom - layer.height))
        out[key] = (canvas, 0, 0)
    return out


def _dim(img: Image.Image, amount: float) -> Image.Image:
    a = np.asarray(img).astype(np.float32)
    a[..., :3] *= 1.0 - amount
    return Image.fromarray(a.astype(np.uint8), "RGBA")


def glow(img: Image.Image, color) -> Image.Image:
    a = img.getchannel("A").filter(ImageFilter.MaxFilter(9)).filter(ImageFilter.GaussianBlur(14))
    g = Image.new("RGBA", img.size, (*color, 255))
    g.putalpha(a)
    return g


def lifebar_frame(img: Image.Image, color) -> Image.Image:
    a = np.asarray(img.convert("RGBA")).astype(np.float32)
    lum = a[..., :3].mean(-1, keepdims=True) / 255.0
    a[..., :3] = np.clip(np.array(color, np.float32) * (0.35 + lum * 1.4), 0, 255)
    return Image.fromarray(a.astype(np.uint8), "RGBA")


# --------------------------------------------------------------------------- def/config editing

def set_in_section(text: str, section: str, key: str, value: str) -> str:
    lines = text.splitlines(keepends=True)
    out, cur, done = [], None, False
    for line in lines:
        s = line.strip()
        if s.startswith("["):
            if cur == section.lower() and not done:
                out.append(f"{key} = {value}\n")
                done = True
            cur = s.strip("[]").strip().lower()
        elif cur == section.lower() and re.match(rf"(?i)^{re.escape(key)}\s*=", s) and not done:
            comment = line.split(";", 1)[1] if ";" in line else ""
            line = f"{key} = {value}" + (f"   ;{comment}" if comment else "\n")
            done = True
        out.append(line)
    if not done:
        if cur != section.lower():
            out += (["\n"] if out and out[-1].strip() else []) + [f"[{section}]\n"]
        out.append(f"{key} = {value}\n")
    return "".join(out)


# --------------------------------------------------------------------------- preview

def read_png_sprites(path: Path) -> dict:
    b = path.read_bytes()
    so, n, po, npal, lofs, llen, tofs = struct.unpack_from("<7I", b, 36)
    nodes = [struct.unpack_from("<HHHHhhHBBIIHH", b, so + 28 * i) for i in range(n)]
    out = {}
    for i, (g, num, w, h, ax, ay, link, fmt, depth, ofs, size, pal, fl) in enumerate(nodes):
        while size == 0 and link < i:                       # follow links
            g2, n2, w, h, _, _, link2, fmt, depth, ofs, size, pal, fl = nodes[link]
            i, link = link, link2
        if fmt in (11, 12) and size:
            start = (tofs if fl & 1 else lofs) + ofs + 4
            out[(g, num)] = (Image.open(io.BytesIO(b[start:start + size - 4])).convert("RGBA"), ax, ay)
    return out


def bg_sections(text: str, prefix: str) -> list[dict]:
    out, cur = [], None
    for line in text.splitlines():
        s = line.split(";", 1)[0].strip()
        if s.startswith("["):
            name = s.strip("[]").strip()
            cur = {} if name.lower().startswith(prefix.lower() + " ") else None
            if cur is not None:
                out.append(cur)
        elif cur is not None and "=" in s:
            k, v = s.split("=", 1)
            cur[k.strip().lower()] = v.strip()
    return out


def render_screen(text: str, prefix: str, sprites: dict, t: int) -> Image.Image:
    canvas = Image.new("RGBA", (1280, 720), (0, 0, 0, 255))
    for layerno in (0, 1):
        for el in bg_sections(text, prefix):
            if int(el.get("layerno", "0")) != layerno or "spriteno" not in el:
                continue
            g, n = (int(v) for v in el["spriteno"].split(",")[:2])
            if (g, n) not in sprites:
                continue
            img, ax, ay = sprites[(g, n)]
            sx, sy = (float(v) for v in el.get("start", "0,0").split(",")[:2])
            vx = float(el.get("velocity", "0,0").split(",")[0])
            x, y = 640 + sx - ax + vx * t, sy - ay
            if "sin.y" in el:
                amp, period, *phase = (float(v) for v in el["sin.y"].split(","))
                y += amp * math.sin(2 * math.pi * (t + (phase[0] if phase else 0)) / period)
            layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
            xs = [x]
            if el.get("tile", "0").split(",")[0].strip() == "1":
                first = x - math.ceil((x + img.width) / img.width) * img.width
                xs = [first + k * img.width for k in range(int((1280 - first) // img.width) + 2)]
            for xx in xs:
                layer.alpha_composite(img, (int(xx), int(y))) if -img.width < xx < 1280 and -img.height < y < 720 else None
            if el.get("trans", "none").lower().startswith("add"):
                alpha = el.get("alpha", "256,256").split(",")
                k = float(alpha[1]) / 256 if len(alpha) > 1 else 1.0
                base = np.asarray(canvas).astype(np.float32)
                add = np.asarray(layer).astype(np.float32)
                base[..., :3] = np.clip(base[..., :3] + add[..., :3] * add[..., 3:4] / 255 * k, 0, 255)
                canvas = Image.fromarray(base.astype(np.uint8), "RGBA")
            else:
                canvas.alpha_composite(layer)
    return canvas


# --------------------------------------------------------------------------- main

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("config", type=Path)
    ap.add_argument("--engine", type=Path, default=ROOT / "engine")
    args = ap.parse_args()
    cfg = json.loads(args.config.read_text())
    name, slug = cfg["name"], cfg.get("slug") or re.sub(r"[^a-z0-9]+", "_", cfg["name"].lower())
    if not re.fullmatch(r"[a-z0-9_\-]+", slug):
        raise SystemExit("slug must be lowercase letters, digits, _ or -")
    c1, c2 = tuple(cfg["colors"]["primary"]), tuple(cfg["colors"]["secondary"])
    eng = args.engine.resolve()
    base = eng / "data" / "ikemen1"
    if not (base / "system.def").exists():
        raise SystemExit(f"{base}/system.def not found: run tools/setup_engine.py first")
    out = eng / "data" / slug

    # placeholder art, generated once (replace the PNGs with real art)
    made = []
    for key, maker in (("logo", lambda: make_logo(name, c1, c2)), ("splash", lambda: make_logo(name, c1, c2)),
                       ("icon", lambda: make_icon(name, c1, c2))):
        p = ROOT / cfg[key]
        if not p.exists():
            p.parent.mkdir(parents=True, exist_ok=True)
            maker().save(p)
            made.append(p.name)
    if made:
        print(f"placeholder art written: {', '.join('brand/' + m for m in made)} (replace with real art and rebuild)")

    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    for f in base.iterdir():                                   # top-level screenpack files; fonts stay shared
        if f.is_file() and f.suffix.lower() != ".sff" and f.name.lower() != "logo.def":
            shutil.copy2(f, out / f.name)

    # ---- system.sff: logo + backgrounds
    logo = fit(Image.open(ROOT / cfg["logo"]).convert("RGBA"), (620, 200))   # margin: the glow fades out inside the sprite
    lx, ly = 349, 128                                          # keep the original centre axis
    canvas = Image.new("RGBA", (699, 257), (0, 0, 0, 0))
    canvas.alpha_composite(logo, ((699 - logo.width) // 2, (257 - logo.height) // 2))
    bgc = cfg["background"]
    title_bg = background(ROOT / bgc["sky"], ROOT / bgc["far"], ROOT / bgc["near"], c1, cfg.get("title_dim", 0.25))
    select_bg = background(ROOT / bgc["sky"], ROOT / bgc["far"], ROOT / bgc["near"], c2, cfg.get("select_dim", 0.5))
    images = {
        (0, 0): (canvas, lx, ly), (0, 2): (glow(canvas, c1), lx, ly), (0, 1): blank(), (0, 3): blank(),
        (100, 0): title_bg["sky"], (100, 2): title_bg["far"], (100, 3): title_bg["near"],
        (101, 0): select_bg["sky"], (101, 2): select_bg["far"], (101, 3): select_bg["near"],
    }
    st = replace_sprites(base / "system.sff", out / "system.sff", images)

    # ---- system.def: name, author, menu highlight colour
    text = (base / "system.def").read_text(encoding="utf-8", errors="replace")
    text = set_in_section(text, "Info", "name", name)
    if cfg.get("author"):
        text = set_in_section(text, "Info", "author", cfg["author"])
    text = re.sub(r"(?im)^(\s*menu\.item\.active\.font\s*=\s*4,0,-1)(,\s*\d+,\s*\d+,\s*\d+)?",
                  lambda m: f"{m.group(1)}, {c2[0]},{c2[1]},{c2[2]}", text, count=1)
    (out / "system.def").write_text(f"; {name} screenpack - generated by tools/build_brand.py from ikemen1\n" + text,
                                    encoding="utf-8")

    # ---- startup splash storyboard
    splash = fit(Image.open(ROOT / cfg["splash"]).convert("RGBA"), (900, 380))
    build([Entry(0, 0, splash.width // 2, splash.height // 2, _save(splash, out / "_splash.png"))],
          out / "splash.sff", trim=False, dedupe=False, compress=9)
    (out / "_splash.png").unlink()
    powered = """layer1.text = Powered by Ikemen GO
layer1.font = 1, 0, 0, 150, 150, 160
layer1.offset = 0, 300
layer1.starttime = 20
""" if cfg.get("powered_by_ikemen", True) else ""
    (out / "logo.def").write_text(f"""; startup splash - generated by tools/build_brand.py
[Info]
localcoord = 1280,720

[SceneDef]
spr = splash.sff
font1 = Open_Sans.def
startscene = 0

[Scene 0]
clearcolor = 0,0,0
fadein.time = 30
fadeout.time = 30
end.time = 170
layerall.pos = 640,360
layer0.spr = 0,0
layer0.offset = 0,-20
{powered}""", encoding="utf-8")

    # ---- lifebars: same fight.def, frame tinted
    if cfg.get("tint_lifebar_frame", True) and (eng / "data" / "fight.sff").exists():
        shutil.copy2(eng / "data" / "fight.def", out / "fight.def")
        frame = read_png_sprites(eng / "data" / "fight.sff").get((11, 0))
        if frame:
            replace_sprites(eng / "data" / "fight.sff", out / "fight.sff",
                            {(11, 0): (lifebar_frame(frame[0], c1), frame[1], frame[2])})

    # ---- window icon + config
    icon = Image.open(ROOT / cfg["icon"]).convert("RGBA")
    for s in (256, 96, 48):
        fit(icon, (s, s)).save(out / f"icon_{s}.png")
    cfg_ini = eng / "save" / "config.ini"
    ini = cfg_ini.read_text(encoding="utf-8") if cfg_ini.exists() else ""
    ini = set_in_section(ini, "Config", "Motif", f"data/{slug}/system.def")
    ini = set_in_section(ini, "Config", "WindowTitle", name)
    ini = set_in_section(ini, "Config", "WindowIcon",
                         ", ".join(f"data/{slug}/icon_{s}.png" for s in (256, 96, 48)))
    cfg_ini.parent.mkdir(parents=True, exist_ok=True)
    cfg_ini.write_text(ini, encoding="utf-8")

    # ---- checks + preview
    sdef = (out / "system.def").read_text(encoding="utf-8")
    merged = read_png_sprites(out / "system.sff")
    refs = {tuple(int(v) for v in m) for m in re.findall(r"(?im)^\s*spriteno\s*=\s*(\d+)\s*,\s*(\d+)", sdef)}
    from build_char_def import read_sff_sprites
    missing = sorted(refs - set(read_sff_sprites(out / "system.sff")))
    sheet = Image.new("RGB", (1280, 760), (16, 16, 20))
    f = font(18)
    for i, (prefix, label) in enumerate((("TitleBG", "title screen"), ("SelectBG", "select screen"))):
        shot = render_screen(sdef, prefix, merged, t=120)
        if prefix == "TitleBG":
            d = ImageDraw.Draw(shot)
            items = ["ARCADE", "VS MODE", "STORY MODE", "NETWORK", "PRACTICE", "MISSION"]
            for k, item in enumerate(items):
                d.text((1240, 330 + 54 * k), item, font=font(34), anchor="rm",
                       fill=c2 if k == 0 else (235, 235, 235))
        sheet.paste(shot.convert("RGB").resize((640, 360)), (i * 640, 30))
        ImageDraw.Draw(sheet).text((i * 640 + 8, 6), label + " (menu text approximate)" * (i == 0), fill=(255, 255, 0), font=f)
    sp = Image.new("RGBA", (1280, 720), (0, 0, 0, 255))
    sp.alpha_composite(splash, (640 - splash.width // 2, 340 - splash.height // 2))
    if cfg.get("powered_by_ikemen", True):
        ImageDraw.Draw(sp).text((640, 660), "Powered by Ikemen GO", font=font(22), fill=(150, 150, 160), anchor="mm")
    sheet.paste(sp.convert("RGB").resize((426, 240)), (0, 420))
    ImageDraw.Draw(sheet).text((8, 396), "startup splash", fill=(255, 255, 0), font=f)
    if (out / "fight.sff").exists():
        lb = read_png_sprites(out / "fight.sff")
        strip = Image.new("RGBA", (460, 60), (40, 40, 48, 255))
        for key in ((10, 0), (13, 0), (11, 0)):
            if key in lb:
                strip.alpha_composite(lb[key][0], (10 + (2 if key != (11, 0) else 0), 16 + (2 if key != (11, 0) else 0)))
        sheet.paste(strip.convert("RGB"), (450, 440))
        ImageDraw.Draw(sheet).text((450, 416), "lifebar (frame tinted)", fill=(255, 255, 0), font=f)
    sheet.paste(fit(icon, (180, 180)).convert("RGB"), (1060, 450))
    ImageDraw.Draw(sheet).text((1060, 426), "window icon", fill=(255, 255, 0), font=f)
    sheet.save(ROOT / "brand" / "preview.png")

    print(f"wrote {out}: system.sff ({st['replaced']} sprites replaced), system.def, logo.def + splash.sff, "
          f"fight.def/.sff, icons")
    print(f"{cfg_ini}: Motif = data/{slug}/system.def, WindowTitle = {name}")
    print(f"sprites referenced by the screenpack missing from system.sff: {missing or 'none'}")
    print(f"preview: {ROOT / 'brand' / 'preview.png'}")
    return 1 if missing else 0


def _save(img: Image.Image, p: Path) -> Path:
    img.save(p)
    return p


if __name__ == "__main__":
    sys.exit(main())
