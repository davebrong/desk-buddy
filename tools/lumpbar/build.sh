#!/bin/zsh
# Build "Lump" (the Mac version of Lump) into ~/Applications/Lump.app and (re)launch it.
# Re-bake the frames first if the drawings changed: python3 tools/lumpbar/bake.py
set -euo pipefail
HERE="${0:A:h}"
APP="$HOME/Applications/Lump.app"
[ -f "$HERE/frames/manifest.json" ] || { echo "no frames yet: run python3 $HERE/bake.py"; exit 1; }

BUILD="$(mktemp -d)"
trap 'rm -rf "$BUILD"' EXIT
swiftc -O -swift-version 5 "$HERE/main.swift" -o "$BUILD/Lump"

pkill -x Lump 2>/dev/null || true
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
cp "$BUILD/Lump" "$APP/Contents/MacOS/Lump"
cp -R "$HERE/frames" "$APP/Contents/Resources/frames"
ICONSET="$BUILD/Lump.iconset"; mkdir -p "$ICONSET"
for px in 16 32 128 256 512; do
  sips -z $px $px "$HERE/icon.png" --out "$ICONSET/icon_${px}x${px}.png" >/dev/null
  sips -z $((px * 2)) $((px * 2)) "$HERE/icon.png" --out "$ICONSET/icon_${px}x${px}@2x.png" >/dev/null
done
iconutil -c icns "$ICONSET" -o "$APP/Contents/Resources/Lump.icns"
cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleIdentifier</key><string>com.deskbuddy.lumpbar</string>
  <key>CFBundleName</key><string>Lump</string>
  <key>CFBundleExecutable</key><string>Lump</string>
  <key>CFBundleIconFile</key><string>Lump</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>1.0</string>
  <key>LSMinimumSystemVersion</key><string>12.0</string>
  <key>LSUIElement</key><true/>
</dict></plist>
PLIST
codesign --force --sign - "$APP"
open "$APP"
echo "Lump is running from $APP"
