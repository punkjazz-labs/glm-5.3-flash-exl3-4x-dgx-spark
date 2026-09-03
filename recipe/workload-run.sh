#!/bin/bash
# Run the workload benchmark detached on the head node (rank 0) against the live :8890 endpoint.
# Usage: [CFG=prod.env] workload-run.sh <label> [phases]   (label TP2* probes ranks 0/1 memory, anything else ranks 0-3)
# Receipt ~/AI/workload-<label>-<UTC>.json, log ~/AI/workload-<label>-<UTC>.log. SOAK_MIN/SOAK_KINDS/SOAK_WORKERS/COLD_TOKENS pass through.
set -uo pipefail
cd "$(dirname "$0")"
# shellcheck disable=SC1091
source "${CFG:-prod.env}"
U=${RANK_USER:-spark}; LABEL=${1:?label}; TS=$(date -u +%Y%m%dT%H%M%SZ)
case "$LABEL" in
  TP2*) HOSTS="r0=$U@${RANK_IPS[0]} r1=$U@${RANK_IPS[1]}" ;;
  *) HOSTS="r0=$U@${RANK_IPS[0]} r1=$U@${RANK_IPS[1]} r2=$U@${RANK_IPS[2]} r3=$U@${RANK_IPS[3]}" ;;
esac
OUT=$HOME/AI/workload-$LABEL-$TS.json; LOG=$HOME/AI/workload-$LABEL-$TS.log
GLM_URL="http://127.0.0.1:$PORT/v1" GLM_LABEL="$LABEL" GLM_OUT="$OUT" GLM_MEM_HOSTS="$HOSTS" GLM_SSH_OPTS="$SSH_OPTS" \
  SOAK_MIN="${SOAK_MIN:-30}" SOAK_KINDS="${SOAK_KINDS-}" SOAK_WORKERS="${SOAK_WORKERS:-4}" SOAK_LONGGEN_TOKENS="${SOAK_LONGGEN_TOKENS:-4096}" COLD_TOKENS="${COLD_TOKENS:-280000}" LONGGEN_TOKENS="${LONGGEN_TOKENS:-12288}" CONC_LEVELS="${CONC_LEVELS:-4,8}" GLM_PHASES="${2:-warmup,sanity,coding,longgen,cold,cancel,soak,sanity_end}" \
  nohup python3 glm_workload.py > "$LOG" 2>&1 < /dev/null &
echo "pid $! log $LOG receipt $OUT"
