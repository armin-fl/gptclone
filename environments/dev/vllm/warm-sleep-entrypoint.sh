#!/bin/sh
set -eu

ready_file="${GPTCLONE_WARM_SLEEP_READY_FILE:-/tmp/vllm-warm-sleep-ready}"
port="${VLLM_PORT:-8000}"
timeout="${GPTCLONE_WARMUP_TIMEOUT_SECONDS:-900}"
timeout="${timeout%.*}"
poll_seconds="${GPTCLONE_WARMUP_POLL_SECONDS:-2}"
sleep_level="${GPTCLONE_WARM_SLEEP_LEVEL:-1}"
sleep_api="${GPTCLONE_WARM_SLEEP_API:-vllm}"
omni_stage_ids="${GPTCLONE_OMNI_SLEEP_STAGE_IDS:-0}"

if [ -z "$timeout" ]; then
  timeout=900
fi

rm -f "$ready_file"

patch_omni_sleep_state() {
  python3 - <<'PY'
from pathlib import Path

api_server_path = Path(
    "/usr/local/lib/python3.12/dist-packages/vllm_omni/entrypoints/openai/api_server.py"
)
if not api_server_path.exists():
    raise SystemExit(f"vLLM-Omni API server not found at {api_server_path}")

api_server = api_server_path.read_text()

old_sleep = '''@router.post("/v1/omni/sleep")
async def omni_sleep(request: OmniSleepRequest, raw_request: Request):
    engine_client = raw_request.app.state.engine_client
    sleeping_set = raw_request.app.state.sleeping_stages
'''
new_sleep = '''@router.post("/v1/omni/sleep")
async def omni_sleep(request: OmniSleepRequest, raw_request: Request):
    engine_client = raw_request.app.state.engine_client
    sleeping_set = getattr(raw_request.app.state, "sleeping_stages", None)
    if sleeping_set is None:
        sleeping_set = set()
        raw_request.app.state.sleeping_stages = sleeping_set
'''

old_wakeup = '''@router.post("/v1/omni/wakeup")
async def omni_wakeup(request: OmniWakeupRequest, raw_request: Request):
    engine_client = raw_request.app.state.engine_client
    sleeping_set = raw_request.app.state.sleeping_stages
'''
new_wakeup = '''@router.post("/v1/omni/wakeup")
async def omni_wakeup(request: OmniWakeupRequest, raw_request: Request):
    engine_client = raw_request.app.state.engine_client
    sleeping_set = getattr(raw_request.app.state, "sleeping_stages", None)
    if sleeping_set is None:
        sleeping_set = set()
        raw_request.app.state.sleeping_stages = sleeping_set
'''

if new_sleep not in api_server:
    if old_sleep not in api_server:
        raise SystemExit("Could not find vLLM-Omni sleep endpoint block to patch.")
    api_server = api_server.replace(old_sleep, new_sleep)

if new_wakeup not in api_server:
    if old_wakeup not in api_server:
        raise SystemExit("Could not find vLLM-Omni wakeup endpoint block to patch.")
    api_server = api_server.replace(old_wakeup, new_wakeup)

api_server_path.write_text(api_server)
PY
}

omni_stage_ids_json() {
  GPTCLONE_OMNI_SLEEP_STAGE_IDS="$omni_stage_ids" python3 - <<'PY'
import json
import os

stage_ids = [
    int(stage_id.strip())
    for stage_id in os.environ["GPTCLONE_OMNI_SLEEP_STAGE_IDS"].split(",")
    if stage_id.strip()
]
if not stage_ids:
    raise SystemExit("GPTCLONE_OMNI_SLEEP_STAGE_IDS must include at least one stage id.")
print(json.dumps(stage_ids))
PY
}

sleep_server() {
  case "$sleep_api" in
    vllm)
      curl -fsS -X POST "http://127.0.0.1:${port}/sleep?level=${sleep_level}" >/dev/null
      ;;
    omni)
      stage_ids="$(omni_stage_ids_json)"
      curl -fsS -X POST "http://127.0.0.1:${port}/v1/omni/sleep" \
        -H "Content-Type: application/json" \
        -d "{\"stage_ids\":${stage_ids},\"level\":${sleep_level}}" >/dev/null
      ;;
    *)
      echo "Unsupported GPTCLONE_WARM_SLEEP_API '${sleep_api}'." >&2
      exit 1
      ;;
  esac
}

if [ "${GPTCLONE_PATCH_OMNI_SLEEP_STATE:-0}" = "1" ]; then
  patch_omni_sleep_state
fi

vllm serve "$@" &
server_pid="$!"

terminate_server() {
  kill -TERM "$server_pid" 2>/dev/null || true
  wait "$server_pid" 2>/dev/null || true
}

trap 'terminate_server; exit 0' INT TERM

deadline="$(($(date +%s) + timeout))"
while true; do
  if ! kill -0 "$server_pid" 2>/dev/null; then
    wait "$server_pid"
    exit "$?"
  fi

  if curl -fsS "http://127.0.0.1:${port}/v1/models" >/dev/null 2>&1; then
    break
  fi

  if [ "$(date +%s)" -ge "$deadline" ]; then
    echo "Timed out waiting for vLLM to become ready on port ${port}." >&2
    terminate_server
    exit 1
  fi

  sleep "$poll_seconds"
done

if [ "${GPTCLONE_WARM_SLEEP_ON_START:-1}" = "1" ]; then
  sleep_server
fi

touch "$ready_file"
wait "$server_pid"
