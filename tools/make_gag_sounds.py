#!/usr/bin/env python3
"""Sound effects for Lump's tap gags (the little animations he plays when you tap the glass).

  python3 tools/make_gag_sounds.py                # writes ~/.desk-buddy/sounds/tap_<gag>.wav
  python3 tools/make_gag_sounds.py --out DIR      # somewhere else (the review page uses this)
  python3 tools/make_gag_sounds.py --only shock   # just one gag

Every sound runs on the same clock as the drawings: 24 frames a second, frame 0 being the first
drawing of the gag itself (TAP_REACT in design/lump.html, after his way in from the current mood).
If a gag's timing changes there, its cue frames here need to follow. The cues are also written to
tap_cues.json so the review page can mark them on the waveform.

His voice is the same ocarina as tools/make_sounds.py (pitch read from sounds.json); everything
else (zaps, thuds, boings, squeaks...) is synthesized here. Pure standard library, no numpy.
Low thumps are kept above ~80 Hz with plenty of mid "knock", so they still read on laptop speakers.
"""
import argparse, json, math, os, random, struct, sys, wave

SR = 44100
FPS = 24
TAU = 2 * math.pi
HOME = os.path.expanduser("~/.desk-buddy")
SETTINGS = os.path.join(HOME, "sounds.json")
PEAK = 0.45          # same ceiling as his voice, so the volume slider's 2x stays clean
LOUD = 0.16          # every gag's loudest moment is brought to this level, so they sit level with each other
rng = random.Random(11)
BASE = 530           # his voice's base note (overridden from sounds.json)


# ---------- building blocks ----------
def line(points):
    """Piecewise-linear function of time through (t, value) points."""
    pts = sorted(points)
    def f(t):
        if t <= pts[0][0]: return pts[0][1]
        for (t0, v0), (t1, v1) in zip(pts, pts[1:]):
            if t <= t1: return v0 + (v1 - v0) * (t - t0) / ((t1 - t0) or 1)
        return pts[-1][1]
    return f


def gline(points):
    """Like line(), but glides evenly in pitch (log-linear)."""
    f = line([(t, math.log(v)) for t, v in points])
    return lambda t: math.exp(f(t))


def fn(v):
    return v if callable(v) else (lambda t: v)


def decay(tau, atk=.003):
    return lambda t: t / atk if t < atk else math.exp(-(t - atk) / tau)


def fade(d, a=.02, r=.04):
    return line([(0, 0), (a, 1), (max(a, d - r), 1), (d, 0)])


def _blep(p, dt):
    if p < dt: x = p / dt; return x + x - x * x - 1
    if p > 1 - dt: x = (p - 1) / dt; return x * x + x + x + 1
    return 0.0


def osc(d, freq, amp=1.0, wave="sine"):
    f, a = fn(freq), fn(amp)
    n = int(d * SR); out = [0.0] * n; p = 0.0 if wave == "sine" else rng.random()
    for i in range(n):
        t = i / SR; dt = f(t) / SR
        p += dt; p -= math.floor(p)
        if wave == "sine": v = math.sin(TAU * p)
        elif wave == "tri": v = 4 * abs(p - .5) - 1
        elif wave == "saw": v = 2 * p - 1 - _blep(p, dt)
        else: v = (1.0 if p < .5 else -1.0) + _blep(p, dt) - _blep((p + .5) % 1, dt)
        out[i] = v * a(t)
    return out


class Biquad:
    """RBJ cookbook filters (the same shapes Web Audio uses)."""
    def __init__(self, kind, f, q=.707):
        self.kind = kind; self.x1 = self.x2 = self.y1 = self.y2 = 0.0; self.set(f, q)

    def set(self, f, q):
        w = TAU * min(max(f, 20), SR * .45) / SR; c = math.cos(w); al = math.sin(w) / (2 * q)
        if self.kind == "lp": b0 = (1 - c) / 2; b1 = 1 - c; b2 = b0
        elif self.kind == "hp": b0 = (1 + c) / 2; b1 = -(1 + c); b2 = b0
        else: b0 = al; b1 = 0.0; b2 = -al
        a0 = 1 + al
        self.b0, self.b1, self.b2, self.a1, self.a2 = b0 / a0, b1 / a0, b2 / a0, -2 * c / a0, (1 - al) / a0

    def __call__(self, x):
        y = self.b0 * x + self.b1 * self.x1 + self.b2 * self.x2 - self.a1 * self.y1 - self.a2 * self.y2
        self.x2, self.x1, self.y2, self.y1 = self.x1, x, self.y1, y
        return y


def filt(sig, kind, f, q=.707):
    sweep = callable(f)
    bq = Biquad(kind, f(0) if sweep else f, q); out = [0.0] * len(sig)
    for i, x in enumerate(sig):
        if sweep and i % 32 == 0: bq.set(f(i / SR), q)
        out[i] = bq(x)
    return out


def noise(d):
    return [rng.uniform(-1, 1) for _ in range(int(d * SR))]


def shape(sig, amp):
    a = fn(amp); return [v * a(i / SR) for i, v in enumerate(sig)]


def g(sig, k):
    return [v * k for v in sig]


def add(*sigs):
    out = [0.0] * max(len(s) for s in sigs)
    for s in sigs:
        for i, v in enumerate(s): out[i] += v
    return out


def at(sig, t):
    """Delay a sound by t seconds (for building composites)."""
    return [0.0] * int(t * SR) + sig


def loudness(sig, win=.05):
    """RMS over the loudest stretch of `win` seconds: a fair stand-in for how loud it sounds."""
    w = max(1, int(win * SR)); acc = 0.0; best = 0.0; sq = [v * v for v in sig]
    for i, v in enumerate(sq):
        acc += v
        if i >= w: acc -= sq[i - w]
        if acc > best: best = acc
    return math.sqrt(best / w)


def lvl(sig, v=1.0):
    """Scale so the loudest moment sits at a standard level times v (so 'vol' means the same everywhere)."""
    L = loudness(sig)
    return g(sig, .3 * v / L) if L else sig


class Track:
    def __init__(self):
        self.b = []; self.cues = []; self.level = 1.0

    def add(self, tick, sig, vol=1.0, label=None, dt=0.0):
        """Place a sound at a drawing frame (plus dt seconds), at a standard loudness times vol."""
        sig = lvl(sig, vol)
        n = min(len(sig), int(.008 * SR))                      # never stop dead (that clicks)
        for k in range(n): sig[len(sig) - 1 - k] *= k / n
        i0 = max(0, int(round((tick / FPS + dt) * SR)))
        if i0 + len(sig) > len(self.b): self.b.extend([0.0] * (i0 + len(sig) - len(self.b)))
        b = self.b
        for i, v in enumerate(sig): b[i0 + i] += v
        if label: self.cues.append([round(tick + dt * FPS, 2), label])


