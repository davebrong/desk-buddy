// Desk Buddy — Lump frame player.
// Plays the hand-drawn frames baked from design/lump.html (tools/export_frames.py -> data/lump.bin).
// The previous Vec firmware lives in archive/vec_main.cpp.
//
// Moods loop (idle, working, alert, done, waiting, longhaul, night). Changing mood always plays
// the hand-drawn transition; events (fidgets, oops, hello, tap) play once and return to their mood.
//
// Serial protocol (115200, newline-terminated), answered with "ok ..." or "err ...":
//   s <mood>          head to a mood: idle working alert done waiting longhaul night
//   s demo            showcase every mood and event on a loop (the boot default)
//   e <event>         play an event: fidget_toss fidget_twitch fidget_bubble oops hello tap
//                     ("tap" picks a reaction for the current mood: giggle, startle, wobble, annoyed
//                      or knocked over; quick repeat taps annoy him, then knock him flat)
//   o <working> <waiting> <unseen>   corner extras: counts of sessions working, waiting on you,
//                     and finished-but-not-looked-at (drawn over the animation)
//   c <RRGGBB>        body colour (fades over ~1 s); default Tangerine FF7A45
//   i                 motion sensor stats: peak jolt in the last few seconds, taps seen, threshold
//   T <g>             tap sensitivity: jolt (in g) that counts as a tap (default 0.35)
//   ?                 report state
// The board prints "tap" when the glass is tapped (and giggles itself when that makes sense).
// On its own the device also: fidgets every minute or so while asleep, escalates "alert" to
// "waiting" after 45 s, slides "working" into "longhaul" after 10 min, and dims at night.

#include <Arduino.h>
#include <LovyanGFX.hpp>
#include <Wire.h>

constexpr int PIN_SCLK = 40, PIN_MOSI = 41, PIN_DC = 38, PIN_CS = 39, PIN_RST = 42, PIN_BL = 20;
constexpr bool PRISM_MIRROR = true;  // rotations 4–7 are mirrored for the prism
constexpr uint32_t ESCALATE_MS = 45000, LONGHAUL_MS = 10UL * 60 * 1000;
constexpr uint8_t BL_DAY = 255, BL_NIGHT = 70;

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

// ---------- baked animation data (embedded from data/lump.bin) ----------
extern const uint8_t lump_start[] asm("_binary_data_lump_bin_start");
extern const uint8_t lump_end[] asm("_binary_data_lump_bin_end");

constexpr int MAX_MOODS = 12, MAX_SEQ = 160;
enum Kind : uint8_t { LOOP = 0, TRANS = 1, EVENT = 2 };
struct Anim {
  uint16_t nFrames = 0; uint8_t fps = 24, nMoods = 0, nSeqs = 0;
  uint8_t base[MAX_MOODS]; String moodName[MAX_MOODS];
  uint8_t kind[MAX_SEQ], from[MAX_SEQ], to[MAX_SEQ]; uint16_t len[MAX_SEQ]; const uint8_t* seq[MAX_SEQ]; String name[MAX_SEQ];
  int8_t loopOf[MAX_MOODS], transOf[MAX_MOODS][MAX_MOODS];
  const uint8_t* offsets; const uint8_t* data;
  bool ok = false;
} A;

static inline uint16_t rd16(const uint8_t* p) { return p[0] | (p[1] << 8); }
static inline uint32_t rd32(const uint8_t* p) { return p[0] | (p[1] << 8) | (p[2] << 16) | (uint32_t(p[3]) << 24); }
static String rdStr(const uint8_t*& p) { int n = *p++; String s; s.reserve(n); for (int i = 0; i < n; i++) s += char(p[i]); p += n; return s; }

