// Desk Buddy — Vec preview firmware.
// Five 3D "Vec" hologram variants x four moments, selectable over USB serial.
// Port of design/vec-variations.html; keep the two in step.
//
// Serial protocol (115200, newline-terminated), each answered with "ok ..." or "err ...":
//   v <prime|facet|swarm|gyro|phosphor>   pick variant
//   s <cycle|idle|working|alert|done>     pick moment (cycle = rotate every 4.2 s)
//   t <own|RRGGBB>                        colour override
//   ?                                     report current settings + fps

#include <Arduino.h>
#include <LovyanGFX.hpp>
#include <map>
#include <esp_heap_caps.h>
#include <esp_memory_utils.h>
#include <vector>

// ---------- board ----------
constexpr int PIN_SCLK = 40, PIN_MOSI = 41, PIN_DC = 38, PIN_CS = 39, PIN_RST = 42, PIN_BL = 20;
constexpr bool PRISM_MIRROR = true;  // rotations 4–7 are mirrored for the prism

class LGFX : public lgfx::LGFX_Device {
  lgfx::Panel_ST7789 panel;
  lgfx::Bus_SPI bus;
 public:
  LGFX() {
    auto b = bus.config();
    b.spi_host = SPI2_HOST; b.spi_mode = 0; b.freq_write = 80000000;
    b.pin_sclk = PIN_SCLK; b.pin_mosi = PIN_MOSI; b.pin_miso = -1; b.pin_dc = PIN_DC;
    b.dma_channel = SPI_DMA_CH_AUTO;
    bus.config(b); panel.setBus(&bus);
    auto p = panel.config();
    p.pin_cs = PIN_CS; p.pin_rst = PIN_RST; p.pin_busy = -1;
    p.panel_width = 240; p.panel_height = 240; p.memory_width = 240; p.memory_height = 320;
    p.invert = true; p.readable = false;
    panel.config(p); setPanel(&panel);
  }
};
static LGFX tft;
static LGFX_Sprite fb(&tft);

// ---------- colour ----------
struct RGB { uint8_t r, g, b; };
static RGB hexRGB(uint32_t h) { return {uint8_t(h >> 16), uint8_t(h >> 8), uint8_t(h)}; }
static RGB mixc(RGB a, RGB b, float k) { return {uint8_t(a.r + (b.r - a.r) * k), uint8_t(a.g + (b.g - a.g) * k), uint8_t(a.b + (b.b - a.b) * k)}; }
static uint16_t col(RGB c, float a) { a = constrain(a, 0.f, 1.f); return lgfx::color565(uint8_t(c.r * a), uint8_t(c.g * a), uint8_t(c.b * a)); }
static const RGB AMB = hexRGB(0xFFB547), GREEN = hexRGB(0x7CFF9B), WHITE = {255, 255, 255};

// ---------- math ----------
constexpr float TAU_F = 6.2831853f, PI_F = 3.1415927f;
struct V3 { float x, y, z; };
struct P2 { float x, y, z, k; };
static inline V3 rotX(V3 p, float a) { float c = cosf(a), s = sinf(a); return {p.x, p.y * c - p.z * s, p.y * s + p.z * c}; }
static inline V3 rotY(V3 p, float a) { float c = cosf(a), s = sinf(a); return {p.x * c + p.z * s, p.y, -p.x * s + p.z * c}; }
static inline V3 rotZ(V3 p, float a) { float c = cosf(a), s = sinf(a); return {p.x * c - p.y * s, p.x * s + p.y * c, p.z}; }
static inline float hashf(float i) { float s = sinf(i * 12.9898f + 78.233f) * 43758.5453f; return s - floorf(s); }
static inline float easeIO(float x) { return x < 0.5f ? 4 * x * x * x : 1 - powf(-2 * x + 2, 3) / 2; }
static inline float depthA(float z) { float v = constrain((z + 1) / 2, 0.f, 1.f); return 0.1f + 0.9f * powf(v, 1.6f); }

enum State { IDLE, WORKING, ALERT, DONE };
enum Mood { SLEEP, LOOK, WIDE, HAPPY };

static uint32_t prof[8];
struct Scene {
  float t, st, dt; State state; Mood mood;
  float blink, bob, sq, cx, cy, spin, yaw, pitch, roll;
  RGB tint, tint2; bool hasTint2;
} S;

// Head pose as a matrix, rebuilt once per frame by updatePose() (squash, roll, pitch, yaw).
static V3 PX, PY, PZ;
static void updatePose() {
  auto r = [](V3 v) { return rotY(rotX(rotZ(v, S.roll), S.pitch), S.yaw); };
  V3 x = r({1, 0, 0}), y = r({0, 1, 0}), z = r({0, 0, 1});
  float sx = 1 - S.sq * 0.5f, sy = 1 + S.sq;
  PX = {x.x * sx, x.y * sx, x.z * sx}; PY = {y.x * sy, y.y * sy, y.z * sy}; PZ = {z.x * sx, z.y * sx, z.z * sx};
}
static inline V3 posep(V3 p) {
  return {PX.x * p.x + PY.x * p.y + PZ.x * p.z, PX.y * p.x + PY.y * p.y + PZ.y * p.z, PX.z * p.x + PY.z * p.y + PZ.z * p.z};
}
static P2 proj(V3 q, float R) { float k = 4.f / (4.f - q.z); return {S.cx + q.x * R * k, S.cy - q.y * R * k, q.z, k}; }
static P2 PP(V3 p, float R) { return proj(posep(p), R); }