# ---------- his voice: the ocarina ----------
def oc(d, pitch, atk=.04, vib=0.0, vibhz=6.0, trem=0.0, tremhz=24.0, breath=.06, tail=.3, scoop=True):
    """One ocarina note. pitch: a ratio of his base note, or [(0..1 through the note, ratio), ...]
    for a contour. vib is vibrato depth in semitones; trem is a flutter in loudness (0..1)."""
    contour = isinstance(pitch, list)
    pf = gline(pitch) if contour else (lambda x: pitch)
    p0 = pitch[0][1] if contour else pitch
    n = max(2, int(d * SR)); out = [0.0] * n
    bp = Biquad("bp", BASE * p0 * 2, 2)
    sT = min(.05, d * .5); ph1 = ph2 = 0.0
    for i in range(n):
        x, t = i / (n - 1), i / SR
        f = BASE * pf(x)
        if scoop and t < sT: f *= .92 ** (1 - t / sT)            # the little scoop up into the note
        if vib: f *= 2 ** (vib / 12 * math.sin(TAU * vibhz * t) * min(1, t / .12))
        ph1 += TAU * f / SR; ph2 += 2 * TAU * f / SR
        e = t / atk if t < atk else .001 ** ((x - (1 - tail)) / tail) if x > 1 - tail else 1
        if trem: e *= 1 - trem * (.5 + .5 * math.sin(TAU * tremhz * t))
        out[i] = e * (math.sin(ph1) + .12 * math.sin(ph2) + breath * bp(rng.uniform(-1, 1)))
    return out


def huff(d, f=1000, q=1.0, a=.01):
    """A puff of breath (for sneezes, hmphs, coughs)."""
    return shape(filt(noise(d), "bp", f, q), line([(0, 0), (a, 1), (d, 0)]))


def hmph():
    return add(lvl(oc(.07, 1.0, atk=.01, breath=.3), .7),
               at(lvl(oc(.2, [(0, .94), (1, .79)], atk=.01, breath=.3), .8), .08),
               at(lvl(huff(.16, 900), .45), .08))


def brrr(d=.4, p=1.12):
    """Shaking it off: a lip-trill flutter."""
    return oc(d, [(0, p), (1, p * .94)], trem=.9, tremhz=26, vib=.4, vibhz=13, breath=.2, atk=.02)


def heh(p=1.26):
    return add(lvl(oc(.09, p, atk=.01), .8), at(lvl(oc(.16, [(0, p * .9), (1, p * .84)], atk=.01), .7), .13))


# ---------- sound effects ----------
def thud(f0=220, f1=80, d=.35, knock=.7):
    body = osc(d, gline([(0, f0), (.1, f1), (d, f1 * .9)]), decay(.08, .002))
    thump = shape(filt(noise(.08), "lp", 700), decay(.018, .001))
    return add(lvl(body, 1), lvl(thump, knock))


def pat(f=300):
    """One soft little footstep."""
    return add(lvl(osc(.06, gline([(0, f), (.05, f * .6)]), decay(.018, .002)), 1),
               lvl(shape(filt(noise(.03), "lp", 1800), decay(.006, .001)), .5))


def clang(f=520, d=1.6):
    parts = [(1, 1, .45), (1.007, .8, .5), (2.76, .55, .3), (5.4, .35, .18), (8.93, .22, .1)]   # (ratio, amp, ring time)
    ring = add(*[osc(d, f * r, (lambda a, tau: lambda t: a * decay(tau, .001)(t))(a, tau)) for r, a, tau in parts])
    hit = shape(filt(noise(.04), "hp", 2500), decay(.008, .0005))
    return add(lvl(ring, 1), lvl(hit, .8))


def boing(d=.6, f=200, rate=13):
    fr = lambda t: f * (1 + .32 * math.exp(-t * 4.5) * math.sin(TAU * rate * t)) * (1 + .5 * math.exp(-t * 25))
    body = osc(d, fr, decay(.2, .003), "tri")
    twang = filt(osc(d, fr, decay(.15, .003), "saw"), "bp", lambda t: 900 * (1 + .6 * math.sin(TAU * rate * t) * math.exp(-3 * t)), 4)
    return add(lvl(body, 1), lvl(twang, .6))


def whistle(d, f0, f1, vib=0.0, air=.35):
    fr = gline([(0, f0), (d, f1)])
    env = fade(d, .05, .06)
    tone = osc(d, lambda t: fr(t) * (1 + vib * math.sin(TAU * 6 * t)), env)
    breath = shape(filt(noise(d), "bp", fr, 8), env)
    return add(lvl(tone, 1), lvl(breath, air))


def whoosh(d, f0, f1, q=1.2):
    fr = gline([(0, f0), (d, f1)])
    return shape(filt(noise(d), "bp", fr, q), lambda t: math.sin(math.pi * min(1, t / d)) ** 1.5)


def zip_(d=.14, f0=500, f1=2200):
    """The cartoon 'zip!' of someone dashing off."""
    return add(lvl(osc(d, gline([(0, f0), (d, f1)]), fade(d, .01, .05)), 1), lvl(whoosh(d, 800, 4000, 1.5), .6))


def squeak(d, f0, f1, stick=36):
    """Rubber dragged on glass: a squeal that judders (stick-slip)."""
    fr = gline([(0, f0), (d, f1)])
    env = fade(d, .015, .04)
    jud = lambda t: .45 + .55 * abs(math.sin(math.pi * stick * t + .7 * math.sin(TAU * 5 * t)))
    s = osc(d, lambda t: fr(t) * (1 + .025 * math.sin(TAU * stick * t)), lambda t: env(t) * jud(t), "tri")
    return filt(s, "lp", 4000)


def bloop(d, f0, f1):
    return osc(d, gline([(0, f0), (d, f1)]), lambda t: decay(d / 2.2, .004)(t) * (1 if t < d - .01 else (d - t) / .01))


def splat(d=.12):
    wet = shape(filt(noise(d), "bp", lambda t: 1600 - 6000 * t, 1.1), decay(.03, .002))
    return add(lvl(wet, 1), lvl(bloop(.08, 260, 120), .6))


def pfft(d=.14, f=2600):
    return shape(filt(noise(d), "bp", f, 1.6), line([(0, 0), (.008, 1), (d, 0)]))


def ping(f, d=.6, tau=.18):
    return add(osc(d, f, decay(tau, .002)), g(osc(d, f * 2.01, decay(tau * .4, .002)), .25))