static bool parseAnim() {
  const uint8_t* p = lump_start;
  if (memcmp(p, "LUMP", 4) != 0) return false;
  p += 4;
  if (rd16(p) != 3) return false; p += 2;
  A.nFrames = rd16(p); p += 2;
  A.nMoods = *p++; A.fps = *p++;
  if (A.nMoods > MAX_MOODS) return false;
  for (int m = 0; m < A.nMoods; m++) { A.base[m] = *p++; A.moodName[m] = rdStr(p); A.loopOf[m] = -1; }
  memset(A.transOf, -1, sizeof(A.transOf));
  A.nSeqs = *p++;
  if (A.nSeqs > MAX_SEQ) return false;
  for (int i = 0; i < A.nSeqs; i++) {
    A.kind[i] = p[0]; A.from[i] = p[1]; A.to[i] = p[2]; p += 3;
    A.name[i] = rdStr(p);
    A.len[i] = rd16(p); p += 2; A.seq[i] = p; p += A.len[i] * 2;
    if (A.kind[i] == LOOP) A.loopOf[A.from[i]] = i;
    else if (A.kind[i] == TRANS) A.transOf[A.from[i]][A.to[i]] = i;
  }
  for (int m = 0; m < A.nMoods; m++) if (A.loopOf[m] < 0) return false;
  A.offsets = p; p += A.nFrames * 4;
  A.data = p;
  return A.ok = true;
}
static int moodIndex(const String& n) { for (int m = 0; m < A.nMoods; m++) if (A.moodName[m] == n) return m; return -1; }
static int eventIndex(const String& n) { for (int i = 0; i < A.nSeqs; i++) if (A.kind[i] == EVENT && A.name[i] == n) return i; return -1; }

// ---------- body colour ----------
// Frames are exported with his body in pure magenta (FF00FF), the only colour besides the cream
// line (FFF1DC) and black. Every pixel is some mix  alpha*magenta + beta*cream  (plus black), and
// since magenta has no green, beta = G/241 and alpha = R/255 - beta. Swapping magenta for the
// current body colour recolours the fill, its soft edges, the tongue and the blanket alike.
static float tintR = 255, tintG = 122, tintB = 69;        // current (fading) colour, Tangerine
static float goalR = 255, goalG = 122, goalB = 69;        // colour we're fading to
static inline uint16_t sw16(uint16_t v) { return (v >> 8) | (v << 8); }
static uint16_t recolor(uint16_t stored) {               // stored = byte-swapped RGB565
  uint16_t v = sw16(stored);
  int r = (v >> 11) << 3, g = ((v >> 5) & 63) << 2, b = (v & 31) << 3;
  if (r < 8 && g < 8 && b < 8) return stored;            // black stays black
  float beta = g / 241.f, alpha = r / 255.f - beta;
  if (alpha <= 0.02f) return stored;                     // pure line colour: unchanged
  int nr = min(255, int(alpha * tintR + beta * 255)), ng = min(255, int(alpha * tintG + beta * 241)), nb = min(255, int(alpha * tintB + beta * 220));
  return sw16(lgfx::color565(nr, ng, nb));
}

// RLE runs of [count][rgb565 big-endian]. The sprite buffer wants byte-swapped RGB565,
// which is big-endian byte order, so each run's two bytes go in as stored (then recoloured).
static void decodeFrame(uint16_t idx) {
  const uint8_t* p = A.data + rd32(A.offsets + 4 * idx);
  uint16_t* out = (uint16_t*)fb.getBuffer();
  int i = 0;
  uint16_t lastIn = 0xFFFF, lastOut = 0;
  while (i < 240 * 240) {
    int n = p[0]; uint16_t v = p[1] | (p[2] << 8); p += 3;
    if (v != lastIn) { lastIn = v; lastOut = recolor(v); }
    v = lastOut;
    if (i + n > 240 * 240) n = 240 * 240 - i;
    for (int k = 0; k < n; k++) out[i + k] = v;
    i += n;
  }
}

// ---------- player ----------
// Always inside one sequence: a mood's loop, a transition, or an event. Nothing ever cuts:
// a new target waits for the current transition/event to finish, then routes there.
static int target = 0, curMood = 0, curSeq = 0, pendingEvent = -1;
static uint32_t seqStart = 0, loopSince = 0, nextFidget = 0;
static int lastFrame = -1;
static float fps = 0;

