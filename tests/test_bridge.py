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
        self.clock.t = NOON + 11 * 3600                         # 23:00
        self.assertEqual(self.mood(), "night")


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


if __name__ == "__main__":
    unittest.main()
