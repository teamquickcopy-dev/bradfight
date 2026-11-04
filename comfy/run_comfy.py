#!/usr/bin/env python3
"""
run_comfy.py - generate every sprite frame in ComfyUI from the Blender control passes.

For each frame in <passes>/openpose/<group>_<label>/<image>.png it uploads that frame's openpose,
depth (and clay) images to a running ComfyUI, queues comfy/workflows/fighter_frame_api.json with
your settings, and saves the result as characters/<char>/01_raw/<group>_<label>/<image>.png:
exactly the name and resolution the build expects. Standard library only.

    python comfy/run_comfy.py --passes comfy/pose_maps/kid_k --out characters/kid_k/01_raw \
        --settings comfy/kid_k.settings.json [--shots characters/kid_k/shots.json] [--only 0,200]

Settings (copy comfy/workflows/fighter_settings.example.json):
  checkpoint, lora {name, strength}   lora name "" = no LoRA
  prompt / negative                   {label} and {group} are filled in per animation (labels from --shots)
  group_prompts {"5000": "..."}       extra text for one group
  openpose / depth {name, strength, end}   depth name "" or strength 0 = openpose only
  mode "txt2img" | "img2img"          img2img starts from the clay render (denoise): silhouettes stay
                                      closer to the rig, so alignment and QA have less to fix
  seed, steps, cfg, sampler, scheduler   one fixed seed for every frame keeps the character consistent

Re-running skips frames that already exist (--force to redo), so an interrupted batch resumes.
"""
from __future__ import annotations

import argparse
import copy
import json
import re
import struct
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_WORKFLOW = HERE / "workflows" / "fighter_frame_api.json"
SAVE_NODE = "16"
_TMP = Path(tempfile.mkdtemp(prefix="run_comfy_"))


class ComfyError(RuntimeError):
    pass


class Comfy:
    def __init__(self, server: str):
        self.base = server.rstrip("/")
        self.client_id = uuid.uuid4().hex

    def _req(self, method: str, path: str, data: bytes | None = None, headers: dict | None = None,
             timeout: float = 60) -> bytes:
        req = urllib.request.Request(self.base + path, data=data, method=method, headers=headers or {})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")
            raise ComfyError(f"{method} {path}: HTTP {e.code}: {body[:2000]}") from None
        except urllib.error.URLError as e:
            raise ComfyError(f"cannot reach ComfyUI at {self.base} ({e.reason}). Is it running?") from None

    def get_json(self, path: str):
        return json.loads(self._req("GET", path))

    def options(self, node: str, field: str) -> list[str]:
        info = self.get_json(f"/object_info/{node}")[node]["input"]["required"][field]
        opts = info[0] if isinstance(info[0], list) else info[1].get("options", [])
        return list(opts)

    def upload(self, path: Path, subfolder: str) -> str:
        boundary = uuid.uuid4().hex
        parts = []
        for name, value in (("subfolder", subfolder), ("type", "input"), ("overwrite", "true")):
            parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode())
        parts.append((f'--{boundary}\r\nContent-Disposition: form-data; name="image"; filename="{path.name}"\r\n'
                      f"Content-Type: image/png\r\n\r\n").encode() + path.read_bytes() + b"\r\n")
        parts.append(f"--{boundary}--\r\n".encode())
        res = json.loads(self._req("POST", "/upload/image", b"".join(parts),
                                   {"Content-Type": f"multipart/form-data; boundary={boundary}"}))
        return f"{res['subfolder']}/{res['name']}" if res.get("subfolder") else res["name"]

    def queue(self, graph: dict) -> str:
        body = json.dumps({"prompt": graph, "client_id": self.client_id}).encode()
        res = json.loads(self._req("POST", "/prompt", body, {"Content-Type": "application/json"}))
        if res.get("node_errors"):
            raise ComfyError(f"workflow rejected: {json.dumps(res['node_errors'])[:2000]}")
        return res["prompt_id"]

    def result(self, prompt_id: str) -> dict | None:
        """Finished history entry, None while still queued/running."""
        hist = self.get_json(f"/history/{prompt_id}")
        entry = hist.get(prompt_id)
        if not entry:
            return None
        status = entry.get("status", {})
        if status.get("status_str") == "error":
            msgs = [m[1] for m in status.get("messages", []) if m[0] == "execution_error"]
            detail = msgs[0] if msgs else status
            if isinstance(detail, dict):
                detail = f"node {detail.get('node_id')} ({detail.get('node_type')}): {detail.get('exception_message', '').strip()}"
            raise ComfyError(f"generation failed: {detail}")
        if not status.get("completed", True) and not entry.get("outputs"):
            return None
        return entry

    def download(self, image: dict) -> bytes:
        q = urllib.parse.urlencode({"filename": image["filename"], "subfolder": image.get("subfolder", ""),
                                    "type": image.get("type", "output")})
        return self._req("GET", f"/view?{q}", timeout=120)


