#!/bin/bash
# Revert the six node ports to the 2026-09-04 15:00Z state (evidence/switch-20260904/ethernet-config-before.json).
set -u; R=http://192.168.88.1/rest; A="admin:${SWPW:?}"
p(){ curl -s -m10 -u "$A" -H 'Content-Type: application/json' -X PATCH "$R/interface/ethernet/$1" -d "$2" | cut -c1-120; echo; }
for id in '*4' '*8' '*C' '*10' '*14' '*18'; do p "$id" '{"rx-flow-control":"off","tx-flow-control":"off"}'; done
p '*18' '{"advertise":"100G-baseCR4","fec-mode":"fec91"}'
p '*14' '{"advertise":"10M-baseT-half,10M-baseT-full,100M-baseT-half,100M-baseT-full,1G-baseT-half,1G-baseT-full,1G-baseX,2.5G-baseT,2.5G-baseX,5G-baseT,10G-baseT,10G-baseSR-LR,10G-baseCR,40G-baseSR4-LR4,40G-baseCR4,25G-baseSR-LR,25G-baseCR,50G-baseSR2-LR2,50G-baseCR2,100G-baseSR4-LR4,100G-baseCR4,50G-baseSR-LR,50G-baseCR,100G-baseSR2-LR2,100G-baseCR2,200G-baseSR4-LR4,200G-baseCR4","fec-mode":"auto"}'
echo "rolled back"
