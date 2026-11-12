# Desk Buddy: handoff for the next chat

**Next up:** refining Lump's tap-gag animations. Joe will point at specific gags/frames to tweak.

## What this is
A SpotPear/Waveshare ESP32-S3-LCD-1.3 "prism" display behind Joe's monitor showing **Lump**, an original hand-drawn, frame-by-frame blob with a cowlick (default colour Tangerine FF7A45). He shows the status of Joe's Claude sessions (Code, Chat, Cowork) and reacts when Joe taps the glass.

## Where things live
- `design/lump.html`: the whole character: drawings (`D`), moods, transitions, events, and every tap gag. Published preview: https://claude.ai/artifact/Ueh5ffohpkdvjbwqDpWDtb
- `firmware/src/main.cpp`: the frame player on the device. **Another session also edits this file** (Wi-Fi/offline mode), so re-read it before editing.
- `tools/export_frames.py`: bakes the page's frames into `firmware/data/lump.bin`. Run it on **port 8767** from the shell (8765/8766 are claimed by another chat; the app can't launch it itself because it can't read iCloud Drive). `.claude/launch.json` has an attach-only `lump-preview` entry for the browser pane.
- `tools/flash.sh`: builds and flashes (paced esptool; plain `pio upload` fails on this Mac). Run it with `VENV=` set to a Python venv that has esptool. A flash takes about 4 min.
- Bridge (Mac to device): `~/.desk-buddy/bridge.py` (copy of `tools/buddy_bridge.py`), HTTP `127.0.0.1:47823` (`/cmd` sends a raw command to the board), log `~/.desk-buddy/bridge.log`.

## How Joe reviews animations (important)
1. Preview in the app's built-in browser pane: start `python3 tools/export_frames.py 8767`, then open `http://localhost:8767/lump.html#gags`.
   - The **"Tap gags"** gallery at the top loops every gag. Click a card to pause it, step with ◀ ▶, and use the speed button for slow motion.
   - The label on each card shows the drawing id on screen (e.g. `FG3`), so Joe can point at exact frames.
   - The page auto-reloads when the file changes.
2. **Never flash until Joe explicitly approves** the animations.
3. **Self-critique every drawing:** render it big (~600 px) and ask "cute cartoon, or does it look terrible?" Fix it before showing. Keep faces simple: dot or oval eyes with a shine, ">" squints, small mouths, soft blush. Sell the gag with props and effects, not facial detail (realistic folds, teeth or angry brows looked grotesque).
4. After approval: run the page's export JS (in `tools/export_frames.py`), then `tools/flash.sh`, then test with `curl -X POST --data "e tap_<gag>" 127.0.0.1:47823/cmd`. `X` fakes a physical tap.

## The tap gags (all 12 approved and flashed 2026-11-11)
Every tap plays a random gag in any mood, never the same one twice in a row. Clips are named `tap_<gag>_<mood>`; each is the mood's way in, the gag, then the way back.

| Gag | What happens | Drawing ids |
|---|---|---|
| Electric shock | pokes a wall socket, flickers jagged with an X-ray flash, frizzed hair, coughs smoke | ZP*, ZA*, ZC* |
| Ring of fire | zooms off, rides a red motorbike through a flaming hoop, walks back with his cowlick smoking | MC*, MB00–15 |
| Wolf howl | moon rises, grows fur, ears, snout and tail, howls, poofs back | WF0–WFd |
| Face on the glass | runs up, splats sideways with "> <" squint eyes, slides down leaving a smudge, wipes it | FG1–FGc |
| Banana peel | yellow peel drops from the sky, he slips, it flips and lands on his head | BN*, BNa–BNm |
| Balloon pop | puffs up, springs a leak, deflates while flying around face-first | BL*, BZ00–43 |
| Sneeze | tissue up to his mouth, ah… CHOO, tissue billows, rockets off-screen, walks back in | SZ* |
| Accordion | squashed into accordion folds with music notes | SQ1, AC1–3, BO1 |
| Anvil | anvil flattens him, he pops back | AV0–6 |
| Disco | disco ball, sunglasses, dancing | DB*, DS*, DP* |
| Mooning | side-eye, turns round, wiggles his bum, raspberry over the shoulder, innocent whistle | MO*, MW*, MR* |
| Cartoon bomb | trench coat, flings it open, cartoon time bomb counts 3-2-1, KABOOM, sooty, pops back | BM*, BX*, BC* |

Situational taps (replace the random gags): **night** → late-night startle (NS*, `tap_startle_night`); **meeting countdown or alarm** → meeting prep (MP*, `tap_prep_<mood>`). The bridge tells the board about the countdown with `p 1` / `p 0`.

Helpers in `lump.html`: `xf()` (move, scale, rotate or stretch a drawing, swap its face, add props), `balloon()`, `accordion()`, `furry()`, `FACE.*` faces. Props are drawn in `drawBackProps` / `drawFrontProps`.

Real colours (yellow banana, fire, red bike, lightning) display correctly on the device; the firmware leaves non-body colours alone.

## Constraints worth remembering
- Keep tick-driven effects repeating on a **12-tick beat** so frames are shared across moods; otherwise the frame file balloons in size.
- Budget: 874 unique frames, 7.9 MB, in a 12 MB app slot; 158 of 250 sequence slots used.
- Recently fixed (in firmware, flashed):
  - Taps were cancelled instantly by a timing bug.
  - Gags queued during a mood change were dropped.
- One fresh real tap was registered after the fix, but whether its gag played through hasn't been confirmed.
