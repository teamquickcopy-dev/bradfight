#!/usr/bin/env python3
"""
setup_engine.py - fetch the parts that aren't in the repo and rebuild what is generated from them.

    python tools/setup_engine.py                 # platform detected automatically
    python tools/setup_engine.py --platform windows

1. Downloads Ikemen GO (default v1.0.0) for windows / macos / linux into engine/.
2. Copies Ikemen's bundled kfm720 character into templates/fighter_base/ (the balanced template).
3. Builds the corner_store stage, installs the CRT shader, builds your branding (brand/brand.json),
   and builds the donation mod if
   mods/donate.json has a url.

The engine and the Kung Fu Man template are third-party (Ikemen GO: MIT; KFM: Elecbyte), so the repo
leaves them out and this script brings them in. Existing folders are kept unless --force.
"""
from __future__ import annotations

import argparse
import json
import platform
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
URL = "https://github.com/ikemen-engine/Ikemen-GO/releases/download/{v}/Ikemen_GO-{v}-{p}.zip"


def detect() -> str:
    s = platform.system().lower()
    return {"windows": "windows", "darwin": "macos"}.get(s, "linux")


def find_root(folder: Path) -> Path:
    """The extracted folder that holds chars/, data/ and external/ (zips differ per platform)."""
    for d in [folder, *sorted(p for p in folder.rglob("*") if p.is_dir())]:
        if all((d / n).is_dir() for n in ("chars", "data", "external")):
            return d
    raise SystemExit("downloaded zip has no chars/data/external folders; has the release layout changed?")


def run(*cmd: str) -> None:
    print("$", " ".join(cmd))
    subprocess.run([sys.executable, *cmd], cwd=ROOT, check=True)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--platform", choices=("windows", "macos", "linux"), default=detect())
    ap.add_argument("--version", default="v1.0.0")
    ap.add_argument("--force", action="store_true", help="re-download the engine and re-copy the template")
    args = ap.parse_args()

    engine = ROOT / "engine"
    if engine.exists() and any(p.name != ".gitkeep" for p in engine.iterdir()) and not args.force:
        print(f"engine/ already set up (use --force to re-download)")
    else:
        url = URL.format(v=args.version, p=args.platform)
        with tempfile.TemporaryDirectory() as tmp:
            zpath = Path(tmp) / "ikemen.zip"
            print(f"downloading {url}")
            with urllib.request.urlopen(url, timeout=300) as r, zpath.open("wb") as fh:
                shutil.copyfileobj(r, fh)
            out = Path(tmp) / "x"
            with zipfile.ZipFile(zpath) as z:
                for info in z.infolist():                      # refuse paths that escape the folder
                    target = (out / info.filename).resolve()
                    if not str(target).startswith(str(out.resolve())):
                        raise SystemExit(f"unsafe path in zip: {info.filename}")
                z.extractall(out)
                modes = {i.filename: i.external_attr >> 16 for i in z.infolist()}
            src = find_root(out)
            if engine.exists() and args.force:
                shutil.rmtree(engine)
            shutil.copytree(src, engine, dirs_exist_ok=True)
            for name, mode in modes.items():                   # zipfile drops the executable bit
                p = out / name
                if mode & 0o111 and p.is_file() and str(p).startswith(str(src)):
                    (engine / p.relative_to(src)).chmod(mode & 0o777)
        print(f"engine ready: {engine}")

    tpl = ROOT / "templates" / "fighter_base"
    tpl.mkdir(parents=True, exist_ok=True)
    if any(p.suffix == ".def" for p in tpl.iterdir()) and not args.force:
        print("templates/fighter_base already has a template")
    else:
        kfm = engine / "chars" / "kfm720"
        if not kfm.is_dir():
            raise SystemExit(f"{kfm} not found in this Ikemen release")
        for f in kfm.iterdir():
            if f.is_file():
                shutil.copy2(f, tpl / f.name)
        print("template: kfm720 copied to templates/fighter_base/")

    # rebuild generated engine content
    select = engine / "data" / "select.def"
    stage_cfg = ROOT / "stages" / "src" / "corner_store" / "stage.json"
    if stage_cfg.exists():
        extra = ["--select", str(select)] if select.exists() else []
        run("tools/build_stage.py", str(stage_cfg), *extra)
    run("tools/install_crt.py", "crt")
    if (ROOT / "brand" / "brand.json").exists():
        run("tools/build_brand.py", "brand/brand.json")
    donate = ROOT / "mods" / "donate.json"
    if donate.exists() and json.loads(donate.read_text()).get("url"):
        run("tools/build_donate_mod.py", str(donate))
    else:
        print("donation button: set \"url\" in mods/donate.json, then run tools/build_donate_mod.py")

    print("\nnext: make a character (see README), or try the greybox test fighter:\n"
          "  python tools/make_shotlist.py characters/fighter01 --air templates/fighter_base/kfm720.air\n"
          "  python tools/make_greybox.py characters/fighter01 --template-sff templates/fighter_base/kfm720.sff\n"
          "  NUMBER_FROM=filename ./build_character.sh fighter01 --source-root 512 600 --skip-rembg")
    return 0


if __name__ == "__main__":
    sys.exit(main())
