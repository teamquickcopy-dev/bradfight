# Fighter sprite pipeline

This pipeline takes ControlNet renders and turns them into a `.sff` that Ikemen GO can load. It runs headless and needs no Fighter Factory.

```
fighter-pipeline/
├── build_character.sh            # one command: align -> manifest -> .sff -> engine/chars/<char>/ -> select.def
├── requirements.txt
├── tools/
│   ├── make_shotlist.py          # template .air -> SHOTLIST.md + empty 01_raw/<group>_<label>/ folders
│   ├── make_greybox.py           # placeholder renders from the template, to test the build before art exists
│   ├── draw_openpose.py          # keypoint JSON -> ControlNet OpenPose images
│   ├── build_donate_mod.py       # mods/donate.json -> engine/external/mods/donate.lua (title-menu button)
│   ├── build_snd.py              # SOUNDLIST.md + recordings (wav/mp3/ogg/flac/m4a) -> <char>.snd
│   ├── build_brand.py            # brand/brand.json -> your screenpack: logo, title/select, splash, lifebar, icon
│   ├── add_juice.py              # tiered hitstop + screen shake on every HitDef (mods/juice.json)
│   ├── install_crt.py            # CRT / bloom post-process shaders + save/config.ini
│   ├── make_stage_art.py         # placeholder night-street art + stage.json
│   ├── build_stage.py            # stage.json -> engine/stages/<stage>.def/.sff, preview, gap check
│   ├── align_sprites.py          # rembg cutout (cached) + rig-locked placement + QA against rig silhouettes
│   ├── make_sff_manifest.py      # aligned tree -> text manifest, cross-checks template .air
│   ├── build_sff.py              # manifest -> SFF v2.01 (32-bit PNG sprites, trimmed, deduped)
│   └── build_char_def.py         # template + .sff -> engine/chars/<char>/ (.def, HD scale match, select.def)
├── rigs/
│   ├── render_passes.py          # Blender: locked ortho side camera -> openpose/depth/clay/mask + anchors.json
│   └── <char>.blend              # your master rig with one action per animation
├── comfy/
│   ├── workflows/                # ComfyUI workflow JSONs (OpenPose + Lineart/Depth ControlNet)
│   └── pose_maps/<group>_<action>/####.png   # control images rendered from the rig
├── templates/fighter_base/       # the balanced template: one .def + its .air/.cns/.cmd/.snd/.sff
├── characters/<char>/
│   ├── 01_raw/<group>_<action>/####.png      # ComfyUI output, one folder per animation
│   ├── 02_cutout/                            # rembg results (auto, cached by mtime)
│   ├── 03_aligned/                           # 768x1024 canvases + alignment.json + alignment_report.csv
│   ├── 04_build/<char>.sffmanifest.txt, <char>.sff
│   └── portraits/9000_portraits/0000.png     # optional, not ground-aligned (axis 0,0)
│   └── sounds/<group>_<label>/<n>.wav        # your recordings (see SOUNDLIST.md)
├── brand/                       # brand.json + logo.png, splash.png, icon.png (placeholders until you replace them)
├── mods/donate.json              # support link + texts for the "KEEP THE RENT PAID" menu button
├── stages/src/<stage>/           # stage.json + art/*.png + ads/*.png (+ preview.png after a build)
└── engine/                       # Ikemen GO v1.0.0 (Linux build); chars/<char>/ + data/select.def
```

## Setup

The repo holds the pipeline only. The Ikemen GO engine and its `kfm720` template character are third-party,
so the setup script downloads them:
```bash
pip install -r requirements.txt
python tools/setup_engine.py        # Ikemen GO v1.0.0 for your OS -> engine/, kfm720 -> templates/fighter_base/
```
It also rebuilds the stage, CRT shader, and (once `mods/donate.json` has your link) the donation button.

## Naming contract

`make_shotlist.py` creates one folder per sprite group the template needs (`0_standing_animation`,
`200_stand_light_punch`, ...) and lists each group's image numbers in `SHOTLIST.md`. Name every frame
after its image number (`0000.png`, `0010.png`) and build with `NUMBER_FROM=filename`. Some groups skip
numbers (5000 uses 0, 10, 11, 20, 21), and numbering by sort order would shift every frame after a gap.

## Blender: render the control passes

