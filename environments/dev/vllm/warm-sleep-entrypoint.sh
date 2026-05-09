#!/bin/sh
set -eu

ready_file="${GPTCLONE_WARM_SLEEP_READY_FILE:-/tmp/vllm-warm-sleep-ready}"
port="${VLLM_PORT:-8000}"
timeout="${GPTCLONE_WARMUP_TIMEOUT_SECONDS:-900}"
timeout="${timeout%.*}"
poll_seconds="${GPTCLONE_WARMUP_POLL_SECONDS:-2}"
sleep_level="${GPTCLONE_WARM_SLEEP_LEVEL:-1}"

if [ -z "$timeout" ]; then
  timeout=900
fi

rm -f "$ready_file"

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
  curl -fsS -X POST "http://127.0.0.1:${port}/sleep?level=${sleep_level}" >/dev/null
fi

touch "$ready_file"
wait "$server_pid"