static void startSeq(int s) { curSeq = s; seqStart = millis(); lastFrame = -1; }
static int nextHop(int a, int b) {
  if (A.transOf[a][b] >= 0) return A.transOf[a][b];
  if (A.base[a] != a) return A.transOf[a][A.base[a]];
  if (A.base[b] != b && A.transOf[a][A.base[b]] >= 0) return A.transOf[a][A.base[b]];
  return -1;
}
static void settle(int m) {  // arrived in mood m's loop
  curMood = m;
  if (target != m) {
    int h = nextHop(m, target);
    if (h >= 0) return startSeq(h);
    curMood = m = target;  // nothing drawn for this route: cut (shouldn't happen)
  }
  if (pendingEvent >= 0 && A.from[pendingEvent] == m) { int e = pendingEvent; pendingEvent = -1; return startSeq(e); }
  startSeq(A.loopOf[m]); loopSince = millis();
}
static void setTarget(int m) {
  target = m;
  if (A.kind[curSeq] == LOOP) settle(curMood);
}
// returns false if there's no such event
// Tapping the glass: the first tap gets a random reaction, a second quick tap annoys him,
// a third knocks him flat. Every mood has its own version (tap_<reaction>_<mood>).
static const char* pickTap() {
  static uint32_t lastTap = 0; static int streak = 0;
  uint32_t now = millis();
  streak = (lastTap && now - lastTap < 6000) ? streak + 1 : 1; lastTap = now;
  if (streak == 2) return "annoyed";
  if (streak >= 3) { streak = 0; return "knocked"; }
  int x = random(100);
  return x < 10 ? "knocked" : x < 50 ? "giggle" : x < 75 ? "startle" : "wobble";
}
static bool playEvent(String n) {
  if (n == "tap") n = String("tap_") + pickTap();
  int e = eventIndex(n);
  if (e < 0) {  // generic names like "tap" resolve per mood: tap_idle, tap_working, ...
    e = eventIndex(n + "_" + A.moodName[target]);
    if (e < 0) e = eventIndex(n + "_" + A.moodName[A.base[target]]);
  }
  if (e < 0) return false;
  pendingEvent = e;
  if (target != A.from[e]) setTarget(A.from[e]); else if (A.kind[curSeq] == LOOP) settle(curMood);
  return true;
}

// ---------- demo: a showcase of every mood and event ----------
struct Step { char type; const char* name; uint32_t ms; };
static const Step DEMO[] = {
  {'s', "idle", 6000}, {'e', "fidget_bubble", 6500}, {'e', "fidget_twitch", 3500},
  {'s', "working", 6000}, {'e', "oops", 7000}, {'e', "tap", 5000}, {'s', "longhaul", 6500},
  {'s', "alert", 6000}, {'s', "waiting", 9000}, {'s', "done", 5000}, {'e', "tap", 4500},
  {'s', "idle", 4000}, {'e', "fidget_toss", 5500}, {'e', "hello", 7000},
  {'s', "night", 9000}, {'s', "idle", 3000},
};
static bool demo = true;
static int demoStep = -1;
static uint32_t demoNext = 0;
static void runDemo(uint32_t now) {
  if (!demo || now < demoNext) return;
  demoStep = (demoStep + 1) % (sizeof(DEMO) / sizeof(DEMO[0]));
  const Step& s = DEMO[demoStep];
  if (s.type == 's') setTarget(moodIndex(s.name)); else playEvent(s.name);
  demoNext = now + s.ms;
}

// ---------- things he does on his own ----------
static void autoBehaviour(uint32_t now) {
  if (demo || A.kind[curSeq] != LOOP || target != curMood) return;
  const String& m = A.moodName[curMood];
  if (m == "idle" && pendingEvent < 0) {
    if (!nextFidget) nextFidget = now + 40000 + random(50000);
    if (now > nextFidget) {
      static const char* F[] = {"fidget_toss", "fidget_twitch", "fidget_bubble"};
      nextFidget = 0; playEvent(F[random(3)]);
    }
  } else nextFidget = 0;
  if (m == "alert" && now - loopSince > ESCALATE_MS) setTarget(moodIndex("waiting"));
  if (m == "working" && now - loopSince > LONGHAUL_MS) setTarget(moodIndex("longhaul"));
}

