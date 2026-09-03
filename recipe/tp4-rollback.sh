#!/bin/bash
# Manual rollback: remove the TP4 quartet and restart the retained TP2 head/worker containers.
# Usage: tp4-rollback.sh [prod.env]
set -uo pipefail
cd "$(dirname "$0")"; CFG=${1:-prod.env}
# shellcheck disable=SC1090
source "$CFG"
: "${TP2_HEAD:?}" "${TP2_WORKER:?}" "${TP2_WORKER_IP:?}" "${PORT:?}" "${SSH_OPTS:?}"
log(){ printf '%s %s\n' "$(date -u +%FT%TZ)" "$*"; }
W(){ ssh -n $SSH_OPTS "${RANK_USER:-spark}@$TP2_WORKER_IP" "$@"; }
health(){ curl -s -o /dev/null -m 5 -w '%{http_code}' "http://127.0.0.1:$PORT/health" 2>/dev/null; }
./tp4-cluster.sh "$CFG" stop
W "docker start $TP2_WORKER" >/dev/null && log "TP2 worker started" || log "ERROR worker start"
sleep 3
docker start "$TP2_HEAD" >/dev/null && log "TP2 head started" || log "ERROR head start"
deadline=$(( $(date +%s) + 1500 ))
while [ "$(health)" != 200 ]; do [ $(date +%s) -lt $deadline ] || { log "TP2 not healthy after 25 min"; exit 1; }; sleep 15; done
log "TP2 healthy on :$PORT (head=$(docker inspect -f '{{.State.Status}}' "$TP2_HEAD") worker=$(W "docker inspect -f '{{.State.Status}}' $TP2_WORKER"))"
