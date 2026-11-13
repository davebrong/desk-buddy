"""Windows stand-in for the hooks' curl one-liner: forwards a Claude Code hook's JSON (stdin) to the
bridge on 127.0.0.1:47823. Never blocks for long and never fails Claude Code, bridge or no bridge.
Installed copy: ~/.desk-buddy/hook_relay.py (tools/install_hooks.py points the hooks at it).

    python hook_relay.py [endpoint]      # endpoint defaults to "hook"
"""
import sys, urllib.request

# no proxies: a corporate HTTP_PROXY must never see (or swallow) a request to localhost
_open = urllib.request.build_opener(urllib.request.ProxyHandler({})).open


def post(endpoint, data):
    try:
        req = urllib.request.Request(f"http://127.0.0.1:47823/{endpoint}", data=data,
                                     headers={"Content-Type": "application/json"})
        _open(req, timeout=1).read()
    except Exception:
        pass


if __name__ == "__main__":
    post(sys.argv[1] if len(sys.argv) > 1 else "hook", sys.stdin.buffer.read())
