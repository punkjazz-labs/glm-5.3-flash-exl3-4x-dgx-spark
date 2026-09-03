#!/bin/bash
# Autoresearch loop for the GLM-5.3-Flash TP4 recipe (AUTORESEARCH.md). Runs on rank0, detached.
# Usage: autoresearch.sh <cfg.env> <experiments.tsv> [outdir]     (outdir default ~/AI/autoresearch)
# experiments.tsv: "<name>\t<VAR=val VAR=val ...>" per line, '#' comments. A name starting with "live-" benches the
# currently running server without relaunching. Every other line: stop -> launch with the overrides -> wait -> bench.
# The first experiment whose gates pass becomes the score baseline (score 1.0); the best gated score is tracked and
# relaunched at the end if it is not already live. Results: <outdir>/results.tsv, receipts <outdir>/<name>.json/.log.
set -uo pipefail
CFG=${1:?cfg.env}; EXPS=${2:?experiments.tsv}; OUTDIR=${3:-$HOME/AI/autoresearch}
BENCH_PHASES=${BENCH_PHASES:-warmup,sanity,coding,longgen,cold,conc,cancel}
BENCH_ENV="LONGGEN_TOKENS=${LONGGEN_TOKENS:-4096} COLD_TOKENS=${COLD_TOKENS:-64000} CONC_LEVELS=${CONC_LEVELS:-4,8}"
MEM_FLOOR=${MEM_FLOOR:-3.0}
cd "$(dirname "$0")"; HERE=$PWD; mkdir -p "$OUTDIR"
# shellcheck disable=SC1090
source "$CFG"
U=${RANK_USER:-spark}; HOSTS="r0=$U@${RANK_IPS[0]} r1=$U@${RANK_IPS[1]} r2=$U@${RANK_IPS[2]} r3=$U@${RANK_IPS[3]}"
RES=$OUTDIR/results.tsv; BASEF=$OUTDIR/baseline.receipt; BESTF=$OUTDIR/best.tsv
log(){ printf '%s %s\n' "$(date -u +%FT%TZ)" "$*" | tee -a "$OUTDIR/autoresearch.log"; }
health(){ curl -s -o /dev/null -m 5 -w '%{http_code}' "http://$HEAD_IP:$PORT/health" 2>/dev/null; }
[ -f "$RES" ] || printf 'ts\tname\toverrides\tstatus\t%s\treceipt\tlive_env\n' "$(python3 autoresearch_score.py --header)" > "$RES"
best_score=0; best_name=; best_over=; live_over="(unknown)"
if [ -f "$BESTF" ]; then IFS=$'\t' read -r best_score best_name best_over < "$BESTF"; fi
BASE=$( [ -f "$BASEF" ] && cat "$BASEF" || true )

relaunch(){ # $1 = overrides
  log "stop + relaunch with: ${1:-(defaults)}"
  ./tp4-cluster.sh "$CFG" stop >>"$OUTDIR/autoresearch.log" 2>&1; sleep 15
  env $1 ./tp4-cluster.sh "$CFG" launch >>"$OUTDIR/autoresearch.log" 2>&1 || return 1
  WAIT_TIMEOUT=1500 ./tp4-cluster.sh "$CFG" wait >>"$OUTDIR/autoresearch.log" 2>&1 || return 1
  live_over=$1; sleep 10
}

while IFS=$'\t' read -r name over; do
  [ -z "$name" ] || [ "${name:0:1}" = "#" ] && continue
  if grep -q "^[^	]*	$name	" "$RES"; then log "skip $name (already in results)"; continue; fi
  status=ok; receipt=$OUTDIR/$name.json
  case "$name" in live-*) over="(live)"; [ "$(health)" = 200 ] || status=not_live ;;
    *) relaunch "$over" || status=launch_failed ;; esac
  row=
  if [ "$status" = ok ]; then
    log "bench $name"
    env GLM_URL="http://127.0.0.1:$PORT/v1" GLM_LABEL="$name" GLM_OUT="$receipt" GLM_MEM_HOSTS="$HOSTS" GLM_SSH_OPTS="$SSH_OPTS" GLM_PHASES="$BENCH_PHASES" $BENCH_ENV \
      timeout 2400 python3 glm_workload.py > "$OUTDIR/$name.log" 2>&1 < /dev/null; rc=$?
    [ "$(health)" = 200 ] || status=engine_died
    [ $rc = 0 ] || [ "$status" != ok ] || status=bench_rc$rc
    if [ -f "$receipt" ]; then
      row=$(python3 autoresearch_score.py "$receipt" ${BASE:+--baseline "$BASE"} --mem-floor "$MEM_FLOOR" 2>>"$OUTDIR/autoresearch.log")
    fi
  fi
  [ -n "$row" ] || row=$(python3 autoresearch_score.py --header | sed 's/[^\t]*//g')
  live_env=$(${RANK_DK[0]} inspect "glm53-tp4-$TAG-r0" --format '{{range .Config.Env}}{{println .}}{{end}}' 2>/dev/null | grep -E '^(GPU_MEM_UTIL|MAX_NUM_SEQS|MAX_NUM_BATCHED_TOKENS|DFLASH_TOKENS|GLM53_MIXED_PREFILL_CHUNK|KV_CACHE_DTYPE|EXL3_FAT_KERNEL|HOST_SWAPPINESS)=' | sort | tr '\n' ' ')
  live_env="$live_env IMAGE=$(${RANK_DK[0]} inspect "glm53-tp4-$TAG-r0" --format '{{.Config.Image}}' 2>/dev/null | cut -c1-40)"
  printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\n' "$(date -u +%FT%TZ)" "$name" "$over" "$status" "$row" "$receipt" "$live_env" >> "$RES"
  gates_pass=$(echo "$row" | awk -F'\t' '{print $(NF-1)}'); score=$(echo "$row" | awk -F'\t' '{print $NF}')
  log "result $name status=$status gates_pass=${gates_pass:-?} score=${score:-?}"
  if [ "$status" = ok ] && [ "$gates_pass" = 1 ]; then
    if [ -z "${BASE:-}" ]; then BASE=$receipt; echo "$BASE" > "$BASEF"; log "baseline := $name"; fi
    if awk -v a="$score" -v b="$best_score" 'BEGIN{exit !(a>b)}'; then best_score=$score; best_name=$name; best_over=$over; printf '%s\t%s\t%s\n' "$best_score" "$best_name" "$best_over" > "$BESTF"; log "best := $name ($score)"; fi
  fi
done < "$EXPS"

log "queue done. best=$best_name score=$best_score overrides=$best_over; live=$live_over"
if [ -n "$best_name" ] && [ "$best_over" != "$live_over" ] && [ "$best_over" != "(live)" ]; then
  relaunch "$best_over" && log "relaunched best ($best_name)" || log "RELAUNCH OF BEST FAILED"
fi
log "final health=$(health)"