def png_size(data: bytes) -> tuple[int, int]:
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ComfyError("ComfyUI returned something that is not a PNG")
    return struct.unpack(">II", data[16:24])


def build_graph(template: dict, s: dict, width: int, height: int) -> dict:
    """Apply settings once; per-frame values (images, prompt) are filled in later."""
    g = copy.deepcopy(template)
    g["1"]["inputs"]["ckpt_name"] = s["checkpoint"]
    lora = s.get("lora") or {}
    if lora.get("name"):
        g["2"]["inputs"].update(lora_name=lora["name"], strength_model=lora.get("strength", 0.9),
                                strength_clip=lora.get("strength", 0.9))
    else:                                            # no LoRA: wire the checkpoint straight through
        del g["2"]
        g["3"]["inputs"]["clip"] = g["4"]["inputs"]["clip"] = ["1", 1]
        g["14"]["inputs"]["model"] = ["1", 0]
    op = s["openpose"]
    g["8"]["inputs"]["control_net_name"] = op["name"]
    g["10"]["inputs"].update(strength=op.get("strength", 1.0), end_percent=op.get("end", 1.0))
    dp = s.get("depth") or {}
    if dp.get("name") and dp.get("strength", 0) > 0:
        g["9"]["inputs"]["control_net_name"] = dp["name"]
        g["11"]["inputs"].update(strength=dp["strength"], end_percent=dp.get("end", 0.8))
    else:
        for n in ("6", "9", "11"):
            del g[n]
        g["14"]["inputs"]["positive"], g["14"]["inputs"]["negative"] = ["10", 0], ["10", 1]
    if s.get("mode", "txt2img") == "img2img":
        del g["12"]
        g["14"]["inputs"]["latent_image"] = ["13", 0]
        g["14"]["inputs"]["denoise"] = s.get("denoise", 0.85)
    else:
        del g["7"], g["13"]
        g["12"]["inputs"].update(width=width, height=height)
        g["14"]["inputs"]["denoise"] = 1.0
    g["14"]["inputs"].update(seed=s.get("seed", 1234), steps=s.get("steps", 28), cfg=s.get("cfg", 6.0),
                             sampler_name=s.get("sampler", "dpmpp_2m"), scheduler=s.get("scheduler", "karras"))
    g["4"]["inputs"]["text"] = s.get("negative", "")
    return g