def sparkle(d, n, lo=2000, hi=4200, trend=0):
    """Twinkly pings scattered over d seconds; trend -1/+1 makes them fall or rise."""
    out = [0.0]
    for k in range(n):
        u = (k + rng.random() * .6) / n
        f = (lo + (hi - lo) * (1 - u if trend < 0 else u)) if trend else rng.uniform(lo, hi)
        out = add(out, at(g(ping(f * rng.uniform(.97, 1.03), .5, .12), rng.uniform(.5, 1)), u * d))
    return out


def tweet():
    """A little bird circling his head: twee-dee-dee."""
    out = [0.0]
    for k, (f0, f1) in enumerate([(2600, 3500), (3000, 3800), (2800, 3300)]):
        out = add(out, at(osc(.06, gline([(0, f0), (.04, f1), (.06, f0)]), fade(.06, .005, .02)), k * .075))
    return out


def poof(d=.3):
    return add(lvl(shape(filt(noise(d), "lp", lambda t: 1600 - 3000 * t), line([(0, 0), (.02, 1), (d, 0)])), 1),
               lvl(bloop(.12, 160, 320), .5))


def cough():
    return add(lvl(shape(filt(noise(.13), "bp", lambda t: 1300 - 4000 * t, 1.2), decay(.035, .004)), 1),
               lvl(oc(.08, .84, atk=.005, tail=.6, breath=.4), .55))


def sniff():
    return shape(filt(noise(.16), "bp", 3200, 1.5), lambda t: (t / .16) ** 1.5 if t < .15 else (.16 - t) / .01)


def crackle(d, rate=40, hp=1800):
    out = [0.0] * int(d * SR)
    for _ in range(int(d * rate)):
        i = rng.randrange(max(1, len(out) - 400)); L = rng.randrange(40, 260); a = rng.uniform(.3, 1)
        for k in range(L): out[i + k] += a * rng.uniform(-1, 1) * (1 - k / L)
    return filt(out, "hp", hp)


def engine(d, hz, amp, bright):
    """A little moped: buzzy saw with a putter, through a filter that opens as it gets closer."""
    hz, amp, bright = fn(hz), fn(amp), fn(bright)
    s = add(osc(d, hz, 1, "saw"), g(osc(d, lambda t: hz(t) / 2, 1, "square"), .5))
    s = shape(s, lambda t: amp(t) * (.75 + .25 * math.sin(TAU * hz(t) * .25 * t)))
    return filt(s, "lp", bright, 1.2)


def reed(d, hzs):
    """Accordion reeds: two saws a hair apart (the musette wobble) plus a hollow square."""
    env = line([(0, 0), (.015, 1), (max(.015, d - .04), .85), (d, 0)])
    parts = []
    for hz in hzs:
        parts += [osc(d, hz * 2 ** (7 / 1200), env, "saw"), osc(d, hz * 2 ** (-7 / 1200), env, "saw"), g(osc(d, hz, env, "square"), .4)]
    return filt(add(*parts), "lp", 2800, .8)


def kick():
    return add(lvl(osc(.22, gline([(0, 210), (.05, 80), (.22, 70)]), decay(.07, .002)), 1),
               lvl(shape(filt(noise(.01), "hp", 3000), decay(.002, .0005)), .4))


def hat(d=.05):
    return shape(filt(noise(d), "hp", 7000), decay(.012, .001))


def clap():
    out = [0.0]
    for k in range(3):
        out = add(out, at(shape(filt(noise(.03 if k < 2 else .12), "bp", 1300, 1.3), decay(.006 if k < 2 else .04, .001)), k * .011))
    return out


def bass(d, hz):
    return filt(osc(d, hz, line([(0, 0), (.005, 1), (d * .6, .7), (d, 0)]), "saw"), "lp", 900, 1.4)


def note(name):
    """'C5' -> Hz."""
    names = {"C": -9, "D": -7, "E": -5, "F": -4, "G": -2, "A": 0, "B": 2}
    n = names[name[0]] + (1 if "#" in name else -1 if name[1] == "b" else 0)
    return 440 * 2 ** (n / 12 + int(name[-1]) - 4)


def sing(name):
    """A pitch for his ocarina voice that's in tune with the band (his base note isn't quite a C)."""
    return note(name) / BASE


def strings(d, hzs, atk=.08, rel=.15, bright=2400):
    """A soft string-machine pad: detuned saws, gently filtered."""
    env = line([(0, 0), (atk, 1), (max(atk, d - rel), .85), (d, 0)])
    parts = [osc(d, hz * 2 ** (c / 1200), env, "saw") for hz in hzs for c in (-9, 9)]
    return filt(add(*parts), "lp", bright, .7)


def stab(hzs, d=.09):
    """A short muted chord chop (the disco rhythm guitar's 'chk')."""
    env = lambda t: decay(.025, .002)(t)
    return filt(add(*[osc(d, hz, env, "saw") for hz in hzs]), "bp", 1500, .9)


def crash(d=.9):
    return add(lvl(shape(filt(noise(d), "hp", 5500), decay(.3, .002)), 1), lvl(shape(filt(noise(d), "bp", 8000, 1), decay(.5, .002)), .5))


def riser(d):
    """A cymbal swell building into something."""
    return shape(filt(noise(d), "hp", gline([(0, 2500), (d, 9000)])), lambda t: (t / d) ** 2.2)


def ohat():
    return shape(filt(noise(.16), "hp", 6500), decay(.06, .002))


VOWEL = {"ah": [(820, 1.0), (1250, .55), (2700, .25)], "eh": [(560, 1.0), (1850, .6), (2650, .3)],
         "oo": [(380, 1.0), (850, .45), (2400, .12)]}


def morph(a, b, t0, t1):
    """Slide from one vowel to another between t0 and t1 seconds."""
    A, B = VOWEL[a], VOWEL[b]
    def f(t):
        u = 0 if t <= t0 else 1 if t >= t1 else (t - t0) / (t1 - t0)
        return [(fa * (fb / fa) ** u, ga + (gb - ga) * u) for (fa, ga), (fb, gb) in zip(A, B)]
    return f


def vox(d, f0, vowel, breath, amp, q=5):
    """A small, breathy, more human voice (for the sneeze): a buzzy source and a hiss of breath,
    shaped by the vowel's formants. vowel is a name, or a function of t (see morph)."""
    br, a = fn(breath), fn(amp)
    vf = vowel if callable(vowel) else (lambda t, v=VOWEL[vowel]: v)
    buzz, hiss = osc(d, f0, 1, "saw"), noise(d)
    src = [(1 - br(i / SR)) * v + br(i / SR) * 2.5 * h for i, (v, h) in enumerate(zip(buzz, hiss))]
    out = [0.0]
    for k in range(3):
        y = filt(src, "bp", lambda t, k=k: vf(t)[k][0], q)
        out = add(out, shape(y, lambda t, k=k: vf(t)[k][1]))
    return filt(shape(out, a), "lp", 6500)