// ---------- fast anti-aliased lines straight into the framebuffer ----------
// LovyanGFX stores 16-bit sprites byte-swapped. Lines blend with per-channel max,
// which mimics light adding up in the prism and makes draw order irrelevant.
static uint16_t* FB = nullptr;
static inline uint16_t sw(uint16_t v) { return (v >> 8) | (v << 8); }
// intensity i is 0..256; cr/cg/cb are the colour's full-strength 5/6/5-bit channels
static inline void plotMax(int x, int y, int cr, int cg, int cb, int i) {
  if (i < 6 || (unsigned)x >= 240 || (unsigned)y >= 240) return;
  uint16_t& d = FB[y * 240 + x]; uint16_t p = sw(d);
  int pr = (p >> 11) & 31, pg = (p >> 5) & 63, pb = p & 31;
  int nr = (cr * i) >> 8, ng = (cg * i) >> 8, nb = (cb * i) >> 8;
  if (nr > pr) pr = nr; if (ng > pg) pg = ng; if (nb > pb) pb = nb;
  d = sw((pr << 11) | (pg << 5) | pb);
}
// Xiaolin Wu line in 16.16 fixed point; thick = 1 or 2 px core, glow = halo intensity either side
static void wuLine(float x0, float y0, float x1, float y1, RGB c, float a, int thick, float glow) {
  int ia = min(256, int(a * 256)), ig = min(256, int(glow * 256));
  int cr = c.r >> 3, cg = c.g >> 2, cb = c.b >> 3;
  bool steep = fabsf(y1 - y0) > fabsf(x1 - x0);
  if (steep) { std::swap(x0, y0); std::swap(x1, y1); }
  if (x0 > x1) { std::swap(x0, x1); std::swap(y0, y1); }
  float dx = x1 - x0, grad = dx < 0.001f ? 0 : (y1 - y0) / dx;
  int xs = lroundf(x0), xe = lroundf(x1);
  int32_t y = int32_t((y0 + grad * (xs - x0)) * 65536.f), g = int32_t(grad * 65536.f);
  for (int x = xs; x <= xe; x++, y += g) {
    int yi = y >> 16, f = (y >> 8) & 255;
    int i1 = (ia * (256 - f)) >> 8, i2 = (ia * f) >> 8;
    if (steep) {
      plotMax(yi, x, cr, cg, cb, i1); if (thick > 1) plotMax(yi + 1, x, cr, cg, cb, ia); plotMax(yi + thick, x, cr, cg, cb, i2);
      if (ig) { plotMax(yi - 1, x, cr, cg, cb, ig); plotMax(yi + thick + 1, x, cr, cg, cb, ig); }
    } else {
      plotMax(x, yi, cr, cg, cb, i1); if (thick > 1) plotMax(x, yi + 1, cr, cg, cb, ia); plotMax(x, yi + thick, cr, cg, cb, i2);
      if (ig) { plotMax(x, yi - 1, cr, cg, cb, ig); plotMax(x, yi + thick + 1, cr, cg, cb, ig); }
    }
  }
}

// ---------- batched line segments (x4 fixed point keeps sub-pixel precision) ----------
struct Seg { int16_t x1, y1, x2, y2; uint8_t bin; };
struct SegBuf {
  Seg* s; int n; int cap;
  void clear() { n = 0; }
  void add(const P2& a, const P2& b, float alpha) {
    if (n >= cap) return;
    s[n++] = {int16_t(a.x * 4), int16_t(a.y * 4), int16_t(b.x * 4), int16_t(b.y * 4), uint8_t(min(4, int(alpha * 5)))};
  }
  void poly(const P2* p, int cnt) { for (int i = 0; i < cnt - 1; i++) add(p[i], p[i + 1], depthA((p[i].z + p[i + 1].z) / 2)); }
};
static Seg segA_[1800], segB_[900], segC_[900];
static SegBuf SA{segA_, 0, 1800}, SB{segB_, 0, 900}, SC{segC_, 0, 900};

static void strokeBins(SegBuf& b, RGB c, float width, float glow = 0.16f) {
  int thick = width >= 1.4f ? 2 : 1;
  for (int i = 0; i < b.n; i++) {
    const Seg& s = b.s[i]; float a = (s.bin + 0.5f) / 5;
    wuLine(s.x1 * 0.25f, s.y1 * 0.25f, s.x2 * 0.25f, s.y2 * 0.25f, c, a, thick, a * glow * 1.8f);
  }
}

typedef V3 (*Deform)(V3);
static void latLong(SegBuf& out, float R, int nLon, const float* lats, int nLat, int steps, Deform df = nullptr) {
  P2 pts[65]; float ct[65], stt[65];
  for (int j = 0; j <= steps; j++) { ct[j] = cosf(j * TAU_F / steps); stt[j] = sinf(j * TAU_F / steps); }
  for (int i = 0; i < nLon; i++) {
    float lon = S.spin + i * PI_F / nLon;
    float sl = sinf(lon), cl = cosf(lon);
    for (int j = 0; j <= steps; j++) { V3 p = {ct[j] * sl, stt[j], ct[j] * cl}; if (df) p = df(p); pts[j] = PP(p, R); }
    out.poly(pts, steps + 1);
  }
  for (int l = 0; l < nLat; l++) {
    float y = sinf(lats[l]), r = cosf(lats[l]);
    for (int j = 0; j <= steps; j++) { float ph = j * TAU_F / steps + S.spin; V3 p = {r * cosf(ph), y, r * sinf(ph)}; if (df) p = df(p); pts[j] = PP(p, R); }
    out.poly(pts, steps + 1);
  }
}

