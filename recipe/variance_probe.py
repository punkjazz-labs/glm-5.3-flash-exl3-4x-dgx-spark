#!/usr/bin/env python3
"""Explain two noisy numbers of the validation suite on an otherwise idle engine.

A. warm follow-up TTFT on a long conversation (suite: longgen followup 3-10 s). The follow-up re-sends the model's
   own previous answer as an assistant message; the engine can only hit the prefix cache up to the first token where
   the re-tokenised history differs from the sequence it generated. This probe generates with return_token_ids,
   tokenises the follow-up history with /tokenize and reports the first divergence, the prefix-cache hit delta and
   the resulting TTFT; then sends the identical follow-up a second time (fully cached floor).
B. single-stream decode rate (suite: longgen primary 32-41 tok/s). Repeated identical generations (temperature 0,
   seed 42, so the text is the same every time) with per-request draft acceptance, a 512-token decode timeline and
   GPU clock/power/memory samples from every rank while they run.

Env: URL (default http://127.0.0.1:8890/v1), MODEL, PROBE_HOSTS ("r0=spark@10.200.0.11 ..."), PROBE_SSH_OPTS,
     PROBE_LONG_TOKENS (12288), PROBE_LONG_N (2), PROBE_B_TOKENS (4096), PROBE_B_N (4), PROBE_PARTS (A,B).
Usage: python3 variance_probe.py <label>   -> ~/AI/variance-<label>-<UTC>.json
"""
import json, os, shlex, subprocess, sys, threading, time, urllib.request

URL = os.environ.get('URL', 'http://127.0.0.1:8890/v1'); BASE = URL.rsplit('/v1', 1)[0]
MODEL = os.environ.get('MODEL', 'GLM-5.3-Flash-EXL3')
NOTHINK = {'enable_thinking': False}; THINK = {'enable_thinking': True}
LONG_TOKENS = int(os.environ.get('PROBE_LONG_TOKENS', 12288)); LONG_N = int(os.environ.get('PROBE_LONG_N', 2))
B_TOKENS = int(os.environ.get('PROBE_B_TOKENS', 4096)); B_N = int(os.environ.get('PROBE_B_N', 4))
PARTS = os.environ.get('PROBE_PARTS', 'A,B').split(',')
LABEL = sys.argv[1] if len(sys.argv) > 1 else 'probe'
SALT = time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())
OUT = os.path.expanduser(f'~/AI/variance-{LABEL}-{SALT}.json')


def log(*a):
    print(time.strftime('%H:%M:%S', time.gmtime()), *a, flush=True)


