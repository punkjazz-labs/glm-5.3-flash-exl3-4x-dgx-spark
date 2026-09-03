#!/usr/bin/env python3
"""GLM-5.3-Flash EXL3 qualification benchmark.

Same suite (CASES/TOOLS, sampling, contract and summary formulas) as the frozen TP2 baseline
FR-2026-09-01-074-tp2-baseline.json (suite_sha256 305f0830...), so TP4 numbers compare 1:1.
Env: GLM_URL (default http://127.0.0.1:8890/v1), GLM_LABEL, GLM_OUT, GLM_MEM_HOSTS ("label=user@host ..." probed
over SSH with GLM_SSH_OPTS), GLM_STRICT=0 to record a failed contract instead of raising.
"""
import concurrent.futures, hashlib, json, math, os, shlex, statistics, subprocess, sys, time, urllib.request
from pathlib import Path

URL = os.environ.get('GLM_URL', 'http://127.0.0.1:8890/v1')
MODEL = 'GLM-5.3-Flash-EXL3'
LABEL = os.environ.get('GLM_LABEL', 'TP4')
OUT = Path(os.environ.get('GLM_OUT', f'glm-benchmark-{LABEL}.json'))
CASES = {
 'exact_json': {'messages': [{'role': 'user', 'content': 'Return exactly this JSON object and nothing else: {"status":"ok","value":7}'}], 'max_tokens': 64},
 'normal_eos': {'messages': [{'role': 'user', 'content': 'Reply with exactly the single word PINEAPPLE.'}], 'max_tokens': 64},
 'decode512': {'messages': [{'role': 'user', 'content': 'Write a continuous technically precise explanation of distributed tensor-parallel inference. Do not use headings or lists.'}], 'max_tokens': 512, 'ignore_eos': True},
 'long_prefill': {'messages': [{'role': 'user', 'content': ('Context block: alpha beta gamma delta. ' * 3000) + '\nNeedle: ORCHID-742. Reply with only the needle.'}], 'max_tokens': 32},
 'concurrency4': {'messages': [{'role': 'user', 'content': 'Explain one practical property of reliable distributed inference in compact prose.'}], 'max_tokens': 256, 'ignore_eos': True},
}
TOOLS = [{'type': 'function', 'function': {'name': 'multiply', 'description': 'Multiply two integers', 'parameters': {'type': 'object', 'properties': {'a': {'type': 'integer'}, 'b': {'type': 'integer'}}, 'required': ['a', 'b'], 'additionalProperties': False}}}]


