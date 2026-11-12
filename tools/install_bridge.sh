#!/bin/bash
# Install (or update) the Desk Buddy bridge and app watcher as macOS login items.
#   com.deskbuddy.bridge    Python bridge: Claude Code hooks + Chat/Cowork state -> Lump over USB
#   com.deskbuddy.appwatch  small native helper that reads the Claude app's accessibility tree
#                           (Chat and Cowork have no hooks). Only this helper needs Accessibility.
# Runtime lives in ~/.desk-buddy (outside iCloud, so it's never evicted):
#   venv/  bridge.py  bridge.log  deskbuddy-appwatch
# Uninstall: tools/install_bridge.sh --uninstall
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
RT="$HOME/.desk-buddy"
LABEL="com.deskbuddy.bridge"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
WLABEL="com.deskbuddy.appwatch"
WPLIST="$HOME/Library/LaunchAgents/$WLABEL.plist"

launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootout "gui/$(id -u)/$WLABEL" 2>/dev/null || true
if [ "${1:-}" = "--uninstall" ]; then
  rm -f "$PLIST" "$WPLIST"
  echo "Bridge and app watcher removed (runtime left in $RT)."
  exit 0
fi

mkdir -p "$RT" "$HOME/Library/LaunchAgents"
[ -x "$RT/venv/bin/python" ] || python3 -m venv "$RT/venv"
"$RT/venv/bin/pip" -q install --disable-pip-version-check --upgrade pip
"$RT/venv/bin/pip" -q install --disable-pip-version-check pyserial icalendar recurring-ical-events
cp "$ROOT/tools/buddy_bridge.py" "$RT/bridge.py"
# Lump's voice: renders the WAVs, and installs sounds.json (which moments make a noise) the first time.
python3 "$ROOT/tools/make_sounds.py"
# ...and the sound effects for his tap gags (tap_<gag>.wav), timed to the drawings in design/lump.html.
python3 "$ROOT/tools/make_gag_sounds.py"

cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key><array>
    <string>$RT/venv/bin/python</string><string>-u</string><string>$RT/bridge.py</string>
  </array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>$RT/bridge.out</string>
  <key>StandardErrorPath</key><string>$RT/bridge.out</string>
</dict></plist>
EOF
launchctl bootstrap "gui/$(id -u)" "$PLIST"

# Build the app watcher. macOS ties its Accessibility permission to the signature. If Claude Switcher's
# local signing certificate exists (its build.sh makes one), sign with that: every build then keeps the
# permission. Otherwise it's an ad hoc signature, which changes with every build, so the installed
# binary is only replaced when the program changed (and you re-allow it in Accessibility).
NEW="$RT/deskbuddy-appwatch.new"
swiftc -O -swift-version 5 "$ROOT/tools/appwatch/main.swift" -o "$NEW"
SIGNING="$HOME/Library/Application Support/Claude Switcher Signing"
if [ -f "$SIGNING/signing.keychain-db" ] && [ -f "$SIGNING/password" ]; then
  KC="$SIGNING/signing.keychain-db"
  security unlock-keychain -p "$(cat "$SIGNING/password")" "$KC"
  ID="$(security find-certificate -c "Claude Switcher Local" -Z "$KC" | awk '/SHA-1/ {print $NF}')"
  # codesign only finds an identity in a keychain on the search list: add it just while signing
  OLD_LIST=()
  while IFS= read -r k; do OLD_LIST+=("$k"); done < <(security list-keychains -d user | sed -E 's/^ *"(.*)"$/\1/')
  security list-keychains -d user -s "${OLD_LIST[@]}" "$KC"
  SIGNED=0; codesign --force --sign "$ID" --identifier "$WLABEL" "$NEW" && SIGNED=1
  security list-keychains -d user -s "${OLD_LIST[@]}"
  [ "$SIGNED" = 1 ] || { echo "signing the app watcher failed"; exit 1; }
  mv "$NEW" "$RT/deskbuddy-appwatch"
elif cmp -s "$NEW" "$RT/deskbuddy-appwatch"; then rm -f "$NEW"; else mv "$NEW" "$RT/deskbuddy-appwatch"; fi
cat > "$WPLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$WLABEL</string>
  <key>ProgramArguments</key><array><string>$RT/deskbuddy-appwatch</string></array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>$RT/appwatch.out</string>
  <key>StandardErrorPath</key><string>$RT/appwatch.out</string>
</dict></plist>
EOF
launchctl bootstrap "gui/$(id -u)" "$WPLIST"

sleep 3
curl -s -m 2 http://127.0.0.1:47823/status && echo
echo "Bridge and app watcher running. If macOS asks, allow deskbuddy-appwatch under"
echo "System Settings > Privacy & Security > Accessibility."