// ---------- backlight: dims gently at night ----------
static void backlight(uint32_t now) {
  static uint32_t last = 0; static float level = BL_DAY;
  if (now - last < 20) return; last = now;
  bool night = A.kind[curSeq] == LOOP && A.moodName[curMood] == "night";
  level += ((night ? BL_NIGHT : BL_DAY) - level) * 0.04f;
  analogWrite(PIN_BL, int(level));
}

// ---------- motion sensor (QMI8658 on I2C SDA 47 / SCL 48): tapping the glass ----------
// Sampled ~400x a second on core 0 so short taps aren't missed while core 1 is drawing.
// A tap is a sharp jolt: the acceleration jumps away from its slow-moving average.
constexpr int IMU_SDA = 47, IMU_SCL = 48;
static uint8_t imuAddr = 0;
static volatile bool tapPending = false;
static volatile float tapThreshold = 0.35f, peakJolt = 0;
static volatile uint32_t tapCount = 0;
static bool imuWrite(uint8_t reg, uint8_t v) { Wire.beginTransmission(imuAddr); Wire.write(reg); Wire.write(v); return Wire.endTransmission() == 0; }
static bool imuRead(uint8_t reg, uint8_t* buf, int n) {
  Wire.beginTransmission(imuAddr); Wire.write(reg); if (Wire.endTransmission(false) != 0) return false;
  if (Wire.requestFrom((int)imuAddr, n) != n) return false;
  for (int i = 0; i < n; i++) buf[i] = Wire.read();
  return true;
}
static bool imuInit() {
  Wire.begin(IMU_SDA, IMU_SCL, 400000);
  for (uint8_t a : {0x6B, 0x6A}) {
    imuAddr = a; uint8_t who = 0;
    if (imuRead(0x00, &who, 1) && who == 0x05) {
      imuWrite(0x60, 0xB0); delay(20);            // soft reset
      imuWrite(0x02, 0x40);                       // CTRL1: register auto-increment, little endian
      imuWrite(0x03, 0x15);                       // CTRL2: accel +-4 g, 250 Hz
      imuWrite(0x08, 0x01);                       // CTRL7: accelerometer on
      return true;
    }
  }
  imuAddr = 0; return false;
}
static void imuTask(void*) {
  float avg = 1.f; uint32_t lastTap = 0, peakAt = 0;
  for (;;) {
    uint8_t b[6];
    if (imuRead(0x35, b, 6)) {
      float x = int16_t(b[0] | b[1] << 8) / 8192.f, y = int16_t(b[2] | b[3] << 8) / 8192.f, z = int16_t(b[4] | b[5] << 8) / 8192.f;
      float mag = sqrtf(x * x + y * y + z * z), jolt = fabsf(mag - avg);
      avg += (mag - avg) * 0.02f;
      uint32_t now = millis();
      if (jolt > peakJolt || now - peakAt > 4000) { peakJolt = jolt; peakAt = now; }
      if (jolt > tapThreshold && now - lastTap > 600) { lastTap = now; tapCount++; tapPending = true; }
    }
    vTaskDelay(pdMS_TO_TICKS(2));
  }
}

