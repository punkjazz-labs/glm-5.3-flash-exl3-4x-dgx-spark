#!/usr/bin/env python3
"""Compare a glm_benchmark.py receipt against the frozen TP2 baseline.

usage: compare_baseline.py baseline.json candidate.json [--min-ratio 0.9] [--max-ttft-ratio 1.5]
Exit 0 = PASS (contract passed on the candidate, no throughput metric below min-ratio x baseline,
TTFT/latency not above max-ttft-ratio x baseline). Prints a table either way.
"""
import argparse, json, sys

HIGHER = ['median_decode_tps', 'median_long_prefill_tps', 'median_concurrency4_aggregate_tps']
LOWER = ['p50_ttft_s', 'p95_e2e_latency_s']

ap = argparse.ArgumentParser()
ap.add_argument('baseline'); ap.add_argument('candidate')
ap.add_argument('--min-ratio', type=float, default=0.9)
ap.add_argument('--max-ttft-ratio', type=float, default=1.5)
a = ap.parse_args()
b = json.load(open(a.baseline)); c = json.load(open(a.candidate))
if b.get('suite_sha256') != c.get('suite_sha256'):
    print(f"FAIL suite mismatch {b.get('suite_sha256')} != {c.get('suite_sha256')}"); sys.exit(1)
ok = True
rows = []
for k in HIGHER + LOWER:
    bv = b['summary'][k]; cv = c['summary'][k]; r = cv / bv if bv else float('nan')
    verdict = 'ok'
    if k in HIGHER and r < a.min_ratio: verdict = 'REGRESSION'; ok = False
    if k in LOWER and r > a.max_ttft_ratio: verdict = 'REGRESSION'; ok = False
    rows.append((k, bv, cv, r, verdict))
contract = c.get('contract', {}).get('all_pass', False)
if not contract: ok = False
print(f"{'metric':36} {'baseline':>12} {'candidate':>12} {'ratio':>7}  verdict")
for k, bv, cv, r, v in rows:
    print(f"{k:36} {bv:12.4f} {cv:12.4f} {r:7.3f}  {v}")
print(f"contract all_pass: {contract}")
print('RESULT:', 'PASS' if ok else 'FAIL')
sys.exit(0 if ok else 1)
