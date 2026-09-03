#!/bin/bash
# GLM-5.3-Flash EXL3: production cutover TP2 -> TP4 on the four DGX Sparks, with automatic rollback.
# Runs on rank0 (rank0). The incumbent TP2 head/worker containers are retained objects: they are
# only `docker stop`ped and are `docker start`ed again on any failure (or by tp4-rollback.sh).
# Usage: tp4-cutover.sh [prod.env]        Env: DRY_RUN=1 runs preflight + verification only.
set -uo pipefail
cd "$(dirname "$0")"; CFG=${1:-prod.env}
# shellcheck disable=SC1090
source "$CFG"
: "${TP2_HEAD:?}" "${TP2_WORKER:?}" "${TP2_WORKER_IP:?}" "${HEAD_IP:?}" "${PORT:?}" "${SSH_OPTS:?}"
TS=$(date -u +%Y%m%dT%H%M%SZ); LOG=$HOME/AI/tp4-cutover-$TS.log; RECEIPT=$HOME/AI/tp4-cutover-$TS.json
API=http://127.0.0.1:$PORT/v1
log(){ printf '%s %s\n' "$(date -u +%FT%TZ)" "$*" | tee -a "$LOG"; }
W(){ ssh -n $SSH_OPTS "${RANK_USER:-spark}@$TP2_WORKER_IP" "$@"; }
health(){ curl -s -o /dev/null -m 5 -w '%{http_code}' "http://127.0.0.1:$PORT/health" 2>/dev/null; }
completion(){ curl -s -m 120 "$API/chat/completions" -H 'Content-Type: application/json' \
  -d '{"model":"GLM-5.3-Flash-EXL3","messages":[{"role":"user","content":"Reply with exactly the single word PINEAPPLE."}],"max_tokens":16,"temperature":0,"chat_template_kwargs":{"enable_thinking":false}}' \
  | python3 -c 'import json,sys; print((json.load(sys.stdin)["choices"][0]["message"]["content"] or "").strip())' 2>/dev/null; }
wait_health(){ local deadline=$(( $(date +%s) + $1 )); while [ "$(health)" != 200 ]; do [ $(date +%s) -lt $deadline ] || return 1; sleep 15; done; }
tp2_state(){ echo "head=$(docker inspect -f '{{.State.Status}}' "$TP2_HEAD" 2>/dev/null) worker=$(W "docker inspect -f '{{.State.Status}}' $TP2_WORKER" 2>/dev/null)"; }
mem_avail_gib(){ awk '/MemAvailable/{printf "%d",$2/1048576}' /proc/meminfo; }

rollback(){
  log "ROLLBACK: reason: $1"
  ./tp4-cluster.sh "$CFG" stop | tee -a "$LOG"
  W "docker start $TP2_WORKER" >/dev/null 2>&1 && log "TP2 worker started" || log "ERROR: TP2 worker start failed"
  sleep 3
  docker start "$TP2_HEAD" >/dev/null 2>&1 && log "TP2 head started" || log "ERROR: TP2 head start failed"
  if wait_health 1500 && [ "$(completion)" = PINEAPPLE ]; then log "TP2 RESTORED and healthy ($(tp2_state))"; else log "ERROR: TP2 NOT healthy after restore ($(tp2_state)) — manual attention required"; fi
  printf '{"result":"ROLLED_BACK","reason":%s,"tp2":"%s","log":"%s"}\n' "$(printf '%s' "$1" | python3 -c 'import json,sys;print(json.dumps(sys.stdin.read()))')" "$(tp2_state)" "$LOG" > "$RECEIPT"
  exit 1
}

