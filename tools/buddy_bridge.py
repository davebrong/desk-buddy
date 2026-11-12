"""Desk Buddy bridge: Claude Code hooks -> Lump on the ESP32.

Claude Code hooks POST their JSON to http://127.0.0.1:47823/hook. The bridge keeps
a little state per session, merges all sessions into one mood, and tells the board
over USB serial only when that mood changes:

  any session waiting on you (permission prompt, question, plan approval) -> alert
  otherwise any session working                                            -> working
  otherwise, something finished that you haven't looked at yet             -> awake
  otherwise                                                                -> idle (night after 22:00)
A session finishing always gets a celebration ("done"): 3.5 s if other things are still going
on (then back to alert/working), 8 s if not. Corner extras on the display ("o" command) show
how many sessions are working / waiting on you / finished but not looked at yet.

It also sends one-off events: "oops" when a tool call fails, and "hello" the first
time you start a session after a long break. The board itself handles escalation
(alert -> waiting after 45 s, working -> longhaul after 10 min) and idle fidgets.

Chat and Cowork have no hooks, so a small helper (tools/appwatch, its own login
item) reads the Claude app's accessibility tree and posts page states to /app.

He also has a little ocarina voice that plays on the Mac's speakers (class Sounds): the WAVs
come from tools/make_sounds.py and which moments make a sound lives in ~/.desk-buddy/sounds.json
("enabled" is the master switch; Claude Switcher's right-click menu flips it).

Extras for debugging:  GET /status   POST /cmd  (body: a raw command such as "s done")
                       GET /sound    POST /sound (body: on | off | toggle | play <moment>)
Install as a login item with tools/install_bridge.sh.
"""
import glob, http.server, json, logging, logging.handlers, os, random, re, subprocess, threading, time
import serial

HTTP_PORT = 47823
HOME = os.path.expanduser("~/.desk-buddy")
NIGHT_START, NIGHT_END = 22, 7          # local hours
DONE_SECS = 8                           # celebration when nothing else is going on
CELEBRATE_SECS = 3.5                    # quick celebration when other things are still going on
UNSEEN_SECS = 15 * 60                   # "check me" sparkle lasts until you reply, or this long
HELLO_AFTER = 2 * 3600                  # say hello if nothing happened for this long
# Give up on a state with no news. Pressing Esc fires no hook at all, so an interrupted
# session would otherwise look busy forever; a working session normally sends tool events
# every few seconds, so 8 quiet minutes means it's almost certainly stopped.
STALE = {"working": 8 * 60, "alert": 15 * 60}
NEEDS_YOU = {"permission_prompt", "elicitation_dialog", "elicitation_url_dialog", "agent_needs_input"}
FORGET = 6 * 3600                       # drop sessions silent for this long
OOPS_GAP = 20                           # at most one oops per this many seconds
DEFAULT_COLOUR = "FF7A45"               # Tangerine
TAP_SNOOZE = 120                        # tapping "needs you" holds off the nagging this long
# Usage gauge: his colour warms from Tangerine to red as the busier of your 5-hour / weekly
# Claude limits fills up (fed by the Claude Code status line, see tools/statusline.sh).
USAGE_STOPS = [(0.0, "FF7A45"), (0.5, "FF7A45"), (0.8, "FF4B2B"), (0.97, "C8102E"), (1.0, "8F7C78")]
# Meetings (read from ~/.desk-buddy/meetings.json, written by a calendar source): colour slides
# to MEETING_COLOUR over the 10 minutes before a meeting, he plays "meeting time" when it starts,
# and keeps the colour for the first few minutes.
MEETING_COLOUR = "3D8BFF"               # electric blue (reached via pink and purple, see meeting_blend)
MEETING_LEAD = 10 * 60
MEETING_ALERT_SECS = 45
MEETING_HOLD = 5 * 60
MEETINGS_FILE = os.path.join(HOME, "meetings.json")
CONFIG_FILE = os.path.join(HOME, "config.json")    # {"calendar_ics": "<private iCal link>", "my_email": "..."}
CALENDAR_EVERY = 5 * 60
# Sounds: see class Sounds. At night (NIGHT_START..NIGHT_END) he only makes noise if you've been
# active in the last NIGHT_AWAKE seconds, so a background job finishing at 2am stays silent.
SOUNDS_FILE = os.path.join(HOME, "sounds.json")
SOUNDS_DIR = os.path.join(HOME, "sounds")
SOUND_GAP = 2.0                         # at least this long between any two sounds...
SOUND_REPEAT = 5.0                      # ...and between two of the same moment
URGENT_SOUNDS = {"needs_you"}           # may cut in on the general gap
STILL_WAITING_AFTER = 45                # matches the board's alert -> waiting escalation
LONG_JOB_AFTER = 10 * 60                # matches the board's working -> longhaul
NIGHT_AWAKE = 15 * 60
FIDGET_EVERY = (5 * 60, 15 * 60)

