#!/bin/zsh
# Build the helper app, install it as a login item (LaunchAgent), and start it.
#
# The Python helper runs inside a tiny app bundle, "Podcast Sync Helper". macOS asks
# you once to let that app read the Podcasts library, and remembers the answer.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LABEL="io.github.podcast-sync.helper"
SUPPORT="$HOME/Library/Application Support/podcast-sync"
APP="$SUPPORT/Podcast Sync Helper.app"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
LOG="$HOME/Library/Logs/podcast-sync.log"

# Any Python 3.9+ works: the privacy permission belongs to the app bundle, not to Python.
PY="${PODSYNC_PYTHON:-}"
if [[ -z "$PY" ]]; then
  for p in /usr/bin/python3 /opt/homebrew/bin/python3 /usr/local/bin/python3; do
    if [[ -x "$p" ]] && "$p" -c 'import sys; sys.exit(sys.version_info < (3, 9))' 2>/dev/null; then PY="$p"; break; fi
  done
fi
if [[ -z "$PY" ]]; then
  echo "Python 3.9+ not found. Install Apple's command line tools: xcode-select --install"
  exit 1
fi
if ! command -v clang >/dev/null; then
  echo "clang not found. Install Apple's command line tools: xcode-select --install"
  exit 1
fi

# 1. Build and sign the app bundle. macOS ties your "Allow" to this exact signature, so
#    rebuild only when the launcher, the Python path, the repo path or APP_BUILD changes.
#    Bump APP_BUILD when you change the Info.plist below.
APP_BUILD=1
mkdir -p "$SUPPORT" "$HOME/Library/LaunchAgents" "$HOME/Library/Logs"
BUILD_KEY="$( { shasum -a 256 "$ROOT/scripts/launcher.c"; echo "$PY|$ROOT|$APP_BUILD"; } | shasum -a 256 | cut -c1-16)"
if [[ "$(cat "$APP/Contents/Resources/build-key" 2>/dev/null)" != "$BUILD_KEY" ]] || ! codesign --verify "$APP" 2>/dev/null; then
  # The signature seals everything in the bundle, so write it all first, then sign.
  rm -rf "$APP"
  mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
  cat > "$APP/Contents/Info.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleIdentifier</key><string>$LABEL</string>
  <key>CFBundleName</key><string>Podcast Sync Helper</string>
  <key>CFBundleExecutable</key><string>podsync-helper</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>$APP_BUILD</string>
  <key>LSUIElement</key><true/>
  <key>LSBackgroundOnly</key><true/>
</dict>
</plist>
EOF
  clang -O2 -Wall -o "$APP/Contents/MacOS/podsync-helper" "$ROOT/scripts/launcher.c" \
    -DPYTHON="\"$PY\"" -DHELPER_DIR="\"$ROOT/helper\""
  echo "$BUILD_KEY" > "$APP/Contents/Resources/build-key"
  codesign --force --sign - --identifier "$LABEL" "$APP"
  echo "Built Podcast Sync Helper.app. macOS will ask once to allow it."
fi

# 2. podcasts-remote moves Apple Podcasts on this Mac to your YouTube spot, and iCloud
#    carries it to the iPhone (config: push_to_podcasts). It lives outside the signed app,
#    so rebuilding it never asks for permission again. Without it, the Shortcut still works.
REMOTE="$SUPPORT/podcasts-remote"
if ! clang -O2 -Wall -o "$REMOTE" "$ROOT/scripts/podcasts_remote.c" -framework CoreFoundation -framework CoreAudio; then
  rm -f "$REMOTE"
  echo "Could not build podcasts-remote. On your iPhone, use the Resume Podcast shortcut instead."
fi

# 3. Login item.
cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key><array><string>$APP/Contents/MacOS/podsync-helper</string></array>
  <key>AssociatedBundleIdentifiers</key><string>$LABEL</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ProcessType</key><string>Background</string>
  <key>StandardOutPath</key><string>$LOG</string>
  <key>StandardErrorPath</key><string>$LOG</string>
</dict>
</plist>
EOF
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"

for _ in {1..20}; do
  curl -sf http://127.0.0.1:47321/health >/dev/null && break
  sleep 0.5
done
if ! curl -sf http://127.0.0.1:47321/health >/dev/null; then
  echo "The helper did not start. See $LOG"
  exit 1
fi
echo "Podcast Sync Helper is installed and running (Python: $PY)."
echo "If macOS asks to let \"Podcast Sync Helper\" access data from other apps, click Allow."
echo "Log: $LOG"