log "=== TP4 cutover start (cfg=$CFG dry_run=${DRY_RUN:-0}) ==="
# 0. Preconditions: incumbent healthy, candidate absent, fabric + assets ready, baseline present.
[ -f baseline-tp2.json ] || { log "baseline-tp2.json missing"; exit 2; }
[ "$(tp2_state)" = "head=running worker=running" ] || { log "TP2 not fully running: $(tp2_state)"; exit 2; }
[ "$(health)" = 200 ] || { log "TP2 health != 200"; exit 2; }
[ "$(completion)" = PINEAPPLE ] || { log "TP2 completion check failed"; exit 2; }
./tp4-cluster.sh "$CFG" preflight | tee -a "$LOG" || { log "preflight failed"; exit 2; }
for r in 0 1 2 3; do
  h=$(ssh -n $SSH_OPTS "${RANK_USER:-spark}@${RANK_IPS[$r]}" "cd $ROOT && find . -type f | sort | xargs sha256sum | sha256sum | cut -d' ' -f1")
  [ "$h" = "$(sha256sum ~/AI/glm53-tp4-root.sha256 | cut -d' ' -f1)" ] || { log "rank $r runtime root hash mismatch ($h)"; exit 2; }
done
log "preconditions OK: TP2 healthy, preflight PASS, runtime roots identical on 4 ranks"
[ "${DRY_RUN:-0}" = 1 ] && { log "DRY_RUN: stopping here"; exit 0; }

# 1. Stop the incumbent (retained objects) and wait for memory to be released.
log "stopping TP2 head+worker"
docker stop -t 120 "$TP2_HEAD" >/dev/null 2>&1; W "docker stop -t 120 $TP2_WORKER" >/dev/null 2>&1
[ "$(tp2_state)" = "head=exited worker=exited" ] || rollback "TP2 did not stop cleanly: $(tp2_state)"
for i in $(seq 1 24); do [ "$(mem_avail_gib)" -ge 100 ] && [ "$(W "awk '/MemAvailable/{printf \"%d\",\$2/1048576}' /proc/meminfo")" -ge 100 ] && break; sleep 5; done
log "TP2 stopped; MemAvailable rank0=$(mem_avail_gib)GiB"

# 2. Launch TP4 and wait for health.
./tp4-cluster.sh "$CFG" launch | tee -a "$LOG" || rollback "TP4 launch failed"
WAIT_TIMEOUT=${WAIT_TIMEOUT:-1500} ./tp4-cluster.sh "$CFG" wait | tee -a "$LOG" || rollback "TP4 did not become healthy"
[ "$(completion)" = PINEAPPLE ] || rollback "TP4 completion check failed"
log "TP4 healthy and answering"

# 3. Qualify against the frozen TP2 baseline (same suite hash) and decide.
GLM_URL=$API GLM_LABEL=TP4-prod GLM_OUT=$HOME/AI/tp4-benchmark-$TS.json GLM_STRICT=0 \
  GLM_MEM_HOSTS="rank0=${RANK_USER:-spark}@${RANK_IPS[0]} rank1=${RANK_USER:-spark}@${RANK_IPS[1]} rank2=${RANK_USER:-spark}@${RANK_IPS[2]} rank3=${RANK_USER:-spark}@${RANK_IPS[3]}" GLM_SSH_OPTS="$SSH_OPTS" \
  python3 glm_benchmark.py 2>&1 | tee -a "$LOG" || rollback "benchmark runner failed"
python3 compare_baseline.py baseline-tp2.json "$HOME/AI/tp4-benchmark-$TS.json" --min-ratio "${MIN_RATIO:-0.9}" 2>&1 | tee -a "$LOG" \
  || rollback "TP4 qualification failed against TP2 baseline"

# 4. Promote: TP4 stays, TP2 objects stay stopped as rollback evidence.
log "PROMOTED: TP4 is live on :$PORT; TP2 containers retained (stopped). Rollback: ./tp4-rollback.sh $CFG"
printf '{"result":"PROMOTED","benchmark":"%s","log":"%s","tp2":"%s"}\n' "$HOME/AI/tp4-benchmark-$TS.json" "$LOG" "$(tp2_state)" > "$RECEIPT"
