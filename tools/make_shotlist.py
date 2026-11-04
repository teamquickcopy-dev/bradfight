#!/usr/bin/env python3
"""
make_shotlist.py - list every sprite a template's .air needs, as a render checklist.

Writes  characters/<char>/SHOTLIST.md            (group, image numbers, which animations use them)
        characters/<char>/shots.json             render config for rigs/render_passes.py: set "action"
                                                 per group (existing entries are kept on re-run)
Creates characters/<char>/01_raw/<group>_<label>/ (one empty folder per sprite group)

Name each rendered frame after its image number (0000.png, 0001.png, 0010.png ...) and build
with NUMBER_FROM=filename. Groups whose numbers have gaps (e.g. 5000: 0, 10, 11, 20) only
work that way; numbering by sort order would shift every frame after the gap.

Example:
    python tools/make_shotlist.py characters/kid_k --air templates/fighter_base/kfm720.air
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from make_sff_manifest import air_references  # noqa: E402

ACTION_RE = re.compile(r"^\s*\[\s*begin\s+action\s+(-?\d+)\s*\]", re.IGNORECASE)


def action_labels(air: Path) -> dict[int, str]:
    """Action number -> the comment line right above its [Begin Action] header."""
    labels, last_comment = {}, ""
    for line in air.read_text(encoding="utf-8", errors="replace").splitlines():
        s = line.strip()
        m = ACTION_RE.match(s)
        if m:
            labels[int(m.group(1))] = last_comment
            last_comment = ""
        elif s.startswith(";"):
            text = s.lstrip(";").strip()
            if not text or set(text) <= set("-=_*"):
                last_comment = ""            # separator line: the action's name comes after it
            elif not last_comment:
                last_comment = text          # first line of the comment block names the action
        elif s:
            last_comment = ""
    return labels


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")[:40] or "anim"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("char_dir", type=Path)
    ap.add_argument("--air", type=Path, required=True, help="template .air")
    ap.add_argument("--no-folders", action="store_true", help="only write SHOTLIST.md")
    args = ap.parse_args()

    refs = air_references(args.air)
    labels = action_labels(args.air)
    groups: dict[int, dict[int, list[int]]] = {}
    for (g, i), actions in refs.items():
        groups.setdefault(g, {})[i] = sorted(set(actions))

    char_dir = args.char_dir.resolve()
    raw = char_dir / "01_raw"
    rows, total = [], 0
    for g in sorted(groups):
        imgs = sorted(groups[g])
        actions = sorted({a for i in imgs for a in groups[g][i]})
        owner = g if g in actions else actions[0]
        label = labels.get(owner, "") or f"action {owner}"
        folder = f"{g}_{slug(label)}"
        gaps = imgs != list(range(len(imgs)))
        total += len(imgs)
        rows.append((g, folder, imgs, actions, label, gaps))
        if not args.no_folders:
            (raw / folder).mkdir(parents=True, exist_ok=True)

    portraits = [p for p in ((9000, 0), (9000, 1)) if p not in refs]
    lines = [
        f"# Shot list: {char_dir.name}",
        "",
        f"Template animations: `{args.air.name}`. You need **{total} sprites in {len(rows)} groups**"
        + (", plus portraits 9000,0 (small) and 9000,1 (big) in `portraits/9000_portraits/`." if portraits else "."),
        "",
        "Save each frame as `<image number>.png`, zero-padded (`0000.png`, `0010.png`), in its folder.",
        "Build with `NUMBER_FROM=filename`. Rows marked *gaps* only build correctly that way.",
        "",
        "| Group | Folder | Images | Count | Used by actions |",
        "|---:|---|---|---:|---|",
    ]
    for g, folder, imgs, actions, label, gaps in rows:
        shown = ", ".join(map(str, imgs)) if len(imgs) <= 12 else f"{imgs[0]}–{imgs[-1]}" + (" (gaps)" if gaps else "")
        if gaps and len(imgs) <= 12:
            shown += " *gaps*"
        acts = ", ".join(f"{a} {labels.get(a, '')}".strip() for a in actions[:4])
        if len(actions) > 4:
            acts += f", +{len(actions) - 4} more"
        lines.append(f"| {g} | `{folder}` | {shown} | {len(imgs)} | {acts} |")
    (char_dir).mkdir(parents=True, exist_ok=True)
    (char_dir / "SHOTLIST.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    if portraits and not args.no_folders:
        (char_dir / "portraits" / "9000_portraits").mkdir(parents=True, exist_ok=True)
    shots_path = char_dir / "shots.json"
    shots = json.loads(shots_path.read_text()) if shots_path.exists() else {
        "armature": None, "facing": "-Y", "resolution": [1024, 1024], "root_pixel": [512, 600],
        "target_height_px": 420, "groups": {}}
    for g, folder, imgs, actions, label, gaps in rows:
        entry = shots["groups"].setdefault(str(g), {"action": "", "frames": "auto", "loop": False, "anchor": "origin"})
        entry.update({"folder": folder, "images": imgs, "label": label})
    shots_path.write_text(json.dumps(shots, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {char_dir / 'SHOTLIST.md'}: {total} sprites in {len(rows)} groups"
          + ("" if args.no_folders else f"; folders created under {raw}"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
