"""
render_passes.py - render every sprite the template needs from ONE locked side-view camera.

Run with Blender 4.2+ (tested on 5.0.1):

    blender -b rigs/fighter.blend -P rigs/render_passes.py -- \
        --config characters/kid_k/shots.json --out comfy/pose_maps/kid_k [--gpu]

or with the bpy module:  python rigs/render_passes.py --blend rigs/fighter.blend --config ... --out ...

Outputs (same <group>_<label>/<image>.png layout as characters/<char>/01_raw/):

    <out>/openpose_json/...json   OpenPose BODY-18 keypoints, image pixels (draw: tools/draw_openpose.py)
    <out>/depth/...png            near = white, one fixed depth range for every frame (ControlNet depth)
    <out>/clay/...png             grey shaded render on white (run ComfyUI's lineart/canny preprocessor on it)
    <out>/mask/...png             RGBA silhouette -> align_sprites.py --reference <out>/mask
    <out>/anchors.json            root pixel per frame -> align_sprites.py --anchors <out>/anchors.json
    <out>/passes.json             camera + scale facts (source_root, px_per_m, ...)

Camera: orthographic, so the character's size never changes with depth. It is placed so the
armature origin (the floor point under the character) lands on `root_pixel`, and so that a
standing character is `target_height_px` tall: 420 px matches Ikemen's kfm720 template.
The character faces screen-right, as MUGEN expects; set "facing" to the world direction your
rig faces (Mixamo imports face -Y).

shots.json (written by tools/make_shotlist.py; fill in "action" per group):

    {
      "facing": "-Y", "resolution": [1024, 1024], "root_pixel": [512, 600], "target_height_px": 420,
      "groups": {
        "0":    {"folder": "0_standing_animation", "images": [0,1,2,3,4,5], "action": "Idle",
                 "frames": "auto", "loop": true},
        "5000": {"folder": "5000_stand_air_hit_high_light", "images": [0,10,11,20,21], "action": "HitLight",
                 "per_image": {"10": ["HitMedium", 4], "20": ["HitHeavy", 3]}}
      }
    }

  frames     "auto" = evenly spaced over the action (loop: true leaves out the repeated last frame),
             or an explicit list of frame numbers, one per image.
  anchor     "origin" (default): fixed root, motion in the action is kept in the sprite (jumps,
             knockdowns). "root": the anchor follows the hips over the floor, i.e. root motion is
             removed (use for non-"in place" Mixamo walks; the template's .cns moves the character).

The .blend file is never saved.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time

import bpy
from bpy_extras.object_utils import world_to_camera_view
from mathutils import Matrix, Vector

AXES = {"+X": (1, 0, 0), "-X": (-1, 0, 0), "+Y": (0, 1, 0), "-Y": (0, -1, 0)}
PASSES = ("openpose", "depth", "clay", "mask")

# OpenPose BODY-18 order -> Mixamo bone (point = bone head). Face points are derived from the head.
BODY = {
    2: "RightArm", 3: "RightForeArm", 4: "RightHand",
    5: "LeftArm", 6: "LeftForeArm", 7: "LeftHand",
    8: "RightUpLeg", 9: "RightLeg", 10: "RightFoot",
    11: "LeftUpLeg", 12: "LeftLeg", 13: "LeftFoot",
}


# --------------------------------------------------------------------------- setup helpers

def parse_args() -> argparse.Namespace:
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else sys.argv[1:]
    ap = argparse.ArgumentParser(prog="render_passes.py")
    ap.add_argument("--config", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--blend", help="open this .blend first (when run as a bpy module)")
    ap.add_argument("--passes", default=",".join(PASSES), help=f"comma list from {','.join(PASSES)}")
    ap.add_argument("--only", help="comma list of group numbers to render")
    ap.add_argument("--gpu", action="store_true", help="Cycles on GPU (CUDA/OptiX/HIP/Metal, first found)")
    ap.add_argument("--samples", type=int, default=16, help="Cycles samples for the clay pass")
    return ap.parse_args(argv)


def find_armature(name: str | None) -> bpy.types.Object:
    if name:
        obj = bpy.data.objects.get(name)
        if not obj or obj.type != "ARMATURE":
            raise SystemExit(f"armature '{name}' not found")
        return obj
    arms = [o for o in bpy.context.scene.objects if o.type == "ARMATURE"]
    if len(arms) != 1:
        raise SystemExit(f"found {len(arms)} armatures; set \"armature\" in the config")
    return arms[0]


def character_meshes(arm: bpy.types.Object) -> list[bpy.types.Object]:
    out = []
    for o in bpy.context.scene.objects:
        if o.type != "MESH":
            continue
        deformed = any(m.type == "ARMATURE" and m.object == arm for m in o.modifiers)
        parent = o.parent
        while parent and parent != arm:
            parent = parent.parent
        if deformed or parent == arm:
            out.append(o)
    if not out:
        raise SystemExit("no meshes are deformed by / parented to the armature")
    return out


def bone_lookup(arm: bpy.types.Object):
    names = {b.name: b for b in arm.pose.bones}

    def get(short: str):
        if short in names:
            return names[short]
        for n, b in names.items():
            if n.endswith(":" + short) or n.endswith("_" + short):
                return b
        return None
    return get


def set_action(arm: bpy.types.Object, action_name: str) -> bpy.types.Action:
    act = bpy.data.actions.get(action_name)
    if act is None:
        raise SystemExit(f"action '{action_name}' not found; available: {sorted(a.name for a in bpy.data.actions)}")
    # Rest pose first: channels an action doesn't key would otherwise keep the previous action's values.
    for pb in arm.pose.bones:
        pb.location = (0.0, 0.0, 0.0)
        pb.rotation_quaternion = (1.0, 0.0, 0.0, 0.0)
        pb.rotation_euler = (0.0, 0.0, 0.0)
        pb.rotation_axis_angle = (0.0, 0.0, 1.0, 0.0)
        pb.scale = (1.0, 1.0, 1.0)
    ad = arm.animation_data or arm.animation_data_create()
    ad.use_nla = False
    ad.action = act
    if hasattr(ad, "action_slot") and ad.action_slot is None and getattr(act, "slots", None):
        ad.action_slot = act.slots[0]                # Blender 4.4+ slotted actions
    return act


def frame_plan(cfg: dict, only: set[int] | None):
    """[(group, folder, image, action, frame, anchor_mode)]"""
    plan, skipped = [], []
    for g_str, gcfg in sorted(cfg["groups"].items(), key=lambda kv: int(kv[0])):
        g = int(g_str)
        if only and g not in only:
            continue
        images = [int(i) for i in gcfg["images"]]
        per = {int(k): v for k, v in gcfg.get("per_image", {}).items()}
        action = gcfg.get("action") or ""
        missing = [i for i in images if i not in per and not action]
        if missing:
            skipped.append(g)
            images = [i for i in images if i in per]
            if not images:
                continue
        frames = gcfg.get("frames", "auto")
        default_imgs = [i for i in images if i not in per]
        if default_imgs:
            act = bpy.data.actions.get(action)
            if act is None:
                raise SystemExit(f"group {g}: action '{action}' not found")
            start, end = (int(round(v)) for v in act.frame_range)
            if frames == "auto":
                n, span = len(default_imgs), end - start
                # loop: the last frame repeats the first, so space over [start, end)
                step = span / n if gcfg.get("loop") else (span / (n - 1) if n > 1 else 0)
                auto = [int(round(start + k * step)) for k in range(n)]
                fmap = dict(zip(default_imgs, auto))
            else:
                if len(frames) != len(default_imgs):
                    raise SystemExit(f"group {g}: {len(frames)} frames for {len(default_imgs)} images")
                fmap = dict(zip(default_imgs, (int(f) for f in frames)))
        for i in images:
            a, f = per[i] if i in per else (action, fmap[i])
            plan.append((g, gcfg["folder"], i, a, int(f), gcfg.get("anchor", "origin")))
    return plan, skipped


def evaluated_bounds(meshes, depsgraph):
    lo = Vector((math.inf,) * 3)
    hi = Vector((-math.inf,) * 3)
    for o in meshes:
        ev = o.evaluated_get(depsgraph)
        me = ev.to_mesh()
        mw = ev.matrix_world
        for v in me.vertices:
            p = mw @ v.co
            lo = Vector(map(min, lo, p))
            hi = Vector(map(max, hi, p))
        ev.to_mesh_clear()
    return lo, hi


def emission_material(name: str, color_socket_builder):
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    nt = mat.node_tree
    nt.nodes.clear()
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    em = nt.nodes.new("ShaderNodeEmission")
    em.inputs["Strength"].default_value = 1.0
    color_socket_builder(nt, em.inputs["Color"])
    nt.links.new(em.outputs["Emission"], out.inputs["Surface"])
    return mat


def depth_material(near: float, far: float):
    def build(nt, color_in):
        cam = nt.nodes.new("ShaderNodeCameraData")
        mr = nt.nodes.new("ShaderNodeMapRange")
        mr.clamp = True
        mr.inputs["From Min"].default_value = near
        mr.inputs["From Max"].default_value = far
        mr.inputs["To Min"].default_value = 1.0      # near = white (ControlNet depth convention)
        mr.inputs["To Max"].default_value = 0.0
        nt.links.new(cam.outputs["View Z Depth"], mr.inputs["Value"])
        nt.links.new(mr.outputs["Result"], color_in)
    return emission_material("_pass_depth", build)


def flat_material(name: str, rgb):
    def build(nt, color_in):
        color_in.default_value = (*rgb, 1.0)
    return emission_material(name, build)


def clay_material():
    mat = bpy.data.materials.new("_pass_clay")
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes.get("Principled BSDF")
    bsdf.inputs["Base Color"].default_value = (0.6, 0.6, 0.6, 1.0)
    bsdf.inputs["Roughness"].default_value = 0.8
    return mat


def set_view_transform(scene, preferred: str) -> None:
    items = scene.view_settings.bl_rna.properties["view_transform"].enum_items.keys()
    scene.view_settings.view_transform = preferred if preferred in items else "Standard"
    scene.view_settings.look = "None"
    scene.view_settings.exposure = 0.0
    scene.view_settings.gamma = 1.0


# --------------------------------------------------------------------------- main

def main() -> None:
    args = parse_args()
    if args.blend:
        bpy.ops.wm.open_mainfile(filepath=os.path.abspath(args.blend))
    with open(args.config, encoding="utf-8") as fh:
        cfg = json.load(fh)
    passes = [p.strip() for p in args.passes.split(",") if p.strip()]
    bad = set(passes) - set(PASSES)
    if bad:
        raise SystemExit(f"unknown passes: {bad}")
    only = {int(g) for g in args.only.split(",")} if args.only else None

    scene = bpy.context.scene
    arm = find_armature(cfg.get("armature"))
    meshes = character_meshes(arm)
    bone = bone_lookup(arm)
    W, H = cfg.get("resolution", [1024, 1024])
    root_px = Vector(cfg.get("root_pixel", [512, 600]))
    facing = Vector(AXES[cfg.get("facing", "-Y")])
    up = Vector((0, 0, 1))
    forward = up.cross(facing).normalized()           # camera view direction: facing == screen right

    plan, skipped = frame_plan(cfg, only)
    if skipped:
        print(f"[render_passes] groups without an action, skipped: {skipped}")
    if not plan:
        raise SystemExit("nothing to render: set \"action\" for at least one group in the config")

    # ---- measure the standing height once (first frame of the first planned group, or "height_from")
    hf = cfg.get("height_from") or {"action": plan[0][3], "frame": plan[0][4]}
    set_action(arm, hf["action"])
    scene.frame_set(int(hf["frame"]))
    dg = bpy.context.evaluated_depsgraph_get()
    lo, hi = evaluated_bounds(meshes, dg)
    floor_pt = arm.matrix_world.translation.copy()
    height_m = hi.z - floor_pt.z
    if height_m <= 0:
        raise SystemExit("character height <= 0: is the armature origin on the floor?")
    target_px = float(cfg.get("target_height_px", 420))
    m_per_px = float(cfg["ortho_scale"]) / max(W, H) if "ortho_scale" in cfg else height_m / target_px
    ortho_scale = m_per_px * max(W, H)

    # ---- locked orthographic camera: floor_pt projects exactly onto root_pixel
    right = facing
    center = floor_pt + right * ((W / 2 - root_px.x) * m_per_px) + up * ((root_px.y - H / 2) * m_per_px)
    cam_data = bpy.data.cameras.new("_pass_cam")
    cam_data.type = "ORTHO"
    cam_data.ortho_scale = ortho_scale
    cam_data.clip_start, cam_data.clip_end = 0.01, 200.0
    cam = bpy.data.objects.new("_pass_cam", cam_data)
    scene.collection.objects.link(cam)
    dist = 50.0
    cam.matrix_world = Matrix.Translation(center - forward * dist) @ forward.to_track_quat("-Z", "Y").to_matrix().to_4x4()
    scene.camera = cam

    # fixed depth range for every frame: the standing pose's depth extent plus a margin for limbs
    corners = [Vector((x, y, z)) for x in (lo.x, hi.x) for y in (lo.y, hi.y) for z in (lo.z, hi.z)]
    depths = [dist + (c - center).dot(forward) for c in corners]
    margin = float(cfg.get("depth_margin_m", 0.2 * height_m))
    near, far = min(depths) - margin, max(depths) + margin

    r = scene.render
    r.engine = "CYCLES"
    r.resolution_x, r.resolution_y, r.resolution_percentage = W, H, 100
    r.pixel_aspect_x = r.pixel_aspect_y = 1.0
    r.image_settings.file_format = "PNG"
    r.image_settings.color_depth = "8"
    r.use_compositing = False
    r.use_sequencer = False
    cy = scene.cycles
    cy.device = "CPU"
    if args.gpu:
        prefs = bpy.context.preferences.addons["cycles"].preferences
        for backend in ("OPTIX", "CUDA", "HIP", "METAL", "ONEAPI"):
            try:
                prefs.compute_device_type = backend
                prefs.get_devices()
                if any(d.type == backend for d in prefs.devices):
                    for d in prefs.devices:
                        d.use = d.type == backend
                    cy.device = "GPU"
                    print(f"[render_passes] GPU: {backend}")
                    break
            except TypeError:
                continue
    cy.use_denoising = False
    cy.use_adaptive_sampling = False
    r.film_transparent = False

    # render only the character
    for o in scene.objects:
        if o.type in {"MESH", "CURVE", "SURFACE", "META", "FONT", "GPENCIL", "GREASEPENCIL", "VOLUME",
                      "POINTCLOUD", "CURVES"} and o not in meshes:
            o.hide_render = True
    # world: what the camera sees (bg colour) is separate from what lights the clay pass (dim grey)
    world = bpy.data.worlds.new("_pass_world")
    world.use_nodes = True
    wn = world.node_tree
    wn.nodes.clear()
    bg = wn.nodes.new("ShaderNodeBackground")
    fill = wn.nodes.new("ShaderNodeBackground")
    fill.inputs["Color"].default_value = (0.35, 0.35, 0.35, 1.0)
    path = wn.nodes.new("ShaderNodeLightPath")
    mix = wn.nodes.new("ShaderNodeMixShader")
    wout = wn.nodes.new("ShaderNodeOutputWorld")
    wn.links.new(path.outputs["Is Camera Ray"], mix.inputs["Fac"])
    wn.links.new(fill.outputs["Background"], mix.inputs[1])
    wn.links.new(bg.outputs["Background"], mix.inputs[2])
    wn.links.new(mix.outputs["Shader"], wout.inputs["Surface"])
    scene.world = world
    vl = bpy.context.view_layer

    os.makedirs(args.out, exist_ok=True)
    anchors, t0 = {}, time.time()

    def out_path(kind: str, folder: str, image: int, ext: str = "png") -> str:
        p = os.path.join(args.out, kind, folder, f"{image:04d}.{ext}")
        os.makedirs(os.path.dirname(p), exist_ok=True)
        return p

    def project(p: Vector) -> tuple[float, float, float]:
        v = world_to_camera_view(scene, cam, p)
        return v.x * W, (1.0 - v.y) * H, (p - cam.matrix_world.translation).dot(forward)

    def goto(action: str, frame: int) -> None:
        set_action(arm, action)
        scene.frame_set(frame)

    # ---- pass 1: keypoints + anchors (no rendering)
    head_rest = None
    hb = bone("Head")
    if hb is not None:
        head_rest = (arm.matrix_world @ hb.bone.matrix_local).to_3x3().normalized()
    missing_bones = [n for n in list(BODY.values()) + ["Head"] if bone(n) is None]
    if "openpose" in passes and missing_bones:
        print(f"[render_passes] ! bones not found, keypoints omitted: {missing_bones}")
    for g, folder, img, action, frame, anchor_mode in plan:
        goto(action, frame)
        key = f"{folder}/{img:04d}.png"
        if anchor_mode == "root" and bone("Hips") is not None:
            hips = arm.matrix_world @ bone("Hips").head
            anchor = Vector((hips.x, hips.y, floor_pt.z))
        else:
            anchor = floor_pt
        ax, ay, _ = project(anchor)
        anchors[key] = [round(ax, 2), round(ay, 2)]
        if "openpose" not in passes:
            continue
        kp = [None] * 18
        for idx, name in BODY.items():
            b = bone(name)
            if b is not None:
                kp[idx] = arm.matrix_world @ b.head
        if kp[2] is not None and kp[5] is not None:
            kp[1] = (kp[2] + kp[5]) / 2                 # OpenPose neck = between the shoulders
        conf = [1.0] * 18
        if hb is not None:
            base = arm.matrix_world @ hb.head
            top_b = bone("HeadTop_End")
            top = arm.matrix_world @ (top_b.head if top_b is not None else hb.tail)
            h = (top - base).length
            rot = (arm.matrix_world @ hb.matrix).to_3x3().normalized() @ head_rest.inverted()
            U = (top - base).normalized()
            F = (rot @ facing).normalized()
            R = F.cross(U).normalized()
            kp[0] = base + U * 0.45 * h + F * 0.55 * h
            kp[14], kp[15] = base + U * 0.6 * h + F * 0.45 * h + R * 0.18 * h, base + U * 0.6 * h + F * 0.45 * h - R * 0.18 * h
            kp[16], kp[17] = base + U * 0.5 * h + R * 0.45 * h, base + U * 0.5 * h - R * 0.45 * h
            head_depth = project(base + U * 0.5 * h)[2]
            for idx in (14, 15, 16, 17):              # far-side eye/ear is hidden behind the head
                if project(kp[idx])[2] > head_depth + 0.1 * h:
                    conf[idx] = 0.0
        flat = []
        for p, c in zip(kp, conf):
            if p is None:
                flat += [0.0, 0.0, 0.0]
            else:
                x, y, _ = project(p)
                flat += [round(x, 2), round(y, 2), c]
        with open(out_path("openpose_json", folder, img, "json"), "w") as fh:
            json.dump({"canvas_width": W, "canvas_height": H,
                       "people": [{"pose_keypoints_2d": flat}]}, fh)

    # ---- render passes
    jobs = {
        "depth": dict(mat=depth_material(near, far), transparent=False, bg=(0, 0, 0), samples=4, view="Raw"),
        "mask": dict(mat=flat_material("_pass_mask", (1, 1, 1)), transparent=True, bg=(0, 0, 0), samples=4, view="Raw"),
        "clay": dict(mat=clay_material(), transparent=False, bg=(1, 1, 1), samples=args.samples, view="Standard"),
    }
    sun = None
    for kind in [p for p in ("depth", "clay", "mask") if p in passes]:
        job = jobs[kind]
        vl.material_override = job["mat"]
        r.film_transparent = job["transparent"]
        r.image_settings.color_mode = "RGBA" if job["transparent"] else "RGB"
        bg.inputs["Color"].default_value = (*job["bg"], 1.0)
        bg.inputs["Strength"].default_value = 1.0
        cy.samples = job["samples"]
        set_view_transform(scene, job["view"])
        if kind == "clay" and sun is None:
            sun = bpy.data.objects.new("_pass_sun", bpy.data.lights.new("_pass_sun", "SUN"))
            sun.data.energy = 3.0
            scene.collection.objects.link(sun)
            sun.rotation_euler = forward.to_track_quat("-Z", "Y").to_euler()
            sun.rotation_euler.x += math.radians(35)
        if sun is not None:
            sun.hide_render = kind != "clay"
        for n, (g, folder, img, action, frame, _a) in enumerate(plan, 1):
            goto(action, frame)
            r.filepath = out_path(kind, folder, img)
            bpy.ops.render.render(write_still=True)
            print(f"[render_passes] {kind} {n}/{len(plan)}  {folder}/{img:04d}  ({time.time() - t0:.0f}s)", flush=True)
    vl.material_override = None

    with open(os.path.join(args.out, "anchors.json"), "w") as fh:
        json.dump(anchors, fh, indent=1)
    roots = {tuple(v) for v in anchors.values()}
    facts = {
        "resolution": [W, H],
        "root_pixel": list(root_px),
        "source_root": list(next(iter(roots))) if len(roots) == 1 else None,
        "px_per_m": round(1.0 / m_per_px, 3),
        "ortho_scale": round(ortho_scale, 4),
        "standing_height_px": round(height_m / m_per_px, 1),
        "depth_near_far_m": [round(near, 3), round(far, 3)],
        "facing": cfg.get("facing", "-Y"),
        "frames": len(plan),
        "skipped_groups": skipped,
        "blender": bpy.app.version_string,
    }
    with open(os.path.join(args.out, "passes.json"), "w") as fh:
        json.dump(facts, fh, indent=2)
    print(f"[render_passes] done: {len(plan)} frames x {len(passes)} passes in {time.time() - t0:.0f}s -> {args.out}")
    print(f"[render_passes] align with: --anchors {os.path.join(args.out, 'anchors.json')} "
          f"--reference {os.path.join(args.out, 'mask')}")


if __name__ == "__main__":
    main()