1. `python tools/make_shotlist.py characters/kid_k --air templates/fighter_base/kfm720.air` writes
   `characters/kid_k/shots.json`. Set `"action"` for each group to one of the `.blend`'s actions. Frames
   default to evenly spaced (`"loop": true` for cycles). Use `"frames": [..]` to pick frames, `"per_image"`
   to mix actions within one group, and `"anchor": "root"` for walks/runs that move forward (this removes
   the root motion, because the template's `.cns` already moves the character). Groups left blank are skipped.
2. Render (Blender 4.2+, tested on 5.0.1, Cycles only, so it also runs headless or on a render farm):
   ```bash
   blender -b rigs/kid_k.blend -P rigs/render_passes.py -- --config characters/kid_k/shots.json \
       --out comfy/pose_maps/kid_k --gpu
   python tools/draw_openpose.py comfy/pose_maps/kid_k/openpose_json comfy/pose_maps/kid_k/openpose
   ```
   The orthographic camera keeps the character the same size at every depth. It puts the armature origin
   on pixel (512, 600) and makes the standing pose 420 px tall, matching `kfm720` (no rescaling needed).
   Set `"facing"` to the direction your rig faces (Mixamo: `-Y`). The `.blend` is never saved.
3. Generate the frames in ComfyUI. Start ComfyUI, copy `comfy/workflows/fighter_settings.example.json` to
   `comfy/kid_k.settings.json`, and set your checkpoint, ControlNets, LoRA and prompt in it. Then:
   ```bash
   python comfy/run_comfy.py --passes comfy/pose_maps/kid_k --out characters/kid_k/01_raw \
       --settings comfy/kid_k.settings.json --shots characters/kid_k/shots.json --limit 6
   ```
   `--limit 6` tries your settings on a few frames first. Drop it to run all of them; re-running skips
   frames that already exist, so an interrupted batch resumes.

   For each frame, the driver uploads the openpose and depth passes (plus clay in `img2img` mode) and queues
   `comfy/workflows/fighter_frame_api.json`. The workflow uses only built-in nodes, in ComfyUI API format.
   The driver saves the result under the exact name and resolution the build expects. Before queueing
   anything, it checks that your models are installed.
   - **Consistency across hundreds of frames:** one fixed seed and the same prompt help. A character LoRA
     trained on a few hero images of the character does most of the work.
   - `"mode": "img2img"` starts each frame from the clay render (`denoise` around 0.8). Silhouettes then stay
     closest to the rig, which the alignment and QA rely on.
   - ControlNets are SDXL OpenPose and Depth models (for example xinsir's openpose-sdxl and diffusers'
     depth-sdxl), renamed to match the settings or the other way round.
4. Build:
   ```bash
   NUMBER_FROM=filename ./build_character.sh kid_k --anchors comfy/pose_maps/kid_k/anchors.json \
       --reference comfy/pose_maps/kid_k/mask
   ```

Tested with a procedural Mixamo-style rig (render, then rembg, align and `.sff`). All 544 keypoints landed on
the body, and cutouts matched the rig masks with at least 92% overlap and at most 2 px drift. The 139 px
jump arc was kept, and the walk stayed in place within 1 px.

## Placement: rig mode (default)

Render every frame from the same locked Blender camera. The rig's root then lands on the same pixel in
every render, and you pass that pixel as `--source-root X Y`. A single fixed transform maps it to the canvas
axis, so jumps, knockdowns and walk cycles keep the motion the rig authored. Use `01_raw/anchors.json`
(`{"0_standing_animation/0000.png": [x, y]}`) instead if the root moves between renders.

Don't snap each frame's lowest pixel to the floor. Measured on kfm720, that misplaces 30 of 50 groups:
airborne and knockdown frames by up to 350 px, and walk cycles jitter by up to 32 px. `--mode snap` is
still there for art that has no rig.

QA: also render a **mask or depth pass** per frame and pass it as `--reference DIR` (same folder layout).
Each generated frame is compared with the rig's silhouette:
- `MISMATCH` means low overlap (morphing, rembg damage).
- `DRIFT` means the body shifted.
- `OUTLINE` means an edge is far from the character's usual offset (a missing head or limb).

A consistent costume difference, like big hair, cancels out. In the greybox test, the check flagged exactly
the two frames that were sabotaged (one head removed, one shifted 15 px) and none of the other 277.

## Run

```bash
pip install -r requirements.txt
python tools/make_shotlist.py characters/kid_k --air templates/fighter_base/kfm720.air
# ... render into characters/kid_k/01_raw/<group folder>/<image>.png ...
NUMBER_FROM=filename DISPLAYNAME="Kid K" AUTHOR="you" ./build_character.sh kid_k \
    --source-root 512 600 --reference comfy/pose_maps/kid_k
```

Greybox dry run (no art needed): `python tools/make_greybox.py characters/fighter01 --template-sff
templates/fighter_base/kfm720.sff`, then the build command above with `--skip-rembg`.

The template's own `.sff` must stay in `templates/fighter_base/`. `build_char_def.py` measures its
standing sprite (0,0) against yours and scales to match:

- `[Size] xscale/yscale` becomes 1/R.
- Every `.air` Clsn box and frame offset is multiplied by R.

As a result, hitboxes, pushboxes and velocities stay identical to the template in game units. Your art also keeps its
full resolution. Aim for R >= 1. If the build warns that sprites are UPSCALED, re-align with a larger `--scale`.
Ikemen GO ships `chars/kfm` (320x240, 105 px tall) and `chars/kfm720` (1280x720, 420 px tall) as templates.

## Things to check

- In `03_aligned/alignment_report.csv`:
  - `MISMATCH`, `DRIFT` and `OUTLINE` come from the `--reference` comparison (see above).
  - `CLIPPED` means the sprite runs off the canvas. Lower `--scale` or move `--ground`.
  - `HEIGHT_OUTLIER` appears in snap mode only.
- The `[air] ... missing` output lists sprites the template animations use that you haven't made yet. Missing sprites don't cause an error; they just don't show up in-game.
- `build_char_def.py` lists every sprite the template's `.air` uses that your `.sff` lacks. A KFM reskin needs
  240 sprites in 49 groups. It also checks for the 9000,0 and 9000,1 select-screen portraits.
- `--scale` is one global value. Never fit each frame to the canvas separately, because the character would change size from frame to frame.
- In snap mode, `--x-mode feet` anchors on the lower body. `mass` makes the character slide backward when an arm extends.

## Stages and sponsor billboards

```bash
python tools/make_stage_art.py stages/src/corner_store          # placeholder art + stage.json (only fills in missing files)
python tools/build_stage.py stages/src/corner_store/stage.json \
    --fighter-sff engine/chars/fighter01/fighter01.sff --select engine/data/select.def --home-stage-for fighter01
```

`stage.json` lists the layers back to front. `"at": [x, y]` means px from the screen centre and px from the
floor (negative is up). `delta` sets the parallax (1 moves with the fighters). Optional keys:

- `frames` for animation (the neon sign flickers with 150/5/8/4-tick frames)
- `velocity` + `tile` for traffic
- `trans: "add"` for glow
- `layer: 1` to draw in front of the fighters

**Sponsors:** each slot (`"sponsors": {"main": {...}}`) has a size, seconds per ad, and a list of images.
Any image size works; it's letterboxed into the slot and never stretched. The slot cycles through the
list. To swap a sponsor, drop in a new PNG and rebuild. A slot's `"qr": {"url": ...}` adds a QR panel for
the support link. Leave the URL empty until you have the real one. The QR decodes from the billboard at
full size and at half size.

Every build writes `preview.png`, showing the stage at the centre, both camera limits, and the highest jump
point, with your fighter for scale. It also fails if any camera position shows a gap with no background.

## "Keep The Rent Paid" menu button

1. Put your link in `mods/donate.json` as `"url"` (Cash App, Ko-fi, PayPal.me, Patreon...). It must be
   `https://` with plain URL characters only; the build refuses quotes, spaces, `%` and shell symbols.
2. Build the mod:
   ```bash
   python tools/build_donate_mod.py mods/donate.json
   ```

This writes `engine/external/mods/donate.lua` plus a QR code in `engine/external/mods/donate/qr.sff`. Ikemen
loads every file in `external/mods/` automatically, so no engine files are edited, and engine updates won't
overwrite it. To disable the button, rename the file to `-donate.lua`.

The button appears on the title menu above OPTIONS (change `"before"` to move it). Selecting it:
- opens the link in the default browser (Windows `rundll32`, macOS `open`, Linux `xdg-open`). The command
  returns at once, so the game never freezes;
- shows a screen with your message, the link, and a QR code to scan with a phone. This also covers players
  whose browser can't open, such as Steam Deck game mode or fullscreen setups, and people watching on a stream;
- returns to the title menu on any button.

The stage's QR billboard has its own `"qr": {"url": ...}` field in `stage.json`; set it to the same link.

## Visual juice

**Hitstop and screen shake.** `build_character.sh` runs this automatically when `mods/juice.json` exists:
```bash
python tools/add_juice.py engine/chars/fighter01
```

Every HitDef gets a tier: light, medium, heavy (from `animtype`), special (states 1000-2999) or super (3000-4999).
- **Hitstop** (`pausetime`) is raised to the tier value (10/13/16/16/22 frames) and never lowered. The
  freeze gap between attacker and defender is kept, so frame advantage and combos are unchanged.
- **Screen shake** (`envshake`) is added from medium hits up. Knockdowns get a ground-impact shake (`fall.envshake`).

Throws, hits that send the opponent to a custom state, and the lighter lead-in hits of multi-hit moves are
left alone. Changed lines are tagged `; juice`; re-running restores the originals first, so tune the JSON
and run again. On kfm720 it changed 31 of 41 hits and left the other 10 alone.

**CRT shaders**
```bash
python tools/install_crt.py crt
```
- `crt`: curved glass, scanlines, RGB aperture grille, bloom on near-white highlights, vignette.
- `flat`: the same without curvature, so the lifebar corners stay sharp.
- `off`: back to the plain picture.

Strengths are flags (`--scanlines 0.3 --bloom 0.25 --curvature 0.035 --mask 0.1`). The tool sets
`[Video] ExternalShaders` in `engine/save/config.ini`. Tested by compiling the shaders the way Ikemen's
OpenGL 3.3 renderer does and rendering a stage frame: average brightness and saturation stay within 5% of
the original. The experimental Vulkan renderer needs `.spv` builds (see the tool's help).

## Character sounds

```bash
python tools/build_snd.py list characters/kid_k     # SOUNDLIST.md + sounds/<group>_<label>/ folders
```

Record into those folders: `sounds/0_voices/0000.wav`, `0001.mp3` and so on. WAV, MP3, OGG, FLAC, M4A and
Opus all work. `build_character.sh` then builds `<char>.snd` automatically, or run it by hand:
```bash
python tools/build_snd.py build characters/kid_k --char-out engine/chars/kid_k
```

Each recording is converted to 16-bit 44.1 kHz mono (`--stereo` keeps stereo), with silence trimmed and
5 ms fades added against clicks. Loudness is evened to -16 dBFS RMS with peaks capped at -1 dBFS (change it
with `--target-rms`). Any sound you haven't recorded keeps the template's, so the character never goes
silent; the build lists which ones.

The list only covers the character's own sounds: `PlaySnd` without an `F`/`GO` prefix, and
`hitsound`/`guardsound` with an `S` prefix. Ordinary hit and block sounds come from the engine's shared
`fight.snd`. On kfm720 that's 9 sounds played by the moves (attack grunts, throw, landing, knee kick), plus 3
the template carries for intros and wins.

Tested by loading the built `.snd` with Ikemen's own audio decoder (its fork of `beep`) using Ikemen's
read loop: every sound streamed to the end, so none would be silently disabled at load.

## Branding

```bash
python tools/build_brand.py brand/brand.json        # setup_engine.py runs this too
```

This builds your own screenpack, `engine/data/<slug>/`, from Ikemen's bundled `ikemen1`, and sets
`engine/save/config.ini` to use it: `Motif`, and the window title and icon. Every menu keeps working,
because only images and colours change:

- **Title screen:** your logo with a glow in the primary colour, and a night-city background built from
  the stage art. The skylines scroll slowly where the clouds were. The highlighted menu item uses the
  secondary colour.
- **Select screen:** the same city, tinted with the secondary colour and darker so the character grid
  reads clearly.
- **Startup splash:** your splash image instead of the Ikemen video, with a small "Powered by Ikemen GO"
  credit. Set `"powered_by_ikemen": false` to drop it.
- **Lifebars:** the frame is tinted with the primary colour. The health colours stay as they are, because
  they tell players who's winning.
- **Window:** your game name as the title, and your icon.

`brand/logo.png`, `splash.png` and `icon.png` start as generated placeholders. Replace them with real art
at any size (it's fitted, never stretched) and rebuild. Colours, background art and how dark each screen
gets (`title_dim`, `select_dim`) are set in `brand.json`. Each build writes `brand/preview.png`.

Sprites are swapped in place in copies of the screenpack's sprite files. Every other sprite and palette
stays byte-identical to the original, so nothing else in the screenpack can break.