def gasp(d, f0, f1, loud=1.0, breath=.55):
    """The sharp 'ah' breath in before a sneeze: voiced, breathy, rising, cut off at the top."""
    return vox(d, gline([(0, f0), (d, f1)]), "ah", breath, line([(0, 0), (d * .75, loud), (d - .02, loud), (d, 0)]))


def pizz(hz):
    """A plucked double-bass note (for sneaking about)."""
    return add(lvl(osc(.3, hz, decay(.09, .002), "tri"), 1), lvl(filt(osc(.3, hz, decay(.05, .002), "saw"), "lp", 1400), .7))


def raspberry(d):
    """Blowing a raspberry: lips flapping unevenly, buzzy and wet."""
    ph = [0.0]
    def flap(t):
        ph[0] += (34 + 8 * math.sin(TAU * 2.3 * t) + 5 * math.sin(TAU * 5.1 * t)) / SR
        return .25 + .75 * max(0.0, math.sin(TAU * ph[0])) ** .6
    src = add(g(osc(d, lambda t: 115 + 12 * math.sin(TAU * 3 * t), 1, "saw"), .7), noise(d))
    s = shape(src, lambda t: flap(t) * fade(d, .02, .08)(t))
    return filt(add(lvl(filt(s, "bp", 850, .8), 1), lvl(filt(s, "bp", 2200, 1.2), .5)), "lp", 3500)


def explosion(d=1.3):
    blast = shape(filt(noise(d), "lp", gline([(0, 6000), (.25, 1200), (d, 300)])), decay(.35, .003))
    rumble = shape(filt(noise(d), "lp", 260), decay(.6, .01))
    debris = shape(crackle(d, 35, 1500), line([(0, 0), (.12, 1), (d, 0)]))
    return add(lvl(blast, 1), lvl(rumble, .8), lvl(thud(170, 55, .7, 1), .9), lvl(debris, .45))


def clock_tick(hz=2400):
    return add(lvl(shape(filt(noise(.02), "hp", 3000), decay(.003, .0005)), 1), lvl(osc(.03, hz, decay(.006, .0005)), .6))


def beep(hz, d=.13):
    return filt(osc(d, hz, fade(d, .005, .02), "square"), "lp", 3500)


def alarm_bell(d):
    """An old wind-up alarm clock: a hammer rattling between two bells."""
    out = [0.0]; n = int(d * 22)
    for k in range(n):
        f = 2600 if k % 2 else 3150
        out = add(out, at(add(osc(.09, f, decay(.04, .001)), g(osc(.09, f * 2.76, decay(.015, .001)), .3)), k / 22))
    return shape(out, fade(d + .09, .005, .06))


def lick():
    """A quick 'shlp' (licking his nub)."""
    return add(lvl(shape(filt(noise(.14), "bp", gline([(0, 2600), (.14, 800)]), 2), fade(.14, .01, .04)), 1), lvl(bloop(.1, 700, 320), .5))


def sigh(d=.8, f0=460, f1=300):
    return vox(d, gline([(0, f0), (d, f1)]), "ah", .75, line([(0, 0), (.12, 1), (d * .5, .8), (d, 0)]))


def yawn(d=1.0):
    return vox(d, gline([(0, 360), (.35, 540), (d, 320)]), morph("ah", "oo", d * .6, d * .9), line([(0, .7), (.3, .4), (d, .6)]),
               line([(0, 0), (.15, .8), (.45, 1), (d * .8, .7), (d, 0)]))


def snore(d=.9):
    """In: a soft rattly snort. Out: a little whistle."""
    din, dout = d * .5, d * .5
    gate = lambda t: .4 + .6 * max(0.0, math.sin(TAU * 32 * t))
    snrk = shape(filt(noise(din), "bp", 550, 1.5), lambda t: gate(t) * math.sin(math.pi * t / din) ** 1.5)
    out_air = shape(filt(noise(dout), "bp", 1400, 1), lambda t: math.sin(math.pi * t / dout))
    whist = osc(dout, gline([(0, 1350), (dout, 1000)]), lambda t: math.sin(math.pi * t / dout) ** 2)
    return add(lvl(snrk, 1), at(add(lvl(out_air, .5), lvl(whist, .45)), din))


def flap():
    """A coat or blanket flung open: a cloth whump."""
    return add(lvl(whoosh(.2, 300, 1500, .9), 1), lvl(shape(filt(noise(.08), "lp", 600), decay(.03, .002)), .7))


def tune_whistle(notes):
    """Whistling a little tune: [(start s, note, length s), ...]."""
    out = [0.0]
    for t0, n, d in notes:
        hz = note(n)
        out = add(out, at(add(lvl(osc(d, lambda t, hz=hz: hz * (1 + .006 * math.sin(TAU * 6 * t)) * (.97 + .03 * min(1, t / .03)), fade(d, .02, .05)), 1),
                              lvl(shape(filt(noise(d), "bp", hz, 6), fade(d, .02, .05)), .25)), t0))
    return out


# ---------- the gags (frame numbers are drawings at 24 fps from the start of the gag) ----------
def g_shock(tr):
    tr.add(3, oc(.22, [(0, 1.26), (1, 1.5)], breath=.1), .55, "hm?")
    tr.add(16, pfft(.03, 4000), .6, dt=.02)
    d = 20 / FPS
    flick = lambda t: (1 if int(t * FPS / 2) % 2 == 0 else .55) * (.85 + .15 * math.sin(TAU * 31 * t))
    buzz = add(osc(d, lambda t: 95 * (1 + .03 * math.sin(TAU * 3 * t)), 1, "square"), g(osc(d, 190.5, 1, "saw"), .6))
    buzz = shape(filt(buzz, "bp", lambda t: 1500 + 700 * math.sin(TAU * 11 * t), .9), lambda t: flick(t) * fade(d, .005, .05)(t))
    tr.add(17, add(lvl(buzz, 1), lvl(crackle(d, 70, 2200), .9)), 1.0, "ZAP")
    tr.add(17, oc(d + .05, [(0, 1.5), (.3, 2.0), (1, 2.1)], vib=1.2, vibhz=21, atk=.01, breath=.2), .75, "aaaa!")
    tr.add(37, oc(.72, [(0, 1.26), (1, .84)], vib=.6, vibhz=5, breath=.12), .45, "dazed")       # frazzled, frames 37-54
    tr.add(37, shape(crackle(.7, 22, 3000), line([(0, 1), (.7, 0)])), .3, "fizzle")            # the last of the static dying off
    tr.add(55, brrr(.5), .6, "brrr")
    tr.add(70, heh(1.33), .45, "heh")


