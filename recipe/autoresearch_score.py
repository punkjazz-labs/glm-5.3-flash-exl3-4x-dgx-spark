#!/usr/bin/env python3
"""Score one glm_workload.py receipt for the autoresearch loop (see AUTORESEARCH.md).

usage: autoresearch_score.py <receipt.json> [--baseline <receipt.json>] [--mem-floor GiB] [--header]
Prints one TSV row: the production metrics, the hard gates, and a single scalar score = geometric mean of
(metric / baseline metric) over the six production metrics (latency metrics inverted and floored at LAT_FLOOR s).
Score 1.0 == baseline.
"""
import json, math, sys
from pathlib import Path

METRICS = [  # (name, higher_is_better)
    ('coding_agg_tps', True), ('coding_ttft_p50', False), ('longgen_decode_tps', True),
    ('shorts_p50_s', False), ('cold_prefill_tps', True), ('conc_top_agg_tps', True)]
EXTRA = ['warm_ttft_s', 'conc_top_decode_p50', 'conc_top_ttft_max', 'spec_accept', 'min_mem_gib', 'min_clock_mhz', 'errors', 'bench_wall_s', 'foreign_reqs']
GATES = ['sanity', 'cancel', 'needle', 'no_errors', 'mem_floor', 'clean', 'clocks',
         'complete', 'final_sanity', 'soak', 'metrics']
CLOCK_MIN_MHZ = 1000  # a GB10 pinned at 600-700 MHz after a reboot invalidates the run (Tech2Wild 2026-09-02)
LAT_FLOOR = 1.0  # seconds


def g(d, *path):
    for k in path:
        if not isinstance(d, dict) or k not in d:
            return None
        d = d[k]
    return d


