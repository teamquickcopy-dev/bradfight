#!/usr/bin/env python3
"""
build_snd.py - character sounds: a recording checklist from the template, and a headless .snd builder.

    python tools/build_snd.py list  characters/kid_k --template templates/fighter_base
    python tools/build_snd.py build characters/kid_k --template templates/fighter_base \
        --char-out engine/chars/kid_k

list   writes characters/<char>/SOUNDLIST.md (every sound the template's moves play: group, number,
       which moves, the template's length as a timing guide) and creates
       characters/<char>/sounds/<group>_<label>/ folders.
build  reads sounds/<group>_<label>/<number>.(wav|mp3|ogg|flac|m4a|aiff|opus), converts each with ffmpeg
       to 16-bit 44.1 kHz mono (--stereo to keep stereo), trims silence, adds 5 ms fades against clicks,
       evens out loudness (RMS target, peaks capped at -1 dBFS), and writes <char-out>/<char>.snd,
       pointing the character's .def at it. Sounds you haven't recorded fall back to the template's
       (--no-fallback to leave them silent); the report lists which.

Only sounds from the character's own .snd are listed: PlaySnd values without an F/GO prefix, and
HitDef hitsound/guardsound values with an S prefix. Hit and guard sounds without S come from the
engine's shared fight.snd.
"""
from __future__ import annotations

import argparse
import io
import re
import shutil
import struct
import subprocess
import sys
import wave
from collections import defaultdict
from pathlib import Path

import numpy as np

AUDIO_EXTS = {".wav", ".mp3", ".ogg", ".flac", ".m4a", ".aiff", ".aif", ".opus"}
RATE = 44100
SECTION_RE = re.compile(r"^\s*\[\s*([^\]]+?)\s*\]")
STATEDEF_RE = re.compile(r"^\s*\[\s*statedef\s+(-?\d+)", re.IGNORECASE)
KV_RE = re.compile(r"^\s*([A-Za-z0-9_.]+)\s*=\s*([^;\r\n]*)")
SND_REF = re.compile(r"^\s*(F|S|GO)?\s*(\d+)\s*(?:,\s*(\d+))?\s*$", re.IGNORECASE)


# --------------------------------------------------------------------------- .snd read / write

def read_snd(path: Path) -> dict[tuple[int, int], bytes]:
    b = path.read_bytes()
    if b[:12] != b"ElecbyteSnd\x00":
        raise SystemExit(f"{path}: not an SND file")
    count, off = struct.unpack_from("<II", b, 16)
    out = {}
    for _ in range(count):
        nxt, ln, g, n = struct.unpack_from("<IIii", b, off)
        out.setdefault((g, n), b[off + 16: off + 16 + ln])
        off = nxt
    return out


def write_snd(path: Path, sounds: dict[tuple[int, int], bytes]) -> None:
    header = bytearray(512)
    header[:12] = b"ElecbyteSnd\x00"
    header[12:16] = bytes((4, 0, 0, 0))                   # same version bytes as MUGEN 1.x files
    struct.pack_into("<II", header, 16, len(sounds), 512)
    body = bytearray()
    pos = 512
    items = sorted(sounds.items())
    for i, ((g, n), wav) in enumerate(items):
        nxt = pos + 16 + len(wav)
        body += struct.pack("<IIii", nxt if i < len(items) - 1 else 0, len(wav), g, n) + wav
        pos = nxt
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_bytes(bytes(header) + bytes(body))
    tmp.replace(path)


def wav_seconds(data: bytes) -> float:
    with wave.open(io.BytesIO(data)) as w:
        return w.getnframes() / w.getframerate()


# --------------------------------------------------------------------------- template analysis

def template_files(tpl: Path) -> tuple[list[Path], Path | None]:
    defs = [d for d in tpl.glob("*.def") if "[files]" in d.read_text(errors="replace").lower()]
    if len(defs) != 1:
        raise SystemExit(f"expected one character .def in {tpl}")
    text = defs[0].read_text(errors="replace")
    files, snd = [], None
    for m in re.finditer(r"^\s*(cmd|cns|st\d*|sound)\s*=\s*([^;\r\n]+)", text, re.M | re.I):
        p = tpl / m.group(2).strip()
        if not p.exists():
            continue
        if m.group(1).lower() == "sound":
            snd = p
        elif p not in files:
            files.append(p)
    return files, snd


