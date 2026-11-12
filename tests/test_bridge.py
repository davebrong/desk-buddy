"""Tests for the bridge's mood rules, heartbeat and saved state.

Run with the bridge's own Python (it has pyserial):
    ~/.desk-buddy/venv/bin/python -m unittest discover -s tests -v
Nothing here touches the real board, speakers or ~/.desk-buddy: the bridge is pointed at a temp
folder and given fake device / focus / sound objects.
"""
import importlib.util, json, os, sys, tempfile, time, unittest
from unittest import mock

HOME = tempfile.mkdtemp(prefix="deskbuddy-test-")
os.environ["DESKBUDDY_HOME"] = HOME
_spec = importlib.util.spec_from_file_location(
    "buddy_bridge", os.path.join(os.path.dirname(__file__), "..", "tools", "buddy_bridge.py"))
bridge = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bridge)

# a fixed moment at local noon, so "night" never kicks in by accident
NOON = time.mktime(time.struct_time((2026, 11, 11, 12, 0, 0, 0, 0, -1)))


class FakeDevice:
    connected = True
    def __init__(self): self.sent = []
    def send(self, cmd, quiet=False): self.sent.append(cmd); return True


class FakeFocus:
    front, code_session = False, None


class FakeSounds:
    def __init__(self): self.played = []
    def settings(self): return {}
    def play(self, moment, force=False): self.played.append(moment); return True


class Clock:
    """Stands in for time.time() inside the bridge."""
    def __init__(self, t): self.t = t
    def __call__(self): return self.t
    def advance(self, s): self.t += s


class BridgeTest(unittest.TestCase):
    def setUp(self):
        for f in os.listdir(HOME):
            os.remove(os.path.join(HOME, f))
        self.clock = Clock(NOON)
        p = mock.patch.object(bridge.time, "time", self.clock)
        p.start(); self.addCleanup(p.stop)
        self.b = self.make()

    def make(self):
        return bridge.Buddy(dev=FakeDevice(), focus=FakeFocus(), sounds=FakeSounds(), start=False)

    def hook(self, ev, sid, **extra):
        self.b.hook({"hook_event_name": ev, "session_id": sid, "cwd": "/x", **extra})

    def mood(self):
        return self.b.mood()