def g_moto(tr):
    tr.add(6, oc(.24, [(0, 1.26), (1, 1.68)]), .6, "ooh!")
    tr.add(14, zip_(), .8, "zip")
    tr.add(18, engine(.35, gline([(0, 70), (.15, 130), (.35, 90)]), fade(.35, .02, .1), 700), .35, "vroom vroom")
    tr.add(22, engine(.4, gline([(0, 80), (.15, 150), (.4, 110)]), fade(.4, .02, .15), 800), .4)
    d = 30 / FPS        # rides in (frame 26), up the ramp, through the ring (37-39), lands (44), gone by 56
    hz = gline([(0, 110), (8 / FPS, 160), (12 / FPS, 175), (18 / FPS, 165), (19 / FPS, 140), (d, 120)])
    amp = line([(0, .2), (6 / FPS, 1), (18 / FPS, 1), (24 / FPS, .4), (d, 0)])
    tr.add(26, engine(d, hz, amp, line([(0, 600), (8 / FPS, 2200), (18 / FPS, 2200), (d, 500)])), .8, "engine")
    tr.add(33, oc(.6, [(0, 1.5), (.45, 2.1), (1, 1.68)], vib=.3, vibhz=7), .7, "wheee!")
    tr.add(36, add(lvl(whoosh(.35, 400, 1800, .8), 1), lvl(crackle(.5, 60, 1200), .7)), .8, "fire whoosh")
    tr.add(44, thud(260, 110, .25, .8), .55, "land")
    tr.add(56, g(crackle(.7, 18, 2500), 1), .2, "sizzle")
    tr.add(63, cough(), .45, "cough")
    tr.add(72, add(lvl(oc(.11, 1.5), 1), at(lvl(oc(.36, [(0, 2.0), (1, 2.04)], vib=.25), 1), .15)), .7, "ta-da!")


def g_wolf(tr):
    tr.add(0, osc(.95, lambda t: 700 * 2 ** (.25 / 12 * math.sin(TAU * 5 * t)) * (1 + .3 * t), fade(.95, .3, .35)), .3, "moon rises")
    tr.add(13, oc(.15, 1.68, atk=.01), .55, "oh!")
    for k, tick in enumerate((21, 24, 27, 30)):
        tr.add(tick, add(lvl(bloop(.08, 400 + 120 * k, 900 + 200 * k), 1), lvl(whoosh(.08, 2000, 5000, 1.5), .6)), .45, "fur!" if k == 0 else None)
    tr.add(21, oc(.5, [(0, 1.0), (1, 1.4)], trem=.7, tremhz=22, breath=.25), .35)
    tr.add(34, oc(.35, .79, trem=.8, tremhz=32, breath=.4, atk=.02), .5, "grr")
    tr.add(45, shape(filt(noise(.2), "bp", 1300, 1.2), line([(0, 0), (.18, 1), (.2, 0)])), .35, "breath in")
    tr.add(50, oc(1.4, [(0, 1.0), (.12, 1.68), (.6, 1.8), (1, 1.26)], atk=.08, vib=.35, vibhz=5.5, breath=.12, tail=.35), .85, "a-wooooo")
    tr.add(88, poof(), .7, "poof")
    tr.add(95, heh(1.26), .45, "heh")


def g_glass(tr):
    for k in range(4):
        tr.add(k * 1.5, pat(330 + 30 * k), .45, "patter" if k == 0 else None)
    tr.add(0, whoosh(.25, 600, 2400, 1.2), .3)
    tr.add(6, add(lvl(thud(240, 120, .2, .9), .9), lvl(splat(), .8)), .9, "SPLAT")
    tr.add(9, filt(oc(.45, [(0, 1.12), (1, 1.0)], breath=.3), "lp", 700), .5, "mmph")
    tr.add(20, squeak(.52, 1150, 720), .7, "squeeeak")
    tr.add(32, bloop(.12, 300, 900), .6, "bloop")
    tr.add(38, heh(1.68), .5, "hehe")
    # scrubbing the smudge in little circles with a cloth, frames 46-89: one circle every 6 frames,
    # working down the glass. A soft swish that follows the circles, and a polish-squeak on each one.
    d, lap = 44 / FPS, 6 / FPS
    swirl = lambda t: math.sin(TAU * t / lap)
    rub = shape(filt(noise(d), "bp", lambda t: 1900 + 700 * swirl(t), 1.4), lambda t: (.35 + .65 * swirl(t) ** 2) * fade(d, .03, .08)(t))
    tr.add(46, rub, .35, "scrub scrub")
    for k, tick in enumerate(range(46, 90, 6)):
        f = 1750 - 45 * k                                          # a touch lower as he works down
        tr.add(tick + 1, squeak(.1, f, f * (1.12 if k % 2 else .88), 46), .38)
    tr.add(90, ping(2900, .5, .12), .4, "glint")
    tr.add(94, add(lvl(ping(3500, .7, .2), 1), at(lvl(ping(4400, .6, .15), .8), .09)), .5, "ting-ting!")
    tr.add(96, add(lvl(oc(.08, 1.5, atk=.01), .8), at(lvl(oc(.16, [(0, 1.68), (1, 1.78)], atk=.01), .9), .12)), .45, "proud")


def g_banana(tr):
    tr.add(0, whistle(.5, 1500, 520, air=.3), .4, "peel falls")
    tr.add(12, splat(.08), .5, "flop")
    for tick in (18, 22, 25):
        tr.add(tick, pat(300), .4, "steps" if tick == 18 else None)
    tr.add(27, add(lvl(whistle(.2, 500, 2300, air=.2), 1), lvl(squeak(.14, 1800, 2300), .5)), .7, "zwip!")
    tr.add(29, oc(.3, [(0, 1.68), (1, 2.1)], atk=.01), .6, "whoa!")
    tr.add(36, thud(200, 70, .45, 1), 1.0, "THUD")
    tr.add(37, oc(.12, [(0, 1.0), (1, .8)], breath=.5, atk=.005), .45)
    tr.add(41, splat(.07), .35, "peel lands")
    for k, tick in enumerate((42, 48, 54)):
        tr.add(tick, tweet(), .35, "tweet tweet" if k == 0 else None)
    tr.add(65, hmph(), .6, "hmph")
    tr.add(87, whoosh(.22, 500, 2500), .5, "fling")