def sound_refs(files: list[Path]) -> tuple[dict[tuple[int, int], set[int]], dict[int, str], list[str]]:
    """{(group, number): {statedefs}}, statedef labels, and refs that are expressions."""
    refs: dict[tuple[int, int], set[int]] = defaultdict(set)
    labels: dict[int, str] = {}
    dynamic: list[str] = []
    for f in files:
        lines = f.read_text(encoding="utf-8", errors="replace").splitlines()
        statedef, comment, block = 0, "", {}

        def flush(block, statedef):
            typ = block.get("type", "").lower()
            keys = ["value"] if typ == "playsnd" else ["hitsound", "guardsound"] if typ == "hitdef" else []
            for k in keys:
                if k not in block:
                    continue
                m = SND_REF.match(block[k])
                if not m:
                    dynamic.append(f"{f.name} state {statedef}: {k} = {block[k].strip()}")
                    continue
                prefix = (m.group(1) or "").upper()
                own = (prefix == "") if typ == "playsnd" else (prefix == "S")
                if own:
                    refs[(int(m.group(2)), int(m.group(3) or 0))].add(statedef)

        for line in lines:
            s = line.strip()
            if s.startswith(";"):
                text = s.lstrip(";").strip()
                if text and not set(text) <= set("-=_*") and not comment:
                    comment = text
                elif not text or set(text) <= set("-=_*"):
                    comment = ""
                continue
            if SECTION_RE.match(line):
                flush(block, statedef)
                block = {}
                m = STATEDEF_RE.match(line)
                if m:
                    statedef = int(m.group(1))
                    labels.setdefault(statedef, comment)
                comment = ""
                continue
            m = KV_RE.match(line)
            if m:
                block.setdefault(m.group(1).lower(), m.group(2).strip())
            if s:
                comment = ""
        flush(block, statedef)
    return refs, labels, dynamic


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")[:40] or "sound"


def group_labels(refs, labels, tpl_sounds) -> dict[int, str]:
    out = {}
    for g in sorted({g for g, _ in refs} | {g for g, _ in tpl_sounds}):
        states = sorted({s for (gg, _), ss in refs.items() if gg == g for s in ss})
        named = [labels.get(s, "") for s in states if labels.get(s) and s >= 0]
        if len(set(named)) > 3:                       # shared across many moves: attack grunts etc.
            out[g] = "voices" if g == 0 else "shared"
        elif named:
            out[g] = named[0]
        else:
            out[g] = "unused" if not states else "always"
    return out


# --------------------------------------------------------------------------- audio processing

def decode(path: Path, stereo: bool) -> np.ndarray:
    """Any audio file -> float32 samples (frames x channels) at RATE, via ffmpeg."""
    ch = 2 if stereo else 1
    res = subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-i", str(path), "-f", "s16le", "-acodec", "pcm_s16le",
                          "-ac", str(ch), "-ar", str(RATE), "-"], capture_output=True)
    if res.returncode != 0 or not res.stdout:
        raise RuntimeError(res.stderr.decode(errors="replace").strip() or "no audio decoded")
    return np.frombuffer(res.stdout, np.int16).reshape(-1, ch).astype(np.float32) / 32768.0