// ---------- the face ----------
struct FaceOpt { float R; RGB c; float eyeLon, eyeLat, er, eh, mouthLat; bool outline; };
static P2 surf(const FaceOpt& o, float lon, float lat, float r = 1.03f) { return PP({sinf(lon) * cosf(lat) * r, sinf(lat) * r, cosf(lon) * cosf(lat) * r}, o.R); }

static void face(const FaceOpt& o) {
  for (int s = -1; s <= 1; s += 2) {
    P2 e = surf(o, s * o.eyeLon, o.eyeLat);
    if (e.z < 0.05f) continue;
    float fz = constrain(e.z, 0.2f, 1.f), rx = o.er * e.k * (0.35f + 0.65f * fz), base = o.eh * e.k, A = 0.45f + 0.55f * fz;
    uint16_t C = col(o.c, A);
    float th = 1.6f * e.k;
    if (S.mood == SLEEP) fb.fillArc(e.x, e.y - base * 0.35f, rx * 1.15f + th, rx * 1.15f - th, 27, 153, C);
    else if (S.mood == HAPPY) fb.fillArc(e.x, e.y + base * 0.5f, rx * 1.15f + th, rx * 1.15f - th, 207, 333, C);
    else {
      float w = S.mood == WIDE ? 1.25f : 1.f, h = max(2.5f, base * 2 * w * (1 - S.blink)), ew = rx * 2 * w, rr = min(ew, h) / 2;
      int x0 = lroundf(e.x - ew / 2), y0 = lroundf(e.y - h / 2), W = max(2L, lroundf(ew)), H = max(2L, lroundf(h));
      if (o.outline) { fb.drawRoundRect(x0, y0, W, H, rr, C); fb.drawRoundRect(x0 + 1, y0 + 1, W - 2, H - 2, max(0.f, rr - 1), C); }
      else {
        fb.fillRoundRect(x0 - 3, y0 - 3, W + 6, H + 6, rr + 3, col(o.c, A * 0.28f));
        fb.fillRoundRect(x0, y0, W, H, rr, C);
        if (S.blink < 0.4f) fb.fillRoundRect(lroundf(e.x - ew * 0.22f), lroundf(e.y - h * 0.32f), max(1L, lroundf(ew * 0.44f)), max(1L, lroundf(h * 0.3f)), ew * 0.2f, col(WHITE, A * 0.75f));
      }
      if (S.mood == WIDE) fb.drawEllipse(e.x, e.y, ew * 0.95f, h * 0.75f, col(o.c, A * (0.35f + 0.25f * sinf(S.t * 10))));
    }
    float b0, b1;
    switch (S.mood) { case SLEEP: b0 = 0; b1 = -0.04f; break; case LOOK: b0 = -0.05f; b1 = 0.02f; break; case WIDE: b0 = 0.07f; b1 = 0.03f; break; default: b0 = 0.03f; b1 = 0.05f; }
    float bl = o.eyeLat + 0.3f;
    P2 a = surf(o, s * (o.eyeLon - 0.11f), bl + b0), b = surf(o, s * (o.eyeLon + 0.12f), bl + b1);
    if (a.z > 0 && b.z > 0) fb.drawWideLine(a.x, a.y, b.x, b.y, 1.3f * e.k, col(o.c, 0.85f * fz));
  }
  // mouth
  P2 pts[25]; int n = 0;
  if (S.mood == WIDE) {
    for (int i = 0; i <= 20; i++) { float th = i * TAU_F / 20, rr = 0.05f + 0.012f * sinf(S.t * 9); pts[n++] = surf(o, cosf(th) * rr, o.mouthLat - 0.02f + sinf(th) * rr * 1.2f); }
  } else {
    float half = S.mood == HAPPY ? 0.24f : S.mood == SLEEP ? 0.1f : 0.17f;
    for (int i = 0; i <= 24; i++) {
      float u = i / 12.f - 1, f;
      if (S.mood == LOOK) f = 0.026f * sinf(u * 9 + S.t * 15) * (1 - u * u) * (0.55f + 0.45f * sinf(S.t * 3.1f)) + 0.012f * u * u;
      else if (S.mood == HAPPY) f = 0.075f * u * u - 0.05f;
      else f = 0.018f * u * u;
      pts[n++] = surf(o, u * half, o.mouthLat + f);
    }
  }
  float rad = (S.mood == HAPPY ? 1.7f : 1.3f) * pts[n / 2].k;
  uint16_t mc = col(o.c, 0.9f);
  for (int i = 0; i < n - 1; i++) if (pts[i].z >= 0.05f && pts[i + 1].z >= 0.05f) fb.drawWideLine(pts[i].x, pts[i].y, pts[i + 1].x, pts[i + 1].y, rad, mc);
}