def g_balloon(tr):
    for k, tick in enumerate((6, 11)):
        d = 5 / FPS + .05
        rub = osc(d, gline([(0, 180 + 70 * k), (d, 260 + 80 * k)]), fade(d, .03, .04), "tri")
        air = shape(filt(noise(d), "bp", gline([(0, 700), (d, 1400)]), 1.4), fade(d, .03, .04))
        tr.add(tick, add(lvl(rub, .7), lvl(air, 1)), .6, "fwoo, fwoo" if k == 0 else None)
    for k, tick in enumerate((16, 19, 22, 25)):
        tr.add(tick, squeak(.09, 680 + 40 * k, 760 + 40 * k, 50), .4, "creak" if k == 0 else None)
    tr.add(16, filt(oc(.5, [(0, 1.0), (1, 1.5)], vib=.3, vibhz=12, breath=.2), "lp", 900), .4, "mmmMM")
    lips = lambda d: shape(filt(osc(d, 140, 1, "saw"), "bp", 900, 1.2), lambda t: .5 + .5 * abs(math.sin(math.pi * 34 * t)))
    tr.add(28, add(lvl(pfft(.14, 2200), 1), lvl(shape(lips(.14), line([(0, 0), (.01, 1), (.14, 0)])), .6)), .8, "pfft! (lips)")   # lets it out of his mouth
    d = 54 / FPS        # the flight: a rubbery squeal that wobbles and sags as the air runs out
    hz = lambda t: 620 * (1 - .5 * t / d) * (1 + .12 * math.sin(TAU * 7 * t) + .06 * math.sin(TAU * 12.3 * t + 1))
    lip = lambda t: .55 + .45 * abs(math.sin(math.pi * 36 * t))
    squeal = shape(osc(d, hz, 1, "saw"), lambda t: lip(t) * line([(0, 0), (.03, 1), (d * .8, .8), (d, 0)])(t))
    squeal = filt(filt(squeal, "bp", lambda t: hz(t) * 2.2, 1.5), "lp", 3000)
    # air rushing out of his mouth the whole way round (the drawings now show it streaming out behind him)
    rush = shape(filt(noise(d), "bp", lambda t: 1500 + 500 * math.sin(TAU * 3 * t), .9), lambda t: lip(t) * line([(0, .9), (d * .7, .6), (d, 0)])(t))
    tr.add(31, add(lvl(squeal, 1), lvl(rush, .55)), 1.0, "pbbbbbbtt")
    tr.add(83, pfft(.08, 2000), .35, "last puff")
    tr.add(91, bloop(.1, 300, 600), .5, "bloop")
    tr.add(94, bloop(.13, 420, 950), .65, "BLOOP")
    tr.add(99, heh(1.5), .4, "heh")


def g_sneeze(tr):
    # The build-up: three gasping breaths in, each higher and bigger, then two catches and a held breath.
    tr.add(0, gasp(.34, 390, 440), .4, "ah...")
    tr.add(9, gasp(.28, 430, 510), .55, "ahh...")
    tr.add(16, gasp(.24, 490, 620, breath=.5), .7, "AHHH")
    tr.add(22, gasp(.08, 600, 640, breath=.65), .45, "ah- ah-")
    tr.add(26, gasp(.07, 630, 670, breath=.65), .5)
    # CHOO: a hard 'tch' burst, a breathy 'oo' that falls in pitch, and the spray hissing out after it.
    tch = shape(filt(noise(.05), "hp", 2800), decay(.012, .001))
    choo = vox(.42, gline([(0, 720), (.1, 600), (.42, 380)]), morph("eh", "oo", .01, .07),
               line([(0, .75), (.08, .35), (.42, .55)]), line([(0, 0), (.004, 1), (.1, .85), (.42, 0)]))
    spray = shape(filt(noise(.5), "bp", 3800, .9), lambda t: 0 if t < .02 else math.exp(-(t - .02) / .15))
    tr.add(31, add(lvl(tch, 1.1), at(lvl(choo, 1), .012), at(lvl(spray, .55), .012)), 1.0, "CHOO!")
    tr.add(37, add(lvl(whoosh(.3, 3000, 700, 1.3), 1), lvl(whistle(.25, 1600, 380, air=0), .6)), .65, "zoom")
    tr.add(47, filt(add(lvl(thud(200, 90, .3), 1), lvl(clang(700, .6), .5)), "lp", 1200), .3, "distant bonk")
    for tick in (55, 59, 63, 67):
        tr.add(tick, pat(320), .35, "steps" if tick == 55 else None)
    tr.add(71, heh(1.26), .45, "heh...")
    tr.add(77, sniff(), .4, "sniff sniff")
    tr.add(80, sniff(), .3)


def g_accordion(tr):
    tr.add(0, add(lvl(thud(230, 110, .2), .8), lvl(bloop(.1, 500, 200), .6)), .5, "squish")
    # one reed note (and its chord underneath) per drawing, a little oom-pah phrase
    tune = [(5, 3, "C5", ["C4", "E4", "G4"]), (8, 3, "E5", None), (11, 5, "G5", None), (16, 3, "E5", None),
            (19, 5, "C5", None), (24, 3, "D5", ["G3", "B3", "F4"]), (27, 5, "F5", None), (32, 3, "B4", None),
            (35, 4, "C5", ["C4", "E4", "G4"])]
    chord = None
    for tick, n, mel, ch in tune:
        chord = ch or chord
        d = n / FPS + .02
        tr.add(tick, add(lvl(reed(d, [note(mel)]), 1), lvl(reed(d, [note(c) for c in chord]), .55)), .75, "accordion" if tick == 5 else None)
        tr.add(tick, shape(filt(noise(d), "lp", 1200), fade(d, .01, .03)), .12)   # the bellows
    tr.add(39, boing(.55, 190), .7, "boing")
    tr.add(48, heh(1.5), .45, "heh")