def process(x: np.ndarray, trim_db: float, target_rms_db: float | None) -> tuple[np.ndarray, dict]:
    info = {}
    mono = np.abs(x).max(axis=1)
    peak = float(mono.max()) if mono.size else 0.0
    if peak <= 1e-6:
        raise RuntimeError("file is silent")
    # trim leading/trailing silence relative to the peak, keeping 5 ms before and 40 ms after
    above = np.flatnonzero(mono > peak * 10 ** (trim_db / 20))
    start = max(0, above[0] - int(0.005 * RATE))
    end = min(len(x), above[-1] + int(0.040 * RATE))
    info["trimmed_ms"] = round((len(x) - (end - start)) / RATE * 1000)
    x = x[start:end].copy()
    if target_rms_db is not None:
        rms = float(np.sqrt(np.mean(x ** 2)))
        gain = 10 ** (target_rms_db / 20) / max(rms, 1e-9)
        gain = min(gain, 10 ** (-1 / 20) / float(np.abs(x).max()))   # never above -1 dBFS peak
        x *= gain
        info["gain_db"] = round(20 * np.log10(gain), 1)
    fade = min(int(0.005 * RATE), len(x) // 4)
    if fade:
        ramp = np.linspace(0.0, 1.0, fade, dtype=np.float32)[:, None]
        x[:fade] *= ramp
        x[-fade:] *= ramp[::-1]
    if len(x) < 64:                                     # Ikemen rejects sounds under 128 bytes
        x = np.vstack([x, np.zeros((64 - len(x), x.shape[1]), np.float32)])
    return x, info


def to_wav(x: np.ndarray) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(x.shape[1])
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes((np.clip(x, -1.0, 1.0) * 32767.0).round().astype("<i2").tobytes())
    return buf.getvalue()


# --------------------------------------------------------------------------- commands

def cmd_list(args) -> int:
    files, tpl_snd = template_files(args.template)
    refs, labels, dynamic = sound_refs(files)
    tpl_sounds = read_snd(tpl_snd) if tpl_snd else {}
    names = group_labels(refs, labels, tpl_sounds)
    char_dir = args.char_dir.resolve()
    keys = sorted(set(refs) | set(tpl_sounds))
    lines = [f"# Sound list: {char_dir.name}", "",
             f"Template: `{args.template}`. The moves play **{len(refs)} sounds**"
             + (f"; the template's .snd also has {len(set(tpl_sounds) - set(refs))} more (intro, win quotes...)."
                if set(tpl_sounds) - set(refs) else "."), "",
             "Save each as `sounds/<folder>/<number>.wav` (or mp3/ogg/flac/m4a). Anything you leave out keeps the",
             "template's sound until you record it. Length is the template's, a guide for timing, not a rule.", "",
             "| Group | Number | Folder | Used by | Template length |", "|---:|---:|---|---|---:|"]
    for g, n in keys:
        folder = f"{g}_{slug(names[g])}"
        states = sorted(refs.get((g, n), []))
        used = ", ".join(("every frame (state -3)" if s in (-3, -2) else f"{s} {labels.get(s, '')}".strip())
                         for s in states[:4]) + (f", +{len(states) - 4} more" if len(states) > 4 else "")
        length = f"{wav_seconds(tpl_sounds[(g, n)]):.2f}s" if (g, n) in tpl_sounds else "-"
        lines.append(f"| {g} | {n} | `{folder}/{n:04d}.wav` | {used or '(not played by the moves)'} | {length} |")
        if not args.no_folders:
            (char_dir / "sounds" / folder).mkdir(parents=True, exist_ok=True)
    missing = sorted(set(refs) - set(tpl_sounds))
    if missing:
        lines += ["", f"Played by the moves but missing from the template's .snd (silent in the template too): {missing}"]
    if dynamic:
        lines += ["", "Computed sound values (check by hand):"] + [f"- {d}" for d in dynamic]
    char_dir.mkdir(parents=True, exist_ok=True)
    (char_dir / "SOUNDLIST.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {char_dir / 'SOUNDLIST.md'}: {len(refs)} sounds played by the moves, {len(keys)} total")
    return 0


def cmd_build(args) -> int:
    if not shutil.which("ffmpeg"):
        print("error: ffmpeg not found (needed to convert recordings)", file=sys.stderr)
        return 2
    files, tpl_snd = template_files(args.template)
    refs, _, _ = sound_refs(files)
    tpl_sounds = read_snd(tpl_snd) if tpl_snd else {}
    char_dir = args.char_dir.resolve()
    src = char_dir / "sounds"
    sounds: dict[tuple[int, int], bytes] = {}
    report, failed = [], 0
    for folder in sorted(src.glob("*")) if src.is_dir() else []:
        m = re.match(r"^(\d+)(?:[_\-\s].*)?$", folder.name)
        if not folder.is_dir() or not m:
            continue
        g = int(m.group(1))
        for f in sorted(folder.iterdir()):
            nm = re.findall(r"\d+", f.stem)
            if f.suffix.lower() not in AUDIO_EXTS or not nm:
                continue
            key = (g, int(nm[-1]))
            if key in sounds:
                print(f"  ! {folder.name}/{f.name}: sound {key} already set, skipped")
                continue
            try:
                x, info = process(decode(f, args.stereo), args.trim_db, None if args.no_normalize else args.target_rms)
            except RuntimeError as e:
                print(f"  ! {folder.name}/{f.name}: {e}")
                failed += 1
                continue
            sounds[key] = to_wav(x)
            report.append((key, f"{folder.name}/{f.name}", len(x) / RATE, info))
    fallback = []
    if not args.no_fallback:
        for key, data in tpl_sounds.items():
            if key not in sounds:
                sounds[key] = data
                fallback.append(key)
    if not sounds:
        print("error: no sounds (record some, or allow the template fallback)", file=sys.stderr)
        return 2

    out_dir = args.char_out.resolve()
    name = char_dir.name
    snd_path = out_dir / f"{name}.snd"
    write_snd(snd_path, sounds)
    # point the character's .def at the new .snd
    def_path = out_dir / f"{name}.def"
    if def_path.exists():
        text = def_path.read_text(encoding="utf-8", errors="replace")
        new, n = re.subn(r"(?im)^(\s*sound\s*=\s*)[^;\r\n]*?(\s*(;.*)?)$", lambda mm: f"{mm.group(1)}{name}.snd{mm.group(2)}",
                         text, count=1)
        if n:
            def_path.write_text(new, encoding="utf-8")
    for key, rel, secs, info in report:
        extra = f", {info['gain_db']:+.1f} dB" if "gain_db" in info else ""
        print(f"  {key[0]:>4},{key[1]:<3} {rel:44s} {secs:5.2f}s (trimmed {info['trimmed_ms']} ms{extra})")
    unplayed = sorted(k for k, *_ in report if k not in refs)
    print(f"wrote {snd_path}: {len(report)} recorded, {len(fallback)} from the template"
          + (f" {sorted(fallback)}" if fallback and len(fallback) <= 12 else "")
          + (f", {failed} failed" if failed else "")
          + (f"; not played by any move (fine for intros/wins): {unplayed}" if unplayed else ""))
    silent = sorted(set(refs) - set(sounds))
    if silent:
        print(f"  ! played by the moves but missing (silent): {silent}")
    return 1 if failed else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("list", help="write SOUNDLIST.md and the sounds/ folders")
    a.add_argument("char_dir", type=Path)
    a.add_argument("--template", type=Path, default=Path("templates/fighter_base"))
    a.add_argument("--no-folders", action="store_true")
    b = sub.add_parser("build", help="convert recordings and write <char>.snd")
    b.add_argument("char_dir", type=Path)
    b.add_argument("--template", type=Path, default=Path("templates/fighter_base"))
    b.add_argument("--char-out", type=Path, required=True, help="engine/chars/<char> (the built character)")
    b.add_argument("--stereo", action="store_true", help="keep stereo (default mono: pans cleanly in game)")
    b.add_argument("--target-rms", type=float, default=-16.0, help="loudness target, dBFS RMS (default -16)")
    b.add_argument("--no-normalize", action="store_true")
    b.add_argument("--trim-db", type=float, default=-45.0, help="silence threshold relative to the peak")
    b.add_argument("--no-fallback", action="store_true", help="don't fill unrecorded sounds from the template")
    args = ap.parse_args()
    return cmd_list(args) if args.cmd == "list" else cmd_build(args)


if __name__ == "__main__":
    sys.exit(main())
