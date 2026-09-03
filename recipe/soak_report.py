#!/usr/bin/env python3
"""Soak report from a glm_workload.py receipt: per-kind latency, time drift (thirds), memory/clock trend per rank, engine
counters, needle accuracy, foreign traffic. Usage: soak_report.py <receipt.json> [--json]"""
import json, statistics, sys


def pct(xs, p):
    if not xs:
        return None
    s = sorted(xs); i = (len(s) - 1) * p; lo = int(i); hi = min(lo + 1, len(s) - 1)
    return round(s[lo] + (s[hi] - s[lo]) * (i - lo), 3)


def main(path, as_json=False):
    rec = json.load(open(path)); soak = rec.get('soak') or {}
    rows = soak.get('rows', []); samples = soak.get('samples', []); dur = soak.get('duration_s', 0)
    ok = [r for r in rows if not r.get('error')]
    out = {'label': rec.get('label'), 'duration_min': round(dur / 60, 1), 'requests': len(rows), 'errors': len(rows) - len(ok),
           'error_samples': soak.get('error_samples', []), 'foreign_requests': rec.get('foreign_requests'), 'own_requests': rec.get('own_requests'),
           'kinds': soak.get('kinds'), 'workers': soak.get('workers'), 'aggregate_completion_tps': soak.get('aggregate_completion_tps'),
           'aggregate_prompt_tps': soak.get('aggregate_prompt_tps'),
           'needle': f"{soak.get('long_prompt_needle_ok')}/{soak.get('long_prompt_total', 'n/a')}",
           'sanity_before': (rec.get('sanity') or {}).get('all_pass'), 'sanity_after': (rec.get('sanity_end') or {}).get('all_pass')}
    kinds = sorted({r['kind'] for r in rows}, key=lambda k: (soak.get('kinds') or [k]).index(k) if k in (soak.get('kinds') or []) else 99)
    by_kind = {}
    for k in kinds:
        rs = [r for r in ok if r['kind'] == k]; allr = [r for r in rows if r['kind'] == k]
        thirds = []
        for j in range(3):
            seg = [r['wall_s'] for r in rs if j * dur / 3 < r['t_end'] <= (j + 1) * dur / 3]
            thirds.append(pct(seg, 0.5))
        by_kind[k] = {'n': len(allr), 'errors': len(allr) - len(rs), 'wall_p50': pct([r['wall_s'] for r in rs], 0.5), 'wall_p95': pct([r['wall_s'] for r in rs], 0.95),
                      'ttft_p50': pct([r['ttft_s'] for r in rs if r.get('ttft_s') is not None], 0.5), 'ttft_p95': pct([r['ttft_s'] for r in rs if r.get('ttft_s') is not None], 0.95),
                      'ttft_max': pct([r['ttft_s'] for r in rs if r.get('ttft_s') is not None], 1.0),
                      'decode_p50': pct([r['decode_tps'] for r in rs if r.get('decode_tps')], 0.5), 'prefill_p50': pct([r['prefill_tps'] for r in rs if r.get('prefill_tps')], 0.5),
                      'prompt_tok_p50': pct([r['prompt_tokens'] for r in rs], 0.5), 'wall_p50_by_third': thirds,
                      'finish': dict(sorted({f: sum(1 for r in rs if r.get('finish_reason') == f) for f in {r.get('finish_reason') for r in rs}}.items(), key=str))}
    out['by_kind'] = by_kind
    mem = {}
    for rname in sorted({h for s in samples for h in s.get('mem', {})}):
        avail = [(s['t'], s['mem'][rname].get('MemAvailable_GiB')) for s in samples if rname in s['mem'] and s['mem'][rname].get('MemAvailable_GiB') is not None]
        swap = [s['mem'][rname].get('SwapFree_GiB') for s in samples if rname in s['mem'] and s['mem'][rname].get('SwapFree_GiB') is not None]
        clk = [s['mem'][rname].get('clock_mhz') for s in samples if rname in s['mem'] and s['mem'][rname].get('clock_mhz') is not None and (s['mem'][rname].get('gpu_util') or 0) >= 20]
        util = [s['mem'][rname].get('gpu_util') for s in samples if rname in s['mem'] and s['mem'][rname].get('gpu_util') is not None]
        n = len(avail); q = max(1, n // 4)
        mem[rname] = {'avail_first_q_med': pct([v for _, v in avail[:q]], 0.5), 'avail_last_q_med': pct([v for _, v in avail[-q:]], 0.5), 'avail_min': min((v for _, v in avail), default=None),
                      'avail_max': max((v for _, v in avail), default=None), 'swapfree_first': swap[0] if swap else None, 'swapfree_last': swap[-1] if swap else None,
                      'clock_min_busy': min(clk, default=None), 'clock_med_busy': pct(clk, 0.5), 'util_med': pct(util, 0.5), 'samples': n}
    out['memory_by_rank'] = mem
    m = [s['metrics'] for s in samples if s.get('metrics')]
    out['engine'] = {'running_max': max((x.get('running', 0) for x in m), default=None), 'waiting_max': max((x.get('waiting', 0) for x in m), default=None),
                     'kv_usage_max': max((x.get('kv_usage', 0) for x in m), default=None), 'samples': len(m),
                     'spec_accept_rate': None}
    if len(m) >= 2 and m[-1].get('spec_drafts') and m[0].get('spec_drafts') is not None and m[-1]['spec_drafts'] > m[0]['spec_drafts']:
        out['engine']['spec_accept_rate'] = round((m[-1]['spec_accepted'] - m[0]['spec_accepted']) / (m[-1]['spec_drafts'] - m[0]['spec_drafts']), 3)
    if as_json:
        print(json.dumps(out, indent=1)); return
    print(f"soak {out['label']}: {out['duration_min']} min, {out['requests']} req, {out['errors']} err, foreign {out['foreign_requests']}, workers {out['workers']}, "
          f"needle {out['needle']}, sanity before/after {out['sanity_before']}/{out['sanity_after']}, gen {out['aggregate_completion_tps']} tok/s, prompt {out['aggregate_prompt_tps']} tok/s")
    print(f"engine: running max {out['engine']['running_max']}, waiting max {out['engine']['waiting_max']}, kv max {out['engine']['kv_usage_max']}, spec accept {out['engine']['spec_accept_rate']}")
    print(f"{'kind':16}{'n':>5}{'err':>4}{'wall p50':>10}{'p95':>9}{'ttft p50':>10}{'p95':>8}{'max':>8}{'dec p50':>9}{'pre p50':>9}{'ptok':>8}  wall p50 by third   finish")
    for k, v in by_kind.items():
        print(f"{k:16}{v['n']:>5}{v['errors']:>4}{v['wall_p50']!s:>10}{v['wall_p95']!s:>9}{v['ttft_p50']!s:>10}{v['ttft_p95']!s:>8}{v['ttft_max']!s:>8}{v['decode_p50']!s:>9}{v['prefill_p50']!s:>9}{v['prompt_tok_p50']!s:>8}  {v['wall_p50_by_third']!s:20} {v['finish']}")
    print(f"{'rank':6}{'avail 1stQ':>11}{'lastQ':>8}{'min':>7}{'max':>7}{'swapfree 1st':>13}{'last':>7}{'clk min':>9}{'med':>7}{'util':>6}")
    for r, v in mem.items():
        print(f"{r:6}{v['avail_first_q_med']!s:>11}{v['avail_last_q_med']!s:>8}{v['avail_min']!s:>7}{v['avail_max']!s:>7}{v['swapfree_first']!s:>13}{v['swapfree_last']!s:>7}{v['clock_min_busy']!s:>9}{v['clock_med_busy']!s:>7}{v['util_med']!s:>6}")
    for e in out['error_samples']:
        print('error:', str(e)[:200])


if __name__ == '__main__':
    main(sys.argv[1], '--json' in sys.argv)