def g_disco(tr):
    # One steady tempo: a beat every 13 frames (111 bpm). His dance steps alternate 4- and 5-frame holds, so
    # following them made the music speed up and slow down; on this grid his steps still land within a frame
    # of beats 18, 31, 44, 57, and the pose (70) is the next downbeat. Am7 for two beats, D9 for two (a disco vamp).
    B = 13; S = B / 4; sec = lambda frames: frames / FPS
    beats = [18, 31, 44, 57]
    tr.add(0, sparkle(.55, 7, 2200, 4500, -1), .4, "ball comes down")
    tr.add(0, riser(sec(18)), .35)
    tr.add(6, oc(.25, [(0, 1.26), (1, 1.68)]), .55, "ooh!")
    tr.add(12, add(lvl(ping(3100, .5, .1), 1), lvl(ping(4650, .4, .07), .6)), .55, "shades on")
    for k in (2, 1):                                                       # a little snare pickup into the groove
        tr.add(18 - k * S, clap(), .3 + .1 * (2 - k))
    tr.add(18 - 2 * S, bass(sec(2 * S), note("E2")), .45)
    AM, D9 = ["E4", "G4", "A4", "C5"], ["E4", "F#4", "A4", "C5"]
    for i, b in enumerate(beats):
        chord, (lo, hi) = (AM, ("A2", "A3")) if i < 2 else (D9, ("D3", "D4"))
        tr.add(b, kick(), .75, "groove (111 bpm)" if i == 0 else None)
        if i % 2: tr.add(b, clap(), .55)                                   # claps on 2 and 4
        tr.add(b, bass(sec(2 * S) * .9, note(lo)), .55)                    # octave-bouncing disco bass
        tr.add(b + 2 * S, bass(sec(S) * .9, note(hi)), .5)
        tr.add(b + 3 * S, bass(sec(S) * .9, note("G#2" if i == 3 else hi)), .4)   # walks up into the pose
        tr.add(b + 2 * S, ohat(), .35)                                     # the open hat on every 'and'
        for k in (1, 3): tr.add(b + k * S, hat(), .18)
        for k in (1, 3): tr.add(b + k * S, stab([note(n) for n in chord]), .3)
        if i % 2 == 0: tr.add(b, strings(sec(2 * B) + .05, [note(n) for n in chord]), .32)
    # his little hook, sung on the ocarina in tune with the band
    hook = [(18 + 2 * S, "E5", .12), (31, "G5", .12), (31 + 3 * S, "A5", .38), (44 + 2 * S, "G5", .12), (57, "E5", .12)]
    for k, (t, n, d) in enumerate(hook):
        tr.add(t, oc(d, sing(n), atk=.01, tail=.4), .5, "doo-doo-dooo" if k == 0 else None)
    tr.add(57 + 2 * S, oc(.2, [(0, sing("G5")), (1, sing("A5"))], atk=.01), .5)
    # the pose: everything hits together and rings while he holds it, then twinkles as the ball goes up
    pose = [note(n) for n in ("A3", "C4", "E4", "G4", "B4")]
    tr.add(70, add(lvl(strings(.75, pose, atk=.01, rel=.4, bright=3200), 1), lvl(kick(), .9), lvl(crash(), .7),
                   lvl(bass(.6, note("A2")), .7)), .85, "pose!")
    tr.add(70, oc(.4, [(0, sing("A5")), (1, sing("E6"))], vib=.3, vibhz=7, atk=.01), .55, "woo!")
    tr.add(80, sparkle(.5, 6, 2200, 4500, 1), .35, "ball goes up")


def g_anvil(tr):
    tr.add(0, whistle(15 / FPS, 2300, 700, vib=.01), .45, "whistle")
    tr.add(10, oc(.1, 2.0, atk=.005), .6, "eep!")
    tr.add(15, add(lvl(clang(480, 1.4), 1), lvl(thud(200, 80, .3), .8)), 1.0, "CLANG")
    tr.add(20, sparkle(.8, 6, 2500, 4000), .25, "seeing stars")
    tr.add(40, boing(.6, 170), .75, "boing")
    tr.add(49, brrr(.3), .5, "brrr")
    tr.add(55, oc(.14, [(0, 1.26), (1, 1.12)], atk=.01), .4, "hm")


def g_moon(tr):
    tr.add(2, add(lvl(oc(.08, 1.12, atk=.01, breath=.2), .8), at(lvl(oc(.08, 1.0, atk=.01, breath=.2), .8), .11),
                  at(lvl(oc(.12, 1.12, atk=.01, breath=.2), .8), .22)), .45, "heh-heh-heh (cheeky)")
    tr.add(12, whoosh(.15, 600, 2000), .45, "turns round")
    for k, tick in enumerate(range(23, 47, 3)):                            # a slide-whistle wiggle, one way then the other
        up = k % 2 == 0
        tr.add(tick, whistle(.12, 700 if up else 1300, 1300 if up else 700, air=.15), .45, "wiggle wiggle" if k == 0 else None)
    tr.add(51, zip_(.12, 1800, 600), .5, "spins back")
    wink = add(lvl(ping(3300, .6, .14), 1), lvl(shape(filt(noise(.02), "bp", 2500, 2), decay(.004, .0005)), .7))   # a twinkle and a little tongue click
    tr.add(57, wink, .55, "wink!")
    tr.add(59, add(lvl(oc(.08, 1.5, atk=.01), .8), at(lvl(oc(.22, [(0, 1.68), (.5, 1.88), (1, 1.68)], atk=.01), .9), .1)), .45, "heh~")


def g_bomb(tr):
    for k, (tick, n) in enumerate(((0, "E2"), (4, "G2"), (8, "A2"), (12, "Bb2"), (16, "A2"), (20, "G2"))):
        tr.add(tick, pizz(note(n)), .5, "sneaky" if k == 0 else None)
    tr.add(22, add(lvl(oc(.07, 1.26, atk=.01), .8), at(lvl(oc(.07, 1.33, atk=.01), .8), .1), at(lvl(oc(.1, 1.5, atk=.01), .8), .2)), .45, "hehehe")
    tr.add(26, flap(), .7, "coat open!")
    tr.add(27, oc(.3, [(0, 1.5), (1, 2.0)], vib=.3, vibhz=8, atk=.01), .55, "ta-daa!")
    fuse = add(lvl(shape(filt(noise(46 / FPS), "hp", 4000), lambda t: .7 + .3 * math.sin(TAU * 13 * t)), 1), lvl(crackle(46 / FPS, 30, 3000), .6))
    tr.add(26, fuse, .22, "fuse hissing")
    for k, f in enumerate(range(36, 72, 6)):                               # the timer ticking
        tr.add(f, clock_tick(2400 if k % 2 == 0 else 1900), .35, "tick tock" if k == 0 else None)
    for tick, hz, lab in ((36, 1100, "3"), (48, 1240, "2"), (60, 1400, "1")):
        tr.add(tick, beep(hz), .45, lab)
    tr.add(49, oc(.16, [(0, 1.26), (1, 1.5)], atk=.01), .4, "hm?")
    tr.add(61, add(lvl(oc(.12, 1.33, atk=.01), 1), at(lvl(oc(.22, [(0, 1.12), (1, 1.0)], atk=.01), 1), .15)), .5, "uh-oh")
    tr.add(72, explosion(), 1.0, "KABOOM")
    tr.add(94, crackle(.5, 12, 3000), .2, "smoking")
    tr.add(99, cough(), .4, "cough")
    for k, tick in enumerate((106, 108)):
        tr.add(tick, bloop(.05, 1800, 2400), .3, "blink" if k == 0 else None)
    tr.add(119, add(lvl(brrr(.45), 1), lvl(shape(filt(noise(.5), "bp", 2500, 1), lambda t: .5 + .5 * abs(math.sin(TAU * 12 * t))), .5)), .55, "shakes off soot")
    tr.add(131, boing(.5, 220), .6, "boing")
    tr.add(137, heh(1.5), .4, "heh")