// ---------- stagecraft ----------
static void projector(RGB c, float R) {
  const int cx = 120, y = 214; bool idle = S.state == IDLE;
  float top = S.cy + R * 0.25f;
  for (int yy = int(top); yy < y; yy++) {
    float f = (yy - top) / (y - top), hw = R * 0.85f + (40 - R * 0.85f) * f;
    fb.drawFastHLine(cx - hw, yy, hw * 2, col(c, (idle ? 0.10f : 0.22f) * f));
  }
  uint16_t rc = col(c, idle ? 0.45f : 0.9f);
  fb.drawEllipse(cx, y, 44, 8, rc); fb.drawEllipse(cx, y, 43, 7, rc);
  fb.drawEllipse(cx, y, 28, 4, col(c, idle ? 0.22f : 0.45f));
  for (int i = 0; i < 8; i++) {
    float a = S.t * (S.state == WORKING ? 2.4f : 0.6f) + i * TAU_F / 8;
    fb.fillRect(cx + cosf(a) * 44 - 1, y + sinf(a) * 8 - 1, 3, 3, col(c, (idle ? 0.35f : 0.9f) * (sinf(a) > 0 ? 1 : 0.35f)));
  }
  if (S.state == ALERT) for (int i = 0; i < 3; i++) { float p = fmodf(S.t * 1.1f + i / 3.f, 1); fb.drawEllipse(cx, y, 44 + p * 66, 8 + p * 12, col(AMB, (1 - p) * 0.8f)); }
  if (S.state == DONE && S.st < 1.1f) { float p = S.st / 1.1f; uint16_t g = col(GREEN, 1 - p); fb.drawEllipse(cx, y, 44 + p * 80, 8 + p * 16, g); fb.drawEllipse(cx, y, 45 + p * 80, 9 + p * 16, g); }
}

static void vz(float x, float y, float s, uint16_t c) {
  fb.drawWideLine(x - s, y - s, x + s, y - s, 1.f, c); fb.drawWideLine(x + s, y - s, x - s, y + s, 1.f, c); fb.drawWideLine(x - s, y + s, x + s, y + s, 1.f, c);
}
static void holoFX(RGB c, float R) {
  if (S.state == IDLE) for (int i = 0; i < 3; i++) { float p = fmodf(S.t * 0.4f + i / 3.f, 1); vz(S.cx + R * 0.75f + p * 26, S.cy - R * 0.7f - p * 44, 3 + p * 4, col(c, sinf(p * PI_F) * 0.9f)); }
  if (S.state == WORKING) for (int i = 0; i < 3; i++) for (int tr = 6; tr >= 0; tr--) {
    float a = S.t * 2.2f + i * TAU_F / 3 - tr * 0.07f;
    P2 p = proj(rotZ({cosf(a) * 1.45f, 0, sinf(a) * 1.45f}, 0.38f + i * 0.25f), R);
    float s = (tr ? 2.f : 3.2f) * p.k; fb.fillRect(p.x - s / 2, p.y - s / 2, ceilf(s), ceilf(s), col(tr ? c : WHITE, depthA(p.z) * (1 - tr / 7.f)));
  }
  if (S.state == ALERT) {
    float pop = constrain(S.st / 0.25f, 0.f, 1.f), sc = 0.6f + 0.4f * easeIO(pop) + 0.06f * sinf(S.t * 9);
    float px = S.cx + R * 1.15f, py = S.cy - R * 1.0f, fl = sinf(S.t * 47) > 0.93f ? 0.35f : 1.f;
    float x0 = S.cx + R * 0.6f, y0 = S.cy - R * 0.45f, x1 = px - 12 * sc, y1 = py + 14 * sc;
    for (int k = 0; k < 12; k++) if (k % 2 == 0) { float u = k / 12.f; fb.drawPixel(x0 + (x1 - x0) * u, y0 + (y1 - y0) * u, col(AMB, 0.5f * fl)); }
    int w = 34 * sc, h = 38 * sc, bx = px - w / 2, by = py - h / 2;
    fb.fillRoundRect(bx, by, w, h, 8 * sc, col(AMB, 0.18f * fl));
    for (int yy = by + 1; yy < by + h - 1; yy += 3) fb.drawFastHLine(bx + 2, yy, w - 4, col(AMB, 0.28f * fl));
    fb.drawRoundRect(bx, by, w, h, 8 * sc, col(AMB, fl)); fb.drawRoundRect(bx + 1, by + 1, w - 2, h - 2, 7 * sc, col(AMB, fl));
    fb.fillRoundRect(px - 3 * sc, py - 12 * sc, 6 * sc, 16 * sc, 3 * sc, col(AMB, fl)); fb.fillCircle(px, py + 11 * sc, 3.4f * sc, col(AMB, fl));
  }
  if (S.state == DONE) {
    float T = fmodf(S.st, 1.8f);
    for (int i = 0; i < 22; i++) {
      float ang = -PI_F / 2 + (hashf(i) - 0.5f) * 2.6f, v = 70 + hashf(i + 9) * 70;
      float x = S.cx + cosf(ang) * v * T, y = S.cy - 10 + sinf(ang) * v * T + 110 * T * T, a = constrain(1.4f - T, 0.f, 1.f);
      RGB cc = i % 3 == 0 ? c : i % 3 == 1 ? GREEN : WHITE; int s = 2 + i % 3;
      fb.fillRect(x - s / 2, y - s / 2, s, s, col(cc, a));
    }
  }
}

