#!/usr/bin/env bash
# One command that exercises the whole pipeline: LiDAR scan -> server -> Grok label ->
# packer3d/physics -> PAN -> iOS plan. Every step below runs even if an earlier one
# failed; a PASS/FAIL/SKIP summary prints at the end and the script exits 1 if any step
# FAILed.
#
#   bash scripts/pipeline_check.sh                # full run (swift build can take minutes)
#   SKIP_SWIFT=1 bash scripts/pipeline_check.sh    # skip the swift package build/test step
set -u

cd "$(git rev-parse --show-toplevel)"

OUT_DIR="out/pipeline_check"
mkdir -p "$OUT_DIR"

SKIP_RC=99
STEP_NAMES=()
STEP_RESULTS=()
STEP_SECONDS=()
ANY_FAILED=0

record() {
  STEP_NAMES+=("$1")
  STEP_RESULTS+=("$2")
  STEP_SECONDS+=("$3")
  [ "$2" = FAIL ] && ANY_FAILED=1
  return 0
}

run() {  # run <label> <step-function>
  local label="$1" fn="$2" t0 t1 rc
  echo "=== $label ==="
  t0=$(date +%s)
  "$fn"
  rc=$?
  t1=$(date +%s)
  if [ "$rc" -eq "$SKIP_RC" ]; then
    record "$label" SKIP "$((t1 - t0))"
  elif [ "$rc" -eq 0 ]; then
    record "$label" PASS "$((t1 - t0))"
  else
    record "$label" FAIL "$((t1 - t0))"
  fi
}

port_open() { (: < "/dev/tcp/127.0.0.1/$1") 2>/dev/null; }

ensure_mongo() {
  port_open 27017 && return 0
  if ! command -v docker >/dev/null 2>&1; then
    echo "no docker and nothing listening on 27017"
    return 1
  fi
  if docker ps -a --format '{{.Names}}' | grep -qx suitcase-mongo; then
    docker start suitcase-mongo >/dev/null
  else
    docker run -d --name suitcase-mongo -p 27017:27017 mongo:7 >/dev/null
  fi
  for _ in $(seq 1 30); do
    port_open 27017 && return 0
    sleep 1
  done
  echo "mongo container started but never answered on 27017"
  return 1
}

# --- steps -------------------------------------------------------------------

step_root_suite() {
  python3 -m unittest discover
}

step_packer3d() {
  ( cd packer3d && PYTHONPATH=. PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 "$HOME/.venv/bin/python" \
      -m pytest -q -p no:cacheprovider -m "not slow" tests/ )
}

step_server_check() {
  # server/main.py pings a real Mongo at import, so check.py needs one up -- not only
  # the live step below.
  if ! ensure_mongo; then
    return $SKIP_RC
  fi
  ( cd server && uv run python check.py )
}

step_swift() {
  if [ "${SKIP_SWIFT:-}" = "1" ]; then
    echo "SKIP_SWIFT=1: skipping swift package build/test"
    return $SKIP_RC
  fi
  make test-swift
}

step_swift_app() {
  local f found=0
  for f in tests/swift/*/run.sh; do
    [ -e "$f" ] && found=1
  done
  if [ "$found" -eq 0 ]; then
    echo "no tests/swift/*/run.sh present yet"
    return $SKIP_RC
  fi
  make test-swift-app
}

step_plan_contract() {
  [ -e tests/plan_contract/run.sh ] || { echo "no tests/plan_contract/run.sh"; return $SKIP_RC; }
  if [ "${SKIP_SWIFT:-0}" = 1 ]; then echo "SKIP_SWIFT=1: skipping plan contract (swift build of packing-core)"; return $SKIP_RC; fi
  bash tests/plan_contract/run.sh
}

STARTED_SERVER=0
cleanup_server() {
  [ "$STARTED_SERVER" = 1 ] && fuser -k 8000/tcp >/dev/null 2>&1
  return 0
}
trap cleanup_server EXIT

step_live_e2e() {
  if ! ensure_mongo; then
    echo "no mongo reachable -- skipping live e2e"
    return $SKIP_RC
  fi

  if ! port_open 8000; then
    if [ -f .env ]; then
      ( cd server && uv run --env-file ../.env uvicorn main:app --port 8000 ) >"$OUT_DIR/server.log" 2>&1 &
    else
      ( cd server && uv run uvicorn main:app --port 8000 ) >"$OUT_DIR/server.log" 2>&1 &
    fi
    STARTED_SERVER=1
  fi

  local ok=0 _i
  for _i in $(seq 1 30); do
    curl -sf -o /dev/null "http://127.0.0.1:8000/suitcases" && { ok=1; break; }
    sleep 1
  done
  if [ "$ok" -eq 0 ]; then
    echo "server never answered GET /suitcases within 30s (see $OUT_DIR/server.log)"
    return 1
  fi

  python3 scripts/demo_e2e.py --out "$OUT_DIR"
}

# --- run everything, even if something above failed --------------------------

run "root-suite" step_root_suite
run "packer3d-suite" step_packer3d
run "server-check" step_server_check
run "swift" step_swift
run "swift-app" step_swift_app
run "plan-contract" step_plan_contract
run "live-e2e" step_live_e2e

echo
printf '%-16s %-6s %8s\n' "STEP" "RESULT" "SECONDS"
for i in "${!STEP_NAMES[@]}"; do
  printf '%-16s %-6s %8s\n' "${STEP_NAMES[$i]}" "${STEP_RESULTS[$i]}" "${STEP_SECONDS[$i]}"
done

exit "$ANY_FAILED"
