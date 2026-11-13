"""Hook Claude Code up to Lump.

    python3 tools/install_hooks.py                      # ~/.claude/settings.json
    python3 tools/install_hooks.py --config-dir ~/.claude-work   # a second Claude Code config
    python3 tools/install_hooks.py --uninstall

Adds one small async hook per Claude Code event that matters to Lump. Each hook POSTs the event's
JSON to the bridge on 127.0.0.1:47823 and never blocks or fails Claude Code. It also installs the
status line (tools/statusline.sh, which feeds your usage limits to his colour) unless you already
have a status line of your own. Running it again changes nothing; settings.json is backed up first.

On Windows the hooks and status line are small Python scripts (tools/hook_relay.py, tools/statusline.py)
run by the bridge's venv, so they work whichever shell Claude Code uses. Run tools/install_bridge.ps1
first. --wrap-statusline keeps a status line you already have: Lump's runs in its place, feeds the
bridge, and prints yours (Windows only).
"""
import argparse, json, os, shutil, sys, time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUNTIME = os.path.expanduser("~/.desk-buddy")
WINDOWS = sys.platform == "win32"
HOOK_CMD = ("curl -s -m 1 -o /dev/null -H 'Content-Type: application/json' "
            "--data-binary @- http://127.0.0.1:47823/hook || true")


def win_cmd(*paths):
    """A command line that runs the same in Git Bash, cmd and PowerShell: forward slashes, quoted
    only when a path has a space (PowerShell won't run a quoted program without "&")."""
    out = []
    for p in paths:
        p = p.replace("\\", "/")
        out.append(f'"{p}"' if " " in p else p)
    return " ".join(out)


if WINDOWS:
    _py = os.path.join(RUNTIME, "venv", "Scripts", "python.exe")
    PY = _py if os.path.exists(_py) else sys.executable
    HOOK_CMD = win_cmd(PY, os.path.join(RUNTIME, "hook_relay.py"))
# events without a tool name take no matcher
EVENTS = {"SessionStart": "*", "UserPromptSubmit": None, "PreToolUse": "*", "PermissionRequest": "*",
          "PostToolUse": "*", "PostToolUseFailure": "*", "Notification": "*", "Elicitation": "*",
          "Stop": None, "StopFailure": "*", "SessionEnd": "*"}
MARK = "127.0.0.1:47823/"


def ours(group):
    return any(MARK in (h.get("command") or "") or "desk-buddy/hook_relay.py" in (h.get("command") or "")
               for h in group.get("hooks", []))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config-dir", default="~/.claude", help="Claude Code config folder (default ~/.claude)")
    ap.add_argument("--uninstall", action="store_true", help="remove Lump's hooks and status line")
    ap.add_argument("--wrap-statusline", action="store_true",
                    help="(Windows) replace your status line with Lump's, which still prints yours")
    args = ap.parse_args()

    path = os.path.join(os.path.expanduser(args.config_dir), "settings.json")
    settings = {}
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            settings = json.load(f)
        shutil.copy2(path, f"{path}.bak-{time.strftime('%Y%m%d-%H%M%S')}")
    hooks = settings.setdefault("hooks", {})
    status_cmd = os.path.join(RUNTIME, "statusline.sh")
    if WINDOWS:
        status_cmd = win_cmd(PY, os.path.join(RUNTIME, "statusline.py"))
        if not args.uninstall:
            os.makedirs(RUNTIME, exist_ok=True)
            shutil.copy2(os.path.join(ROOT, "tools", "hook_relay.py"), os.path.join(RUNTIME, "hook_relay.py"))

    changed = []
    for event, matcher in EVENTS.items():
        groups = hooks.get(event, [])
        if args.uninstall:
            kept = [g for g in groups if not ours(g)]
            if len(kept) != len(groups):
                changed.append(event)
                if kept: hooks[event] = kept
                else: hooks.pop(event, None)
        elif not any(ours(g) for g in groups):
            group = {"hooks": [{"type": "command", "command": HOOK_CMD, "async": True, "timeout": 5}]}
            if matcher: group = {"matcher": matcher, **group}
            hooks[event] = groups + [group]
            changed.append(event)
    if not hooks:
        settings.pop("hooks")

    line = settings.get("statusLine") or {}
    if args.uninstall:
        if line.get("command") == status_cmd:
            old = passthrough(None)
            if old: settings["statusLine"] = {"type": "command", "command": old}
            else: settings.pop("statusLine")
            changed.append("statusLine")
    else:
        os.makedirs(RUNTIME, exist_ok=True)
        if WINDOWS:
            shutil.copy2(os.path.join(ROOT, "tools", "statusline.py"), os.path.join(RUNTIME, "statusline.py"))
        else:
            shutil.copy2(os.path.join(ROOT, "tools", "statusline.sh"), status_cmd)
            os.chmod(status_cmd, 0o755)
        if line and line.get("command") != status_cmd and args.wrap_statusline and WINDOWS:
            passthrough(line.get("command"))
            settings["statusLine"] = {"type": "command", "command": status_cmd}; changed.append("statusLine (wrapping yours)")
        elif not line:
            settings["statusLine"] = {"type": "command", "command": status_cmd}; changed.append("statusLine")
        elif line.get("command") != status_cmd:
            print(f"You already have a status line ({line.get('command')}), so it was left alone.\n"
                  f"Lump's usage colour needs {status_cmd} to see the status line JSON: call it from yours.")

    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(settings, f, indent=2, ensure_ascii=False)
        f.write("\n")
    verb = "Removed" if args.uninstall else "Added"
    print(f"{verb}: {', '.join(changed)}" if changed else "Nothing to change.")
    print(f"({path})")


def passthrough(cmd):
    """Windows status line wrapping: store your own status line command in config.json (cmd given),
    or take it back out (cmd None, on uninstall) and return it."""
    path = os.path.join(RUNTIME, "config.json")
    cfg = {}
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            cfg = json.load(f)
    old = cfg.pop("statusline_passthrough", None)
    if cmd:
        cfg["statusline_passthrough"] = cmd
    if cmd or old:
        with open(path + ".tmp", "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
        os.replace(path + ".tmp", path)
    return old


if __name__ == "__main__":
    sys.exit(main())
