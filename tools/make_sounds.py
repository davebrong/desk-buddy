"""Render Lump's ocarina voice to WAV files for the bridge to play.

  python3 tools/make_sounds.py            # writes ~/.desk-buddy/sounds/<moment>_<version>.wav

Every version of every moment is rendered (not just the ticked ones), so changing your picks
in ~/.desk-buddy/sounds.json never needs a re-render. The voice sliders (pitch, speed,
breath) do: they're read from the "voice" part of that file, so re-run this after changing them.

This is a straight port of the ocarina in design/sound-audition.html (sine + a soft octave
partial + a breath of band-passed noise, with a little scoop up into each note). The phrase
tables below mirror the page's MOMENTS list; keep the two in step if you add versions.
Pure standard library, no numpy.
"""
import json, math, os, random, shutil, struct, sys, wave

SR = 44100
HOME = os.path.expanduser("~/.desk-buddy")
OUT = os.path.join(HOME, "sounds")
SETTINGS = os.path.join(HOME, "sounds.json")
DEFAULTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "lump_sounds.json")
MASTER = 0.45            # peak level; afplay's volume (sounds.json "volume") scales on top

# Pitch is a ratio of the base note (1.26 = major third, 1.5 = fifth, 2 = octave); d is seconds
# before the speed stretch; gap is silence before the syllable; vol and atk are optional.
def S(p0, p1=None, d=.1, gap=0, vol=1, atk=.05):
    return {"p0": p0, "p1": p1 if p1 is not None else p0, "d": d, "gap": gap, "vol": vol, "atk": atk}

MOMENTS = {
    "hello": {
        "a": [S(1, 1.26, .16), S(1.26, 1.5, .3, gap=.04)],
        "b": [S(1.2, d=.12), S(1.5, d=.12, gap=.03), S(1.26, 1.68, .32, gap=.03)],
        "c": [S(1.68, 1.5, .12), S(1.26, 1.68, .26, gap=.1)],
    },
    "needs_you": {
        "a": [S(1.5, 1.6, .14), S(1.5, 1.8, .2, gap=.12)],
        "b": [S(1.26, d=.1), S(1.5, 2, .28, gap=.05)],
        "c": [S(2, d=.16), S(1.5, d=.3, gap=.02)],
    },
    "still_waiting": {
        "a": [S(1.5, 1.4, .18, vol=.75), S(1.26, 1.6, .38, gap=.08, vol=.75)],
        "b": [S(1.68, 1.26, .4, vol=.7), S(1.26, 1.5, .22, gap=.06, vol=.7)],
    },
    "finished": {
        "a": [S(1, d=.09), S(1.26, d=.09, gap=.02), S(1.5, d=.09, gap=.02), S(2, 2.1, .35, gap=.02)],
        "b": [S(1.5, d=.12), S(2, d=.42, gap=.05)],
        "c": [S(1.26, 2.2, .45)],
    },
    "finished_while_busy": {
        "a": [S(1.5, 2, .14)],
        "b": [S(1.26, d=.1), S(1.5, 1.7, .15, gap=.02)],
    },
    "uh_oh": {
        "a": [S(1.26, 1.22, .16), S(1, .8, .36, gap=.08)],
        "b": [S(1.68, 1, .35), S(.9, .95, .12, gap=.1, vol=.7)],
    },
    "thanks": {
        "a": [S(1.5, d=.12), S(1.26, 1.33, .24, gap=.03)],
        "b": [S(1.26, 1.6, .22)],
    },
    "long_job": {
        "a": [S(1, 1.05, .4, vol=.7), S(1.26, .9, .4, gap=.1, vol=.6)],
        "b": [S(1, d=.16, vol=.7), S(1.12, d=.16, gap=.02, vol=.7), S(1, .94, .3, gap=.02, vol=.7)],
    },
    "goodnight": {
        "a": [S(1.5, .8, .7, vol=.6, atk=.12), S(.84, d=.25, gap=.15, vol=.4)],
        "b": [S(1.5, d=.3, vol=.6), S(1.26, d=.3, gap=.04, vol=.6), S(1, d=.55, gap=.04, vol=.55)],
    },
    "waking_up": {
        "a": [S(1, d=.07), S(1.12, d=.07, gap=.01), S(1.26, d=.07, gap=.01), S(1.5, d=.07, gap=.01), S(2, d=.25, gap=.01)],
        "b": [S(.9, 1.1, .3, vol=.6, atk=.1), S(1.5, 1.8, .2, gap=.1)],
    },
    "on_it": {
        "a": [S(1, 1.1, .08), S(1.26, 1.35, .12, gap=.02)],
        "b": [S(1.26, d=.08), S(1.68, d=.16, gap=.03)],
    },
    "fidget": {
        "a": [S(1, 1.33, .25, vol=.6)],
        "b": [S(1.12, d=.14, vol=.6), S(1.33, d=.18, gap=.03, vol=.6)],
    },
}


