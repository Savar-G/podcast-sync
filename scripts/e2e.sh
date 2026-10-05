#!/bin/zsh
# Browser end-to-end test: real Chromium + the extension + real YouTube, against a
# throwaway helper that reads a fake Podcasts library. Runs on any Mac; your real
# state, Podcasts app and iCloud files are never touched.
#
#   npm install && npx playwright install chromium   # once
#   ./scripts/e2e.sh
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LABEL="io.github.podcast-sync.helper"
TMP="$(mktemp -d)"

# The fake library: "you paused this episode on your iPhone at 40:30, a minute ago".
cat > "$TMP/library.json" <<'EOF'
[{"track_id": 1000792339679, "collection_id": 1836497887, "show": "David Senra",
  "title": "Bringing AI to the Real Economy | Alexander Taubman", "duration": 3729.0,
  "pub_date": 1790769600, "playhead": 2430.0, "last_played_ago": 60}]
EOF

RESTART=0
if launchctl print "gui/$(id -u)/$LABEL" >/dev/null 2>&1; then
  launchctl bootout "gui/$(id -u)/$LABEL"; RESTART=1
fi
(cd "$ROOT/helper" && PODSYNC_FAKE_LIBRARY="$TMP/library.json" PODSYNC_STATE_DIR="$TMP/state" \
  PODSYNC_HANDOFF_DIR="$TMP/handoff" PODSYNC_CONFIG=/dev/null/none python3 -m podsync >"$TMP/helper.log" 2>&1) &
HELPER=$!
for _ in {1..20}; do curl -sf http://127.0.0.1:47321/health >/dev/null && break; sleep 0.5; done

HANDOFF="$TMP/handoff/resume.json" node "$ROOT/tests/e2e/extension.e2e.mjs" w3-nMklTFjY 2430
STATUS=$?

kill $HELPER 2>/dev/null; wait $HELPER 2>/dev/null
(( RESTART )) && launchctl bootstrap "gui/$(id -u)" "$HOME/Library/LaunchAgents/$LABEL.plist"
echo "helper log: $TMP/helper.log"
exit $STATUS