// ---------- corner extras ----------
// Small marks drawn over the animation so nothing is hidden when several sessions are busy:
//   bottom-left  three bouncing dots: something is working in the background
//   top-left     amber dots, one per session waiting on you (when more than he can show)
//   top-right    a twinkling sparkle: something finished that you haven't looked at yet
static int ovWorking = 0, ovWaiting = 0, ovUnseen = 0;
static uint16_t rgb(uint8_t r, uint8_t g, uint8_t b) { return lgfx::color565(r, g, b); }
static bool overlayVisible() {
  const String& m = A.moodName[target];
  bool workingMood = m == "working" || m == "longhaul", alertMood = m == "alert" || m == "waiting";
  return (ovWorking > 0 && !workingMood) || ovWaiting >= 2 || (ovWaiting >= 1 && !alertMood) || (ovUnseen > 0 && m != "done");
}
static void drawOverlay(uint32_t now) {
  const String& m = A.moodName[target];
  bool workingMood = m == "working" || m == "longhaul", alertMood = m == "alert" || m == "waiting";
  float t = now / 1000.f;
  if (ovWorking > 0 && !workingMood) {
    for (int i = 0; i < 3; i++) {
      float b = max(0.f, sinf(t * 6 - i * 0.9f));
      fb.fillCircle(16 + i * 10, 226 - b * 4, 2.5f, rgb(255 * (0.45f + 0.55f * b), 241 * (0.45f + 0.55f * b), 220 * (0.45f + 0.55f * b)));
    }
  }
  if (ovWaiting >= 2 || (ovWaiting >= 1 && !alertMood)) {
    float pulse = 0.6f + 0.4f * sinf(t * 5);
    for (int i = 0; i < min(ovWaiting, 5); i++) fb.fillCircle(14 + i * 11, 14, 3.5f, rgb(255 * pulse, 181 * pulse, 71 * pulse));
  }
  if (ovUnseen > 0 && m != "done") {
    for (int k = 0; k < min(ovUnseen, 2); k++) {
      float s = (k ? 4.f : 6.f) * (0.75f + 0.25f * sinf(t * 4 + k * 2));
      int x = 224 - k * 13, y = 16 + k * 6;
      uint16_t c = rgb(200, 255, 210);
      fb.fillTriangle(x, y - s, x + s * 0.3f, y, x - s * 0.3f, y, c); fb.fillTriangle(x, y + s, x + s * 0.3f, y, x - s * 0.3f, y, c);
      fb.fillTriangle(x - s, y, x, y - s * 0.3f, x, y + s * 0.3f, c); fb.fillTriangle(x + s, y, x, y - s * 0.3f, x, y + s * 0.3f, c);
    }
  }
}

// ---------- serial ----------
static void reply() {
  Serial.printf("ok mode=%s target=%s playing=%s(%s) fps=%.1f frames=%u seqs=%u\n",
    demo ? "demo" : "direct", A.moodName[target].c_str(), A.name[curSeq].c_str(),
    A.kind[curSeq] == LOOP ? "loop" : A.kind[curSeq] == TRANS ? "transition" : "event", fps, A.nFrames, A.nSeqs);
}
static void handleLine(String ln) {
  ln.trim(); if (!ln.length()) return;
  char cmd = ln[0]; String arg = ln.substring(1); arg.trim(); arg.toLowerCase();
  if (cmd == 's') {
    if (arg == "demo" || arg == "cycle") { demo = true; demoNext = 0; return reply(); }
    int m = moodIndex(arg);
    if (m < 0) { Serial.println("err unknown mood"); return; }
    // repeating the current mood is a no-op, so the bridge can re-assert its mood without
    // restarting the loop or undoing an escalation (alert -> waiting) the board made itself
    // (night is chosen by the bridge, so "s idle" while at night does wake him)
    bool selfEscalated = A.moodName[target] == "waiting" || A.moodName[target] == "longhaul";
    bool same = !demo && (m == target || (selfEscalated && A.base[target] == m));
    demo = false;
    if (!same) setTarget(m);
    return reply();
  }
  if (cmd == 'e') {
    demo = false;
    if (!playEvent(arg)) { Serial.println("err unknown event"); return; }
    return reply();
  }
  if (cmd == 'o') {
    int w = 0, a = 0, u = 0;
    sscanf(arg.c_str(), "%d %d %d", &w, &a, &u);
    ovWorking = max(0, w); ovWaiting = max(0, a); ovUnseen = max(0, u); lastFrame = -1;
    Serial.printf("ok overlay working=%d waiting=%d unseen=%d\n", ovWorking, ovWaiting, ovUnseen);
    return;
  }
  if (cmd == 'i') { Serial.printf("ok imu=%s peak=%.2fg taps=%lu threshold=%.2fg\n", imuAddr ? "on" : "missing", (float)peakJolt, (unsigned long)tapCount, (float)tapThreshold); return; }
  if (cmd == 'T') { float t = arg.toFloat(); if (t > 0.05f && t < 4) tapThreshold = t; Serial.printf("ok threshold=%.2fg\n", (float)tapThreshold); return; }
  if (cmd == 'c') {
    if (arg.startsWith("#")) arg = arg.substring(1);
    if (arg.length() != 6) { Serial.println("err bad colour"); return; }
    long c = strtol(arg.c_str(), nullptr, 16);
    goalR = (c >> 16) & 255; goalG = (c >> 8) & 255; goalB = c & 255;
    Serial.printf("ok colour %06lX\n", c);
    return;
  }
  if (cmd == '?') return reply();
  Serial.println("err unknown command");
}
static void pollSerial() {
  static String buf;
  while (Serial.available()) { char ch = Serial.read(); if (ch == '\n' || ch == '\r') { if (buf.length()) handleLine(buf); buf = ""; } else if (buf.length() < 64) buf += ch; }
}

