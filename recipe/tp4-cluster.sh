#!/bin/bash
# GLM-5.3-Flash EXL3 TP4 cluster orchestrator. Runs on the controller (rank0) and drives the
# four ranks over the RoCE fabric with plain SSH. Usage: tp4-cluster.sh <config.env> launch|wait|status|stop|logs [rank]
set -uo pipefail
CFG=${1:?config}; CMD=${2:?command}; ARG=${3:-}
# Environment overrides for the tunables beat the values in the config file (autoresearch/A-B relaunches rely on it).
TUNABLES="SPEC_METHOD MAX_MODEL_LEN GPU_MEM_UTIL MAX_NUM_SEQS MAX_NUM_BATCHED_TOKENS KV_CACHE_DTYPE DFLASH_TOKENS NCCL_DEBUG NCCL_PROTO NCCL_ALGO NCCL_IB_TIMEOUT NCCL_IB_RETRY_CNT NCCL_BUFFSIZE NCCL_MIN_NCHANNELS NCCL_MAX_NCHANNELS NCCL_IB_QPS_PER_CONNECTION NCCL_IB_SPLIT_DATA_ON_QPS NCCL_NET_GDR_LEVEL EXTRA_ARGS MIXED_PREFILL_CHUNK EXL3_FAT_KERNEL SWAPPINESS"
# ROOT and IMG_ALL (one image for all four ranks) may also be overridden from the environment (image/overlay experiments).
for v in $TUNABLES ROOT IMG_ALL; do [ -n "${!v-}" ] && declare "_OV_$v=${!v}"; done
# shellcheck disable=SC1090
source "$CFG"
for v in $TUNABLES ROOT; do ov="_OV_$v"; [ -n "${!ov-}" ] && declare "$v=${!ov}"; done
[ -n "${_OV_IMG_ALL-}" ] && RANK_IMG=("$_OV_IMG_ALL" "$_OV_IMG_ALL" "$_OV_IMG_ALL" "$_OV_IMG_ALL")
RANK_ROOT=("${RANK_ROOT[@]-}"); RANK_HF=("${RANK_HF[@]-}"); RANK_VC=("${RANK_VC[@]-}")
: "${TAG:?}" "${HEAD_IP:?}" "${PORT:?}" "${RDV:?}" "${ROOT:?}" "${HF:?}" "${VC:?}" "${SPEC_METHOD:?}" "${DRAFT_TP:?}" "${SSH_OPTS:?}"
[ "${#RANK_IPS[@]}" = 4 ] && [ "${#RANK_DK[@]}" = 4 ] && [ "${#RANK_IMG[@]}" = 4 ] || { echo "config needs 4 ranks" >&2; exit 2; }
HERE=$(cd "$(dirname "$0")" && pwd); LOGDIR=$HOME/AI/tp4-$TAG-logs; mkdir -p "$LOGDIR"
NAME(){ echo "glm53-tp4-$TAG-r$1"; }
RUSER=${RANK_USER:-spark}
S(){ local r=$1; shift; ssh -n $SSH_OPTS "$RUSER@${RANK_IPS[$r]}" "$@"; }
log(){ printf '%s %s\n' "$(date -u +%FT%TZ)" "$*" | tee -a "$LOGDIR/cluster.log"; }
health(){ curl -s -o /dev/null -m 5 -w '%{http_code}' "http://$HEAD_IP:$PORT/health" 2>/dev/null; }
case "$CMD" in
  preflight)
    # NCCL bootstraps over the FIRST IPv4 address of NCCL_SOCKET_IFNAME on each rank, RDMA over FABRIC_IP's GID.
    # Every rank must reach both addresses of every other rank, or NCCL init hangs silently.
    fail=0; declare -a FIRST
    for r in 0 1 2 3; do FIRST[$r]=$(S $r "ip -4 -o addr show dev ${NETDEV:-enp1s0f1np1} | head -1 | awk '{print \$4}' | cut -d/ -f1"); echo "rank $r ${RANK_IPS[$r]} first-addr=${FIRST[$r]}"; done
    for a in 0 1 2 3; do for b in 0 1 2 3; do [ $a = $b ] && continue
      for ip in "${RANK_IPS[$b]}" "${FIRST[$b]}"; do
        S $a "ping -c1 -W1 -I ${NETDEV:-enp1s0f1np1} $ip >/dev/null 2>&1" || { echo "UNREACHABLE: rank $a -> rank $b ($ip)"; fail=1; }
      done; done; done
    for r in 0 1 2 3; do S $r "${RANK_DK[$r]} image inspect ${RANK_IMG[$r]} >/dev/null 2>&1" || { echo "rank $r: image missing"; fail=1; }; done
    # GPU clocks: after a reboot a GB10 can come up pinned at 600-700 MHz (Tech2Wild, 2026-09-02). Only a BUSY GPU (util >=
    # CLOCK_MIN_UTIL, default 20 %) can be judged: with no engine running a GB10 idles at 208 MHz, which is normal (the
    # 2026-09-03 watchdog recovery failed on exactly that; the idle reading is informational only).
    for r in 0 1 2 3; do c=$(S $r "nvidia-smi --query-gpu=clocks.sm,clocks.max.sm,utilization.gpu --format=csv,noheader,nounits" 2>/dev/null | head -1 | tr -d " "); IFS=, read -r sm mx ut <<<"$c"
      echo "rank $r clocks: sm=${sm:-?} MHz max=${mx:-?} MHz util=${ut:-?}%"
      if [ "${ut:-0}" -ge "${CLOCK_MIN_UTIL:-20}" ] 2>/dev/null; then
        [ "${sm:-0}" -ge "${CLOCK_MIN_MHZ:-1000}" ] 2>/dev/null || { echo "rank $r: GPU clock ${sm:-unknown} MHz below ${CLOCK_MIN_MHZ:-1000} under load (capped clocks; reboot the node and re-check)"; fail=1; }
      fi; done
    [ $fail = 0 ] && log "preflight PASS" || { log "preflight FAIL"; exit 1; }
    ;;
  launch)
    for r in 0 1 2 3; do
      if [ -n "$(S $r "${RANK_DK[$r]} ps -aq --filter name=^/$(NAME $r)\$")" ]; then log "rank $r: $(NAME $r) already exists; run stop first"; exit 4; fi
    done
    for r in 0 1 2 3; do
      env_str="NODE_RANK=$r HEAD_IP=$HEAD_IP FABRIC_IP=${RANK_IPS[$r]} NETDEV=${NETDEV:-enp1s0f1np1} HCA=${HCA:-rocep1s0f1} RDV=$RDV PORT=$PORT IMG=${RANK_IMG[$r]} ROOT=${RANK_ROOT[$r]:-$ROOT} HF=${RANK_HF[$r]:-$HF} VC=${RANK_VC[$r]:-$VC} TAG=$TAG DK='${RANK_DK[$r]}' SPEC_METHOD=$SPEC_METHOD DRAFT_TP=$DRAFT_TP"
      for v in $TUNABLES; do [ -n "${!v-}" ] && env_str="$env_str $v='${!v}'"; done
      out=$(ssh $SSH_OPTS "$RUSER@${RANK_IPS[$r]}" "env $env_str bash -s" < "$HERE/node-launch.sh" 2>&1); rc=$?
      log "rank $r (${RANK_IPS[$r]}): rc=$rc $(echo "$out" | tr '\n' ' ')"
      [ $rc = 0 ] || { log "launch failed on rank $r; stopping"; "$0" "$CFG" stop; exit 1; }
      [ -s "$LOGDIR/r$r.log" ] && mv "$LOGDIR/r$r.log" "$LOGDIR/r$r.$(date -u +%Y%m%dT%H%M%SZ).log"  # keep the previous run (crash evidence)
      ( setsid nohup ssh -n $SSH_OPTS "$RUSER@${RANK_IPS[$r]}" "${RANK_DK[$r]} logs -f $(NAME $r)" > "$LOGDIR/r$r.log" 2>&1 & )
    done
    log "launched 4 ranks (tag=$TAG, logs in $LOGDIR)"
    ;;
  wait)
    deadline=$(( $(date +%s) + ${WAIT_TIMEOUT:-1800} )); t0=$(date +%s)
    while :; do
      code=$(health)
      if [ "$code" = 200 ]; then log "HEALTHY after $(( $(date +%s) - t0 ))s"; exit 0; fi
      for r in 0 1 2 3; do
        st=$(S $r "${RANK_DK[$r]} inspect -f '{{.State.Status}} {{.State.ExitCode}}' $(NAME $r)" 2>/dev/null)
        case "$st" in running*) ;; *) log "rank $r container state: '$st' — FAILED"; tail -40 "$LOGDIR/r$r.log"; exit 1;; esac
      done
      [ $(date +%s) -lt $deadline ] || { log "TIMEOUT waiting for health"; exit 1; }
      log "waiting... health=$code r0: $(tail -1 "$LOGDIR/r0.log" 2>/dev/null | cut -c1-160)"
      sleep 30
    done
    ;;
  status)
    for r in 0 1 2 3; do printf 'rank %s %s: ' "$r" "${RANK_IPS[$r]}"; S $r "${RANK_DK[$r]} ps -a --filter name=^/$(NAME $r)\$ --format '{{.Status}}'" 2>&1 | head -1; done
    echo "health: $(health)"
    ;;
  stop)
    for r in 3 2 1 0; do S $r "${RANK_DK[$r]} rm -f $(NAME $r) >/dev/null 2>&1 && echo removed || echo absent" | sed "s/^/rank $r: /"; done
    pkill -f "logs -f glm53-tp4-$TAG-" 2>/dev/null; log "stopped tag=$TAG"
    ;;
  logs) tail -n "${LINES:-60}" "$LOGDIR/r${ARG:-0}.log" ;;
  *) echo "usage: $0 <config.env> preflight|launch|wait|status|stop|logs [rank]" >&2; exit 2 ;;
esac