class MoodRules(BridgeTest):
    def test_idle_with_nothing_going_on(self):
        self.assertEqual(self.mood(), "idle")

    def test_working_after_a_prompt(self):
        self.hook("UserPromptSubmit", "a")
        self.assertEqual(self.mood(), "working")

    def test_needs_you_beats_working(self):
        self.hook("UserPromptSubmit", "a")
        self.hook("UserPromptSubmit", "b")
        self.hook("PermissionRequest", "b")
        self.assertEqual(self.mood(), "alert")
        c = self.b.counts_locked(self.clock())
        self.assertEqual((c["working"], c["alert"]), (1, 1))

    def test_question_tool_counts_as_needs_you(self):
        self.hook("PreToolUse", "a", tool_name="AskUserQuestion")
        self.assertEqual(self.mood(), "alert")

    def test_answering_goes_back_to_work(self):
        self.hook("PermissionRequest", "a")
        self.hook("PostToolUse", "a")
        self.assertEqual(self.mood(), "working")

    def test_finish_while_others_busy_is_a_short_celebration(self):
        self.hook("UserPromptSubmit", "a")
        self.hook("UserPromptSubmit", "b")
        self.hook("Stop", "b")
        self.assertEqual(self.mood(), "done")
        self.clock.advance(bridge.CELEBRATE_SECS + 0.1)
        self.assertEqual(self.mood(), "working")

    def test_finish_when_quiet_celebrates_longer_then_waits_to_be_seen(self):
        self.hook("UserPromptSubmit", "a")
        self.hook("Stop", "a")
        self.clock.advance(bridge.CELEBRATE_SECS + 0.1)
        self.assertEqual(self.mood(), "done")
        self.clock.advance(bridge.DONE_SECS)
        self.assertEqual(self.mood(), "awake")                 # finished but you haven't looked
        self.assertEqual(self.b.counts_locked(self.clock())["unseen"], 1)

    def test_next_prompt_marks_result_seen(self):
        self.hook("UserPromptSubmit", "a")
        self.hook("Stop", "a")
        self.clock.advance(bridge.DONE_SECS + 1)
        self.hook("UserPromptSubmit", "a")
        self.assertEqual(self.b.counts_locked(self.clock())["unseen"], 0)

    def test_unseen_mark_expires(self):
        self.hook("UserPromptSubmit", "a")
        self.hook("Stop", "a")
        self.clock.advance(bridge.UNSEEN_SECS + 1)
        self.assertEqual(self.mood(), "idle")

    def test_silent_working_session_goes_stale(self):
        self.hook("UserPromptSubmit", "a")
        self.clock.advance(bridge.STALE["working"] + 1)
        self.assertEqual(self.mood(), "idle")

    def test_session_end_forgets_it(self):
        self.hook("PermissionRequest", "a")
        self.hook("SessionEnd", "a")
        self.assertEqual(self.mood(), "idle")

    def test_failure_never_interrupts_needs_you(self):
        self.hook("PermissionRequest", "a")
        self.hook("PostToolUseFailure", "b")
        self.assertFalse(any(c.startswith("e oops") for c in self.b.dev.sent))

    def test_failure_while_working_plays_oops(self):
        self.hook("UserPromptSubmit", "a")
        self.hook("PostToolUseFailure", "a")
        self.assertIn("e oops", self.b.dev.sent)

    def test_tap_snoozes_needs_you_but_keeps_the_count(self):
        self.hook("PermissionRequest", "a")
        self.b.tapped()
        self.assertNotEqual(self.mood(), "alert")
        self.assertEqual(self.b.counts_locked(self.clock())["alert"], 1)
        self.clock.advance(bridge.TAP_SNOOZE + 1)
        self.assertEqual(self.mood(), "alert")

    def test_night(self):
        self.clock.t = NOON + 13 * 3600                         # 01:00
        self.assertEqual(self.mood(), "night")

    def test_night_is_midnight_to_eight(self):
        for hour, want in ((23, "idle"), (0, "night"), (7, "night"), (8, "idle")):
            self.clock.t = NOON + (hour - 12) % 24 * 3600 + 30 * 24 * 3600   # well after anything happened
            self.assertEqual(self.mood(), want, f"{hour}:00")

    def test_regular_sleep_for_a_few_minutes_before_night(self):
        self.clock.t = NOON + 13 * 3600                         # 01:00, busy
        self.hook("PermissionRequest", "a")
        self.assertEqual(self.mood(), "alert")
        self.hook("SessionEnd", "a")
        self.clock.advance(4 * 60)
        self.assertEqual(self.mood(), "idle")                   # just been up: plain sleep first
        self.clock.advance(2 * 60)
        self.assertEqual(self.mood(), "night")                  # then the blanket comes up


class MeetingPrep(BridgeTest):
    """While a meeting is coming up (or its alarm is ringing) the board is told "p 1", so a tap plays meeting prep."""
    def meeting_in(self, secs):
        with open(bridge.MEETINGS_FILE, "w") as f:
            json.dump([{"start": self.clock() + secs, "title": "standup"}], f)

    def prep_flags(self):
        return [c for c in self.b.dev.sent if c.startswith("p ")]

    def test_no_meeting_means_no_prep(self):
        self.b.update()
        self.assertEqual(self.prep_flags(), ["p 0"])

    def test_countdown_turns_prep_on_then_off_after_the_alarm(self):
        self.meeting_in(30 * 60)
        self.b.update()
        self.clock.advance(21 * 60); self.b.update()              # 9 minutes to go: countdown
        self.clock.advance(9 * 60 + 10); self.b.update()           # alarm ringing
        self.clock.advance(60); self.b.update()                    # alarm window over
        self.assertEqual(self.prep_flags(), ["p 0", "p 1", "p 0"])

    def test_resend_repeats_the_prep_flag(self):
        self.meeting_in(5 * 60)
        self.b.update(); self.b.dev.sent.clear()
        self.b.resend()
        self.assertIn("p 1", self.b.dev.sent)


class AppWatcher(BridgeTest):
    """Chat/Cowork state read from the Claude app's screen."""
    def snap(self, title, buttons=(), statuses=()):
        self.b_pages.snapshot({"trusted": True, "pages": {title: {"buttons": list(buttons), "statuses": list(statuses), "markers": []}}})

    def setUp(self):
        super().setUp()
        self.b_pages = bridge.AppPages(self.b)

    def test_claudes_stop_button_means_a_chat_is_working(self):
        self.snap("Planning - Claude", buttons=["Stop response"])
        self.assertEqual(self.mood(), "working")

    def test_a_web_page_with_its_own_stop_button_is_not_a_chat(self):
        self.snap("Lump Gag Sounds", buttons=["Play all", "Stop"])
        self.assertEqual(self.mood(), "idle")