os.makedirs(HOME, exist_ok=True)
log = logging.getLogger("bridge")
log.setLevel(logging.INFO)
_h = logging.handlers.RotatingFileHandler(os.path.join(HOME, "bridge.log"), maxBytes=500_000, backupCount=2)
_h.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
log.addHandler(_h)
log.addHandler(logging.StreamHandler())


class Device:
    """Owns the serial port; reconnects when the board is unplugged or reboots."""

    def __init__(self, on_ready, on_line=None):
        self.ser, self.lock, self.on_ready, self.on_line = None, threading.Lock(), on_ready, on_line
        threading.Thread(target=self._run, daemon=True).start()

    @property
    def connected(self):
        return self.ser is not None

    def _open(self):
        ports = [os.environ["DESKBUDDY_PORT"]] if os.environ.get("DESKBUDDY_PORT") else sorted(glob.glob("/dev/cu.usbmodem*"))
        for p in ports:
            try:
                s = serial.Serial(); s.port, s.baudrate, s.timeout = p, 115200, 0.3
                s.dtr = False; s.rts = False      # don't reset the board when opening
                s.open()
                log.info("connected to %s", p)
                return s
            except Exception as e:
                log.info("can't open %s: %s", p, e)
        return None

    def _run(self):
        buf = b""
        while True:
            if not self.ser:
                self.ser = self._open()
                if not self.ser:
                    time.sleep(3); continue
                time.sleep(0.3); self.on_ready()
            try:
                buf += self.ser.read(256)
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    text = line.decode(errors="replace").strip()
                    if text:
                        log.info("board: %s", text)
                    if "lump ready" in text:      # board rebooted: tell it the mood again
                        self.on_ready()
                    elif text == "tap" and self.on_line:
                        self.on_line(text)
            except Exception as e:
                log.info("serial lost: %s", e)
                try: self.ser.close()
                except Exception: pass
                self.ser = None

    def send(self, cmd):
        with self.lock:
            if not self.ser:
                return False
            try:
                self.ser.write((cmd + "\n").encode()); self.ser.flush()
                log.info("-> %s", cmd); return True
            except Exception as e:
                log.info("write failed: %s", e); return False


class Calendar:
    """Polls your Google Calendar's private iCal link (config.json "calendar_ics") every 5 minutes
    and writes the next day and a half of timed meetings to meetings.json, which drives the
    teal countdown and the "meeting time" animation. Recurring meetings are expanded properly;
    all-day events, cancelled events, events marked "free", and (if "my_email" is set)
    meetings you've declined are skipped. Nothing leaves your Mac except the fetch itself."""

    def __init__(self):
        self.wake = threading.Event()
        threading.Thread(target=self._run, daemon=True).start()

    def refresh_now(self):
        self.wake.set()

    def _run(self):
        while True:
            try:
                self.refresh()
            except Exception as e:
                log.info("calendar refresh failed: %s", e)
            self.wake.wait(CALENDAR_EVERY)        # or sooner if someone asks (POST /calendar)
            self.wake.clear()

    def refresh(self):
        try:
            cfg = json.load(open(CONFIG_FILE))
        except Exception:
            return
        url = (cfg.get("calendar_ics") or "").strip()
        if not url:
            return
        import datetime, urllib.request
        import icalendar, recurring_ical_events
        data = urllib.request.urlopen(url, timeout=20).read()
        cal = icalendar.Calendar.from_ical(data)
        now = datetime.datetime.now(datetime.timezone.utc)
        me = (cfg.get("my_email") or "").strip().lower()
        out = []
        for e in recurring_ical_events.of(cal).between(now - datetime.timedelta(hours=1), now + datetime.timedelta(hours=36)):
            st = e.get("DTSTART").dt
            if not isinstance(st, datetime.datetime):
                continue                                   # all-day
            if str(e.get("STATUS", "")).upper() == "CANCELLED" or str(e.get("TRANSP", "")).upper() == "TRANSPARENT":
                continue
            if me:
                atts = e.get("ATTENDEE") or []
                atts = atts if isinstance(atts, list) else [atts]
                if any(me in str(a).lower() and str(a.params.get("PARTSTAT", "")).upper() == "DECLINED" for a in atts):
                    continue
            en = e.get("DTEND").dt if e.get("DTEND") else st + datetime.timedelta(minutes=30)
            if st.tzinfo is None: st = st.astimezone()
            if en.tzinfo is None: en = en.astimezone()
            out.append({"start": st.timestamp(), "end": en.timestamp(), "title": str(e.get("SUMMARY", ""))[:80]})
        out.sort(key=lambda m: m["start"])
        tmp = MEETINGS_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(out, f)
        os.replace(tmp, MEETINGS_FILE)
        log.info("calendar: %d upcoming meetings", len(out))


