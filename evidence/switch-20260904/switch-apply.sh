#!/bin/bash
# Runs on the Mini. MikroTik CRS812 RoCE fabric ports: flow control on (honour the NICs' pause and pause the senders),
# 2d65's port from a 100G pin to 200G with RS-FEC, 2b4f's port forced to 200G with RS-FEC (falls back to 100G + RS-FEC
# if the link does not come up). Password from SWPW in the environment; nothing is written to disk.
set -u; R=http://192.168.88.1/rest; A="admin:${SWPW:?}"
patch(){ curl -s -m10 -u "$A" -H 'Content-Type: application/json' -X PATCH "$R/interface/ethernet/$1" -d "$2" | cut -c1-160; echo; }
mon(){ curl -s -m10 -u "$A" -H 'Content-Type: application/json' -X POST "$R/interface/ethernet/monitor" -d "{\"numbers\":\"$1\",\"once\":\"true\"}" | python3 -c 'import json,sys
for m in json.load(sys.stdin): print(" ", m.get("name"), m.get("status"), m.get("rate"), "fec="+str(m.get("fec")), "an="+str(m.get("auto-negotiation")))'; }
echo "== $(date -u +%T) step 1: flow control on, six node ports"
for id in '*4' '*8' '*C' '*10' '*14' '*18'; do patch "$id" '{"rx-flow-control":"on","tx-flow-control":"on"}'; done
sleep 20; mon "qsfp56-1-1,qsfp56-2-1,qsfp56-dd-1-1,qsfp56-dd-1-5,qsfp56-dd-2-1,qsfp56-dd-2-5"
echo "== $(date -u +%T) step 2: 2d65 port (qsfp56-2-1) -> 200G-baseCR4, RS-FEC"
patch '*18' '{"advertise":"200G-baseCR4","fec-mode":"fec91"}'
sleep 25; mon "qsfp56-2-1"
echo "== $(date -u +%T) step 3: 2b4f port (qsfp56-1-1) -> 200G-baseCR4, RS-FEC"
patch '*14' '{"advertise":"200G-baseCR4","fec-mode":"fec91"}'
sleep 30; st=$(mon "qsfp56-1-1"); echo "$st"
if ! echo "$st" | grep -q 'link-ok'; then
  echo "== $(date -u +%T) 2b4f did not link at 200G; fallback 100G-baseCR4 + RS-FEC"
  patch '*14' '{"advertise":"100G-baseCR4","fec-mode":"fec91"}'; sleep 30; mon "qsfp56-1-1"
fi
echo "== $(date -u +%T) final state"; mon "qsfp56-1-1,qsfp56-2-1,qsfp56-dd-1-1,qsfp56-dd-1-5,qsfp56-dd-2-1,qsfp56-dd-2-5"
curl -s -m10 -u "$A" "$R/interface/ethernet" | python3 -c 'import json,sys
for p in json.load(sys.stdin):
    if p["name"] in ("qsfp56-1-1","qsfp56-2-1","qsfp56-dd-1-1","qsfp56-dd-1-5","qsfp56-dd-2-1","qsfp56-dd-2-5"): print(" ", p["name"], p.get("advertise"), p.get("fec-mode"), "rxfc="+p.get("rx-flow-control","?"), "txfc="+p.get("tx-flow-control","?"))'