static void scanSweep() {
  uint16_t* buf = (uint16_t*)fb.getBuffer();
  for (int y = 0; y < 240; y += 3) for (int x = 0; x < 240; x++) {
    uint16_t p = sw(buf[y * 240 + x]); if (!p) continue;
    p = ((p >> 1) & 0x7BEF) + ((p >> 2) & 0x39E7);  // ~0.75
    buf[y * 240 + x] = sw(p);
  }
  int y0 = int(fmodf(S.t * 70, 300)) - 30;
  for (int y = max(0, y0); y < min(240, y0 + 6); y++) for (int x = 0; x < 240; x++) {
    uint16_t p = sw(buf[y * 240 + x]); if (!p) continue;
    int r = (p >> 11) & 31, g = (p >> 5) & 63, b = p & 31;
    r += (31 - r) * 0.3f; g += (63 - g) * 0.3f; b += (31 - b) * 0.3f;
    buf[y * 240 + x] = sw((r << 11) | (g << 5) | b);
  }
}
static void glitch() {
  if (S.state != ALERT || sinf(S.t * 23) < 0.82f) return;
  uint16_t* buf = (uint16_t*)fb.getBuffer(); int fr = int(S.t * 30);
  for (int i = 0; i < 3; i++) {
    int y = int(hashf(fr + i) * 200) + 20, h = 4 + i * 3, dx = int((hashf(i + fr) - 0.5f) * 14);
    for (int yy = y; yy < min(240, y + h); yy++) {
      uint16_t* row = buf + yy * 240;
      if (dx > 0) memmove(row + dx, row, (240 - dx) * 2); else if (dx < 0) memmove(row, row - dx, (240 + dx) * 2);
    }
  }
}
static void fadeFrame() {  // phosphor afterglow: keep ~62% of last frame
  uint16_t* buf = (uint16_t*)fb.getBuffer();
  for (int i = 0; i < 240 * 240; i++) { uint16_t p = sw(buf[i]); if (!p) continue; p = ((p >> 1) & 0x7BEF) + ((p >> 3) & 0x18E3); buf[i] = sw(p); }
}

// ---------- geometry ----------
static std::vector<V3> icoV; static std::vector<std::array<int, 3>> icoF; static std::vector<std::pair<int, int>> icoE;
static void buildIco() {
  float t = (1 + sqrtf(5)) / 2;
  float raw[12][3] = {{-1, t, 0}, {1, t, 0}, {-1, -t, 0}, {1, -t, 0}, {0, -1, t}, {0, 1, t}, {0, -1, -t}, {0, 1, -t}, {t, 0, -1}, {t, 0, 1}, {-t, 0, -1}, {-t, 0, 1}};
  auto norm = [](V3 p) { float l = sqrtf(p.x * p.x + p.y * p.y + p.z * p.z); return V3{p.x / l, p.y / l, p.z / l}; };
  for (auto& r : raw) icoV.push_back(norm({r[0], r[1], r[2]}));
  int f[20][3] = {{0, 11, 5}, {0, 5, 1}, {0, 1, 7}, {0, 7, 10}, {0, 10, 11}, {1, 5, 9}, {5, 11, 4}, {11, 10, 2}, {10, 7, 6}, {7, 1, 8}, {3, 9, 4}, {3, 4, 2}, {3, 2, 6}, {3, 6, 8}, {3, 8, 9}, {4, 9, 5}, {2, 4, 11}, {6, 2, 10}, {8, 6, 7}, {9, 8, 1}};
  std::map<uint32_t, int> cache;
  auto mid = [&](int a, int b) { uint32_t k = a < b ? (a << 16) | b : (b << 16) | a; auto it = cache.find(k); if (it != cache.end()) return it->second;
    V3 m = norm({(icoV[a].x + icoV[b].x) / 2, (icoV[a].y + icoV[b].y) / 2, (icoV[a].z + icoV[b].z) / 2}); icoV.push_back(m); return cache[k] = icoV.size() - 1; };
  for (auto& tri : f) { int a = tri[0], b = tri[1], c = tri[2], ab = mid(a, b), bc = mid(b, c), ca = mid(c, a);
    icoF.push_back({a, ab, ca}); icoF.push_back({b, bc, ab}); icoF.push_back({c, ca, bc}); icoF.push_back({ab, bc, ca}); }
  std::map<uint32_t, bool> seen;
  for (auto& tri : icoF) for (int e = 0; e < 3; e++) { int x = tri[e], y = tri[(e + 1) % 3]; uint32_t k = x < y ? (x << 16) | y : (y << 16) | x; if (!seen[k]) { seen[k] = true; icoE.push_back({min(x, y), max(x, y)}); } }
}
static V3 fib[420];
static void buildFib() { float ga = PI_F * (3 - sqrtf(5)); for (int i = 0; i < 420; i++) { float y = 1 - (i / 419.f) * 2, r = sqrtf(1 - y * y), th = ga * i; fib[i] = {cosf(th) * r, y, sinf(th) * r}; } }

// ---------- the five variants ----------
static void drawPrime() {
  const float R = 56; RGB T = S.tint;
  uint32_t a = micros();
  projector(T, R); prof[0] = micros() - a; a = micros();
  static const float lats[] = {-1.1f, -0.72f, -0.36f, 0, 0.36f, 0.72f, 1.1f};
  SA.clear(); latLong(SA, R, 7, lats, 7, 40); prof[1] = micros() - a; a = micros();
  strokeBins(SA, T, 1.3f); prof[2] = micros() - a; a = micros();
  fb.drawCircle(S.cx, S.cy, R * 1.02f, col(T, 0.35f));
  face({R, T, 0.36f, 0.12f, 7, 10, -0.3f, false}); prof[3] = micros() - a; a = micros();
  holoFX(T, R); prof[4] = micros() - a; a = micros();
  scanSweep(); glitch(); prof[5] = micros() - a;
}