class Focus:
    """What you're looking at: whether the Claude app is in front, and which Code session it shows.

    The desktop app logs "LocalSessions.setFocusedSession: sessionId=local_…" whenever you switch
    Code sessions, and keeps a small file per session (claude-code-sessions/…/local_….json) whose
    cliSessionId is the session_id the hooks report. Chat/Cowork pages on screen come from the
    app watcher instead (AppPages.visible)."""

    LOGS = os.path.expanduser("~/Library/Logs/Claude")
    SESSIONS = os.path.expanduser("~/Library/Application Support/Claude/claude-code-sessions")
    FOCUS_RE = re.compile(r"setFocusedSession: sessionId=(local_[0-9a-f-]+|null)")

    def __init__(self):
        self.code_session, self.front, self._log, self._pos, self._map = None, False, None, 0, {}
        threading.Thread(target=self._run, daemon=True).start()

    def _cli_id(self, local):
        if local not in self._map:
            files = glob.glob(os.path.join(self.SESSIONS, "*", "*", local + ".json"))
            try: self._map[local] = json.load(open(files[0])).get("cliSessionId") if files else None
            except Exception: self._map[local] = None
        return self._map[local]

    def _apply(self, local):
        self.code_session = None if local == "null" else self._cli_id(local)

    def _poll_log(self):
        logs = sorted(glob.glob(os.path.join(self.LOGS, "main*.log")), key=os.path.getmtime)
        if not logs: return
        if logs[-1] != self._log:                    # first run or the app rotated its log
            self._log = logs[-1]
            size = os.path.getsize(self._log)
            with open(self._log, "rb") as f:          # catch up from the recent past
                f.seek(max(0, size - 4_000_000)); hits = self.FOCUS_RE.findall(f.read().decode(errors="replace"))
            if hits: self._apply(hits[-1])
            self._pos = size
            return
        size = os.path.getsize(self._log)
        if size < self._pos: self._pos = 0
        if size > self._pos:
            with open(self._log, "rb") as f:
                f.seek(self._pos); hits = self.FOCUS_RE.findall(f.read().decode(errors="replace"))
            self._pos = size
            if hits: self._apply(hits[-1])

    def _run(self):
        import subprocess
        while True:
            try:
                front = subprocess.run(["lsappinfo", "info", "-only", "bundleid", subprocess.run(["lsappinfo", "front"], capture_output=True, text=True).stdout.strip()],
                                       capture_output=True, text=True, timeout=2).stdout
                self.front = "com.anthropic.claudefordesktop" in front
                self._poll_log()
            except Exception as e:
                log.info("focus check failed: %s", e)
            time.sleep(1.5)


