#!/usr/bin/env python3
"""
add_juice.py - stronger hit feel for a built character: tiered hitstop, screen shake, knockdown shake.

    python tools/add_juice.py engine/chars/fighter01 [--profile mods/juice.json] [--dry-run]

Every HitDef in the character's .cns files is classified by strength:
  super    state number in super_states (default 3000-4999)
  special  state number in special_states (default 1000-2999)
  heavy    animtype Hard / Heavy / Back / Up / DiagUp
  medium   animtype Medium / Med
  light    everything else

Left alone: throws (attr with T) and hits that send the opponent to a custom state (p2stateno), and the lighter lead-in hits of a multi-hit move
(a state whose HitDefs have different pausetimes: only the strongest hit gets the juice, so quick
multi-hits stay quick).

Then, per tier (mods/juice.json):
  pausetime  raised to the tier's hitstop, never lowered. The gap between the attacker's freeze and the
             defender's shake is kept, so frame advantage and combos are unchanged. Hits with 0,0 are left
             alone (multi-hit and projectile designs).
  envshake   added when the HitDef has none (amplitude scaled to the character's localcoord).
  fall.envshake  added to hits that knock down (fall = 1) and have none.

Changed lines are tagged "; juice", so re-running restores the originals first: tune juice.json and run again.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

STATE_RE = re.compile(r"^\s*\[\s*state\s+(-?\d+)\s*(?:,[^\]]*)?\]", re.IGNORECASE)
STATEDEF_RE = re.compile(r"^\s*\[\s*statedef\s+(-?\d+)", re.IGNORECASE)
KV_RE = re.compile(r"^(\s*)([A-Za-z0-9_.]+)(\s*=\s*)([^;\r\n]*?)(\s*)(;.*)?$")
JUICE_WAS = re.compile(r";\s*juice \(was (.*?)\)")
HEAVY = {"hard", "heavy", "back", "up", "diagup"}
MEDIUM = {"medium", "med"}


def read_def_localcoord(char_dir: Path) -> tuple[int, list[Path]]:
    defs = [d for d in char_dir.glob("*.def") if "[files]" in d.read_text(errors="replace").lower()]
    if len(defs) != 1:
        raise SystemExit(f"expected one character .def in {char_dir}")
    text = defs[0].read_text(errors="replace")
    m = re.search(r"^\s*localcoord\s*=\s*(\d+)", text, re.M | re.I)
    width = int(m.group(1)) if m else 320
    files = []
    for key in ("cns", "st", *[f"st{i}" for i in range(10)]):
        m = re.search(rf"^\s*{key}\s*=\s*([^;\r\n]+)", text, re.M | re.I)
        if m:
            p = char_dir / m.group(1).strip()
            if p.exists() and p not in files:
                files.append(p)
    return width, files


def restore(lines: list[str]) -> list[str]:
    """Undo a previous run: drop added lines, put original values back."""
    out = []
    for line in lines:
        code = line.split(";", 1)[0]
        if line.rstrip().endswith("; juice"):
            continue
        m = JUICE_WAS.search(line)
        if m:
            kv = KV_RE.match(code.rstrip("\r\n"))
            line = f"{kv.group(1)}{kv.group(2)}{kv.group(3)}{m.group(1)}\n"
        out.append(line)
    return out


def tier_of(statedef: int, animtype: str, prof: dict) -> str:
    lo, hi = prof.get("super_states", [3000, 4999])
    if lo <= statedef <= hi:
        return "super"
    lo, hi = prof.get("special_states", [1000, 2999])
    if lo <= statedef <= hi:
        return "special"
    a = animtype.strip().lower()
    return "heavy" if a in HEAVY else "medium" if a in MEDIUM else "light"


def process(path: Path, prof: dict, shake_scale: float, report: list) -> str:
    lines = restore(path.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True))
    # find controller blocks: [State ...] header up to the next header
    heads = [i for i, l in enumerate(lines) if l.lstrip().startswith("[")]
    statedef = 0
    out = list(lines)
    inserts: list[tuple[int, list[str]]] = []
    hitdefs = []                                           # (statedef, kv)
    for n, i in enumerate(heads):
        m = STATEDEF_RE.match(lines[i])
        if m:
            statedef = int(m.group(1))
            continue
        if not STATE_RE.match(lines[i]):
            continue
        end = heads[n + 1] if n + 1 < len(heads) else len(lines)
        kv = {}
        for j in range(i + 1, end):
            mm = KV_RE.match(lines[j].rstrip("\r\n"))
            if mm and not lines[j].lstrip().startswith(";"):
                kv[mm.group(2).lower()] = (j, mm)
        if "type" in kv and kv["type"][1].group(4).strip().lower() == "hitdef":
            hitdefs.append((statedef, kv))

    def pause_of(kv) -> int | None:
        if "pausetime" not in kv:
            return None
        try:
            return int(float(kv["pausetime"][1].group(4).split(",")[0]))
        except ValueError:
            return None
    strongest: dict[int, int] = {}
    for sd, kv in hitdefs:
        p = pause_of(kv)
        if p is not None:
            strongest[sd] = max(strongest.get(sd, p), p)

    for statedef, kv in hitdefs:
        animtype = kv["animtype"][1].group(4) if "animtype" in kv else "light"
        attr = kv["attr"][1].group(4).replace(" ", "").upper() if "attr" in kv else ""
        if "T" in attr.split(",", 1)[-1] or "p2stateno" in kv:
            why = "throw" if "T" in attr.split(",", 1)[-1] else "custom"
            report.append((path.name, statedef, animtype.strip(), why,
                           "skipped" + ("" if why == "throw" else " (p2stateno: custom reaction timing)"), "-", "-"))
            continue
        p = pause_of(kv)
        if p is not None and p < strongest.get(statedef, p):
            report.append((path.name, statedef, animtype.strip(), "lead-in", f"{p} kept (multi-hit lead-in)", "-", "-"))
            continue
        tier = tier_of(statedef, animtype, prof)
        cfg = prof["tiers"][tier]
        last = max(j for j, _ in kv.values())
        indent = kv["type"][1].group(1)
        added, change = [], ""
        # hitstop
        if "pausetime" in kv:
            j, mm = kv["pausetime"]
            vals = [v.strip() for v in mm.group(4).split(",")]
            try:
                p1, p2 = int(float(vals[0])), int(float(vals[1] if len(vals) > 1 else vals[0]))
            except ValueError:
                p1 = p2 = None                         # expression: leave it to the designer
            if p1 is not None and (p1, p2) != (0, 0) and p1 < cfg["hitstop"]:
                n1 = cfg["hitstop"]
                n2 = n1 + (p2 - p1)                    # keep the attacker/defender gap
                out[j] = f"{mm.group(1)}{mm.group(2)}{mm.group(3)}{n1},{n2} ; juice (was {mm.group(4).strip()})\n"
                change = f"{p1},{p2} -> {n1},{n2}"
            elif p1 is not None:
                change = f"{p1},{p2} kept" + (" (0,0 = multi-hit/projectile)" if (p1, p2) == (0, 0) else "")
        else:
            n1 = cfg["hitstop"]
            added.append(f"{indent}pausetime = {n1},{n1} ; juice\n")
            change = f"none -> {n1},{n1}"
        # screen shake on hit
        shake = cfg.get("shake")
        has_shake = any(k.startswith("envshake.") for k in kv)
        if shake and not has_shake:
            added += [f"{indent}envshake.time = {shake['time']} ; juice\n",
                      f"{indent}envshake.freq = {shake['freq']} ; juice\n",
                      f"{indent}envshake.ampl = {round(shake['ampl'] * shake_scale)} ; juice\n"]
        # knockdown impact shake
        falls = "fall" in kv and kv["fall"][1].group(4).strip() == "1"
        kd = prof.get("knockdown_shake")
        if falls and kd and not any(k.startswith("fall.envshake.") for k in kv):
            added += [f"{indent}fall.envshake.time = {kd['time']} ; juice\n",
                      f"{indent}fall.envshake.freq = {kd['freq']} ; juice\n",
                      f"{indent}fall.envshake.ampl = {round(kd['ampl'] * shake_scale)} ; juice\n"]
        if added:
            inserts.append((last + 1, added))
        report.append((path.name, statedef, animtype.strip(), tier, change,
                       "added" if shake and not has_shake else ("kept" if has_shake else "-"),
                       "added" if falls and kd and not any(k.startswith("fall.envshake.") for k in kv) else "-"))
    for pos, block in sorted(inserts, reverse=True):
        if pos > 0 and not out[pos - 1].endswith("\n"):
            out[pos - 1] += "\n"
        out[pos:pos] = block
    return "".join(out)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("char_dir", type=Path)
    ap.add_argument("--profile", type=Path, default=Path("mods/juice.json"))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    prof = json.loads(args.profile.read_text())
    width, files = read_def_localcoord(args.char_dir)
    if not files:
        raise SystemExit("no .cns files found in the .def's [Files]")
    scale = width / 1280.0
    report: list = []
    for f in files:
        text = process(f, prof, scale, report)
        if not args.dry_run:
            f.write_text(text, encoding="utf-8")
    print(f"{'state':>6} {'animtype':9} {'tier':8} {'pausetime':34} {'shake':6} {'knockdown shake'}")
    for name, st, at, tier, change, shake, fall in report:
        print(f"{st:>6} {at[:9]:9} {tier:8} {change:34} {shake:6} {fall}")
    raised = sum("->" in r[4] for r in report)
    shakes = sum(r[5] == "added" for r in report)
    falls = sum(r[6] == "added" for r in report)
    print(f"{len(report)} HitDefs: hitstop raised on {raised}, screen shake added to {shakes}, knockdown shake added to {falls}"
          f" (localcoord {width}: shake x{scale:.2f})" + ("  [dry run]" if args.dry_run else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
