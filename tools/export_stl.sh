#!/bin/bash
# Re-render the ready-to-print STLs in enclosure/print/ from the OpenSCAD sources.
# Run it after changing a measurement in enclosure/*.scad. Needs OpenSCAD with the Manifold
# backend (2024 or newer; brew install openscad). Binary STL, one file per printable part.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="$ROOT/enclosure/print"
mkdir -p "$OUT"
cd "$ROOT/enclosure"
render() {   # render <scad> <out name> <-D defines...>
  local scad="$1" name="$2"; shift 2
  openscad --backend=manifold --export-format binstl -o "$OUT/$name.stl" "$@" "$scad" 2>&1 \
    | grep -iE "warning|error" | grep -v NoError || true
  echo "  $name.stl"
}
for p in plate arm cradle hinge_bolt hinge_nut swivel_bolt thread_test print_all; do
  render lump_monitor_mount.scad "mount_$p" -D "part=\"$p\"" &
done
for s in round box; do for p in pot deck; do
  render lump_planter.scad "planter_${s}_$p" -D "part=\"$p\"" -D "pot_shape=\"$s\"" &
done; done
wait
echo "Done: $OUT"