def post(path, payload, timeout=900):
    b = json.dumps(payload, separators=(',', ':')).encode()
    req = urllib.request.Request(BASE + path, data=b, headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def tokenize_messages(messages, think=False):
    return post('/tokenize', {'model': MODEL, 'messages': messages, 'add_generation_prompt': True,
                              'chat_template_kwargs': THINK if think else NOTHINK})['tokens']


def detok(ids):
    try:
        return post('/detokenize', {'model': MODEL, 'tokens': ids})['prompt']
    except Exception as e:  # noqa: BLE001
        return f'<detokenize failed: {e}>'


def metrics():
    want = {'running': 'vllm:num_requests_running', 'prefix_queries': 'vllm:prefix_cache_queries_total',
            'prefix_hits': 'vllm:prefix_cache_hits_total', 'spec_accepted': 'vllm:spec_decode_num_accepted_tokens_total',
            'spec_drafts': 'vllm:spec_decode_num_draft_tokens_total', 'requests_total': 'vllm:request_success_total'}
    out = {k: None for k in want}
    txt = urllib.request.urlopen(BASE + '/metrics', timeout=20).read().decode()
    for line in txt.splitlines():
        if not line.startswith('vllm:'):
            continue
        name = line.split('{', 1)[0].split(' ', 1)[0]
        for k, n in want.items():
            if name == n:
                out[k] = (out[k] or 0) + float(line.rsplit(' ', 1)[1])
    return out


def delta(m0, m1):
    d = {}
    for k in ('prefix_queries', 'prefix_hits', 'spec_accepted', 'spec_drafts', 'requests_total'):
        d[k] = None if m0.get(k) is None or m1.get(k) is None else int(m1[k] - m0[k])
    d['accept_rate'] = round(d['spec_accepted'] / d['spec_drafts'], 3) if d.get('spec_drafts') else None
    return d


class Sampler(threading.Thread):
    """Every 10 s: SM clock, power, util, MemAvailable and SwapFree on every rank (ssh)."""
    def __init__(self):
        super().__init__(daemon=True); self.rows = []; self.stop = threading.Event()
        self.hosts = [h.split('=', 1) for h in os.environ.get('PROBE_HOSTS', '').split()]
        self.opts = shlex.split(os.environ.get('PROBE_SSH_OPTS', '-o BatchMode=yes -o ConnectTimeout=5'))

    def sample(self):
        row = {'t': round(time.time() - T0, 1)}
        script = ("nvidia-smi --query-gpu=clocks.sm,power.draw,utilization.gpu,temperature.gpu --format=csv,noheader,nounits | head -1 | tr -d ' '; "
                  "awk '/MemAvailable|SwapFree/{printf \"%d \", $2/1048576}' /proc/meminfo")
        for label, host in self.hosts:
            try:
                r = subprocess.run(['ssh', '-n', *self.opts, host, script], text=True, capture_output=True, timeout=30)
                a, b = r.stdout.strip().split('\n')[:2]
                sm, pw, ut, tp = a.split(',')[:4]; ma, sw = b.split()[:2]
                row[label] = {'sm_mhz': int(sm), 'power_w': float(pw), 'util': int(ut), 'temp_c': int(tp), 'memavail_gib': int(ma), 'swapfree_gib': int(sw)}
            except Exception as e:  # noqa: BLE001
                row[label] = {'error': str(e)[:80]}
        self.rows.append(row)

    def run(self):
        while not self.stop.is_set():
            self.sample(); self.stop.wait(10)


def stream(name, messages, max_tokens, think=False, ignore_eos=False, timeout=1500):
    """One streaming chat completion with token ids. Timeline = (elapsed, cumulative completion tokens) per chunk."""
    p = {'model': MODEL, 'messages': messages, 'max_tokens': max_tokens, 'temperature': 0, 'seed': 42, 'stream': True,
         'stream_options': {'include_usage': True}, 'chat_template_kwargs': THINK if think else NOTHINK, 'return_token_ids': True}
    if ignore_eos:
        p['ignore_eos'] = True
    m0 = metrics(); t0 = time.perf_counter(); first = None; text = []; reasoning = []; ids = []; prompt_ids = None
    usage = {}; finish = None; timeline = []; n = 0; error = None
    req = urllib.request.Request(URL + '/chat/completions', data=json.dumps(p).encode(), headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            for raw in r:
                line = raw.decode('utf-8', 'replace').strip()
                if not line.startswith('data:'):
                    continue
                data = line[5:].strip()
                if data == '[DONE]':
                    break
                j = json.loads(data); usage = j.get('usage') or usage
                if prompt_ids is None and j.get('prompt_token_ids'):
                    prompt_ids = j['prompt_token_ids']
                for c in j.get('choices') or []:
                    d = c.get('delta') or {}
                    if c.get('token_ids'):
                        ids.extend(c['token_ids'])
                    if d.get('content'):
                        text.append(d['content'])
                    if d.get('reasoning_content') or d.get('reasoning'):
                        reasoning.append(d.get('reasoning_content') or d.get('reasoning'))
                    if d.get('content') or d.get('reasoning_content') or d.get('reasoning') or c.get('token_ids'):
                        now = time.perf_counter(); n += len(c.get('token_ids') or [1])
                        if first is None:
                            first = now
                        timeline.append((round(now - t0, 3), n))
                    finish = c.get('finish_reason') or finish
    except Exception as e:  # noqa: BLE001
        error = str(e)[:200]
    t1 = time.perf_counter(); m1 = metrics()
    ct = int(usage.get('completion_tokens') or 0); pt = int(usage.get('prompt_tokens') or 0)
    dec = round((ct - 1) / (t1 - first), 2) if first and ct > 1 and t1 > first else None
    windows = []
    if timeline:
        w = 512; last_t, last_n = timeline[0]
        for t, k in timeline:
            if k - last_n >= w:
                windows.append({'to_token': k, 'tps': round((k - last_n) / (t - last_t), 1)}); last_t, last_n = t, k
    row = {'name': name, 'wall_s': round(t1 - t0, 3), 'ttft_s': round(first - t0, 3) if first else None, 'decode_tps': dec,
           'prompt_tokens': pt, 'completion_tokens': ct, 'finish_reason': finish, 'error': error, 'text': ''.join(text),
           'reasoning_chars': sum(len(x) for x in reasoning), 'prompt_token_ids': prompt_ids, 'token_ids': ids,
           'ids_in_stream': bool(ids), 'windows': windows, 'metrics_delta': delta(m0, m1), 'running_before': m0.get('running')}
    log(name, {k: row[k] for k in ('wall_s', 'ttft_s', 'decode_tps', 'prompt_tokens', 'completion_tokens', 'finish_reason', 'error', 'ids_in_stream')}, row['metrics_delta'])
    return row


def divergence(gen_prompt_ids, gen_ids, fu_ids):
    """First index where the re-tokenised follow-up history differs from what the engine generated."""
    seq = list(gen_prompt_ids or []) + list(gen_ids or [])
    n = min(len(seq), len(fu_ids)); i = 0
    while i < n and seq[i] == fu_ids[i]:
        i += 1
    where = 'prompt' if i < len(gen_prompt_ids or []) else ('generated' if i < len(seq) else 'beyond')
    return {'first_divergence': i, 'generated_seq_len': len(seq), 'followup_prompt_len': len(fu_ids), 'prompt_len': len(gen_prompt_ids or []),
            'where': where, 'generated_context': detok(seq[max(0, i - 12):i + 8]) if i < len(seq) else None,
            'followup_context': detok(fu_ids[max(0, i - 12):i + 8]) if i < len(fu_ids) else None,
            'generated_tail_tokens': seq[max(0, i - 4):i + 4], 'followup_tail_tokens': fu_ids[max(0, i - 4):i + 4]}


def followup_case(name, conv, primary, question, think=False):
    hist = conv + [{'role': 'assistant', 'content': primary['text'] or '(no answer)'}, {'role': 'user', 'content': question}]
    fu_ids = tokenize_messages(hist, think)
    div = divergence(primary['prompt_token_ids'], primary['token_ids'], fu_ids)
    f1 = stream(name + '-followup', hist, 64, think=think)
    f2 = stream(name + '-followup-again', hist, 64, think=think)
    res = {'divergence': div, 'followup': slim(f1), 'followup_again': slim(f2)}
    log(name, 'divergence', {k: div[k] for k in ('first_divergence', 'prompt_len', 'generated_seq_len', 'followup_prompt_len', 'where')},
        'hits', f1['metrics_delta']['prefix_hits'], 'ttft', f1['ttft_s'], '| again hits', f2['metrics_delta']['prefix_hits'], 'ttft', f2['ttft_s'])
    if div['where'] != 'beyond':
        log(name, 'generated:', repr(div['generated_context'])); log(name, 'followup :', repr(div['followup_context']))
    return res


def slim(r):
    return {k: v for k, v in r.items() if k not in ('text', 'prompt_token_ids', 'token_ids')} | {'text_preview': (r['text'] or '')[:120]}


NOVEL = 'Write an extremely long, continuous technical novel about a team bringing up a four-node inference cluster (working title {s}). No headings, no lists, just prose.'
ESSAYS = ['Write a 3000-word essay on the history of packet switching, continuous prose, no headings.',
          'Explain, in about 3000 words of continuous prose, how a modern garbage collector works.',
          'Write a 3000-word short story about a lighthouse keeper who repairs radios. Prose only.']
CODING = [('py_lru_ttl', 'Write a Python module implementing an LRU cache with per-entry TTL, thread safe, with a small pytest suite. Keep it self-contained.'),
          ('go_bugfix', 'This Go function is supposed to merge overlapping intervals but returns wrong results for unsorted input and drops the last interval. Fix it and add a table-driven test.\n\n```go\nfunc Merge(iv [][2]int) [][2]int {\n\tvar out [][2]int\n\tcur := iv[0]\n\tfor i := 1; i < len(iv); i++ {\n\t\tif iv[i][0] <= cur[1] {\n\t\t\tcur[1] = iv[i][1]\n\t\t} else {\n\t\t\tout = append(out, cur)\n\t\t\tcur = iv[i]\n\t\t}\n\t}\n\treturn out\n}\n```')]

T0 = time.time()
rec = {'label': LABEL, 'url': URL, 'started': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), 'long_tokens': LONG_TOKENS, 'b_tokens': B_TOKENS,
       'metrics_before': metrics(), 'A': {}, 'B': []}
sampler = Sampler(); sampler.start()
# warm-up: one short request of each shape so JIT/graph state is steady
stream('warm-nothink', [{'role': 'user', 'content': 'Reply with READY.'}], 8)
stream('warm-think', [{'role': 'user', 'content': 'What is 2+2? One word.'}], 64, think=True)

if 'A' in PARTS:
    A = rec['A']
    A['long_ignore_eos'] = []
    for i in range(LONG_N):
        conv = [{'role': 'user', 'content': NOVEL.format(s=f'{SALT}-{i}')}]
        p = stream(f'long{i}', conv, LONG_TOKENS, ignore_eos=True)
        A['long_ignore_eos'].append({'primary': slim(p), **followup_case(f'long{i}', conv, p, 'In one sentence, who was the main character?')})
        rec['B'].append({'part': 'A-long', **slim(p)})
    A['natural_nothink'] = []
    for i, q in enumerate(ESSAYS):
        conv = [{'role': 'user', 'content': q}]
        p = stream(f'essay{i}', conv, 6000)
        A['natural_nothink'].append({'primary': slim(p), **followup_case(f'essay{i}', conv, p, 'Summarise your answer in one sentence.')})
    A['thinking_on'] = []
    for name, q in CODING:
        conv = [{'role': 'user', 'content': q}]
        p = stream(f'think-{name}', conv, 3072, think=True)
        A['thinking_on'].append({'primary': slim(p), **followup_case(f'think-{name}', conv, p, 'Name the one function a reviewer should look at first.', think=True)})

if 'B' in PARTS:
    conv = [{'role': 'user', 'content': NOVEL.format(s='fixed-B')}]
    for i in range(B_N):
        p = stream(f'B{i}', conv, B_TOKENS, ignore_eos=True)
        rec['B'].append({'part': 'B', **slim(p)})
    code_conv = [{'role': 'user', 'content': 'Write a complete, very long Rust implementation of a B-tree with tests. Code only.'}]
    p = stream('B-code', code_conv, B_TOKENS, ignore_eos=True)
    rec['B'].append({'part': 'B-code', **slim(p)})

sampler.stop.set(); sampler.join(timeout=60)
rec['samples'] = sampler.rows; rec['metrics_after'] = metrics(); rec['finished'] = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
rec['B_summary'] = [{'name': b['name'], 'decode_tps': b['decode_tps'], 'accept': b['metrics_delta']['accept_rate'], 'ct': b['completion_tokens'],
                     'windows': [w['tps'] for w in b['windows']]} for b in rec['B']]
json.dump(rec, open(OUT, 'w'), indent=1)
log('B summary:'); [log('  ', s) for s in rec['B_summary']]
log('written', OUT)