class Sounds:
    """Plays Lump's voice through afplay. Settings are re-read whenever sounds.json changes, so
    flipping "enabled" (Claude Switcher, POST /sound, or by hand) takes effect immediately.
      {"enabled": true, "volume": 1.0, "quiet_at_night": true,
       "moments": {"needs_you": {"on": true, "pick": ["a", "c"]}, ...}}
    Several picks: a random one each time, never the same one twice in a row."""

    def __init__(self):
        self.lock, self.cfg, self.mtime = threading.Lock(), {}, None
        self.last_any, self.last_at, self.last_pick, self.proc = 0.0, {}, {}, None

    def settings(self):
        try:
            m = os.path.getmtime(SOUNDS_FILE)
            if m != self.mtime:
                with open(SOUNDS_FILE) as f:
                    self.cfg = json.load(f)
                self.mtime = m
        except FileNotFoundError:
            self.cfg, self.mtime = {}, None
        except Exception as e:
            log.info("can't read %s: %s", SOUNDS_FILE, e)     # keep the last good settings
        return self.cfg

    def set_enabled(self, value):
        with self.lock:
            cfg = dict(self.settings())
            cfg["enabled"] = (not cfg.get("enabled", False)) if value == "toggle" else bool(value)
            tmp = SOUNDS_FILE + ".tmp"
            with open(tmp, "w") as f:
                json.dump(cfg, f, indent=2)
            os.replace(tmp, SOUNDS_FILE)
            log.info("sounds %s", "on" if cfg["enabled"] else "off")
            return cfg["enabled"]

    def play(self, moment, force=False):
        """Play one of the picked versions of a moment. force skips every check (for testing)."""
        with self.lock:
            cfg, now = self.settings(), time.time()
            m = (cfg.get("moments") or {}).get(moment, {})
            picks = m.get("pick") or []
            if not force:
                if not cfg.get("enabled") or not m.get("on") or not picks:
                    return False
                if now - self.last_at.get(moment, 0) < SOUND_REPEAT:
                    return False
                if now - self.last_any < SOUND_GAP and moment not in URGENT_SOUNDS:
                    return False
            if not picks:
                picks = sorted(os.path.basename(p)[len(moment) + 1:-4] for p in glob.glob(os.path.join(SOUNDS_DIR, moment + "_?.wav")))
            pool = [k for k in picks if k != self.last_pick.get(moment)] or picks
            if not pool:
                return False
            k = random.choice(pool)
            path = os.path.join(SOUNDS_DIR, f"{moment}_{k}.wav")
            if not os.path.exists(path):
                log.info("sound %s missing (run tools/make_sounds.py)", path)
                return False
            if self.proc and self.proc.poll() is None:
                self.proc.terminate()                         # one voice at a time
            try:
                self.proc = subprocess.Popen(["afplay", "-v", str(cfg.get("volume", 1.0)), path],
                                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except Exception as e:
                log.info("afplay failed: %s", e); return False
            self.last_any = self.last_at[moment] = now
            self.last_pick[moment] = k
            log.info("sound %s %s", moment, k)
            return True


def _hex(c): return [int(c[i:i + 2], 16) for i in (0, 2, 4)]
def _mix(a, b, k): A, B = _hex(a), _hex(b); return "".join(f"{round(x + (y - x) * k):02X}" for x, y in zip(A, B))


# --- vivid blending: OKLCH, around the colour wheel (a straight RGB blend from orange toward
# blue/teal passes through grey). Same maths as the colour lab on design/lump.html.
def _s2l(c): return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
def _l2s(c): return 12.92 * c if c <= 0.0031308 else 1.055 * c ** (1 / 2.4) - 0.055


def hex_to_oklch(h):
    import math
    r, g, b = (_s2l(int(h[i:i + 2], 16) / 255) for i in (0, 2, 4))
    l = (0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b) ** (1 / 3)
    m = (0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b) ** (1 / 3)
    s = (0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b) ** (1 / 3)
    L = 0.2104542553 * l + 0.7936177850 * m - 0.0040720468 * s
    A = 1.9779984951 * l - 2.4285922050 * m + 0.4505937099 * s
    B = 0.0259040371 * l + 0.7827717662 * m - 0.8086757660 * s
    return L, math.hypot(A, B), (math.degrees(math.atan2(B, A)) + 360) % 360


def oklch_to_hex(L, C, H):
    import math
    for _ in range(40):                       # pull chroma in until it fits sRGB
        A, B = C * math.cos(math.radians(H)), C * math.sin(math.radians(H))
        l = (L + 0.3963377774 * A + 0.2158037573 * B) ** 3
        m = (L - 0.1055613458 * A - 0.0638541728 * B) ** 3
        s = (L - 0.0894841775 * A - 1.2914855480 * B) ** 3
        rgb = [4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s,
               -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s,
               -0.0041960863 * l - 0.7034186147 * m + 1.7076147010 * s]
        if all(-0.002 <= v <= 1.002 for v in rgb):
            return "".join(f"{round(_l2s(min(1, max(0, v))) * 255):02X}" for v in rgb)
        C *= 0.93
    return "888888"


def meeting_blend(base, t):
    """Countdown colour: from his current colour, hue going down (through pink and purple) to blue."""
    L1, C1, H1 = hex_to_oklch(base)
    L2, C2, H2 = hex_to_oklch(MEETING_COLOUR)
    dh = H2 - H1
    if dh > 0: dh -= 360
    return oklch_to_hex(L1 + (L2 - L1) * t, max(0.13, C1 + (C2 - C1) * t), (H1 + dh * t + 360) % 360)


def usage_colour(frac):
    frac = max(0.0, min(1.0, frac))
    for (f0, c0), (f1, c1) in zip(USAGE_STOPS, USAGE_STOPS[1:]):
        if frac <= f1:
            return _mix(c0, c1, 0 if f1 == f0 else (frac - f0) / (f1 - f0))
    return USAGE_STOPS[-1][1]


class Buddy:
    def __init__(self):
        self.lock = threading.Lock()
        self.sessions = {}            # session_id -> {"state", "t", "cwd"}
        self.last_event = 0.0
        self.sent_mood = None
        self.sent_overlay = None
        self.celebrate_until = 0.0
        self.usage = {}                # from the status line: five_hour / seven_day pct + resets
        self.sent_colour = None
        self.meeting_alerted = set()   # meeting start times we've already announced
        self.last_oops = 0.0
        self.resync_at = 0.0
        self.sounds = Sounds()
        self.last_active = 0.0         # last time you did something (prompt, answer, new session)
        self.goodnight_night = None    # which night we've already said goodnight for
        self.woke_at = 0.0
        self.working_since, self.long_played = None, False
        self.next_fidget = time.time() + random.uniform(*FIDGET_EVERY)
        self.focus = Focus()
        self.meeting_dismissed = set()            # meeting starts you've tapped to silence
        self.dev = Device(self.resend, self.board_line)
        threading.Thread(target=self._tick, daemon=True).start()

    # ---- session bookkeeping ----
    def hook(self, p):
        ev, sid, now = p.get("hook_event_name", ""), p.get("session_id", "?"), time.time()
        event = None
        with self.lock:
            fresh = now - self.last_event > HELLO_AFTER
            self.last_event = now
            s = self.sessions.setdefault(sid, {"state": "idle", "t": now, "cwd": p.get("cwd", "")})
            state = None
            if ev == "SessionStart":
                state = "idle"
                if p.get("source") in (None, "startup", "resume"):
                    self.last_active = now
                if fresh and self.mood_locked(now) in ("idle", "night"):
                    event = "hello"
            elif ev == "UserPromptSubmit":
                state = "working"
                s.pop("unseen", None)           # you're back in this session: result seen
                self.last_active = now
                self.sound("on_it")
            elif ev == "PreToolUse":
                state = "alert" if p.get("tool_name") in ("AskUserQuestion", "ExitPlanMode") else "working"
            elif ev in ("PermissionRequest", "Elicitation"):
                state = "alert"
            elif ev in ("PostToolUse", "PermissionDenied", "ElicitationResult"):
                state = "working"
            elif ev == "PostToolUseFailure":
                state = "working"
                if now - self.last_oops > OOPS_GAP and not p.get("is_interrupt"):
                    event = "oops"                # only played if he's showing "working" (see below)
            elif ev == "Notification":
                kind = (p.get("notification_type") or "").lower()
                if kind in NEEDS_YOU:
                    state = "alert"
                # idle_prompt ("still waiting for your input") and the rest aren't new requests: ignore
            elif ev in ("Stop", "StopFailure"):
                state = "done"
            elif ev == "SessionEnd":
                self.sessions.pop(sid, None)
            if state:
                self._set(s, state, now)
            if event == "oops":
                # a failure never interrupts "needs you": only show it while he's working
                if self.base_locked(now) == "working":
                    self.last_oops = now
                else:
                    event = None
            log.info("hook %s %s -> %s", ev, sid[:8], state or "-")
        if event:
            self.event(event)
            self.sound({"hello": "hello", "oops": "uh_oh"}[event])
        self.update()

    def external(self, sid, state, stale=None):
        """State from a non-hook source (the app watcher for Chat and Cowork)."""
        now = time.time()
        with self.lock:
            fresh = now - self.last_event > HELLO_AFTER
            self.last_event = now
            s = self.sessions.setdefault(sid, {"state": "idle", "t": now, "cwd": ""})
            if state in ("working", "alert") and s.get("state") == "done":
                s.pop("unseen", None)           # a new message in that conversation: result seen
            if state == "working" and s.get("state") in ("idle", "done"):
                self.last_active = now          # you just sent a message there
            self._set(s, state, now)
            s["stale"] = stale
            log.info("app %s -> %s", sid[:40], state)
        self.update()

    def forget(self, sid):
        with self.lock:
            if self.sessions.pop(sid, None) is not None:
                log.info("app %s -> gone", sid[:40])
        self.update()

    def _set(self, s, state, now):
        """Record a session's new state; a finish starts a celebration and a "check me" mark."""
        was = s.get("state")
        s["state"], s["t"] = state, now
        if state == "alert" and was != "alert":
            s.pop("nagged", None)
            s.pop("snooze", None)                   # a new request: no longer snoozed
            self.sound("needs_you")
        elif was == "alert" and state == "working":
            self.last_active = now                  # you answered: he's back to work
            self.sound("thanks")
        if state == "done" and was != "done":
            watching = self.on_screen_locked(s)
            if not watching:
                s["unseen"] = now                   # finished while you weren't looking at it
            # celebrate briefly if other things are still going on, longer if it's all quiet
            busy = self.base_locked(now, skip=s) in ("alert", "working")
            self.celebrate_until = max(self.celebrate_until, now + (CELEBRATE_SECS if busy else DONE_SECS))
            if not watching:                        # you can already see that reply: no need to call out
                self.sound("finished_while_busy" if busy else "finished")

    def board_line(self, text):
        if text == "tap":
            self.tapped()

    def tapped(self):
        """You tapped the glass. What it means depends on what he's showing:
        meeting alarm -> silence it; needs you -> "seen it", snooze 2 min (amber dots stay);
        awake with unread results -> mark them read so he can sleep. Otherwise the board giggles."""
        now = time.time()
        with self.lock:
            mood, base = self.mood_locked(now), self.base_locked(now)
            m = self.next_meeting(now)
            if mood == "meeting" and m:
                self.meeting_dismissed.add(m["start"]); what = "meeting alarm silenced"
            elif base == "alert":
                for st in self.sessions.values():
                    if self._effective(st, now) == "alert":
                        st["snooze"] = now + TAP_SNOOZE
                what = "needs-you snoozed"
            elif base == "awake":
                for st in self.sessions.values():
                    st.pop("unseen", None)
                what = "marked as read"
            else:
                what = "giggle"
            self.last_active = now
        log.info("tap: %s", what)
        self.update()

    def sound(self, moment):
        """Play a moment's sound, unless it's night and you've not been around for a while
        (goodnight is the exception: it's the last thing he says)."""
        now = time.time()
        h = time.localtime(now).tm_hour
        night = h >= NIGHT_START or h < NIGHT_END
        if (night and moment != "goodnight" and now - self.last_active > NIGHT_AWAKE
                and self.sounds.settings().get("quiet_at_night", True)):
            return False
        return self.sounds.play(moment)

    def on_screen_locked(self, s):
        """Is this session's conversation showing in the Claude app, with the app in front?"""
        if not self.focus.front:
            return False
        sid = next((k for k, v in self.sessions.items() if v is s), None)
        if sid is None: return False
        if sid.startswith("app:"):
            app = getattr(self, "app", None)
            return bool(app) and sid[4:] in app.visible
        return sid == self.focus.code_session

    def clear_seen_locked(self):
        for s in self.sessions.values():
            if s.get("unseen") and self.on_screen_locked(s):
                s.pop("unseen", None)

    def _effective(self, s, now):
        st, age = s["state"], now - s["t"]
        limit = s.get("stale") or STALE.get(st)
        if st in STALE and limit and age > limit:
            return "idle"
        if st == "alert" and s.get("snooze", 0) > now:
            return "snoozed"                        # you tapped to say "seen it": hold off nagging
        return "idle" if st == "done" else st

    def counts_locked(self, now):
        for sid, s in list(self.sessions.items()):
            if now - s["t"] > FORGET:
                del self.sessions[sid]
        self.clear_seen_locked()                    # looking at it counts as reading it
        eff = [self._effective(s, now) for s in self.sessions.values()]
        unseen = sum(1 for s in self.sessions.values() if s.get("unseen") and now - s["unseen"] < UNSEEN_SECS)
        return {"alert": eff.count("alert") + eff.count("snoozed"), "working": eff.count("working"), "unseen": unseen}

    def base_locked(self, now, skip=None):
        """Most urgent ongoing state across sessions: alert > working > awake (something finished
        that you haven't looked at yet) > asleep (idle, or night)."""
        eff = {self._effective(s, now) for s in self.sessions.values() if s is not skip}
        if "alert" in eff: return "alert"
        if "working" in eff: return "working"
        if "snoozed" in eff or any(s.get("unseen") and now - s["unseen"] < UNSEEN_SECS for s in self.sessions.values() if s is not skip):
            return "awake"
        h = time.localtime(now).tm_hour
        return "night" if (h >= NIGHT_START or h < NIGHT_END) else "idle"

    def next_meeting(self, now):
        try:
            with open(MEETINGS_FILE) as f:
                ms = json.load(f)
        except Exception:
            return None
        live = [m for m in ms if m.get("start") and now < m["start"] + MEETING_HOLD]
        return min(live, key=lambda m: m["start"]) if live else None

    def mood_locked(self, now):
        self.counts_locked(now)                 # also drops forgotten sessions
        m = self.next_meeting(now)
        if m and 0 <= now - m["start"] < MEETING_ALERT_SECS and m["start"] not in self.meeting_dismissed:
            return "meeting"                    # it's time: this beats everything, briefly
        if now < self.celebrate_until:
            return "done"                       # a finish just happened: celebrate, then back to base
        return self.base_locked(now)

    def mood(self):
        with self.lock:
            return self.mood_locked(time.time())

    # ---- talking to the board ----
    def update(self, force=False):
        with self.lock:
            now = time.time()
            m, c = self.mood_locked(now), self.counts_locked(now)
        if force or m != self.sent_mood:
            if m == "night" and self.sent_mood not in (None, "night"):
                night = time.strftime("%Y-%m-%d", time.localtime(now - NIGHT_END * 3600))
                if self.goodnight_night != night:        # once a night, however often he dozes off
                    self.goodnight_night = night
                    self.sound("goodnight")
            if self.dev.send("s " + m):
                self.sent_mood = m
        colour = self.colour(now)
        if force or colour != self.sent_colour:
            if self.dev.send("c " + colour):
                self.sent_colour = colour
        # corner extras on the display: things working / waiting / finished-but-unseen
        overlay = f"o {c['working']} {c['alert']} {c['unseen']}"
        if force or overlay != self.sent_overlay:
            if self.dev.send(overlay):
                self.sent_overlay = overlay

    def usage_frac(self, now):
        fr = [w["pct"] / 100 for w in self.usage.values() if w.get("pct") is not None
              and ((w.get("resets") and now < w["resets"]) or (not w.get("resets") and now - w["t"] < 12 * 3600))]
        return max(fr) if fr else None

    def colour(self, now):
        """Meeting countdown beats the usage gauge, which beats plain Tangerine."""
        base = DEFAULT_COLOUR
        frac = self.usage_frac(now)
        if frac is not None:
            base = usage_colour(round(frac * 50) / 50)      # 2% steps: no flicker
        m = self.next_meeting(now)
        if m:
            until = m["start"] - now
            if until <= 0:
                return MEETING_COLOUR
            if until < MEETING_LEAD:
                return meeting_blend(base, round((1 - until / MEETING_LEAD) * 40) / 40)
        return base

    def set_usage(self, rl, source="statusline"):
        """Usage readings from the Claude Code status line, or the Claude app's usage ring."""
        changed = False
        with self.lock:
            for key in ("five_hour", "seven_day"):
                w = (rl or {}).get(key)
                if isinstance(w, dict) and w.get("used_percentage") is not None:
                    old = self.usage.get(key, {})
                    resets = w.get("resets_at") or (old.get("resets") if source == "app" else None)
                    new = {"pct": float(w["used_percentage"]), "resets": resets, "t": time.time(), "source": source}
                    changed |= old.get("pct") != new["pct"]
                    self.usage[key] = new
        if changed:
            log.info("usage %s", {k: round(v["pct"]) for k, v in self.usage.items()})
        self.update()

    def resend(self):
        self.sent_mood = self.sent_overlay = self.sent_colour = None
        if time.time() - self.woke_at > 60:            # board plugged in or restarted
            self.woke_at = time.time()
            self.sound("waking_up")
        self.update()

    def sound_tick(self, now):
        """Sounds that come from time passing rather than from a hook."""
        play = []
        with self.lock:
            for s in self.sessions.values():                 # one nudge per unanswered request
                if s["state"] == "alert" and not s.get("nagged") and now - s["t"] >= STILL_WAITING_AFTER \
                        and self._effective(s, now) == "alert":
                    s["nagged"] = True
                    play.append("still_waiting")
            base = self.base_locked(now)
            if base == "working":
                self.working_since = self.working_since or now
                if now - self.working_since >= LONG_JOB_AFTER and not self.long_played:
                    self.long_played = True
                    play.append("long_job")
            else:
                self.working_since, self.long_played = None, False
            if now >= self.next_fidget:
                self.next_fidget = now + random.uniform(*FIDGET_EVERY)
                if base == "idle" and now - self.last_active < 30 * 60:   # only if you're around
                    play.append("fidget")
        for moment in dict.fromkeys(play):
            self.sound(moment)

    def event(self, name):
        # an event plays in its home mood (oops: working, hello: idle); the board walks
        # there by itself, so re-assert our mood once it has finished
        if self.dev.send("e " + name):
            self.resync_at = time.time() + 8

    def _tick(self):
        while True:
            time.sleep(1)
            if self.resync_at and time.time() > self.resync_at:
                self.resync_at = 0; self.update(force=True)
            else:
                self.update()
            try: self.sound_tick(time.time())
            except Exception as e: log.info("sound tick failed: %s", e)

    def status(self):
        with self.lock:
            now = time.time()
            app = getattr(self, "app", None)
            return {"mood": self.mood_locked(now), "sent": self.sent_mood, "counts": self.counts_locked(now), "device": self.dev.connected,
                    "colour": self.colour(now), "usage": self.usage,
                    "sound": bool(self.sounds.settings().get("enabled")),
                    "next_meeting": self.next_meeting(now),
                    "focus": {"claude_in_front": self.focus.front, "code_session": (self.focus.code_session or "")[:8],
                              "app_pages": sorted(getattr(getattr(self, "app", None), "visible", []))},
                    "app_watcher": None if not app or not app.seen else {"trusted": app.trusted, "last_report_s": int(now - app.seen)},
                    "sessions": {(k if k.startswith("app:") else k[:8]): {"state": v["state"], "age_s": int(now - v["t"]), "cwd": v["cwd"]} for k, v in self.sessions.items()}}


class AppPages:
    """Chat and Cowork state from the deskbuddy-appwatch helper (tools/appwatch), which reports raw
    labels read from the Claude app's accessibility tree. The rules for turning those labels into
    moods live here, so they can be tuned without rebuilding (and re-permitting) the helper.

    Each conversation becomes a session "app:<name>". Sidebar status badges cover conversations
    that aren't on screen; a page that leaves the screen while working gets a short expiry."""

    APP_STALE = 3 * 60
    STOP_BTN = re.compile(r"^stop( response| generating)?$", re.I)
    NEEDS_BTN = re.compile(r"\b(allow|approve|deny|don.?t allow|reject|decline)\b", re.I)
    WORK_STATUS = re.compile(r"^(claude is (responding|thinking)|running a command.*|working…?|running|thinking|responding)$", re.I)
    USAGE_LABEL = re.compile(r"^usage:\s*(.+?):\s*(\d+(?:\.\d+)?)\s*%", re.I)
    TAB_NEEDS = re.compile(r"awaiting|needs you|needs your", re.I)
    TAB_WORK = re.compile(r"working|running|responding|thinking", re.I)
    NEEDS_TEXT = re.compile(r"asking a question|awaiting (answer|approval|permission|response)|needs (approval|permission|an answer)|approval needed|waiting for (your )?(approval|answer|permission)", re.I)

    def __init__(self, buddy):
        self.buddy, self.last, self.trusted, self.seen, self.tabs = buddy, {}, None, 0.0, []
        self.visible = set()                        # conversation names currently on screen

    @staticmethod
    def name(title):
        return (title[:-len(" - Claude")] if title.endswith(" - Claude") else title) or "Claude"

    def snapshot(self, snap):
        self.seen = time.time()
        if snap.get("trusted") != self.trusted:
            self.trusted = snap.get("trusted")
            log.info("app watcher %s", "has Accessibility permission" if self.trusted else "is waiting for Accessibility permission")
        if (snap.get("tabs") or []) != self.tabs:  # logged to learn what the mode tabs announce
            self.tabs = snap.get("tabs") or []
            log.info("app tabs: %s", " | ".join(self.tabs))
        self.visible = {self.name(t) for t in (snap.get("pages") or {})}
        # usage ring in the app, e.g. "Usage: Weekly · all models: 25%, Resets Mon 8:00 AM"
        for lab in snap.get("labels") or []:
            m = self.USAGE_LABEL.match(lab)
            if m:
                key = "seven_day" if re.search(r"week", m.group(1), re.I) else "five_hour"
                self.buddy.set_usage({key: {"used_percentage": float(m.group(2)), "resets_at": None}}, source="app")
        states = {}
        def bump(name, st):
            rank = {"working": 1, "alert": 2}
            if rank[st] > rank.get(states.get(name), 0):
                states[name] = st
        for title, pg in (snap.get("pages") or {}).items():
            n = self.name(title)
            states.setdefault(n, None)
            if any(self.NEEDS_BTN.search(b) for b in pg.get("buttons", [])) or any(self.NEEDS_TEXT.search(m) for m in pg.get("markers", [])):
                bump(n, "alert")
            if any(self.STOP_BTN.match(b) for b in pg.get("buttons", [])) or any(self.WORK_STATUS.match(x) for x in pg.get("statuses", [])):
                bump(n, "working")
        # Section tabs announce sections you're not looking at, e.g. "Claude, awaiting your input"
        # while you're in Code: that's what covers Chat and Cowork in the background. (The Code tab
        # is ignored: hooks already cover Code, with more detail.)
        for t in snap.get("tabs") or []:
            sec, sep, st = t.partition(",")
            if not sep or sec.strip().lower() in ("code", ""): continue
            n = "section " + sec.strip()
            if self.NEEDS_TEXT.search(st) or self.TAB_NEEDS.search(st): bump(n, "alert")
            elif self.WORK_STATUS.match(st.strip()) or self.TAB_WORK.search(st): bump(n, "working")
        for r in snap.get("sidebar") or []:
            n = r.get("row") or "Claude"
            st = r.get("status", "")
            states.setdefault(n, None)
            if self.NEEDS_TEXT.search(st): bump(n, "alert")
            elif self.WORK_STATUS.match(st): bump(n, "working")
        for n, state in states.items():
            prev = self.last.get(n)
            if state != prev:
                if state:
                    self.buddy.external("app:" + n, state)
                elif prev in ("working", "alert"):
                    self.buddy.external("app:" + n, "done")
                self.last[n] = state
        for n in [x for x in self.last if x not in states]:    # no longer on screen or in the sidebar
            # A conversation that still needs you keeps its sidebar badge, so if it vanished
            # entirely it was answered elsewhere, closed or deleted: drop it. Work in progress
            # we simply can't see any more gets a short grace period instead.
            if self.last[n] == "working":
                self.buddy.external("app:" + n, "working", stale=self.APP_STALE)
            else:
                self.buddy.forget("app:" + n)
            del self.last[n]


buddy = None


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _reply(self, code, body=b"", ctype="text/plain"):
        self.send_response(code); self.send_header("Content-Type", ctype); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", 0) or 0))
        if self.path == "/hook":
            self._reply(204)                      # answer first so the hook never waits on us
            try: buddy.hook(json.loads(body or b"{}"))
            except Exception as e: log.info("bad hook payload: %s", e)
        elif self.path == "/app":
            self._reply(204)
            try: buddy.app.snapshot(json.loads(body or b"{}"))
            except Exception as e: log.info("bad app snapshot: %s", e)
        elif self.path == "/usage":               # from tools/statusline.sh (status line JSON)
            self._reply(204)
            try: buddy.set_usage((json.loads(body or b"{}") or {}).get("rate_limits"))
            except Exception as e: log.info("bad usage payload: %s", e)
        elif self.path == "/calendar":            # re-read the calendar now
            buddy.calendar.refresh_now(); self._reply(200, b"refreshing")
        elif self.path == "/cmd":
            ok = buddy.dev.send(body.decode().strip()); self._reply(200 if ok else 503, b"ok" if ok else b"board not connected")
        elif self.path == "/sound":               # on | off | toggle | play <moment>
            cmd = body.decode().strip().split()
            if cmd and cmd[0] in ("on", "off", "toggle"):
                on = buddy.sounds.set_enabled({"on": True, "off": False}.get(cmd[0], "toggle"))
                self._reply(200, b"on" if on else b"off")
            elif len(cmd) == 2 and cmd[0] == "play":
                ok = buddy.sounds.play(cmd[1], force=True); self._reply(200 if ok else 404, b"ok" if ok else b"no such sound")
            else:
                self._reply(400, b"use: on | off | toggle | play <moment>")
        else:
            self._reply(404)

    def do_GET(self):
        if self.path == "/status":
            self._reply(200, json.dumps(buddy.status(), indent=2).encode(), "application/json")
        elif self.path == "/sound":
            self._reply(200, json.dumps(buddy.sounds.settings(), indent=2).encode(), "application/json")
        else:
            self._reply(404)


if __name__ == "__main__":
    buddy = Buddy()
    buddy.app = AppPages(buddy)
    buddy.calendar = Calendar()
    log.info("desk-buddy bridge listening on 127.0.0.1:%d", HTTP_PORT)
    http.server.ThreadingHTTPServer(("127.0.0.1", HTTP_PORT), Handler).serve_forever()
