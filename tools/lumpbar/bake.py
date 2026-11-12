"""Bake small Lump frames for the Mac app (tools/lumpbar).

Renders every mood loop from design/lump.html in headless Chrome, shrunk to fit an
80 px square (cropped to his body for each mood, like design/lump-homes.html), and
writes tools/lumpbar/frames/:

  manifest.json            {"size": 80, "fps": 24, "moods": {mood: {"frames": n, "ticks": [frame per tick]}}}
  <mood>-<ink>-<k|w>.png   one horizontal strip per mood, ink (glow = cream line for black
                           backgrounds, paper = dark line for light ones) and body colour:
                           k = body drawn black, w = body drawn white.

The two strips let the app paint him any colour: every pixel is linear in the body colour,
so  pixel = k + (w - k) * colour / 255  (alpha is the same in both).

Usage:  python3 tools/lumpbar/bake.py
"""
import http.server, json, os, shutil, socket, subprocess, sys, tempfile, threading
from urllib.parse import urlparse, parse_qs

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DESIGN = os.path.join(ROOT, "design")
OUT = os.path.join(ROOT, "tools", "lumpbar", "frames")
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
SIZE = 80

BAKE_PAGE = r"""<!doctype html><meta charset="utf-8"><body>
<iframe id="src" src="lump.html" style="width:240px;height:240px;border:0"></iframe>
<script>
const S = __SIZE__, INKS = ['glow', 'paper'], TINTS = { k: '#000000', w: '#FFFFFF' };
const off = document.createElement('canvas'); off.width = off.height = 240;
const octx = off.getContext('2d', { willReadFrequently: true });
const small = document.createElement('canvas'); small.width = small.height = S;
const sctx = small.getContext('2d', { willReadFrequently: true }); sctx.imageSmoothingQuality = 'high';
// render() paints a background and a floor shadow first: skip both so he's on transparency.
function wrap() {
  let fills = 0;
  return new Proxy(octx, {
    get(t, p) {
      if (p === 'fillRect') return (x, y, w, h) => { if (x === 0 && y === 0 && w === 240 && h === 240) return; t.fillRect(x, y, w, h); };
      if (p === 'fill') return (...a) => { if (fills++ === 0) return; t.fill(...a); };
      const v = t[p]; return typeof v === 'function' ? v.bind(t) : v;
    },
    set(t, p, v) { t[p] = v; return true; }
  });
}
function draw(L, m, t, ink, tint, noFX) {
  L.setLine(ink); L.setTint(tint);
  octx.setTransform(1, 0, 0, 1, 0, 0); octx.clearRect(0, 0, 240, 240);
  L.render(wrap(), m, L.at(m, t).id, t, { noFX, dim: false });
}
const loopLen = (L, m) => L.LOOPS[m].reduce((s, [, h]) => s + h, 0);
function fit(L, m) {      // square around everything his body covers across the loop (sparks may poke out)
  let x0 = 240, y0 = 240, x1 = 0, y1 = 0;
  for (let t = 0; t < loopLen(L, m); t++) {
    draw(L, m, t, 'glow', '#FF7A45', true);
    const d = octx.getImageData(0, 0, 240, 240).data;
    for (let y = 0; y < 240; y++) for (let x = 0; x < 240; x++) if (d[(y * 240 + x) * 4 + 3] > 40) { if (x < x0) x0 = x; if (x > x1) x1 = x; if (y < y0) y0 = y; if (y > y1) y1 = y; }
  }
  const pad = 14, side = Math.max(x1 - x0, y1 - y0) + pad * 2;
  return [(x0 + x1) / 2 - side / 2, (y0 + y1) / 2 - side / 2, side, side];
}
function hash(a) { let h = 2166136261; for (let i = 0; i < a.length; i++) h = Math.imul(h ^ a[i], 16777619); return h >>> 0; }
async function post(path, body) { await fetch(path, { method: 'POST', body }); }
async function bake() {
  const L = document.getElementById('src').contentWindow.__lump;
  const manifest = { size: S, fps: 24, moods: {} };
  for (const m of L.MOMENTS) {
    const crop = fit(L, m), uniq = new Map(), layers = [], ticks = [];
    for (let t = 0; t < loopLen(L, m); t++) {
      const imgs = {};
      for (const ink of INKS) for (const [k, tint] of Object.entries(TINTS)) {
        draw(L, m, t, ink, tint, false);
        sctx.clearRect(0, 0, S, S); sctx.drawImage(off, ...crop, 0, 0, S, S);
        imgs[`${ink}-${k}`] = sctx.getImageData(0, 0, S, S);
      }
      const key = Object.values(imgs).map(i => hash(i.data)).join('.');
      if (!uniq.has(key)) { uniq.set(key, layers.length); layers.push(imgs); }
      ticks.push(uniq.get(key));
    }
    manifest.moods[m] = { frames: layers.length, ticks };
    for (const name of Object.keys(layers[0])) {
      const strip = document.createElement('canvas'); strip.width = S * layers.length; strip.height = S;
      const c = strip.getContext('2d'); layers.forEach((imgs, i) => c.putImageData(imgs[name], i * S, 0));
      await post(`/png?name=${m}-${name}`, await new Promise(r => strip.toBlob(r, 'image/png')));
    }
  }
  await post('/done', JSON.stringify(manifest));
}
document.getElementById('src').addEventListener('load', () => bake().catch(e => post('/fail', String(e.stack || e))));
</script>"""

done = threading.Event()
result = {}


class H(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **k):
        super().__init__(*a, directory=DESIGN, **k)

    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path == "/__lumpbar_bake.html":
            body = BAKE_PAGE.replace("__SIZE__", str(SIZE)).encode()
            self.send_response(200); self.send_header("Content-Type", "text/html"); self.end_headers(); self.wfile.write(body)
        else:
            super().do_GET()

    def do_POST(self):
        u = urlparse(self.path); body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        if u.path == "/png":
            with open(os.path.join(OUT, parse_qs(u.query)["name"][0] + ".png"), "wb") as f:
                f.write(body)
        elif u.path == "/done":
            with open(os.path.join(OUT, "manifest.json"), "w") as f:
                json.dump(json.loads(body), f, separators=(",", ":"))
            result["ok"] = json.loads(body); done.set()
        elif u.path == "/fail":
            result["error"] = body.decode(); done.set()
        self.send_response(200); self.end_headers()


def main():
    if os.path.isdir(OUT):
        shutil.rmtree(OUT)
    os.makedirs(OUT)
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", port), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    profile = tempfile.mkdtemp(prefix="lumpbar-chrome-")
    chrome = subprocess.Popen([CHROME, "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
                               f"--user-data-dir={profile}", f"http://127.0.0.1:{port}/__lumpbar_bake.html"],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        if not done.wait(300):
            sys.exit("timed out waiting for the bake page")
    finally:
        chrome.terminate(); chrome.wait(10); srv.shutdown(); shutil.rmtree(profile, ignore_errors=True)
    if "error" in result:
        sys.exit("bake failed:\n" + result["error"])
    moods = result["ok"]["moods"]
    print(f"baked {sum(m['frames'] for m in moods.values())} frames for {len(moods)} moods into {OUT}")


if __name__ == "__main__":
    main()
