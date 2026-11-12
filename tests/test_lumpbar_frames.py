"""The baked Mac frames (tools/lumpbar/frames) match what the app reads.

Run: python3 -m unittest tests.test_lumpbar_frames
"""
import json, os, re, struct, unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FRAMES = os.path.join(ROOT, "tools", "lumpbar", "frames")
LUMP = os.path.join(ROOT, "design", "lump.html")


def png_size(path):
    with open(path, "rb") as f:
        head = f.read(24)
    assert head[:8] == b"\x89PNG\r\n\x1a\n", path
    return struct.unpack(">II", head[16:24])


@unittest.skipUnless(os.path.exists(os.path.join(FRAMES, "manifest.json")), "frames not baked (python3 tools/lumpbar/bake.py)")
class Frames(unittest.TestCase):
    def setUp(self):
        with open(os.path.join(FRAMES, "manifest.json")) as f:
            self.m = json.load(f)

    def test_every_mood_the_bridge_can_send_is_baked(self):
        src = open(LUMP).read()
        moments = re.findall(r"'(\w+)'", re.search(r"const MOMENTS = \[(.*?)\]", src).group(1))
        self.assertEqual(sorted(self.m["moods"]), sorted(moments))

    def test_strips_hold_every_frame_the_ticks_point_at(self):
        size = self.m["size"]
        for mood, info in self.m["moods"].items():
            self.assertTrue(info["ticks"], mood)
            self.assertEqual(max(info["ticks"]) + 1, info["frames"], mood)
            for ink in ("glow", "paper"):
                for layer in ("k", "w"):
                    w, h = png_size(os.path.join(FRAMES, f"{mood}-{ink}-{layer}.png"))
                    self.assertEqual((w, h), (size * info["frames"], size), f"{mood}-{ink}-{layer}")


if __name__ == "__main__":
    unittest.main()
