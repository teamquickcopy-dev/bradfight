#!/usr/bin/env python3
"""
align_sprites.py - batch background removal + ground-locked sprite alignment.

Input layout (one folder per animation, folder name starts with the MUGEN group):

    characters/<char>/01_raw/
        0_stand/0000.png 0001.png ...
        20_walk_fwd/...
        200_light_punch/...

Output:

    characters/<char>/02_cutout/<same tree>   rembg results (cached, reused on rerun)
    characters/<char>/03_aligned/<same tree>  uniform canvases, feet on the ground line
    characters/<char>/03_aligned/alignment.json   canvas + axis metadata (read by make_sff_manifest.py)
    characters/<char>/03_aligned/alignment_report.csv   per-frame measurements and QA flags

Placement modes:

  rig (default)  Every render comes from the same locked camera, so the rig's root/ground point
                 sits at the same pixel in every frame: --source-root X Y. One fixed transform maps
                 that pixel to the canvas axis. Jumps, knockdowns, lunges and walk cycles keep
                 exactly the motion the rig authored. If the root moves between renders, export it
                 per frame from Blender into 01_raw/anchors.json: {"0_stand/0000.png": [x, y], ...}.
                 QA (--reference DIR): compares each cutout with the rig's own silhouette for that
                 frame (a Blender alpha-mask or depth pass, same <group>_<label>/<file> layout;
                 OpenPose maps are skeletons and won't work). Low overlap is flagged MISMATCH
                 (morphing, rembg damage), a shifted silhouette is flagged DRIFT.
  snap           No rig data: put each frame's lowest opaque row at ground_y - 1 and centre it on
                 the feet. Only valid for grounded animations. Measured against Ikemen's kfm720,
                 it misplaces airborne/knockdown frames by up to 350 px and jitters walk cycles.

The axis (ground_x, ground_y) is what goes into the SFF for every sprite.

Example:
    python tools/align_sprites.py characters/kid_k --source-root 512 900 --scale 0.5
    python tools/align_sprites.py characters/kid_k --mode snap --skip-rembg
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import statistics
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from PIL import Image

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tga"}
SEQ_DIR_RE = re.compile(r"^(\d+)(?:[_\-\s].*)?$")


# --------------------------------------------------------------------------- utils

def natural_key(p: Path):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", p.name)]


def list_images(folder: Path) -> list[Path]:
    return sorted((p for p in folder.iterdir() if p.suffix.lower() in IMAGE_EXTS and p.is_file()),
                  key=natural_key)


def list_sequences(root: Path) -> list[Path]:
    seqs = [d for d in root.iterdir() if d.is_dir() and SEQ_DIR_RE.match(d.name)]
    skipped = [d.name for d in root.iterdir() if d.is_dir() and not SEQ_DIR_RE.match(d.name)]
    for name in skipped:
        print(f"  ! skipping '{name}': folder name must start with a group number, e.g. 200_light_punch")
    return sorted(seqs, key=lambda d: (int(SEQ_DIR_RE.match(d.name).group(1)), d.name))


# --------------------------------------------------------------------------- stage 1: rembg

_SESSION = None


def _init_worker(model: str) -> None:
    global _SESSION
    from rembg import new_session  # imported lazily so --skip-rembg needs no onnx models
    _SESSION = new_session(model)


def _cutout_one(src: str, dst: str, alpha_matting: bool) -> str:
    from rembg import remove
    with Image.open(src) as im:
        im = im.convert("RGBA")
        out = remove(im, session=_SESSION, alpha_matting=alpha_matting,
                     alpha_matting_foreground_threshold=240,
                     alpha_matting_background_threshold=10,
                     alpha_matting_erode_size=10)
    Path(dst).parent.mkdir(parents=True, exist_ok=True)
    out.save(dst, optimize=False)
    return dst


def run_rembg(jobs: list[tuple[Path, Path]], model: str, workers: int, alpha_matting: bool, force: bool) -> None:
    todo = [(s, d) for s, d in jobs
            if force or not d.exists() or d.stat().st_mtime < s.stat().st_mtime]
    print(f"[rembg] {len(jobs) - len(todo)} cached, {len(todo)} to process (model={model}, workers={workers})")
    if not todo:
        return
    if workers <= 1:
        _init_worker(model)
        for i, (s, d) in enumerate(todo, 1):
            _cutout_one(str(s), str(d), alpha_matting)
            print(f"  [{i}/{len(todo)}] {s.parent.name}/{s.name}", end="\r", flush=True)
        print()
        return
    with ProcessPoolExecutor(max_workers=workers, initializer=_init_worker, initargs=(model,)) as ex:
        futs = [ex.submit(_cutout_one, str(s), str(d), alpha_matting) for s, d in todo]
        for i, f in enumerate(as_completed(futs), 1):
            f.result()
            print(f"  [{i}/{len(todo)}]", end="\r", flush=True)
    print()


def copy_as_cutout(jobs: list[tuple[Path, Path]]) -> None:
    """--skip-rembg: source images already have alpha; normalise them to RGBA PNGs."""
    for s, d in jobs:
        if d.exists() and d.stat().st_mtime >= s.stat().st_mtime:
            continue
        d.parent.mkdir(parents=True, exist_ok=True)
        with Image.open(s) as im:
            if im.mode not in ("RGBA", "LA", "PA") and "transparency" not in im.info:
                print(f"  ! {s} has no alpha channel; it will be treated as fully opaque")
            im.convert("RGBA").save(d)


# --------------------------------------------------------------------------- stage 2: measure

@dataclass
class Frame:
    sequence: str
    file: str
    cutout: str
    width: int = 0
    height: int = 0
    bbox: tuple[int, int, int, int] | None = None   # left, top, right, bottom (inclusive)
    anchor_x: float = 0.0
    foot_y: int = 0
    silhouette_h: int = 0
    paste_x: int = 0
    paste_y: int = 0
    placed_foot: int | None = None
    iou: float | None = None
    drift: tuple[float, float] | None = None
    edge_delta: tuple[int, int, int, int] | None = None   # cutout bbox minus reference bbox (l, t, r, b)
    ref_height: int = 0
    flags: list[str] = field(default_factory=list)


def load_clean(path: Path, scale: float, alpha_cutoff: int) -> Image.Image:
    im = Image.open(path).convert("RGBA")
    if scale != 1.0:
        im = im.resize((max(1, round(im.width * scale)), max(1, round(im.height * scale))),
                       Image.Resampling.LANCZOS)
    if alpha_cutoff > 0:
        arr = np.array(im)
        a = arr[..., 3]
        arr[a < alpha_cutoff] = 0          # kill rembg haze / ghost pixels (RGB too, avoids colour fringes)
        im = Image.fromarray(arr, "RGBA")
    return im


def measure(fr: Frame, im: Image.Image, solid: int, min_row_px: int, x_mode: str, foot_band: float) -> None:
    fr.width, fr.height = im.size
    alpha = np.asarray(im)[..., 3]
    mask = alpha >= solid

    # rows/cols with enough solid pixels; ignores single stray specks below the feet
    rows = np.flatnonzero(mask.sum(axis=1) >= min_row_px)
    cols = np.flatnonzero(mask.sum(axis=0) >= 1)
    if rows.size == 0 or cols.size == 0:
        fr.flags.append("EMPTY")
        return
    top, bottom = int(rows[0]), int(rows[-1])
    left, right = int(cols[0]), int(cols[-1])
    fr.bbox = (left, top, right, bottom)
    fr.foot_y = bottom
    fr.silhouette_h = bottom - top + 1

    if x_mode == "bbox":
        fr.anchor_x = (left + right) / 2.0
    elif x_mode == "mass":
        w = alpha.astype(np.float64)
        fr.anchor_x = float((w.sum(axis=0) * np.arange(w.shape[1])).sum() / w.sum())
    else:  # feet: centre of mass of the lowest band of the silhouette
        band_h = max(4, int(round(fr.silhouette_h * foot_band)))
        band = alpha[max(top, bottom - band_h + 1):bottom + 1].astype(np.float64)
        col_w = band.sum(axis=0)
        fr.anchor_x = float((col_w * np.arange(col_w.size)).sum() / col_w.sum()) if col_w.sum() else (left + right) / 2.0


def compare_reference(fr: Frame, ref_dir: Path, solid: int, foot_band: float, drift_tol: float, min_iou: float) -> None:
    """Rig-truth QA in raw-render pixels: overlap and offset of the cutout vs the rig silhouette."""
    ref_path = next((p for p in (ref_dir / fr.file, ref_dir / (Path(fr.file).stem + ".png")) if p.exists()), None)
    if ref_path is None:
        fr.flags.append("NO_REFERENCE")
        return
    cut = np.asarray(Image.open(fr.cutout).convert("RGBA"))[..., 3] >= solid
    ref_im = Image.open(ref_path)
    if ref_im.size != (cut.shape[1], cut.shape[0]):
        ref_im = ref_im.resize((cut.shape[1], cut.shape[0]), Image.Resampling.NEAREST)
    if ref_im.mode in ("RGBA", "LA", "PA") or "transparency" in ref_im.info:
        ref = np.asarray(ref_im.convert("RGBA"))[..., 3] >= solid
    else:                                            # depth / mask pass: anything not black is body
        ref = np.asarray(ref_im.convert("L")) > 8
    union = np.logical_or(cut, ref).sum()
    fr.iou = float(np.logical_and(cut, ref).sum() / union) if union else 0.0

    def anchor(mask: np.ndarray) -> tuple[float, float] | None:
        rows = np.flatnonzero(mask.sum(axis=1) >= 3)
        if rows.size == 0:
            return None
        top, bot = rows[0], rows[-1]
        band = mask[max(top, bot - max(4, int((bot - top + 1) * foot_band)) + 1):bot + 1].sum(axis=0)
        return float((band * np.arange(band.size)).sum() / band.sum()), float(bot)

    def bbox(mask: np.ndarray) -> tuple[int, int, int, int] | None:
        ys, xs = np.nonzero(mask)
        return (int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())) if xs.size else None

    cb, rb = bbox(cut), bbox(ref)
    if cb and rb:
        fr.edge_delta = tuple(c - r for c, r in zip(cb, rb))
        fr.ref_height = rb[3] - rb[1] + 1
    a, b = anchor(cut), anchor(ref)
    if a and b:
        fr.drift = (a[0] - b[0], a[1] - b[1])
        if max(abs(fr.drift[0]), abs(fr.drift[1])) > drift_tol:
            fr.flags.append(f"DRIFT({fr.drift[0]:+.0f},{fr.drift[1]:+.0f}px)")
    if fr.iou < min_iou:
        fr.flags.append(f"MISMATCH(overlap {fr.iou:.2f})")


# --------------------------------------------------------------------------- main

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("char_dir", type=Path, help="characters/<name> folder containing 01_raw/")
    ap.add_argument("--raw", default="01_raw")
    ap.add_argument("--cutout", default="02_cutout")
    ap.add_argument("--out", default="03_aligned")
    ap.add_argument("--mode", choices=("rig", "snap"), default="rig", help="see the placement modes above")
    ap.add_argument("--source-root", type=float, nargs=2, metavar=("X", "Y"),
                    help="rig mode: pixel of the rig's root on the floor in the RAW renders (before --scale)")
    ap.add_argument("--anchors", type=Path,
                    help="rig mode: per-frame root pixels from rigs/render_passes.py (default 01_raw/anchors.json)")
    ap.add_argument("--reference", type=Path,
                    help="rig mode QA: folder of per-frame silhouette masks/depth renders from the rig")
    ap.add_argument("--drift-tol", type=float, default=6.0, help="DRIFT threshold in raw-render px")
    ap.add_argument("--min-iou", type=float, default=0.75, help="MISMATCH threshold (silhouette overlap 0-1)")
    ap.add_argument("--canvas", type=int, nargs=2, default=(768, 1024), metavar=("W", "H"))
    ap.add_argument("--ground", type=int, nargs=2, default=(384, 600), metavar=("X", "Y"),
                    help="axis point on the canvas: root X and floor line Y (default 384 600: room for "
                         "jumps above and airborne hit poses below the axis)")
    ap.add_argument("--scale", type=float, default=1.0,
                    help="ONE global scale for every frame. Never auto-fit per frame: sizes would pump.")
    ap.add_argument("--x-mode", choices=("feet", "mass", "bbox"), default="feet",
                    help="feet (default): centre of the lowest band of the body, stable when arms/legs extend. "
                         "mass: whole-body centre of mass (character slides backward on punches). bbox: box centre.")
    ap.add_argument("--foot-band", type=float, default=0.12, help="fraction of silhouette height used by --x-mode feet")
    ap.add_argument("--sequence-lock", action="store_true",
                    help="snap mode: use ONE offset per animation (median of its frames) instead of per-frame. Keeps authored "
                         "root motion (lunges, steps) when renders come from a locked camera.")
    ap.add_argument("--alpha-cutoff", type=int, default=24, help="alpha below this becomes fully transparent (0 = off)")
    ap.add_argument("--solid", type=int, default=128, help="alpha at/above this counts as body when measuring")
    ap.add_argument("--min-row-px", type=int, default=3, help="a row needs this many solid pixels to count as feet")
    ap.add_argument("--height-tolerance", type=float, default=0.12,
                    help="snap mode: flag frames whose silhouette height deviates from the animation median "
                         "by more than this")
    ap.add_argument("--skip-rembg", action="store_true", help="sources already have transparency")
    ap.add_argument("--model", default="isnet-anime",
                    help="rembg model: isnet-anime (stylised/anime), u2net, isnet-general-use, birefnet-general ...")
    ap.add_argument("--alpha-matting", action="store_true", help="softer hair/edge cutouts (slower)")
    ap.add_argument("--workers", type=int, default=1, help="rembg processes (each loads its own model)")
    ap.add_argument("--force", action="store_true", help="re-run rembg even if cached cutouts are newer")
    args = ap.parse_args()

    char_dir: Path = args.char_dir.resolve()
    raw_dir, cut_dir, out_dir = char_dir / args.raw, char_dir / args.cutout, char_dir / args.out
    if not raw_dir.is_dir():
        print(f"error: {raw_dir} not found", file=sys.stderr)
        return 2
    cw, ch = args.canvas
    gx, gy = args.ground
    if not (0 <= gx < cw and 0 < gy <= ch):
        print("error: --ground must lie inside --canvas", file=sys.stderr)
        return 2
    anchors: dict[str, list[float]] = {}
    if args.mode == "rig":
        anchor_file = args.anchors or raw_dir / "anchors.json"
        if anchor_file.exists():
            anchors = json.loads(anchor_file.read_text())
            print(f"[rig] {len(anchors)} per-frame root positions from {anchor_file.name}")
        if not args.source_root and not anchors:
            print("error: rig mode needs --source-root X Y (or 01_raw/anchors.json); "
                  "use --mode snap if you have no rig data", file=sys.stderr)
            return 2

    sequences = list_sequences(raw_dir)
    jobs: list[tuple[Path, Path]] = []
    frames_by_seq: dict[str, list[Frame]] = {}
    for seq in sequences:
        for img in list_images(seq):
            dst = cut_dir / seq.name / (img.stem + ".png")
            jobs.append((img, dst))
            frames_by_seq.setdefault(seq.name, []).append(Frame(seq.name, img.name, str(dst)))
    if not jobs:
        print(f"error: no images found under {raw_dir}", file=sys.stderr)
        return 2
    print(f"{len(jobs)} frames in {len(frames_by_seq)} animations")

    # ---- stage 1
    if args.skip_rembg:
        copy_as_cutout(jobs)
    else:
        run_rembg(jobs, args.model, max(1, args.workers), args.alpha_matting, args.force)

    # ---- stage 2: measure every frame
    images: dict[tuple[str, str], Image.Image] = {}
    for seq_name, frames in frames_by_seq.items():
        for fr in frames:
            im = load_clean(Path(fr.cutout), args.scale, args.alpha_cutoff)
            measure(fr, im, args.solid, args.min_row_px, args.x_mode, args.foot_band)
            images[(seq_name, fr.file)] = im

    # ---- stage 3: compute offsets, QA, paste onto canvas
    out_dir.mkdir(parents=True, exist_ok=True)
    warnings = 0
    for seq_name, frames in frames_by_seq.items():
        valid = [f for f in frames if f.bbox]
        if not valid:
            continue
        med_h = statistics.median(f.silhouette_h for f in valid)
        lock_dx = statistics.median(gx - f.anchor_x for f in valid)
        lock_dy = statistics.median((gy - 1) - f.foot_y for f in valid)

        (out_dir / seq_name).mkdir(parents=True, exist_ok=True)
        for fr in frames:
            if not fr.bbox:
                warnings += 1
                continue
            if args.mode == "snap" and med_h and abs(fr.silhouette_h - med_h) / med_h > args.height_tolerance:
                fr.flags.append(f"HEIGHT_OUTLIER({fr.silhouette_h}px vs median {med_h:.0f})")

            if args.mode == "rig":
                rx, ry = anchors.get(f"{seq_name}/{fr.file}", args.source_root or (None, None))
                if rx is None:
                    fr.flags.append("NO_ANCHOR")
                    continue
                dx, dy = gx - rx * args.scale, gy - ry * args.scale
            elif args.sequence_lock:
                dx, dy = lock_dx, lock_dy
            else:
                dx, dy = gx - fr.anchor_x, (gy - 1) - fr.foot_y
            fr.paste_x, fr.paste_y = int(round(dx)), int(round(dy))

            l, t, r, b = fr.bbox
            if l + fr.paste_x < 0 or t + fr.paste_y < 0 or r + fr.paste_x >= cw or b + fr.paste_y >= ch:
                fr.flags.append("CLIPPED")

            fr.placed_foot = fr.foot_y + fr.paste_y
            canvas = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
            canvas.paste(images[(seq_name, fr.file)], (fr.paste_x, fr.paste_y))
            canvas.save(out_dir / seq_name / (Path(fr.file).stem + ".png"))
            if fr.flags:
                warnings += 1

        if args.reference:
            for fr in frames:
                if fr.bbox:
                    compare_reference(fr, args.reference / seq_name, args.solid, args.foot_band,
                                      args.drift_tol, args.min_iou)

    # ---- outline QA: an edge far from the character's usual offset = missing/extra body part.
    # Using the median offset over all frames cancels a consistent costume difference (hair, coat).
    if args.reference:
        with_ref = [f for v in frames_by_seq.values() for f in v if f.edge_delta]
        if with_ref:
            med = [statistics.median(f.edge_delta[k] for f in with_ref) for k in range(4)]
            for f in with_ref:
                tol = max(args.drift_tol * 3, 0.05 * f.ref_height)
                bad = [f"{name} {f.edge_delta[k] - med[k]:+.0f}px" for k, name in
                       enumerate(("left", "top", "right", "bottom")) if abs(f.edge_delta[k] - med[k]) > tol]
                if bad:
                    f.flags.append(f"OUTLINE({', '.join(bad)})")

    # ---- metadata + report
    meta = {
        "canvas": [cw, ch],
        "axis": [gx, gy],
        "scale": args.scale,
        "mode": args.mode,
        "source_root": args.source_root,
        "x_mode": args.x_mode,
        "sequence_lock": args.sequence_lock,
        "sequences": {k: len(v) for k, v in frames_by_seq.items()},
    }
    (out_dir / "alignment.json").write_text(json.dumps(meta, indent=2))

    report = out_dir / "alignment_report.csv"
    with report.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["sequence", "file", "src_w", "src_h", "bbox_l", "bbox_t", "bbox_r", "bbox_b",
                    "anchor_x", "foot_y", "silhouette_h", "paste_x", "paste_y", "ref_overlap", "drift_x",
                    "drift_y", "flags"])
        for frames in frames_by_seq.values():
            for f in frames:
                bb = f.bbox or ("", "", "", "")
                w.writerow([f.sequence, f.file, f.width, f.height, *bb, f"{f.anchor_x:.1f}", f.foot_y,
                            f.silhouette_h, f.paste_x, f.paste_y,
                            "" if f.iou is None else f"{f.iou:.3f}",
                            "" if f.drift is None else f"{f.drift[0]:.1f}",
                            "" if f.drift is None else f"{f.drift[1]:.1f}", "; ".join(f.flags)])

    flagged = [f for v in frames_by_seq.values() for f in v if f.flags]
    for f in flagged[:25]:
        print(f"  ! {f.sequence}/{f.file}: {', '.join(f.flags)}")
    if len(flagged) > 25:
        print(f"  ! ... {len(flagged) - 25} more, see {report}")
    print(f"done: {out_dir}  ({len(flagged)} flagged frame(s), report: {report.name})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