class Heartbeat(BridgeTest):
    def beats(self):
        return [c for c in self.b.dev.sent if c.startswith("h ")]

    def test_heartbeat_carries_time_of_day(self):
        self.b.tick(self.clock())
        self.assertEqual(self.beats(), ["h 43200"])             # noon

    def test_heartbeat_is_paced(self):
        self.b.tick(self.clock())
        self.clock.advance(5); self.b.tick(self.clock())
        self.assertEqual(len(self.beats()), 1)
        self.clock.advance(bridge.HEARTBEAT_EVERY); self.b.tick(self.clock())
        self.assertEqual(len(self.beats()), 2)

    def test_resend_beats_straight_away(self):
        self.b.tick(self.clock())
        self.clock.advance(1)
        self.b.resend()
        self.b.tick(self.clock())
        self.assertEqual(len(self.beats()), 2)

    def test_resend_repeats_mood_colour_and_overlay(self):
        self.b.update()
        self.b.dev.sent.clear()
        self.b.resend()
        kinds = {c.split()[0] for c in self.b.dev.sent}
        self.assertTrue({"s", "c", "o"} <= kinds, self.b.dev.sent)

    def test_a_broken_sound_doesnt_stop_heartbeat_or_saving(self):
        with mock.patch.object(self.b, "sound_tick", side_effect=RuntimeError("boom")):
            self.b.tick(self.clock())
        self.assertEqual(len(self.beats()), 1)
        self.assertTrue(os.path.exists(bridge.STATE_FILE))


class SavedState(BridgeTest):
    def test_sessions_survive_a_restart(self):
        self.hook("PermissionRequest", "a")
        self.hook("UserPromptSubmit", "b")
        self.b.save_state()
        again = self.make()
        self.assertEqual(again.mood(), "alert")
        self.assertEqual(set(again.sessions), {"a", "b"})

    def test_app_sessions_and_old_ones_are_not_restored(self):
        self.b.external("app:Some chat", "working")
        self.hook("UserPromptSubmit", "old")
        self.b.save_state()
        self.clock.advance(bridge.FORGET + 1)
        again = self.make()
        self.assertEqual(again.sessions, {})

    def test_no_hello_after_a_quick_restart(self):
        self.hook("UserPromptSubmit", "a")
        self.b.save_state()
        again = self.make()
        again.dev.sent.clear()
        again.hook({"hook_event_name": "SessionStart", "session_id": "b", "source": "startup"})
        self.assertNotIn("e hello", again.dev.sent)

    def test_unreadable_state_is_ignored(self):
        with open(bridge.STATE_FILE, "w") as f:
            f.write("{not json")
        self.assertEqual(self.make().sessions, {})

    def test_only_writes_when_something_changed(self):
        self.b.save_state()
        m = os.path.getmtime(bridge.STATE_FILE)
        os.utime(bridge.STATE_FILE, (m - 100, m - 100))
        self.b.save_state()
        self.assertEqual(os.path.getmtime(bridge.STATE_FILE), m - 100)


class DeviceLinks(unittest.TestCase):
    def device(self):
        d = bridge.Device.__new__(bridge.Device)               # no thread, no real ports
        d._last_err = {}
        return d

    def write_config(self, cfg):
        with open(bridge.CONFIG_FILE, "w") as f:
            json.dump(cfg, f)
        self.addCleanup(lambda: os.path.exists(bridge.CONFIG_FILE) and os.remove(bridge.CONFIG_FILE))

    def test_callback_errors_are_contained(self):
        def bad(): raise RuntimeError("boom")
        bridge.Device._call(bad)                                # must not raise

    def test_usb_first_then_wifi_when_paired(self):
        self.write_config({"lump_token": "abcdef123456"})
        with mock.patch.object(bridge.Device, "usb_ports", staticmethod(lambda: ["/dev/cu.usbmodem1"])):
            self.assertEqual(self.device()._targets(), ["/dev/cu.usbmodem1", "socket://lump.local:4210"])

    def test_no_wifi_without_pairing(self):
        self.write_config({"calendar_ics": "x"})
        with mock.patch.object(bridge.Device, "usb_ports", staticmethod(lambda: [])):
            self.assertEqual(self.device()._targets(), [])

    def test_repeated_failures_are_logged_once(self):
        d = self.device()
        with self.assertLogs("bridge", "INFO") as logs:
            for _ in range(5): d._fail("socket://lump.local:4210", OSError("no route"))
            d._fail("socket://lump.local:4210", OSError("refused"))
        self.assertEqual(len(logs.output), 2)


