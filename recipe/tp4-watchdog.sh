#!/bin/bash
# Supervisor for the TP4 quartet. Runs detached on rank0; every INTERVAL s it checks /health on the head.
# A dead engine (rank0 container gone or exited, or health failing for longer than STARTUP_GRACE s while the container
# runs) triggers one recovery: stop -> preflight -> launch -> wait. If that fails, tp4-rollback.sh restores TP2 so the
# gateway is not left dark. Recoveries are rate-limited (MAX_RECOVERIES per hour). Touch $MAINT (default
# ~/AI/tp4-maintenance) to pause the watchdog while relaunching by hand or running autoresearch. Every CLOCK_EVERY
# intervals it reads the GPU sm clock of every rank and logs CLOCK-LOW when a BUSY one (util >= CLOCK_MIN_UTIL, 20 %) is under CLOCK_MIN_MHZ (1000).
# A frozen engine (health 200 but no token progress with requests running, and a probe completion failing twice) is recovered too.
# Usage: nohup tp4-watchdog.sh prod.env > ~/AI/tp4-watchdog.out 2>&1 &
set -uo pipefail
cd "$(dirname "$0")"; CFG=${1:-prod.env}
# shellcheck disable=SC1090
source "$CFG"
INTERVAL=${INTERVAL:-60}; STARTUP_GRACE=${STARTUP_GRACE:-1200}; MAX_RECOVERIES=${MAX_RECOVERIES:-3}; MAINT=${MAINT:-$HOME/AI/tp4-maintenance}
LOG=$HOME/AI/tp4-watchdog.log; log(){ printf '%s %s\n' "$(date -u +%FT%TZ)" "$*" | tee -a "$LOG"; }
health(){ curl -s -o /dev/null -m 5 -w '%{http_code}' "http://$HEAD_IP:$PORT/health" 2>/dev/null; }
r0state(){ ${RANK_DK[0]} inspect -f '{{.State.Status}}' "glm53-tp4-$TAG-r0" 2>/dev/null || echo absent; }
recoveries=(); unhealthy_since=0
log "watchdog start cfg=$CFG interval=${INTERVAL}s grace=${STARTUP_GRACE}s"
clocks(){ # log every rank whose GPU sm clock is below CLOCK_MIN_MHZ (a GB10 can come out of a reboot pinned at 600-700 MHz)
  local r c sm ut low=
  for r in 0 1 2 3; do c=$(ssh -n $SSH_OPTS "${RANK_USER:-spark}@${RANK_IPS[$r]}" "nvidia-smi --query-gpu=clocks.sm,utilization.gpu --format=csv,noheader,nounits" 2>/dev/null | head -1 | tr -d ' '); IFS=, read -r sm ut <<<"$c"
    [ "${ut:-0}" -lt "${CLOCK_MIN_UTIL:-20}" ] 2>/dev/null && continue  # idle GB10 = 208 MHz, normal; only judge a busy GPU
    [ "${sm:-0}" -ge "${CLOCK_MIN_MHZ:-1000}" ] 2>/dev/null || low="$low r$r=${sm:-unknown}MHz/util${ut:-?}%"; done
  [ -z "$low" ] || log "CLOCK-LOW:$low (below ${CLOCK_MIN_MHZ:-1000} MHz; reboot-capped clocks? no automatic action)"
}
# Liveness: /health stays 200 while the engine core is frozen in its shared-memory wait (2026-09-03, run 6: 35 min at
# 21 W with no RPC timeout). frozen() returns 0 when requests are running, the token counters have not moved for
# STALL_TICKS intervals (4) and a tiny completion then fails PROBE_FAILS times in a row (2, PROBE_TIMEOUT 180 s each).
flat=0; last_tok=; probe_fail=0
frozen(){
  local m run tok
  m=$(curl -s -m5 "http://127.0.0.1:$PORT/metrics" 2>/dev/null) || return 1
  run=$(printf '%s' "$m" | awk '/^vllm:num_requests_running/{print int($2); exit}'); [ -n "$run" ] || return 1
  tok=$(printf '%s' "$m" | awk '/^vllm:(generation|prompt)_tokens_total/{s+=$2} END{printf "%d", s}')
  if [ "$run" = 0 ] || [ "$tok" != "$last_tok" ]; then flat=0; probe_fail=0; last_tok=$tok; return 1; fi
  flat=$((flat + 1)); [ $flat -lt "${STALL_TICKS:-4}" ] && return 1
  if curl -s -m "${PROBE_TIMEOUT:-180}" "http://127.0.0.1:$PORT/v1/chat/completions" -H 'Content-Type: application/json' \
       -d '{"model":"GLM-5.3-Flash-EXL3","messages":[{"role":"user","content":"Reply with the word READY."}],"max_tokens":8,"temperature":0,"chat_template_kwargs":{"enable_thinking":false}}' 2>/dev/null | grep -q '"content"'; then
    flat=0; probe_fail=0; return 1; fi
  probe_fail=$((probe_fail + 1)); log "liveness probe failed ($probe_fail/${PROBE_FAILS:-2}): $run requests running, token counters flat for $flat intervals"
  [ $probe_fail -ge "${PROBE_FAILS:-2}" ]
}
tick=0
while :; do
  sleep "$INTERVAL"; tick=$((tick + 1))
  [ $((tick % ${CLOCK_EVERY:-5})) = 0 ] && clocks
  [ -e "$MAINT" ] && { unhealthy_since=0; flat=0; probe_fail=0; continue; }
  if [ "$(health)" = 200 ]; then
    unhealthy_since=0
    frozen || continue
    now=$(date +%s); st=frozen; flat=0; probe_fail=0; last_tok=
    log "engine frozen (health 200, requests running, no token progress, probes failed): recovering"
  else
    now=$(date +%s); st=$(r0state)
  fi
  if [ "$st" = running ]; then
    [ $unhealthy_since = 0 ] && unhealthy_since=$now
    [ $((now - unhealthy_since)) -ge "$STARTUP_GRACE" ] || { log "health!=200, rank0 running for $((now - unhealthy_since))s (grace $STARTUP_GRACE)"; continue; }
    log "rank0 running but unhealthy for $((now - unhealthy_since))s: recovering"
  elif [ "$st" != frozen ]; then
    log "rank0 container state '$st' and health!=200: recovering"
  fi
  recoveries=($(for t in "${recoveries[@]-}"; do [ -n "$t" ] && [ $((now - t)) -lt 3600 ] && echo "$t"; done)); 
  if [ "${#recoveries[@]}" -ge "$MAX_RECOVERIES" ]; then log "recovery limit reached ($MAX_RECOVERIES/h); rolling back to TP2"; ./tp4-rollback.sh "$CFG" >>"$LOG" 2>&1; touch "$MAINT"; log "paused (remove $MAINT to resume)"; continue; fi
  recoveries+=("$now"); unhealthy_since=0
  ./tp4-cluster.sh "$CFG" stop >>"$LOG" 2>&1; sleep 15
  if ./tp4-cluster.sh "$CFG" preflight >>"$LOG" 2>&1 && ./tp4-cluster.sh "$CFG" launch >>"$LOG" 2>&1 && WAIT_TIMEOUT=1500 ./tp4-cluster.sh "$CFG" wait >>"$LOG" 2>&1; then
    log "recovered: TP4 healthy"
  else
    log "TP4 recovery failed; restoring TP2"; ./tp4-rollback.sh "$CFG" >>"$LOG" 2>&1; touch "$MAINT"; log "paused on TP2 (remove $MAINT to resume TP4 supervision)"
  fi
done
