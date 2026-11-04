#!/usr/bin/env bash
# Usage: ./build_character.sh <char_name> [extra align_sprites.py args...]
# Env:   DISPLAYNAME="Kid K" AUTHOR="Big Deals" TEMPLATE=templates/fighter_base
#        NUMBER_FROM=filename  (frames named by image number, as in SHOTLIST.md)
# Runs: rembg + ground alignment -> manifest (+ .air coverage check) -> .sff
#       -> engine/chars/<char>/ (template wired, HD-scaled) -> registered in engine/data/select.def
set -euo pipefail
cd "$(dirname "$0")"
CHAR="$1"; shift
DIR="characters/$CHAR"
TEMPLATE="${TEMPLATE:-templates/fighter_base}"
AIR="$(ls "$TEMPLATE"/*.air 2>/dev/null | head -1 || true)"

python3 tools/align_sprites.py "$DIR" "$@"

EXTRA=()
[[ -d "$DIR/portraits" ]] && EXTRA+=(--extra "$DIR/portraits")
AIRARG=()
[[ -n "$AIR" ]] && AIRARG+=(--air "$AIR")
python3 tools/make_sff_manifest.py "$DIR" --number-from "${NUMBER_FROM:-index}" "${EXTRA[@]}" "${AIRARG[@]}"

python3 tools/build_sff.py "$DIR/04_build/$CHAR.sffmanifest.txt" -o "$DIR/04_build/$CHAR.sff"

if ! ls "$TEMPLATE"/*.def >/dev/null 2>&1; then
  echo "no template .def in $TEMPLATE; stopping after .sff ($DIR/04_build/$CHAR.sff)"
  exit 0
fi
SELECT=()
[[ -f engine/data/select.def ]] && SELECT+=(--select engine/data/select.def)
python3 tools/build_char_def.py "$CHAR" --sff "$DIR/04_build/$CHAR.sff" --template "$TEMPLATE" \
  --out "engine/chars/$CHAR" --displayname "${DISPLAYNAME:-$CHAR}" --author "${AUTHOR:-}" \
  --force "${SELECT[@]}"

# hit feel: tiered hitstop + screen shake (tune mods/juice.json, rebuild)
if [[ -f mods/juice.json ]]; then
  python3 tools/add_juice.py "engine/chars/$CHAR" --profile mods/juice.json | tail -1
fi