static void drawFacet() {
  const float R = 58; RGB T = S.tint;
  projector(T, R);
  static P2 pts[64];
  for (size_t i = 0; i < icoV.size(); i++) {
    V3 v = icoV[i]; float r = 1;
    if (S.state == ALERT) r += 0.06f * sinf(S.t * 9 + v.y * 6);
    if (S.state == DONE) r += 0.45f * expf(-S.st * 3.2f) * sinf(min(S.st * 7, PI_F)) * (0.5f + hashf(i));
    if (S.state == IDLE) r += 0.02f * sinf(S.t * 1.3f);
    pts[i] = PP(rotX(rotY({v.x * r, v.y * r, v.z * r}, S.spin), 0.35f), R);
  }
  if (S.state == WORKING) for (int k = 0; k < 3; k++) {
    auto& f = icoF[int(hashf(floorf(S.t * 5) * 3 + k) * icoF.size()) % icoF.size()];
    float z = (pts[f[0]].z + pts[f[1]].z + pts[f[2]].z) / 3; if (z < 0) continue;
    fb.fillTriangle(pts[f[0]].x, pts[f[0]].y, pts[f[1]].x, pts[f[1]].y, pts[f[2]].x, pts[f[2]].y, col(T, 0.35f * depthA(z)));
  }
  SA.clear(); for (auto& e : icoE) SA.add(pts[e.first], pts[e.second], depthA((pts[e.first].z + pts[e.second].z) / 2));
  strokeBins(SA, T, 1.3f);
  for (size_t i = 0; i < icoV.size(); i++) { float a = depthA(pts[i].z); int s = lroundf(1.2f + 2.2f * a); fb.fillRect(pts[i].x - s / 2, pts[i].y - s / 2, s, s, col(WHITE, a)); }
  face({R, mixc(T, WHITE, 0.25f), 0.36f, 0.12f, 7, 10, -0.3f, false});
  holoFX(T, R);
}

static void drawSwarm() {
  const float R = 58; RGB T = S.tint, T2 = S.hasTint2 ? S.tint2 : mixc(T, WHITE, 0.45f);
  projector(T2, R);
  for (int i = 8; i >= 1; i--) fb.fillCircle(S.cx, S.cy, R * 1.25f * i / 8, col(T, 0.16f * (1 - i / 8.f)));
  RGB cols[3] = {T, mixc(T, T2, 0.5f), T2};
  static int16_t px[420], py[420]; static uint8_t pc[420], pa[420];
  float wave = 1 - fmodf(S.t * 1.4f, 1) * 2.4f;
  for (int i = 0; i < 420; i++) {
    V3 b = fib[i]; float x = b.x, y = b.y, z = b.z, r = 1;
    if (S.state == IDLE) { float br = 0.02f * sinf(S.t * 1.3f); y = y * (0.8f + br) - 0.12f; x *= 1.08f; z *= 1.08f; }
    if (S.state == ALERT) r += 0.03f * sinf(S.t * 22 + i) + 0.13f * expf(-(z - wave) * (z - wave) * 18);
    if (S.state == DONE) r += 0.7f * expf(-S.st * 2.6f) * fabsf(sinf(S.st * 6)) * (0.4f + hashf(i));
    float tw = S.state == WORKING ? b.y * sinf(S.t * 2) * 0.9f : 0;
    P2 q = PP(rotY({x * r, y * r, z * r}, S.spin + tw), R);
    px[i] = q.x; py[i] = q.y; pa[i] = min(3, int(depthA(q.z) * 4)); pc[i] = b.y > 0.33f ? 0 : b.y > -0.33f ? 1 : 2;
  }
  for (int ai = 0; ai < 4; ai++) {
    int s = lroundf(1.2f + ai * 0.7f); uint16_t cc[3]; for (int k = 0; k < 3; k++) cc[k] = col(cols[k], (ai + 0.6f) / 4);
    for (int i = 0; i < 420; i++) if (pa[i] == ai) fb.fillRect(px[i] - s / 2, py[i] - s / 2, s, s, cc[pc[i]]);
  }
  face({R, WHITE, 0.36f, 0.12f, 7, 10, -0.3f, false});
  holoFX(T, R);
}