def check_models(comfy: Comfy, s: dict) -> None:
    wanted = [("CheckpointLoaderSimple", "ckpt_name", s["checkpoint"]),
              ("ControlNetLoader", "control_net_name", s["openpose"]["name"])]
    if (s.get("depth") or {}).get("name") and s["depth"].get("strength", 0) > 0:
        wanted.append(("ControlNetLoader", "control_net_name", s["depth"]["name"]))
    if (s.get("lora") or {}).get("name"):
        wanted.append(("LoraLoader", "lora_name", s["lora"]["name"]))
    for node, field, name in wanted:
        opts = comfy.options(node, field)
        if name not in opts:
            raise ComfyError(f"'{name}' is not installed in ComfyUI ({node}). Available: {opts[:12]}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--passes", type=Path, required=True, help="render_passes.py output folder")
    ap.add_argument("--out", type=Path, required=True, help="characters/<char>/01_raw")
    ap.add_argument("--settings", type=Path, required=True)
    ap.add_argument("--shots", type=Path, help="shots.json, for {label} in the prompt")
    ap.add_argument("--workflow", type=Path, default=DEFAULT_WORKFLOW)
    ap.add_argument("--server", default="http://127.0.0.1:8188")
    ap.add_argument("--only", help="comma list of group numbers")
    ap.add_argument("--limit", type=int, help="stop after this many frames (try settings on a few first)")
    ap.add_argument("--inflight", type=int, default=2, help="prompts kept queued ahead")
    ap.add_argument("--timeout", type=float, default=900, help="seconds per frame before giving up")
    ap.add_argument("--force", action="store_true", help="regenerate frames that already exist")
    args = ap.parse_args()

    s = json.loads(args.settings.read_text())
    template = json.loads(args.workflow.read_text())
    facts_path = args.passes / "passes.json"
    width, height = json.loads(facts_path.read_text())["resolution"] if facts_path.exists() else (1024, 1024)
    labels = {}
    if args.shots:
        labels = {int(k): v.get("label", "") for k, v in json.loads(args.shots.read_text())["groups"].items()}
    only = {int(g) for g in args.only.split(",")} if args.only else None
    mode = s.get("mode", "txt2img")
    use_depth = bool((s.get("depth") or {}).get("name")) and s["depth"].get("strength", 0) > 0

    pose_dir = args.passes / "openpose"
    if not pose_dir.is_dir():
        print(f"error: {pose_dir} missing. Run: python tools/draw_openpose.py {args.passes / 'openpose_json'} {pose_dir}",
              file=sys.stderr)
        return 2
    frames = []
    for p in sorted(pose_dir.glob("*/*.png")):
        group = int(p.parent.name.split("_", 1)[0])
        if only and group not in only:
            continue
        dst = args.out / p.parent.name / p.name
        if dst.exists() and not args.force:
            continue
        frames.append((group, p.parent.name, p.name, dst))
    if args.limit:
        frames = frames[:args.limit]
    if not frames:
        print("nothing to do (all frames exist; --force to regenerate)")
        return 0

    comfy = Comfy(args.server)
    try:
        check_models(comfy, s)
    except ComfyError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    base = build_graph(template, s, width, height)
    print(f"{len(frames)} frames to generate ({mode}, depth {'on' if use_depth else 'off'}, seed {s.get('seed', 1234)})")

    def submit(group: int, folder: str, name: str) -> str:
        g = copy.deepcopy(base)
        sub = f"fighter/{args.passes.name}/{folder}"
        stem = Path(name).stem
        g["5"]["inputs"]["image"] = comfy.upload(_copy_named(args.passes / "openpose" / folder / name, f"{stem}_pose.png"), sub)
        if use_depth:
            g["6"]["inputs"]["image"] = comfy.upload(_copy_named(args.passes / "depth" / folder / name, f"{stem}_depth.png"), sub)
        if mode == "img2img":
            g["7"]["inputs"]["image"] = comfy.upload(_copy_named(args.passes / "clay" / folder / name, f"{stem}_clay.png"), sub)
        text = s["prompt"].replace("{label}", labels.get(group, "")).replace("{group}", str(group))
        extra = (s.get("group_prompts") or {}).get(str(group))
        text = f"{text}, {extra}" if extra else text
        g["3"]["inputs"]["text"] = re.sub(r"\s*(,\s*)+", ", ", text).strip(" ,")   # drop empty {label} slots
        g[SAVE_NODE]["inputs"]["filename_prefix"] = f"fighter/{args.passes.name}/{folder}/{stem}"
        return comfy.queue(g)

    pending: list[tuple[str, tuple, float]] = []
    done = failed = 0
    t0 = time.time()
    todo = list(frames)
    try:
        while todo or pending:
            while todo and len(pending) < max(1, args.inflight):
                fr = todo.pop(0)
                try:
                    pending.append((submit(*fr[:3]), fr, time.time()))
                except ComfyError as e:
                    print(f"error: {fr[1]}/{fr[2]}: {e}", file=sys.stderr)
                    return 1
            pid, fr, started = pending[0]
            try:
                entry = comfy.result(pid)
            except ComfyError as e:
                print(f"  ! {fr[1]}/{fr[2]}: {e}", file=sys.stderr)
                failed += 1
                pending.pop(0)
                if failed >= 3 and done == 0:
                    print("error: first frames all failed; fix the settings and re-run", file=sys.stderr)
                    return 1
                continue
            if entry is None:
                if time.time() - started > args.timeout:
                    print(f"  ! {fr[1]}/{fr[2]}: timed out", file=sys.stderr)
                    failed += 1
                    pending.pop(0)
                time.sleep(0.5)
                continue
            pending.pop(0)
            images = entry.get("outputs", {}).get(SAVE_NODE, {}).get("images", [])
            if not images:
                print(f"  ! {fr[1]}/{fr[2]}: no image in the result", file=sys.stderr)
                failed += 1
                continue
            data = comfy.download(images[0])
            w, h = png_size(data)
            if (w, h) != (width, height):
                print(f"  ! {fr[1]}/{fr[2]}: got {w}x{h}, passes are {width}x{height}; anchors won't line up",
                      file=sys.stderr)
            fr[3].parent.mkdir(parents=True, exist_ok=True)
            tmp = fr[3].with_suffix(".tmp")
            tmp.write_bytes(data)
            tmp.replace(fr[3])
            done += 1
            rate = (time.time() - t0) / done
            print(f"  [{done}/{len(frames)}] {fr[1]}/{fr[2]}  ({rate:.1f}s/frame, ~{rate * (len(frames) - done) / 60:.0f} min left)",
                  flush=True)
    except KeyboardInterrupt:
        print("\ninterrupted; re-run the same command to resume")
        return 130
    print(f"done: {done} generated, {failed} failed -> {args.out}")
    return 1 if failed else 0


def _copy_named(src: Path, name: str) -> Path:
    """Uploads are keyed by filename; give each pass a distinct name inside the frame's subfolder."""
    if not src.exists():
        raise ComfyError(f"missing pass image {src}")
    dst = _TMP / name
    dst.write_bytes(src.read_bytes())
    return dst


if __name__ == "__main__":
    sys.exit(main())
