# Ready-to-print parts

Binary STLs for the monitor mount, rendered from `../lump_monitor_mount.scad`. They're sized for the **stock case**
of the Waveshare/SpotPear ESP32-S3-LCD-1.3 prism (33.4 W × 50.1 D × 37.2 H mm; the case stays on).
Every file is watertight and lies flat on the bed.

If your case measures differently, change the numbers at the top of the `.scad` and run
`tools/export_stl.sh` to regenerate this folder.

## Monitor mount (`lump_monitor_mount.scad`)

A small adjustable shelf that sticks to the back of your monitor, so Lump peeks over the top at eye
level against a dark background. A plate sticks on, an arm hinges off it (tilt), and a cradle for
the case swivels on the arm. The thumbscrews are printed too, so you set the angle by hand.

| File | What it is |
|---|---|
| `mount_thread_test.stl` | **Print this first** (~15 min): one short screw and one knob nut, to check the printed threads on your printer |
| `mount_print_all.stl` | Everything on one plate: plate, arm, cradle, and the three thumbscrew parts |
| `mount_plate.stl` | Sticks to the monitor (double-sided foam tape such as 3M VHB on the flat lower part) |
| `mount_arm.stl` | Hinges off the plate; the cradle swivels on its end |
| `mount_cradle.stl` | Holds the case; slot at the back for the USB-C cable |
| `mount_hinge_bolt.stl` + `mount_hinge_nut.stl` | Tilt hinge: through the plate's knuckles and the arm |
| `mount_swivel_bolt.stl` | Swivel: up through the arm into the cradle's threaded hole |

Print notes: PLA or PETG, 0.2 mm layers, **supports off** (every overhang bridges or is 45° or
less). If the test nut is tight, raise `th_clear` to 0.4 in the `.scad`; if it's sloppy, drop it to 0.2.

Assembly: stick the plate to the monitor's back with the knuckles at the top, slot the arm between
the knuckles, push the hinge bolt through and spin the nut on. Screw the swivel bolt up through the
arm into the cradle, drop Lump in with the cable out the back slot, and tighten both knobs once he
looks right.