struct Ring { float ax, ay; };
static Ring rings[3] = {{1.2f, 0}, {2.2f, 1.1f}, {3.2f, 2.2f}};
static float nearAngle(float cur, float tgt, float k) { float d = fmodf(fmodf(tgt - cur + PI_F / 2, PI_F) + PI_F, PI_F) - PI_F / 2; return cur + d * k; }
static void drawGyro() {
  const float R = 52, Rc = 0.62f; RGB T = S.tint, RC = mixc(T, WHITE, 0.55f);
  const float radii[3] = {1.12f, 1.32f, 1.52f};
  for (int i = 0; i < 3; i++) {
    Ring& rg = rings[i];
    if (S.state == WORKING) { rg.ax += S.dt * (1.7f + i * 0.8f); rg.ay += S.dt * (0.6f + i * 0.35f); }
    else {
      float tx = S.state == ALERT ? 0 : PI_F / 2, k = min(1.f, S.dt * 5);
      rg.ax = nearAngle(rg.ax, tx + (S.state == IDLE ? 0.12f * sinf(S.t * 0.8f + i) : 0), k);
      rg.ay = S.state == DONE ? rg.ay + S.dt * 4 : nearAngle(rg.ay, 0, k);
    }
  }
  projector(T, R * Rc);
  SB.clear(); SC.clear(); P2 beads[3]; float beadR[3];
  for (int i = 0; i < 3; i++) {
    float rad = radii[i] * (S.state == ALERT ? 1 + 0.05f * sinf(S.t * 8 - i) : 1);
    auto xf = [&](V3 p) { p = rotX(p, rings[i].ax); p = rotY(p, rings[i].ay); return proj(rotX(p, S.pitch * 0.4f), R); };
    P2 prev = xf({rad, 0, 0});
    for (int j = 1; j <= 56; j++) { float a = j * TAU_F / 56; P2 cur = xf({cosf(a) * rad, sinf(a) * rad, 0}); float z = (prev.z + cur.z) / 2; (z < 0 ? SB : SC).add(prev, cur, depthA(z / rad)); prev = cur; }
    float ba = S.t * (1.4f + i * 0.5f) + i * 2; beads[i] = xf({cosf(ba) * rad, sinf(ba) * rad, 0}); beadR[i] = rad;
  }
  RGB ringCol = (S.state == ALERT && sinf(S.t * 8) > 0) ? AMB : RC;
  strokeBins(SB, ringCol, 1.6f);
  static const float lats[] = {-0.8f, -0.4f, 0, 0.4f, 0.8f};
  SA.clear(); latLong(SA, R * Rc, 5, lats, 5, 32); strokeBins(SA, T, 1.2f);
  face({R * Rc, T, 0.38f, 0.12f, 9, 12, -0.32f, false});
  strokeBins(SC, ringCol, 1.6f);
  for (int i = 0; i < 3; i++) { int s = lroundf(4 * beads[i].k); fb.fillRect(beads[i].x - s / 2, beads[i].y - s / 2, s, s, col(WHITE, depthA(beads[i].z / beadR[i]))); }
  holoFX(T, R * 0.9f);
}

static void drawPhosphor() {
  const float R = 58; RGB T = S.tint;
  projector(T, R);
  SA.clear(); SB.clear(); P2 pts[45];
  for (int j = 1; j <= 13; j++) {
    float lat = -PI_F / 2 + j * PI_F / 14, y = sinf(lat), r = cosf(lat), dx = 0;
    if (S.state == IDLE) { y = y * (0.8f + 0.03f * sinf(S.t * 1.3f)) - 0.12f; r *= 1.08f; }
    if (S.state == WORKING) y += 0.06f * sinf(S.t * 6 - j * 0.7f);
    if (S.state == ALERT) dx = 0.035f * sinf(S.t * 40 + j * 1.7f);
    if (S.state == DONE) y += 0.16f * max(0.f, sinf(S.st * 6 - j * 0.45f)) * expf(-S.st * 1.1f);
    for (int k = 0; k <= 44; k++) { float ph = k * TAU_F / 44 + S.spin; pts[k] = PP({r * cosf(ph) + dx, y, r * sinf(ph)}, R); }
    SA.poly(pts, 45);
  }
  for (int i = 0; i < SA.n; i++) if (SA.s[i].bin >= 3 && SB.n < SB.cap) SB.s[SB.n++] = SA.s[i];
  strokeBins(SA, T, 1.5f, 0.22f);
  strokeBins(SB, hexRGB(0xE9FFF0), 0.8f, 0);
  face({R, mixc(T, WHITE, 0.4f), 0.36f, 0.12f, 7, 10, -0.3f, true});
  holoFX(T, R);
}

struct Variant { const char* id; uint32_t tint; int32_t tint2; bool persist; void (*draw)(); };
static const Variant VARIANTS[] = {
  {"prime", 0x7CFFCB, -1, false, drawPrime},
  {"facet", 0x8FD8FF, -1, false, drawFacet},
  {"swarm", 0xC9A8FF, 0x6FE7FF, false, drawSwarm},
  {"gyro", 0x7CFFCB, -1, false, drawGyro},
  {"phosphor", 0x6BFF8E, -1, true, drawPhosphor},
};
constexpr int NV = sizeof(VARIANTS) / sizeof(VARIANTS[0]);
static const char* STATE_NAMES[] = {"idle", "working", "alert", "done"};

// ---------- control state ----------
static int curV = 0;
static int fixedState = -1;  // -1 = cycle
static int32_t tintOverride = -1;
static float yawS = 0, pitchS = 0, rollS = 0, spinS = 0, stateSince = 0, fps = 0;
static uint32_t usDraw = 0, usPush = 0;
static int prevState = -1;

static void poseTarget(State st, float t, float& y, float& p, float& r) {
  if (st == IDLE) { y = 0.05f * sinf(t * 0.4f); p = 0.38f + 0.05f * sinf(t * 1.3f); r = 0.1f; }
  else if (st == WORKING) { float i = floorf(t / 0.75f); y = (hashf(i) - 0.5f) * 0.95f; p = 0.12f + (hashf(i + 7) - 0.5f) * 0.25f; r = 0; }
  else if (st == ALERT) { y = 0; p = -0.16f; r = 0.09f * sinf(t * 3); }
  else { y = 0; p = -0.06f; r = 0; }
}