void setup() {
  Serial.begin(115200);
  pinMode(PIN_BL, OUTPUT); analogWrite(PIN_BL, BL_DAY);
  tft.init(); tft.setRotation(PRISM_MIRROR ? 4 : 0); tft.fillScreen(TFT_BLACK);
  fb.setColorDepth(16); fb.setPsram(false);
  if (!fb.createSprite(240, 240)) { fb.setPsram(true); fb.createSprite(240, 240); }
  if (!parseAnim()) {
    Serial.println("err lump.bin missing or bad");
    tft.setTextColor(TFT_RED); tft.drawString("lump.bin bad", 60, 110);
    return;
  }
  Serial.printf("desk-buddy lump ready: %u moods, %u sequences, %u frames, %u bytes\n", A.nMoods, A.nSeqs, A.nFrames, unsigned(lump_end - lump_start));
  randomSeed(esp_random());
  if (imuInit()) { xTaskCreatePinnedToCore(imuTask, "imu", 4096, nullptr, 2, nullptr, 0); Serial.println("imu ready"); }
  else Serial.println("imu not found");
  curMood = target = 0; startSeq(A.loopOf[0]); loopSince = millis();
}

void loop() {
  pollSerial();
  if (!A.ok) { delay(100); return; }
  uint32_t now = millis();
  if (tapPending) {
    tapPending = false;
    Serial.println("tap");                         // the bridge decides what a tap means for Claude
    playEvent("tap");                              // he reacts in every mood (the bridge may then move him on)
  }
  runDemo(now);
  autoBehaviour(now);
  backlight(now);

  uint32_t tick = (now - seqStart) * A.fps / 1000;
  if (A.kind[curSeq] != LOOP && tick >= A.len[curSeq]) { settle(A.to[curSeq]); tick = 0; }
  uint32_t pos = A.kind[curSeq] == LOOP ? tick % A.len[curSeq] : min<uint32_t>(tick, A.len[curSeq] - 1);
  uint16_t idx = rd16(A.seq[curSeq] + 2 * pos);
  // fade the body colour toward its goal (~1 s), redrawing as it changes
  static uint32_t lastFade = 0;
  if (now - lastFade >= 30 && (fabsf(goalR - tintR) + fabsf(goalG - tintG) + fabsf(goalB - tintB)) > 1.5f) {
    lastFade = now;
    tintR += (goalR - tintR) * 0.12f; tintG += (goalG - tintG) * 0.12f; tintB += (goalB - tintB) * 0.12f;
    lastFrame = -1;
  }
  static uint32_t lastOverlayTick = 0;
  uint32_t overlayTick = now / 100;              // corner extras animate at ~10 fps
  bool ov = overlayVisible();
  if (idx != lastFrame || (ov && overlayTick != lastOverlayTick)) {
    lastOverlayTick = overlayTick;
    decodeFrame(idx);
    if (ov) drawOverlay(now);
    fb.pushSprite(0, 0);
    lastFrame = idx;
    static uint32_t t0 = now; static int n = 0;
    if (++n, now - t0 >= 2000) { fps = n * 1000.f / (now - t0); n = 0; t0 = now; }
  }
  delay(2);
}