class FakeSlack:
    """Stands in for slack.com: a few users, DMs with them, unread counts and search results."""
    def __init__(self):
        self.users = [{"id": "UME", "name": "joe", "profile": {"display_name": "Joe"}},
                      {"id": "UBOSS", "name": "sam", "real_name": "Sam Boss", "profile": {"display_name": "Sam"}},
                      {"id": "UPAL", "name": "pat", "real_name": "Pat Pal", "profile": {}},
                      {"id": "UBOT", "name": "jira", "is_bot": True, "profile": {}}]
        self.ims = {"UBOSS": "DBOSS", "UPAL": "DPAL"}
        self.unread = {"DBOSS": 0, "DPAL": 0}
        self.latest = {"DBOSS": "1.0", "DPAL": "1.0"}
        self.found = {"to:me": [], "<@UME>": []}
        self.calls = []

    def __call__(self, method, **p):
        self.calls.append((method, p))
        if method == "auth.test": return {"ok": True, "user_id": "UME"}
        if method == "users.list": return {"ok": True, "members": self.users}
        if method == "users.conversations": return {"ok": True, "channels": [{"id": c, "user": u} for u, c in self.ims.items()]}
        if method == "conversations.info":
            ch = p["channel"]
            return {"ok": True, "channel": {"id": ch, "unread_count_display": self.unread[ch], "latest": {"ts": self.latest[ch]}, "last_read": "0"}}
        if method == "search.messages": return {"ok": True, "messages": {"matches": self.found[p["query"]]}}
        raise AssertionError("unexpected call " + method)

    def message(self, query, user, ts, ch="DPAL", im=True, **extra):
        self.found[query].insert(0, {"user": user, "ts": "%.6f" % ts, "channel": {"id": ch, "is_im": im}, "text": "secret words", **extra})