def post(path, payload, timeout=660):
    b = json.dumps(payload, separators=(',', ':')).encode()
    req = urllib.request.Request(URL + path, data=b, headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def streaming(case, rep):
    p = {'model': MODEL, 'messages': case['messages'], 'max_tokens': case['max_tokens'], 'temperature': 0, 'stream': True,
         'stream_options': {'include_usage': True}, 'chat_template_kwargs': {'enable_thinking': False}, 'seed': 42}
    if case.get('ignore_eos'):
        p['ignore_eos'] = True
    t0 = time.perf_counter(); first = None; finish = None; usage = {}; parts = []
    req = urllib.request.Request(URL + '/chat/completions', data=json.dumps(p, separators=(',', ':')).encode(), headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=660) as r:
        for raw in r:
            line = raw.decode('utf-8', 'replace').strip()
            if not line.startswith('data: '):
                continue
            data = line[6:]
            if data == '[DONE]':
                break
            j = json.loads(data); usage = j.get('usage') or usage
            for c in j.get('choices', []):
                d = c.get('delta') or {}; txt = d.get('content') or ''
                if txt and first is None:
                    first = time.perf_counter()
                parts.append(txt); finish = c.get('finish_reason') or finish
    t1 = time.perf_counter(); text = ''.join(parts); ct = int(usage.get('completion_tokens') or 0); pt = int(usage.get('prompt_tokens') or 0)
    preview = text[:80] if case is CASES['normal_eos'] or case is CASES['long_prefill'] or case is CASES['exact_json'] else None
    return {'rep': rep, 'wall_s': round(t1 - t0, 6), 'ttft_s': None if first is None else round(first - t0, 6),
            'decode_tps': None if first is None or t1 <= first or ct < 2 else round((ct - 1) / (t1 - first), 6),
            'prompt_tps': None if first is None or first <= t0 or pt < 1 else round(pt / (first - t0), 6),
            'prompt_tokens': pt, 'completion_tokens': ct, 'finish_reason': finish,
            'text_sha256': hashlib.sha256(text.encode()).hexdigest(), 'text_chars': len(text), 'text_preview': preview}


def mem():
    out = {}
    opts = shlex.split(os.environ.get('GLM_SSH_OPTS', '-o BatchMode=yes'))
    for item in os.environ.get('GLM_MEM_HOSTS', '').split():
        label, host = item.split('=', 1)
        script = "python3 - <<'PY'\nimport json\nd={}\nfor l in open('/proc/meminfo'):\n k,v=l.split(':',1);d[k]=int(v.split()[0])*1024\nprint(json.dumps({'MemAvailable':d['MemAvailable'],'SwapFree':d['SwapFree']}))\nPY"
        r = subprocess.run(['ssh', '-n', *opts, host, script], text=True, capture_output=True, timeout=60)
        out[label] = json.loads(r.stdout) if r.returncode == 0 else {'error': r.stderr.strip()[:200]}
    return out


def percentile(xs, p):
    s = sorted(xs); i = (len(s) - 1) * p; lo = math.floor(i); hi = math.ceil(i)
    return s[lo] if lo == hi else s[lo] * (hi - i) + s[hi] * (i - lo)


def main():
    models = json.loads(urllib.request.urlopen(URL + '/models', timeout=30).read())
    if [x['id'] for x in models['data']] != [MODEL]:
        raise RuntimeError('model identity mismatch: %r' % [x['id'] for x in models['data']])
    suite_bytes = json.dumps(CASES, sort_keys=True, separators=(',', ':')).encode()
    runner_hash = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    rec = {'schema': 'fr074-glm-benchmark-v1', 'candidate': LABEL, 'endpoint': URL, 'model': MODEL,
           'suite_sha256': hashlib.sha256(suite_bytes).hexdigest(), 'runner_sha256': runner_hash,
           'sampling': {'temperature': 0, 'seed': 42, 'enable_thinking': False}, 'memory_before': mem(), 'cases': {}, 'contract': {}}
    for rep in range(3):
        ej = post('/chat/completions', {'model': MODEL, **CASES['exact_json'], 'temperature': 0, 'seed': 42, 'chat_template_kwargs': {'enable_thinking': False}}, 300)
        text = ej['choices'][0]['message'].get('content') or ''
        rec['contract'].setdefault('exact_json', []).append({'rep': rep, 'pass': text.strip() == '{"status":"ok","value":7}', 'finish': ej['choices'][0]['finish_reason'], 'sha256': hashlib.sha256(text.encode()).hexdigest(), 'preview': text[:80]})
        tool = post('/chat/completions', {'model': MODEL, 'messages': [{'role': 'user', 'content': 'Use the multiply tool to multiply 6 by 7.'}], 'tools': TOOLS, 'tool_choice': 'required', 'temperature': 0, 'seed': 42, 'max_tokens': 128, 'chat_template_kwargs': {'enable_thinking': False}}, 300)
        ch = tool['choices'][0]; calls = ch['message'].get('tool_calls') or []; ok = False
        if len(calls) == 1:
            try:
                a = json.loads(calls[0]['function']['arguments']); ok = calls[0]['function']['name'] == 'multiply' and a == {'a': 6, 'b': 7}
            except Exception:
                pass
        rec['contract'].setdefault('tool_call', []).append({'rep': rep, 'pass': ok, 'finish': ch.get('finish_reason'), 'call_count': len(calls)})
    for name in ('normal_eos', 'decode512', 'long_prefill'):
        rec['cases'][name] = [streaming(CASES[name], rep) for rep in range(3)]
    batches = []
    for rep in range(3):
        t = time.perf_counter()
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as ex:
            rows = list(ex.map(lambda i: streaming(CASES['concurrency4'], i), range(4)))
        wall = time.perf_counter() - t; tokens = sum(x['completion_tokens'] for x in rows)
        batches.append({'rep': rep, 'wall_s': round(wall, 6), 'completion_tokens': tokens, 'aggregate_tps': round(tokens / wall, 6), 'requests': rows})
    rec['cases']['concurrency4'] = batches
    rec['memory_after'] = mem()
    rec['contract']['normal_eos_pass'] = all((x['text_preview'] or '').strip() == 'PINEAPPLE' and x['finish_reason'] == 'stop' for x in rec['cases']['normal_eos'])
    rec['contract']['long_prefill_pass'] = all('ORCHID-742' in (x['text_preview'] or '') for x in rec['cases']['long_prefill'])
    rec['contract']['all_pass'] = all(x['pass'] for k in ('exact_json', 'tool_call') for x in rec['contract'][k]) and rec['contract']['normal_eos_pass'] and rec['contract']['long_prefill_pass']
    dec = [x['decode_tps'] for x in rec['cases']['decode512'] if x['decode_tps']]
    tt = [x['ttft_s'] for x in rec['cases']['normal_eos'] if x['ttft_s']]
    lat = [x['wall_s'] for x in rec['cases']['normal_eos']]
    pre = [x['prompt_tps'] for x in rec['cases']['long_prefill'] if x['prompt_tps']]
    agg = [x['aggregate_tps'] for x in rec['cases']['concurrency4']]
    rec['summary'] = {'p50_ttft_s': statistics.median(tt), 'median_decode_tps': statistics.median(dec), 'p95_e2e_latency_s': percentile(lat, .95),
                      'median_long_prefill_tps': statistics.median(pre), 'median_concurrency4_aggregate_tps': statistics.median(agg)}
    rec['result'] = 'PASS' if rec['contract']['all_pass'] else 'FAIL'
    OUT.write_text(json.dumps(rec, indent=2, sort_keys=True)); os.chmod(OUT, 0o600)
    print(json.dumps({'result': rec['result'], 'summary': rec['summary'], 'contract': {k: v for k, v in rec['contract'].items() if k.endswith('pass')}, 'suite_sha256': rec['suite_sha256'], 'receipt': str(OUT)}, sort_keys=True))
    if rec['result'] != 'PASS' and os.environ.get('GLM_STRICT', '1') == '1':
        sys.exit(1)


if __name__ == '__main__':
    main()