def prep_rest(tr, sh):
    """Meeting prep from the tie onward (sh: how many frames later it runs in the alarm version)."""
    tr.add(13 + sh, add(lvl(poof(), 1), lvl(sparkle(.25, 3, 3000, 4500), .4)), .6, "tie poofs on")
    for k, tick in enumerate(range(19, 37, 2)):                            # doing up the tie, frantically
        tr.add(tick + sh, whoosh(.07, 1500 if k % 2 else 2200, 3500, 1.4), .3, "frantic" if k == 0 else None)
    tr.add(19 + sh, oc(.75, [(0, 1.5), (1, 1.6)], vib=1, vibhz=9, trem=.5, tremhz=11, breath=.2), .35, "mm-mm-mm")
    tr.add(37 + sh, lick(), .55, "lick")
    for k, tick in enumerate((45, 49, 53, 57)):                            # combing the cowlick flat
        tr.add(tick + sh, add(lvl(whoosh(.14, 900, 2600, 1.6), 1), lvl(squeak(.08, 1900, 2100, 50), .3)), .4, "comb" if k == 0 else None)
    tr.add(61 + sh, ping(3200, .6, .15), .45, "glint")
    tr.add(63 + sh, add(lvl(oc(.12, 1.26, atk=.01), .8), at(lvl(oc(.26, [(0, 1.5), (1, 1.55)], vib=.25), .9), .15)), .45, "hmm-HM (smug)")
    tr.add(79 + sh, boing(.9, 260, 11), .85, "BOING")
    tr.add(95 + sh, sigh(), .55, "sigh")


def g_prep(tr):
    tr.add(0, oc(.09, 2.0, atk=.005), .5, "eep!")
    tr.add(4, gasp(.16, 520, 640, breath=.6), .5, "the time!")
    prep_rest(tr, 0)


def g_prep_alarm(tr):
    tr.add(0, alarm_bell(15 / FPS), .8, "alarm RINGING")
    tr.add(1, oc(.5, [(0, 1.88), (1, 2.1)], vib=.8, vibhz=12, atk=.01, breath=.2), .5, "aaah!")
    prep_rest(tr, 2)


def g_startle(tr):
    tr.level = .6                                                          # night only: slow and soft
    for k, tick in enumerate((0, 24)):
        tr.add(tick + 2, snore(.95), .5, "snore" if k == 0 else None)
    tr.add(48, add(lvl(shape(filt(noise(.12), "bp", 500, 1.5), lambda t: .4 + .6 * max(0, math.sin(TAU * 32 * t))), .9),
                   at(lvl(oc(.2, [(0, 1.26), (1, 1.88)], atk=.01), 1), .1)), .8, "snrk-HUH?!")
    tr.add(48, whoosh(.3, 500, 1800, .9), .5, "blanket flies")
    tr.add(50, bloop(.06, 900, 1600), .35, "drool snaps")
    tr.add(63, oc(.18, [(0, 1.0), (1, 1.12)], atk=.03, breath=.2), .35, "hm?")
    tr.add(77, oc(.18, [(0, 1.12), (1, 1.0)], atk=.03, breath=.2), .35, "hm..")
    for k, tick in enumerate((90, 96, 102, 108)):
        tr.add(tick, shape(filt(noise(.16), "bp", 1500 if k % 2 else 1800, 1.2), fade(.16, .03, .06)), .3, "wipes mouth" if k == 0 else None)
    tr.add(116, heh(1.26), .35, "heh...")
    tr.add(132, yawn(1.0), .6, "big yawn")
    tr.add(166, whoosh(.5, 400, 900, .8), .35, "blanket back up")
    tr.add(182, shape(filt(noise(.15), "lp", 500), decay(.05, .005)), .45, "flump")
    tr.add(196, oc(.4, [(0, 1.0), (1, .94)], atk=.1, breath=.25), .3, "mm (asleep)")


GAGS = {"shock": g_shock, "moto": g_moto, "wolf": g_wolf, "glass": g_glass, "banana": g_banana, "balloon": g_balloon,
        "sneeze": g_sneeze, "accordion": g_accordion, "anvil": g_anvil, "disco": g_disco, "moon": g_moon, "bomb": g_bomb,
        "prep": g_prep, "prep_alarm": g_prep_alarm, "startle": g_startle}


def limit(s, knee=.6):
    """Round off the odd sharp peak (a click, the front of a thud) so it never passes PEAK,
    instead of turning the whole gag down to make room for it."""
    th = PEAK * knee; room = PEAK - th
    return [v if abs(v) <= th else math.copysign(th + room * math.tanh((abs(v) - th) / room), v) for v in s]


def write_wav(path, samples):
    with wave.open(path, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(SR)
        w.writeframes(b"".join(struct.pack("<h", max(-32767, min(32767, int(s * 32767)))) for s in samples))


def main():
    global BASE
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(HOME, "sounds"))
    ap.add_argument("--only", nargs="*")
    a = ap.parse_args()
    try: BASE = json.load(open(SETTINGS)).get("voice", {}).get("pitch", BASE)
    except (OSError, ValueError): pass
    os.makedirs(a.out, exist_ok=True)
    cues_path = os.path.join(a.out, "tap_cues.json")
    try: cues = json.load(open(cues_path))
    except (OSError, ValueError): cues = {}
    for name, make in GAGS.items():
        if a.only and name not in a.only: continue
        rng.seed(name)                                     # same render every time
        tr = Track(); make(tr)
        tail = int(.05 * SR); s = tr.b + [0.0] * tail
        s = limit(g(s, LOUD * tr.level / loudness(s, .1)))
        write_wav(os.path.join(a.out, f"tap_{name}.wav"), s)
        cues[name] = sorted(tr.cues)
        print(f"tap_{name}.wav  {len(s) / SR:4.2f}s  peak {max(abs(v) for v in s):.2f}  loud {loudness(s, .1):.3f}")
    cues = {k: v for k, v in cues.items() if k in GAGS}               # forget gags that have been dropped
    json.dump(cues, open(cues_path, "w"), indent=1)


if __name__ == "__main__":
    sys.exit(main())