class BandPass:
    """The same biquad Web Audio uses for "bandpass" (RBJ cookbook, 0 dB peak)."""
    def __init__(self, freq, q):
        w = 2 * math.pi * freq / SR
        alpha = math.sin(w) / (2 * q)
        a0 = 1 + alpha
        self.b0, self.b2 = alpha / a0, -alpha / a0
        self.a1, self.a2 = -2 * math.cos(w) / a0, (1 - alpha) / a0
        self.x1 = self.x2 = self.y1 = self.y2 = 0.0

    def __call__(self, x):
        y = self.b0 * x + self.b2 * self.x2 - self.a1 * self.y1 - self.a2 * self.y2
        self.x2, self.x1, self.y2, self.y1 = self.x1, x, self.y1, y
        return y


def render(parts, base, tempo, breath, rng):
    out, t = [], 0.0
    for p in parts:
        start = t + p["gap"] * tempo
        d = p["d"] * tempo
        n = max(2, int(d * SR))
        i0 = int(start * SR)
        if len(out) < i0 + n:
            out.extend([0.0] * (i0 + n - len(out)))
        P0, P1 = p["p0"] * base, p["p1"] * base
        sT = min(.05 * tempo, d * .5)
        bp = BandPass(p["p0"] * base * 2, 2)
        ph1 = ph2 = 0.0
        for i in range(n):
            x, tt = i / (n - 1), i / SR
            f = P0 * (P1 / P0) ** x
            if tt < sT:
                f *= .92 ** (1 - tt / sT)                      # the little scoop up into the note
            ph1 += 2 * math.pi * f / SR
            ph2 += 4 * math.pi * f / SR
            env = p["vol"] * (tt / p["atk"] if tt < p["atk"] else .001 ** ((x - .7) / .3) if x > .7 else 1)
            out[i0 + i] += env * (math.sin(ph1) + .12 * math.sin(ph2) + breath * bp(rng.uniform(-1, 1)))
        t = start + d
    return out + [0.0] * int(.03 * SR)


def write_wav(path, samples, peak):
    k = MASTER / peak if peak else 0
    frames = b"".join(struct.pack("<h", max(-32767, min(32767, int(s * k * 32767)))) for s in samples)
    with wave.open(path, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(SR); w.writeframes(frames)


def main():
    if not os.path.exists(SETTINGS):
        os.makedirs(HOME, exist_ok=True)
        shutil.copy(DEFAULTS, SETTINGS)
        print("installed default settings:", SETTINGS)
    voice = json.load(open(SETTINGS)).get("voice", {})
    base, tempo, breath = voice.get("pitch", 530), voice.get("speed", .95), voice.get("breath", .06)
    os.makedirs(OUT, exist_ok=True)
    rng = random.Random(7)                                    # same breath noise every render
    rendered = {f"{m}_{k}": render(parts, base, tempo, breath, rng) for m, vs in MOMENTS.items() for k, parts in vs.items()}
    # One shared scale for every file, so quiet moments (still waiting, goodnight) stay quieter.
    peak = max(abs(s) for samples in rendered.values() for s in samples)
    for name, samples in rendered.items():
        write_wav(os.path.join(OUT, name + ".wav"), samples, peak)
    print(f"wrote {len(rendered)} sounds to {OUT} (pitch {base} Hz, speed {tempo}x, breath {breath})")


if __name__ == "__main__":
    sys.exit(main())
