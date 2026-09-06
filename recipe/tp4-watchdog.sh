#!/bin/bash
# Supervise the whole TP group. Recovery restarts its existing containers so the
# pinned image, environment and retained diagnostics survive. A health endpoint
# alone is never sufficient to declare recovery successful.
set -uo pipefail
cd "$(dirname "$0")" || exit 1
CFG=${1:-prod.env}
source "$CFG"
INTERVAL=${INTERVAL:-60}; STARTUP_GRACE=${STARTUP_GRACE:-1200}
UNHEALTHY_GRACE=${UNHEALTHY_GRACE:-120}
MAX_RECOVERIES=${MAX_RECOVERIES:-3}; MAINT=${MAINT:-$HOME/AI/tp4-maintenance}
LOG=$HOME/AI/tp4-watchdog.log
exec 9>"$HOME/AI/tp4-watchdog-$TAG.lock"
flock -n 9 || { echo "another watchdog owns tag=$TAG" >&2; exit 1; }
log(){ printf '%s %s\n' "$(date -u +%FT%TZ)" "$*" | tee -a "$LOG"; }
health(){ curl -s -o /dev/null -m 5 -w '%{http_code}' "http://$HEAD_IP:$PORT/health" 2>/dev/null; }
rank_states(){
  local r st bad=0
  unreachable=0
  for r in 0 1 2 3; do
    st=$(timeout 30 ssh -n $SSH_OPTS "${RANK_USER:-spark}@${RANK_IPS[$r]}" "${RANK_DK[$r]} inspect -f '{{.State.Status}}' glm53-tp4-$TAG-r$r" 2>/dev/null) || { st=unreachable; unreachable=1; }
    if [ "$st" != running ]; then log "rank $r state=$st"; bad=1; fi
  done
  return "$bad"
}
flat=0; last_tok=; probe_fail=0
frozen(){
  local m run tok
  m=$(curl -sf -m5 "http://$HEAD_IP:$PORT/metrics") || return 1
  run=$(printf '%s' "$m" | awk '/^vllm:num_requests_(running|waiting)/{s+=$2; found=1} END{if(found)print int(s)}')
  [ -n "$run" ] || return 1
  tok=$(printf '%s' "$m" | awk '/^vllm:(generation|prompt)_tokens_total/{s+=$2} END{printf "%.0f", s}')
  if [ "$run" = 0 ] || [ "$tok" != "$last_tok" ]; then flat=0; probe_fail=0; last_tok=$tok; return 1; fi
  flat=$((flat + 1)); [ "$flat" -lt "${STALL_TICKS:-4}" ] && return 1
  if timeout "$(( ${PROBE_TIMEOUT:-180} + 5 ))" python3 readiness.py "http://$HEAD_IP:$PORT" "${PROBE_TIMEOUT:-180}" >>"$LOG" 2>&1; then
    flat=0; probe_fail=0; return 1
  fi
  probe_fail=$((probe_fail + 1))
  log "liveness probe failed ($probe_fail/${PROBE_FAILS:-2}), running=$run flat_intervals=$flat"
  [ "$probe_fail" -ge "${PROBE_FAILS:-2}" ]
}
pause_failed(){
  if [ "${AUTO_TP2_ROLLBACK:-0}" = 1 ]; then
    if ./tp4-rollback.sh "$CFG" >>"$LOG" 2>&1; then log 'TP2 fallback verified by real inference';
    else log 'ERROR: TP2 fallback failed; service is unavailable'; fi
  else
    log 'ERROR: TP4 recovery failed; no qualified automatic fallback is configured'
  fi
  touch "$MAINT"
  log "supervision paused at $MAINT; operator intervention required"
}
recoveries=(); unhealthy_since=0; was_healthy=0
log "watchdog start cfg=$CFG interval=${INTERVAL}s startup_grace=${STARTUP_GRACE}s unhealthy_grace=${UNHEALTHY_GRACE}s pid=$$"
while :; do
  sleep "$INTERVAL"
  [ -e "$MAINT" ] && { unhealthy_since=0; was_healthy=0; flat=0; probe_fail=0; continue; }
  now=$(date +%s)
  grace=$STARTUP_GRACE
  [ "$was_healthy" = 1 ] && grace=$UNHEALTHY_GRACE
  if ! rank_states; then
    # A user service can start before the host fabric is ready after boot.
    # Allow network restoration time instead of permanently pausing on its
    # first failed SSH connection. Known stopped ranks still recover promptly.
    if [ "$unreachable" = 1 ]; then
      [ "$unhealthy_since" = 0 ] && unhealthy_since=$now
      [ "$((now - unhealthy_since))" -ge "$grace" ] || continue
    fi
    reason=rank-not-running
  elif [ "$(health)" = 200 ]; then
    unhealthy_since=0; was_healthy=1
    frozen || continue
    reason=stalled-inference
  else
    [ "$unhealthy_since" = 0 ] && unhealthy_since=$now
    [ "$((now - unhealthy_since))" -ge "$grace" ] || continue
    reason=health-timeout
  fi
  kept=()
  for t in "${recoveries[@]}"; do [ "$((now - t))" -lt 3600 ] && kept+=("$t"); done
  recoveries=("${kept[@]}")
  if [ "${#recoveries[@]}" -ge "$MAX_RECOVERIES" ]; then log "recovery limit reached ($MAX_RECOVERIES/h)"; pause_failed; continue; fi
  recoveries+=("$now"); unhealthy_since=0; flat=0; probe_fail=0; last_tok=
  began=$(date +%s); log "recovery begin reason=$reason"
  if ./tp4-cluster.sh "$CFG" restart >>"$LOG" 2>&1 && WAIT_TIMEOUT=1500 ./tp4-cluster.sh "$CFG" wait >>"$LOG" 2>&1; then
    was_healthy=1
    log "recovered: all four ranks and real inference PASS; recovery_seconds=$(( $(date +%s) - began ))"
  else
    log "recovery failed after $(( $(date +%s) - began ))s"
    pause_failed
  fi
done
