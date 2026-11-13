"""Claude Code status line for Desk Buddy on Windows (tools/statusline.sh is the Mac one).

Claude Code runs this after each reply with session JSON on stdin; it includes your plan's usage
limits (rate_limits.five_hour / seven_day). This forwards that JSON to the bridge (Lump's usage-gauge
colour) and prints a status line: your own, if you had one (install_hooks.py --wrap-statusline keeps
it in ~/.desk-buddy/config.json as "statusline_passthrough"), otherwise a short one of its own.
Installed copy: ~/.desk-buddy/statusline.py.
"""
import json, os, subprocess, sys, threading, urllib.request

CONFIG = os.path.expanduser("~/.desk-buddy/config.json")
_open = urllib.request.build_opener(urllib.request.ProxyHandler({})).open


def forward(data):
    try:
        _open(urllib.request.Request("http://127.0.0.1:47823/usage", data=data,
                                     headers={"Content-Type": "application/json"}), timeout=1).read()
    except Exception:
        pass


def own_line(data):
    try:
        p = json.loads(data or b"{}")
    except ValueError:
        return ""
    rl = p.get("rate_limits") or {}
    parts = [(p.get("model") or {}).get("display_name")]
    for key, label in (("five_hour", "5h"), ("seven_day", "week")):
        pct = (rl.get(key) or {}).get("used_percentage")
        if pct is not None:
            parts.append(f"{label} {int(pct)}%")
    return " · ".join(x for x in parts if x)


def main():
    data = sys.stdin.buffer.read()
    t = threading.Thread(target=forward, args=(data,), daemon=True)
    t.start()
    try:
        passthrough = (json.load(open(CONFIG, encoding="utf-8")) or {}).get("statusline_passthrough")
    except Exception:
        passthrough = None
    if passthrough:
        try:
            out = subprocess.run(passthrough, shell=True, input=data, capture_output=True, timeout=10).stdout
            sys.stdout.buffer.write(out)
        except Exception:
            pass
    else:
        sys.stdout.buffer.write(own_line(data).encode("utf-8"))
    sys.stdout.flush()
    t.join(1.2)


if __name__ == "__main__":
    main()
