"""Bake Lump's browser animation into a binary the firmware plays back.

Run it, open http://localhost:8765/lump.html in a browser, then run EXPORT_JS in
that page (its console, or Claude does it via the browser pane). The page posts
its mood/sequence list to /meta, then every 24 fps frame of every sequence as raw
RGBA to /frame. This server dedupes identical frames, converts to RGB565,
RLE-compresses, and writes firmware/data/lump.bin.

lump.bin layout (little-endian), version 3:
  "LUMP" u16 version=3  u16 nFrames  u8 nMoods  u8 fps
  per mood:      u8 base mood index, u8 nameLen, name
  u8 nSeqs
  per sequence:  u8 kind (0 loop, 1 transition, 2 event), u8 from, u8 to,
                 u8 nameLen, name, u16 length, u16 frame index per tick
  u32 offset[nFrames]  (from start of frame data)
  frame data: runs of [u8 count (1..255)][u16 rgb565 big-endian] until 57600 px
"""
import hashlib, http.server, json, os, struct, sys
from urllib.parse import urlparse, parse_qs

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DESIGN = os.path.join(ROOT, "design")
OUT = os.path.join(ROOT, "firmware", "data", "lump.bin")

frames = {}   # (sequence key, tick) -> rgba bytes
meta = {}

EXPORT_JS = r"""
(async () => {
  // Body colour is exported as pure magenta, a marker the firmware swaps for the real colour
  // at runtime (so his colour can change on the fly).
  const TINT = '#FF00FF';
  const L = window.__lump, cv = document.createElement('canvas'); cv.width = cv.height = 240;
  const c = cv.getContext('2d', { willReadFrequently: true });
  L.setLine('glow'); L.setTint(TINT);
  const meta = L.meta();
  await fetch('/meta', { method: 'POST', body: JSON.stringify(meta) });
  let n = 0;
  for (const s of meta.seqs) {
    for (let t = 0; t < L.seqLen(s.key); t++) {
      const a = s.kind === 0 ? L.at(s.key, t) : L.atSeq(s.key, t);
      L.render(c, s.kind === 0 ? s.key : L.fxFor(a.id), a.id, t, { dim: false });
      await fetch(`/frame?seq=${encodeURIComponent(s.key)}&tick=${t}`, { method: 'POST', body: c.getImageData(0, 0, 240, 240).data }); n++;
    }
  }
  return n + ' frames; ' + await (await fetch('/finish', { method: 'POST' })).text();
})()
"""


def rgb565(r, g, b):
    return ((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3)


def encode(rgba):
    px = [rgb565(rgba[i], rgba[i + 1], rgba[i + 2]) for i in range(0, len(rgba), 4)]
    out = bytearray(); i = 0; n = len(px)
    while i < n:
        v = px[i]; j = i + 1
        while j < n and px[j] == v and j - i < 255:
            j += 1
        out += bytes([j - i]) + struct.pack(">H", v)
        i = j
    return bytes(out)


def pstr(s):
    b = s.encode(); return bytes([len(b)]) + b


def build():
    moods = [m["name"] for m in meta["moments"]]
    uniq, blobs, body = {}, [], bytearray()
    for s in meta["seqs"]:
        ticks = sorted(t for (k, t) in frames if k == s["key"])
        seq = []
        for t in ticks:
            data = frames[(s["key"], t)]; h = hashlib.sha1(data).digest()
            if h not in uniq:
                uniq[h] = len(blobs); blobs.append(encode(data))
            seq.append(uniq[h])
        body += struct.pack("<BBB", s["kind"], moods.index(s["from"]), moods.index(s["to"])) + pstr(s["name"])
        body += struct.pack("<H", len(seq)) + struct.pack(f"<{len(seq)}H", *seq)
    out = bytearray(b"LUMP") + struct.pack("<HHBB", 3, len(blobs), len(moods), 24)
    for m in meta["moments"]:
        out += bytes([m["base"]]) + pstr(m["name"])
    out += bytes([len(meta["seqs"])]) + body
    off = 0
    for blob in blobs:
        out += struct.pack("<I", off); off += len(blob)
    for blob in blobs:
        out += blob
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "wb") as f:
        f.write(out)
    return {"frames_rendered": len(frames), "unique_frames": len(blobs), "bytes": len(out),
            "moods": len(moods), "sequences": len(meta["seqs"]), "out": OUT}


class H(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **k):
        super().__init__(*a, directory=DESIGN, **k)

    def log_message(self, *a):
        pass

    def do_POST(self):
        u = urlparse(self.path); n = int(self.headers.get("Content-Length", 0)); body = self.rfile.read(n)
        if u.path == "/meta":
            meta.clear(); meta.update(json.loads(body)); frames.clear(); res = b"ok"
        elif u.path == "/frame":
            q = parse_qs(u.query); frames[(q["seq"][0], int(q["tick"][0]))] = body; res = b"ok"
        elif u.path == "/finish":
            info = build(); print(json.dumps(info), flush=True); res = json.dumps(info).encode()
        else:
            self.send_error(404); return
        self.send_response(200); self.send_header("Content-Type", "text/plain"); self.end_headers(); self.wfile.write(res)


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    print(f"serving {DESIGN} on http://localhost:{port}", flush=True)
    http.server.ThreadingHTTPServer(("127.0.0.1", port), H).serve_forever()
