#!/bin/zsh
# Browser end-to-end test: real Chromium + a test copy of the extension + real YouTube,
# against a throwaway helper on its own port that reads a fake Podcasts library.
# Runs on any Mac. Your installed helper keeps running, and your real state,
# Podcasts app and iCloud files are never touched.
#
#   npm install && npx playwright install chromium   # once
#   ./scripts/e2e.sh
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TMP="$(mktemp -d)"

# The fake library: "you paused this episode on your iPhone at 40:30, a minute ago".
cat > "$TMP/library.json" <<'EOF'
[{"track_id": 1000792339679, "collection_id": 1836497887, "show": "David Senra",
  "title": "Bringing AI to the Real Economy | Alexander Taubman", "duration": 3729.0,
  "pub_date": 1790769600, "playhead": 2430.0, "last_played_ago": 60}]
EOF
# A returning user: the helper already knows which podcast this channel publishes.
mkdir -p "$TMP/state"
echo '{"channels": {"UCy2FPslt0LLPsIV0iukvHpQ": [1836497887]}}' > "$TMP/state/state.json"

# A test copy of the extension that talks to the test port. Same key, so the same ID.
PORT=47399
cp -R "$ROOT/extension" "$TMP/extension"
sed -i '' "s/127.0.0.1:47321/127.0.0.1:$PORT/" "$TMP/extension/manifest.json" "$TMP/extension/background.js"

(cd "$ROOT/helper" && PODSYNC_PORT=$PORT PODSYNC_FAKE_LIBRARY="$TMP/library.json" PODSYNC_STATE_DIR="$TMP/state" \
  PODSYNC_HANDOFF_DIR="$TMP/handoff" PODSYNC_CONFIG=/dev/null/none python3 -m podsync >"$TMP/helper.log" 2>&1) &
HELPER=$!
for _ in {1..20}; do curl -sf "http://127.0.0.1:$PORT/health" >/dev/null && break; sleep 0.5; done

EXT_DIR="$TMP/extension" HANDOFF="$TMP/handoff/resume.json" HELPER_LOG="$TMP/helper.log" LIBRARY="$TMP/library.json" node "$ROOT/tests/e2e/extension.e2e.mjs" w3-nMklTFjY 2430
STATUS=$?

kill $HELPER 2>/dev/null; wait $HELPER 2>/dev/null
echo "helper log: $TMP/helper.log"
exit $STATUS