def med(xs):
    xs = sorted(x for x in xs if x is not None)
    return None if not xs else (xs[len(xs) // 2] if len(xs) % 2 else (xs[len(xs) // 2 - 1] + xs[len(xs) // 2]) / 2)


def extract(r):
    m, gates = {}, {}
    coding = r.get('coding') or {}
    m['coding_agg_tps'] = g(coding, 'summary', 'aggregate_completion_tps')
    m['coding_ttft_p50'] = med([g(x, 'ttft_s') for x in (coding.get('requests') or {}).values()])
    m['longgen_decode_tps'] = g(r, 'longgen', 'primary', 'decode_tps')
    m['shorts_p50_s'] = g(r, 'longgen', 'shorts_during_primary', 'wall_s', 'p50')
    m['cold_prefill_tps'] = g(r, 'cold', 'primary', 'prefill_tps')
    m['warm_ttft_s'] = g(r, 'cold', 'followup', 'ttft_s')
    conc = r.get('conc') or {}
    top = max((k for k in conc if k.isdigit()), key=int, default=None)
    m['conc_top_agg_tps'] = g(conc, top, 'aggregate_completion_tps') if top else None
    m['conc_top_decode_p50'] = g(conc, top, 'decode_tps', 'p50') if top else None
    m['conc_top_ttft_max'] = g(conc, top, 'ttft_s', 'max') if top else None
    a0, d0, a1, d1 = (g(r, 'metrics_before', 'spec_accepted'), g(r, 'metrics_before', 'spec_drafts'), g(r, 'metrics_after', 'spec_accepted'), g(r, 'metrics_after', 'spec_drafts'))
    m['spec_accept'] = round((a1 - a0) / (d1 - d0), 3) if None not in (a0, d0, a1, d1) and d1 > d0 else None
    snapshots = list((r.get('memory_by_phase') or {}).values())
    snapshots += [x.get('mem') or {} for x in (r.get('soak') or {}).get('samples', [])]
    mems = [v.get('MemAvailable_GiB') for ph in snapshots for v in ph.values() if isinstance(v, dict)]
    memory_complete = bool(mems) and all(isinstance(x, (int, float)) and math.isfinite(x) for x in mems)
    m['min_mem_gib'] = min(mems) if memory_complete else None
    # sm clock while the GPU is busy (an idle GB10 legitimately drops to ~208 MHz, e.g. after the engine died)
    clks = [v.get('clock_mhz') for ph in (r.get('memory_by_phase') or {}).values() for v in ph.values()
            if isinstance(v, dict) and v.get('clock_mhz') and (v.get('gpu_util') is None or v['gpu_util'] >= 20)]
    m['min_clock_mhz'] = min(clks) if clks else None
    errs = 0
    for x in (coding.get('requests') or {}).values(): errs += bool(x.get('error'))
    for x in (coding.get('followups') or {}).values(): errs += bool(x.get('error'))
    for ph in ('longgen', 'cold'):
        p = r.get(ph) or {}
        errs += bool(g(p, 'primary', 'error')) + bool(g(p, 'followup', 'error')) + sum(bool(x.get('error')) for x in p.get('shorts') or [])
    for k in conc:
        if k.isdigit(): errs += conc[k].get('errors') or 0
    errs += sum(1 for ph in r.get('phases', []) if isinstance(r.get(ph), dict) and 'error' in r.get(ph) and len(r[ph]) == 1)
    errs += (r.get('soak') or {}).get('errors') or 0
    m['errors'] = errs
    m['bench_wall_s'] = round(sum((r.get('phase_wall_s') or {}).values()), 1)
    m['foreign_reqs'] = r.get('foreign_requests')
    gates['sanity'] = bool(g(r, 'sanity', 'all_pass'))
    gates['cancel'] = bool(g(r, 'cancel', 'pass'))
    gates['needle'] = bool(g(r, 'cold', 'needle_found'))
    gates['no_errors'] = errs == 0
    gates['clean'] = m['foreign_reqs'] == 0  # missing instrumentation is not proof of isolation
    gates['clocks'] = m['min_clock_mhz'] is None or m['min_clock_mhz'] >= CLOCK_MIN_MHZ  # None = receipt predates the clock probe
    phases = r.get('phases') or []
    required = {'sanity', 'coding', 'longgen', 'cold', 'conc', 'cancel', 'sanity_end'}
    gates['complete'] = bool(r.get('finished')) and required.issubset(phases) and all(
        isinstance(r.get(ph), dict) and not r[ph].get('error') for ph in phases)
    gates['final_sanity'] = g(r, 'sanity_end', 'all_pass') is True
    soak = r.get('soak') or {}
    gates['soak'] = 'soak' not in phases or (
        isinstance(soak.get('errors'), int) and soak['errors'] == 0 and
        bool(soak.get('requests')) and bool(soak.get('samples')) and
        isinstance(soak.get('duration_s'), (int, float)) and
        isinstance(r.get('soak_min'), (int, float)) and
        soak['duration_s'] >= r['soak_min'] * 60 and
        bool(soak.get('long_prompt_total')) and
        soak.get('long_prompt_needle_ok') == soak['long_prompt_total'])
    gates['metrics'] = all(isinstance(m.get(n), (int, float)) and math.isfinite(m[n]) and m[n] > 0
                          for n, _ in METRICS)
    return m, gates


def main():
    args = sys.argv[1:]
    if '--header' in args:
        print('\t'.join([n for n, _ in METRICS] + EXTRA + GATES + ['gates_pass', 'score'])); return
    rec = json.load(open(args[0])); base = None; floor = 3.0
    if '--baseline' in args: base = json.load(open(args[args.index('--baseline') + 1]))
    if '--mem-floor' in args: floor = float(args[args.index('--mem-floor') + 1])
    m, gates = extract(rec)
    gates['mem_floor'] = m['min_mem_gib'] is not None and m['min_mem_gib'] >= floor
    ratios = []
    if base:
        bm, _ = extract(base)
        for n, hib in METRICS:
            a, b = m.get(n), bm.get(n)
            if isinstance(a, (int, float)) and isinstance(b, (int, float)) and math.isfinite(a) and math.isfinite(b) and a > 0 and b > 0:
                if not hib:  # latencies enter as max(x, LAT_FLOOR): sub-second jitter is not signal, a queueing blow-up is
                    a, b = max(a, LAT_FLOOR), max(b, LAT_FLOOR)
                ratios.append((a / b) if hib else (b / a))
    score = round(math.exp(sum(map(math.log, ratios)) / len(ratios)), 4) if ratios else (1.0 if not base else 0.0)
    ok = all(gates.values())
    if base and len(ratios) != len(METRICS):
        ok = False
    fmt = lambda v: '' if v is None else (f'{v:.3f}' if isinstance(v, float) else str(v))
    print('\t'.join([fmt(m.get(n)) for n, _ in METRICS] + [fmt(m.get(n)) for n in EXTRA] + [str(int(gates[k])) for k in GATES] + [str(int(ok)), fmt(score)]))


if __name__ == '__main__':
    main()