static void reply() {
  Serial.printf("ok v=%s s=%s t=", VARIANTS[curV].id, fixedState < 0 ? "cycle" : STATE_NAMES[fixedState]);
  if (tintOverride < 0) Serial.print("own"); else Serial.printf("%06lX", (unsigned long)tintOverride);
  Serial.printf(" fps=%.1f draw_us=%lu push_us=%lu prof=%lu,%lu,%lu,%lu,%lu,%lu segs=%d\n", fps, (unsigned long)usDraw, (unsigned long)usPush, prof[0],prof[1],prof[2],prof[3],prof[4],prof[5], SA.n);
}
static void handleLine(String ln) {
  ln.trim(); if (!ln.length()) return;
  char cmd = ln[0]; String arg = ln.substring(1); arg.trim(); arg.toLowerCase();
  if (cmd == 'v') { for (int i = 0; i < NV; i++) if (arg == VARIANTS[i].id) { curV = i; fb.fillScreen(TFT_BLACK); return reply(); } Serial.println("err unknown variant"); }
  else if (cmd == 's') {
    if (arg == "cycle") { fixedState = -1; return reply(); }
    for (int i = 0; i < 4; i++) if (arg == STATE_NAMES[i]) { fixedState = i; return reply(); }
    Serial.println("err unknown state");
  } else if (cmd == 't') {
    if (arg == "own") { tintOverride = -1; return reply(); }
    if (arg.startsWith("#")) arg = arg.substring(1);
    if (arg.length() == 6) { tintOverride = strtol(arg.c_str(), nullptr, 16); return reply(); }
    Serial.println("err bad colour");
  } else if (cmd == '?') reply();
  else Serial.println("err unknown command");
}
static void pollSerial() {
  static String buf;
  while (Serial.available()) { char ch = Serial.read(); if (ch == '\n' || ch == '\r') { if (buf.length()) handleLine(buf); buf = ""; } else if (buf.length() < 64) buf += ch; }
}

void setup() {
  Serial.begin(115200);
  pinMode(PIN_BL, OUTPUT); digitalWrite(PIN_BL, HIGH);
  tft.init(); tft.setRotation(PRISM_MIRROR ? 4 : 0); tft.fillScreen(TFT_BLACK);
  fb.setColorDepth(16);
  fb.setPsram(false);
  if (!fb.createSprite(240, 240)) { fb.setPsram(true); fb.createSprite(240, 240); Serial.println("note: framebuffer in PSRAM"); }
  FB = (uint16_t*)fb.getBuffer();
  Serial.printf("fb=%p internal_free=%u largest_internal=%u psram=%d\n", FB, (unsigned)heap_caps_get_free_size(MALLOC_CAP_INTERNAL), (unsigned)heap_caps_get_largest_free_block(MALLOC_CAP_INTERNAL), (int)esp_ptr_external_ram(FB));
  buildIco(); buildFib();
  Serial.println("desk-buddy vec ready");
  reply();
}

void loop() {
  pollSerial();
  static uint32_t lastMs = millis(), fpsT0 = millis(); static int frames = 0;
  uint32_t now = millis();
  float t = now / 1000.f, dt = constrain((now - lastMs) / 1000.f, 0.f, 0.05f); lastMs = now;
  State st = fixedState < 0 ? State(int(t / 4.2f) % 4) : State(fixedState);
  if (st != prevState) { prevState = st; stateSince = t; }
  float sst = t - stateSince;

  S.t = t; S.st = sst; S.dt = dt; S.state = st;
  S.mood = st == IDLE ? SLEEP : st == WORKING ? LOOK : st == ALERT ? WIDE : HAPPY;
  float bp = fmodf(t, 3.6f);
  S.blink = ((st == WORKING || st == ALERT) && bp < 0.14f) ? 1 - fabsf(bp - 0.07f) / 0.07f : 0;
  S.bob = st == IDLE ? sinf(t * 1.2f) * 3 : st == WORKING ? sinf(t * 6) : st == ALERT ? -fabsf(sinf(t * 5.5f)) * 10 : -fabsf(sinf(t * 3.2f)) * 6;
  S.sq = st == ALERT ? 0.07f * (1 - min(1.f, fabsf(S.bob) / 3)) : st == IDLE ? 0.015f * sinf(t * 1.3f) : 0;
  float ty, tp, tr; poseTarget(st, t, ty, tp, tr);
  float L = 1 - expf(-dt * (st == WORKING ? 14 : 6));
  yawS += (ty - yawS) * L; pitchS += (tp - pitchS) * L; rollS += (tr - rollS) * L;
  float spd = st == IDLE ? 0.18f : st == WORKING ? 1.9f : st == ALERT ? 1.4f * expf(-sst * 5) : 0.8f;
  spinS += spd * dt;
  float flip = (st == DONE && sst < 0.95f) ? easeIO(sst / 0.95f) * TAU_F : 0;
  S.cx = 120; S.cy = 108 + S.bob; S.spin = spinS; S.yaw = yawS + flip; S.pitch = pitchS; S.roll = rollS;
  const Variant& V = VARIANTS[curV];
  updatePose();
  S.tint = hexRGB(tintOverride >= 0 ? tintOverride : V.tint);
  S.hasTint2 = tintOverride < 0 && V.tint2 >= 0; if (S.hasTint2) S.tint2 = hexRGB(V.tint2);

  uint32_t u0 = micros();
  if (V.persist) fadeFrame(); else fb.fillScreen(TFT_BLACK);
  V.draw();
  uint32_t u1 = micros();
  fb.pushSprite(0, 0);
  uint32_t u2 = micros(); usDraw = u1 - u0; usPush = u2 - u1;

  frames++;
  if (now - fpsT0 >= 2000) { fps = frames * 1000.f / (now - fpsT0); frames = 0; fpsT0 = now; }
}