class SlackTest(BridgeTest):
    def setUp(self):
        super().setUp()
        with open(bridge.CONFIG_FILE, "w") as f:
            json.dump({"slack_token": "xoxp-test-token", "slack_vips": ["Sam Boss"]}, f)
        self.api = FakeSlack()
        self.w = bridge.SlackWatch(self.b, api=self.api, start=False)
        p = mock.patch.object(bridge.SlackWatch, "slack_in_front", staticmethod(lambda: self.in_slack))
        p.start(); self.addCleanup(p.stop)
        self.in_slack = False

    def poll(self):
        self.w.poll(self.clock())

    def planes(self):
        return self.b.dev.sent.count("e tap_plane")

    def test_vip_dm_holds_a_letter_until_read(self):
        self.poll()
        self.assertEqual(self.mood(), "idle")
        self.api.unread["DBOSS"], self.api.latest["DBOSS"] = 1, "5.0"
        self.poll()
        self.assertEqual(self.mood(), "letter")
        self.b.update()
        self.assertIn("s letter", self.b.dev.sent)
        self.clock.advance(60); self.poll()
        self.assertEqual(self.mood(), "letter")            # still unread: still holding it
        self.api.unread["DBOSS"] = 0
        self.poll()
        self.assertEqual(self.mood(), "idle")              # read in Slack: put away

    def test_letter_beats_working_but_not_needs_you(self):
        self.hook("UserPromptSubmit", "a")
        self.api.unread["DBOSS"] = 1
        self.poll()
        self.assertEqual(self.mood(), "letter")
        self.hook("PermissionRequest", "a")
        self.assertEqual(self.mood(), "alert")
        self.hook("PostToolUse", "a")
        self.assertEqual(self.mood(), "letter")

    def test_letter_times_out_and_a_newer_message_brings_it_back(self):
        self.api.unread["DBOSS"], self.api.latest["DBOSS"] = 1, "5.0"
        self.poll()
        self.clock.advance(bridge.SLACK_VIP_HOLD + 1)
        self.assertEqual(self.mood(), "idle")
        self.poll()                                         # same unread message: stays put away
        self.assertEqual(self.mood(), "idle")
        self.api.latest["DBOSS"] = "9.0"
        self.poll()
        self.assertEqual(self.mood(), "letter")

    def test_hold_time_comes_from_config(self):
        with open(bridge.CONFIG_FILE, "w") as f:
            json.dump({"slack_token": "xoxp-test-token", "slack_vips": ["Sam Boss"], "slack_vip_minutes": 5}, f)
        self.api.unread["DBOSS"] = 1
        self.poll()
        self.clock.advance(5 * 60 + 1)
        self.assertEqual(self.mood(), "idle")

    def test_letters_survive_a_restart(self):
        self.api.unread["DBOSS"] = 1
        self.poll(); self.b.save_state()
        self.b = self.make()
        self.assertEqual(self.mood(), "letter")

    def test_vips_matched_by_display_name_too(self):
        with open(bridge.CONFIG_FILE, "w") as f:
            json.dump({"slack_token": "xoxp-test-token", "slack_vips": ["sam"]}, f)
        self.api.unread["DBOSS"] = 1
        self.poll()
        self.assertEqual(self.mood(), "letter")

    def test_dm_or_mention_sends_one_plane(self):
        self.poll()
        self.clock.advance(30)
        self.api.message("to:me", "UPAL", self.clock() - 5)
        self.api.message("<@UME>", "UPAL", self.clock() - 3, ch="CGEN", im=False)
        self.poll()
        self.assertEqual(self.planes(), 1)
        self.clock.advance(30); self.poll()
        self.assertEqual(self.planes(), 1)                 # nothing new: no second plane
        self.clock.advance(bridge.PLANE_GAP)
        self.api.message("<@UME>", "UPAL", self.clock() - 1, ch="CGEN", im=False)
        self.poll()
        self.assertEqual(self.planes(), 2)

    def test_planes_are_rationed(self):
        self.api.message("to:me", "UPAL", self.clock() + 1)
        self.clock.advance(5); self.poll()
        self.api.message("to:me", "UPAL", self.clock() + 1)
        self.clock.advance(5); self.poll()
        self.assertEqual(self.planes(), 1)

    def test_no_plane_for_own_bot_old_or_vip_messages(self):
        t = self.clock()
        self.api.message("to:me", "UME", t + 1)
        self.api.message("to:me", "UBOT", t + 2)
        self.api.message("to:me", "UPAL", t + 3, bot_id="B1")
        self.api.message("to:me", "UBOSS", t + 4, ch="DBOSS")
        self.clock.advance(10); self.poll()
        self.assertEqual(self.planes(), 0)
        self.w.seen = 0                                     # Mac asleep for an hour: stale messages
        self.api.found["to:me"] = []
        self.api.message("to:me", "UPAL", t - bridge.PLANE_FRESH - 60)
        self.poll()
        self.assertEqual(self.planes(), 0)

    def test_needs_you_and_night_get_no_plane(self):
        self.hook("PermissionRequest", "a")
        self.api.message("to:me", "UPAL", self.clock() + 1)
        self.clock.advance(5); self.poll()
        self.assertEqual(self.planes(), 0)

    def test_no_plane_while_youre_in_slack_and_letter_waits_a_poll(self):
        self.in_slack = True
        self.api.message("to:me", "UPAL", self.clock() + 1)
        self.api.unread["DBOSS"] = 1
        self.clock.advance(5); self.poll()
        self.assertEqual(self.planes(), 0)
        self.assertEqual(self.mood(), "idle")              # you may be reading it right now
        self.api.unread["DBOSS"] = 0
        self.poll()
        self.assertEqual(self.mood(), "idle")              # you did: no letter at all
        self.api.unread["DBOSS"], self.api.latest["DBOSS"] = 1, "7.0"
        self.poll(); self.poll()
        self.assertEqual(self.mood(), "letter")            # still unread a poll later: letter

    def test_only_searches_for_messages_to_you(self):
        self.poll()
        methods = {m for m, _ in self.api.calls}
        self.assertNotIn("conversations.history", methods)
        self.assertEqual({p["query"] for m, p in self.api.calls if m == "search.messages"}, {"to:me", "<@UME>"})
        self.assertEqual({p["channel"] for m, p in self.api.calls if m == "conversations.info"}, {"DBOSS"})   # only VIP DMs

    def test_without_a_token_nothing_is_called(self):
        with open(bridge.CONFIG_FILE, "w") as f:
            json.dump({}, f)
        self.poll()
        self.assertEqual(self.api.calls, [])

    def test_token_and_message_text_never_reach_the_log(self):
        self.api.message("to:me", "UPAL", self.clock() + 1)
        def boom(method, **p): raise RuntimeError("bad url https://slack.com/api/x token=xoxp-test-token")
        with self.assertLogs("bridge", level="INFO") as logs:
            self.clock.advance(5); self.poll()
            self.w.call = boom
            with mock.patch.object(bridge.time, "time", self.clock):
                try: self.w.poll(self.clock())
                except RuntimeError as e: self.w._fail(type(e).__name__ + ": " + str(e).replace(self.w.token, "<token>"))
        text = "\n".join(logs.output)
        self.assertNotIn("xoxp-test-token", text)
        self.assertNotIn("secret words", text)


if __name__ == "__main__":
    unittest.main()
