#!/usr/bin/env python3
"""GLM-5.3-Flash workload benchmark: realistic mixed loads, warm-cache follow-ups, cancellation and a soak.

Runs the same phases against any OpenAI-compatible vLLM endpoint so TP2 and TP4 receipts compare 1:1.
Phases (GLM_PHASES, comma separated; default all):
  warmup     one request of every shape used below (triggers Triton/TileLang JIT so measurements are steady state)
  sanity     exact JSON, EOS, tool call, json_schema structured output (the xgrammar + spec-decode path)
  coding     three coding requests (thinking on) + one cron-sized request (24k-token context, tools), all at once;
             each fires a warm follow-up on its own conversation as soon as it finishes, while the others still run
  longgen    one 12k-token generation (LONGGEN_TOKENS) + three short requests fired into it; warm follow-up on the long conversation
  cold       one cold 256-300k-token prompt (needle) + three short requests fired into it; warm follow-up on the same context
  conc       N simultaneous fixed-length generations for each N in CONC_LEVELS (aggregate and per-stream decode, queueing)
  cancel     client aborts a stream mid-decode and mid-prefill; checks the engine drains and stays responsive
  soak       SOAK_WORKERS (4) workers cycling SOAK_KINDS for SOAK_MIN minutes, memory/clock and engine metrics sampled every 60 s
  sanity_end the sanity block again after the soak
Env: GLM_URL (http://127.0.0.1:8890/v1), GLM_LABEL, GLM_OUT, SOAK_MIN (30), SOAK_KINDS, SOAK_WORKERS (4), SOAK_LONGGEN_TOKENS (4096), COLD_TOKENS (280000), LONGGEN_TOKENS (12288), CONC_LEVELS (4,8),
     GLM_MEM_HOSTS ("label=user@host ...", probed over SSH with GLM_SSH_OPTS; MemAvailable, SwapFree and the GPU sm clock),
     GLM_SALT (defaults to the start time). Every prompt, short or long, carries a per-request salt so no measurement
     is served from the prefix cache unless the phase says so (Tech2Wild 2026-09-02: repeat a prompt and you measure the cache).
Compare two receipts: glm_workload.py --compare a.json b.json
"""
import concurrent.futures as cf, hashlib, json, math, os, random, shlex, statistics, subprocess, sys, threading, time, urllib.request, urllib.error
from pathlib import Path

URL = os.environ.get('GLM_URL', 'http://127.0.0.1:8890/v1')
MODEL = 'GLM-5.3-Flash-EXL3'
LABEL = os.environ.get('GLM_LABEL', 'run')
OUT = Path(os.environ.get('GLM_OUT', f'glm-workload-{LABEL}.json'))
SOAK_MIN = float(os.environ.get('SOAK_MIN', '30'))
COLD_TOKENS = int(os.environ.get('COLD_TOKENS', '280000'))
LONGGEN_TOKENS = int(os.environ.get('LONGGEN_TOKENS', '12288'))
CONC_LEVELS = [int(x) for x in os.environ.get('CONC_LEVELS', '4,8').split(',') if x]
SALT = os.environ.get('GLM_SALT') or time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())
PHASES = os.environ.get('GLM_PHASES', 'warmup,sanity,coding,longgen,cold,cancel,soak,sanity_end').split(',')
NOTHINK = {'enable_thinking': False}
THINK = {'enable_thinking': True}
LOG_LOCK = threading.Lock()
OWN_REQUESTS = [0]  # requests this process sent; compared with the engine's success counter to flag foreign traffic
T_START = time.time()


def log(*a):
    with LOG_LOCK:
        print(time.strftime('%H:%M:%S', time.gmtime()), *a, flush=True)


def post(path, payload, timeout=600):
    OWN_REQUESTS[0] += 1
    b = json.dumps(payload, separators=(',', ':')).encode()
    req = urllib.request.Request(URL + path, data=b, headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def tokenize(text):
    b = json.dumps({'model': MODEL, 'prompt': text}, separators=(',', ':')).encode()
    req = urllib.request.Request(URL.rsplit('/v1', 1)[0] + '/tokenize', data=b, headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=900) as r:
        return int(json.loads(r.read())['count'])


def metrics():
    """A few engine gauges from /metrics (None when the gauge is absent)."""
    want = {'running': 'vllm:num_requests_running', 'waiting': 'vllm:num_requests_waiting',
            'kv_usage': ('vllm:kv_cache_usage_perc', 'vllm:gpu_cache_usage_perc'),
            'prefix_queries': 'vllm:prefix_cache_queries_total', 'prefix_hits': 'vllm:prefix_cache_hits_total',
            'spec_accepted': 'vllm:spec_decode_num_accepted_tokens_total', 'spec_drafts': 'vllm:spec_decode_num_draft_tokens_total',
            'requests_total': 'vllm:request_success_total'}
    out = {k: None for k in want}
    try:
        txt = urllib.request.urlopen(URL.rsplit('/v1', 1)[0] + '/metrics', timeout=20).read().decode()
    except Exception as e:  # noqa: BLE001
        return {'error': str(e)[:120]}
    for line in txt.splitlines():
        if not line.startswith('vllm:'):
            continue
        name = line.split('{', 1)[0].split(' ', 1)[0]
        for k, names in want.items():
            if name in ((names,) if isinstance(names, str) else names):
                try:
                    out[k] = (out[k] or 0) + float(line.rsplit(' ', 1)[1])
                except ValueError:
                    pass
    return out


def mem():
    out = {}
    opts = shlex.split(os.environ.get('GLM_SSH_OPTS', '-o BatchMode=yes'))
    for item in os.environ.get('GLM_MEM_HOSTS', '').split():
        label, host = item.split('=', 1)
        script = ("awk '/MemAvailable|SwapFree/{print $1,$2}' /proc/meminfo; nvidia-smi --query-gpu=clocks.sm,utilization.gpu "
                  "--format=csv,noheader,nounits 2>/dev/null | head -1 | tr -d ' ' | awk -F, '{print \"clock_mhz:\",$1; print \"gpu_util:\",$2}'")
        try:
            r = subprocess.run(['ssh', '-n', *opts, host, script], text=True, capture_output=True, timeout=60)
            d = dict(l.split() for l in r.stdout.strip().splitlines()) if r.returncode == 0 else {}
            out[label] = {'MemAvailable_GiB': round(int(d['MemAvailable:']) / 2**20, 2), 'SwapFree_GiB': round(int(d['SwapFree:']) / 2**20, 2),
                          'clock_mhz': int(d['clock_mhz:']) if d.get('clock_mhz:', '').isdigit() else None,
                          'gpu_util': int(d['gpu_util:']) if d.get('gpu_util:', '').isdigit() else None} if d else {'error': r.stderr.strip()[:200]}
        except Exception as e:  # noqa: BLE001
            out[label] = {'error': str(e)[:200]}
    return out


class Cancelled(Exception):
    pass


def stream(name, messages, max_tokens, think=False, tools=None, ignore_eos=False, cancel_after_tokens=None, cancel_after_s=None, timeout=1500, extra=None):
    """One streaming chat completion. Returns timing + usage; on client-side cancel returns what was seen so far."""
    OWN_REQUESTS[0] += 1
    p = {'model': MODEL, 'messages': messages, 'max_tokens': max_tokens, 'temperature': 0, 'seed': 42, 'stream': True,
         'stream_options': {'include_usage': True}, 'chat_template_kwargs': THINK if think else NOTHINK}
    if think:
        p['reasoning_effort'] = 'medium'
    if tools:
        p['tools'] = tools
    if ignore_eos:
        p['ignore_eos'] = True
    if extra:
        p.update(extra)
    t0 = time.perf_counter(); first = None; finish = None; usage = {}; n_chunks = 0; text = []; reasoning_chars = 0; tool_calls = 0
    err = None; cancelled = False
    req = urllib.request.Request(URL + '/chat/completions', data=json.dumps(p, separators=(',', ':')).encode(), headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            for raw in r:
                line = raw.decode('utf-8', 'replace').strip()
                if not line.startswith('data: '):
                    continue
                data = line[6:]
                if data == '[DONE]':
                    break
                j = json.loads(data); usage = j.get('usage') or usage
                for c in j.get('choices', []):
                    d = c.get('delta') or {}
                    if d.get('content') or d.get('reasoning_content') or d.get('reasoning') or d.get('tool_calls'):
                        if first is None:
                            first = time.perf_counter()
                        n_chunks += 1
                    text.append(d.get('content') or '')
                    reasoning_chars += len(d.get('reasoning_content') or d.get('reasoning') or '')
                    tool_calls += len(d.get('tool_calls') or [])
                    finish = c.get('finish_reason') or finish
                now = time.perf_counter()
                if (cancel_after_tokens and n_chunks >= cancel_after_tokens) or (cancel_after_s and now - t0 >= cancel_after_s):
                    cancelled = True
                    r.close()
                    break
    except urllib.error.HTTPError as e:
        err = f'HTTP {e.code}: {e.read()[:300].decode("utf-8", "replace")}'
    except Exception as e:  # noqa: BLE001
        err = f'{type(e).__name__}: {str(e)[:200]}'
    t1 = time.perf_counter(); txt = ''.join(text)
    ct = int(usage.get('completion_tokens') or 0); pt = int(usage.get('prompt_tokens') or 0)
    cached = (usage.get('prompt_tokens_details') or {}).get('cached_tokens')
    return {'name': name, 'wall_s': round(t1 - t0, 3), 'ttft_s': None if first is None else round(first - t0, 3),
            'decode_tps': None if first is None or t1 <= first or ct < 2 else round((ct - 1) / (t1 - first), 2),
            'prefill_tps': None if first is None or first <= t0 or pt < 1 else round((pt - (cached or 0)) / (first - t0), 1),
            'prompt_tokens': pt, 'cached_tokens': cached, 'completion_tokens': ct, 'finish_reason': finish, 'chunks': n_chunks,
            'reasoning_chars': reasoning_chars, 'tool_calls': tool_calls, 'cancelled': cancelled, 'error': err,
            'text_chars': len(txt), 'text_sha256': hashlib.sha256(txt.encode()).hexdigest()[:16], 'text_preview': txt[:120], 'text': txt}


def summarize(rows, keys=('wall_s', 'ttft_s', 'decode_tps', 'prefill_tps')):
    ok = [r for r in rows if not r.get('error')]
    s = {'n': len(rows), 'errors': sum(1 for r in rows if r.get('error')), 'completion_tokens': sum(r['completion_tokens'] for r in ok)}
    for k in keys:
        xs = [r[k] for r in ok if r.get(k) is not None]
        if xs:
            s[k] = {'p50': round(statistics.median(xs), 3), 'p95': round(percentile(xs, 0.95), 3), 'min': round(min(xs), 3), 'max': round(max(xs), 3)}
    return s


def percentile(xs, p):
    s = sorted(xs); i = (len(s) - 1) * p; lo = math.floor(i); hi = math.ceil(i)
    return s[lo] if lo == hi else s[lo] * (hi - i) + s[hi] * (i - lo)


def strip(r):
    r = dict(r); r.pop('text', None); return r


# ---------------------------------------------------------------- prompts
WORDS = ('ledger scheduler fabric tensor quorum replica gateway packet buffer kernel lattice vector matrix cache token '
         'stream socket thread mutex queue batch shard index cursor schema record field column table cluster node rank '
         'link switch cable port frame header payload checksum window latency throughput budget quota policy audit').split()


def prose(n_words, seed):
    rng = random.Random(seed); out = []
    for i in range(n_words):
        out.append(rng.choice(WORDS))
        if i % 17 == 16:
            out[-1] += '.'
        if i % 120 == 119:
            out.append(f'\n\nSection {i // 120 + 1}:')
    return ' '.join(out)


def sized_prompt(target_tokens, seed, needle):
    """Synthetic text of about target_tokens tokens with the needle sentence at ~40 % depth (calibrated via /tokenize)."""
    probe = prose(2000, seed)
    per_word = tokenize(probe) / 2000
    words = int(target_tokens / per_word)
    body = prose(words, seed).split('\n\n')
    body.insert(max(1, int(len(body) * 0.4)), f'Note for the reader: {needle}')
    return '\n\n'.join(body)


SHORT_PROMPTS = ['What is the capital of France? Answer in one word.', 'Give one sentence on why TCP uses sequence numbers.',
                 'Name three prime numbers below 20, comma separated.', 'Explain in one sentence what a mutex is.',
                 'Reply with the word READY.', 'What is 17 times 23? Just the number.', 'One sentence: what does RoCE stand for?',
                 'List two Unix signals, comma separated.', 'Translate "good morning" to Italian, just the phrase.',
                 'In one sentence, what is tensor parallelism?']

CODING = [
    ('py_lru_ttl', 'Write a Python module implementing an LRU cache with per-entry TTL, thread safe, with a small pytest suite. Keep it self-contained.'),
    ('bash_review', 'Review this bash script for bugs and portability problems and return a corrected version with a short list of the changes:\n\n```bash\n#!/bin/bash\nset -uo pipefail\nfor f in $(ls *.log); do\n  n=`grep -c ERROR $f`\n  if [ $n > 0 ]; then echo "$f: $n errors"; fi\n  tail -n 5 $f | while read l; do echo "  $l"; done\ndone\nfind . -name "*.tmp" -exec rm {} \\;\n```'),
    ('go_bugfix', 'This Go function is supposed to merge overlapping intervals but returns wrong results for unsorted input and drops the last interval. Fix it and add a table-driven test.\n\n```go\nfunc Merge(iv [][2]int) [][2]int {\n\tvar out [][2]int\n\tcur := iv[0]\n\tfor i := 1; i < len(iv); i++ {\n\t\tif iv[i][0] <= cur[1] {\n\t\t\tcur[1] = iv[i][1]\n\t\t} else {\n\t\t\tout = append(out, cur)\n\t\t\tcur = iv[i]\n\t\t}\n\t}\n\treturn out\n}\n```'),
]

def short_q(i, tag=''):
    """A short prompt with a per-request salt (never a prefix-cache hit)."""
    return f'{SHORT_PROMPTS[i % len(SHORT_PROMPTS)]} (ref {SALT}-{tag}{i})'


def coding_p(i, tag=''):
    n, p = CODING[i % len(CODING)]
    return n, f'Ticket {SALT}-{tag}{i}.\n\n{p}'


CRON_TOOLS = [{'type': 'function', 'function': {'name': n, 'description': d, 'parameters': {'type': 'object', 'properties': {'query': {'type': 'string'}, 'limit': {'type': 'integer'}}, 'required': ['query']}}}
              for n, d in [('search_notes', 'Search the knowledge base'), ('read_file', 'Read a file'), ('list_dir', 'List a directory'), ('run_check', 'Run a health check'),
                           ('send_digest', 'Send the daily digest'), ('git_log', 'Show recent commits'), ('backup_status', 'Backup job status'), ('fetch_url', 'Fetch a URL')]]

SANITY_SCHEMA = {'type': 'object', 'properties': {'host': {'type': 'string'}, 'healthy': {'type': 'boolean'}, 'ranks': {'type': 'integer'}}, 'required': ['host', 'healthy', 'ranks'], 'additionalProperties': False}
MULTIPLY = [{'type': 'function', 'function': {'name': 'multiply', 'description': 'Multiply two integers', 'parameters': {'type': 'object', 'properties': {'a': {'type': 'integer'}, 'b': {'type': 'integer'}}, 'required': ['a', 'b'], 'additionalProperties': False}}}]


def cron_messages(seed):
    ctx = sized_prompt(24000, seed, 'the backup job BK-77 finished at 03:12 with 4 archives listed.')
    return [{'role': 'system', 'content': 'You are the nightly operations assistant. Use the tools when data is needed. Produce a concise digest.'},
            {'role': 'user', 'content': f'Operations journal for the last 24 hours follows.\n\n{ctx}\n\nWrite the daily digest: 5 bullet points, then state at what time backup job BK-77 finished and how many archives it listed.'}]


def followup(conv, reply, question, **kw):
    return conv + [{'role': 'assistant', 'content': reply or '(no answer)'}, {'role': 'user', 'content': question}]


# ---------------------------------------------------------------- phases
class ShortWorkers:
    """N workers issuing short requests back to back until stopped."""
    def __init__(self, n, tag):
        self.n, self.tag, self.rows, self.stop = n, tag, [], threading.Event()
        self.threads = [threading.Thread(target=self.run, args=(i,), daemon=True) for i in range(n)]

    def run(self, i):
        k = 0
        while not self.stop.is_set():
            q = short_q(i * 7 + k, f'{self.tag}w{i}k')
            r = stream(f'{self.tag}-short-w{i}-{k}', [{'role': 'user', 'content': q}], 64, timeout=600)
            r['t_end'] = round(time.time() - T_START, 1)
            self.rows.append(strip(r)); k += 1
            time.sleep(0.3)

    def __enter__(self):
        for t in self.threads:
            t.start()
        return self

    def __exit__(self, *a):
        self.stop.set()
        for t in self.threads:
            t.join(timeout=700)


def with_prefix_delta(fn):
    """Run fn(); attach the prefix-cache hit/query token deltas seen on the engine (usage has no cached_tokens here)."""
    m0 = metrics(); r = fn(); m1 = metrics()
    for k in ('prefix_hits', 'prefix_queries'):
        r[k + '_delta'] = None if m0.get(k) is None or m1.get(k) is None else int(m1[k] - m0[k])
    return r


def phase_warmup():
    """Exercise every shape once (JIT compilation happens on first use of a shape/config)."""
    rows = []
    with ShortWorkers(2, 'warmup') as sw:
        rows.append(stream('warm-think', [{'role': 'user', 'content': coding_p(0, 'warm')[1]}], 256, think=True))
        rows.append(stream('warm-tools', cron_messages(SALT + 'warm')[:1] + [{'role': 'user', 'content': 'List the tools you have, one line.'}], 64, tools=CRON_TOOLS))
        rows.append(stream('warm-gen', [{'role': 'user', 'content': 'Write a long essay about switches.'}], 512, ignore_eos=True))
        body = sized_prompt(12000, SALT + 'warm', 'the code is WARM-1')
        rows.append(stream('warm-12k', [{'role': 'user', 'content': body + '\n\nWhat is the code? Reply with only it.'}], 16))
        rows.append(with_prefix_delta(lambda: stream('warm-12k-again', [{'role': 'user', 'content': body + '\n\nReply with only the code again.'}], 16)))
    for r in rows:
        log('warmup', r['name'], r['wall_s'], 'ttft', r['ttft_s'], 'err', r['error'])
    return {'requests': [strip(r) for r in rows], 'shorts': summarize(sw.rows, ('wall_s', 'ttft_s'))}


def phase_sanity(tag):
    out = {}
    j = post('/chat/completions', {'model': MODEL, 'messages': [{'role': 'user', 'content': 'Return exactly this JSON object and nothing else: {"status":"ok","value":7}'}], 'max_tokens': 64, 'temperature': 0, 'seed': 42, 'chat_template_kwargs': NOTHINK}, 300)
    txt = j['choices'][0]['message']['content'].strip()
    try:
        out['exact_json'] = json.loads(txt) == {'status': 'ok', 'value': 7}
    except ValueError:
        out['exact_json'] = False
    j = post('/chat/completions', {'model': MODEL, 'messages': [{'role': 'user', 'content': 'Reply with exactly the single word PINEAPPLE.'}], 'max_tokens': 64, 'temperature': 0, 'seed': 42, 'chat_template_kwargs': NOTHINK}, 300)
    out['normal_eos'] = j['choices'][0]['message']['content'].strip().strip('.') == 'PINEAPPLE' and j['choices'][0]['finish_reason'] == 'stop'
    j = post('/chat/completions', {'model': MODEL, 'messages': [{'role': 'user', 'content': 'Use the multiply tool to multiply 6 by 7.'}], 'tools': MULTIPLY, 'tool_choice': 'required', 'temperature': 0, 'seed': 42, 'max_tokens': 128, 'chat_template_kwargs': NOTHINK}, 300)
    tc = (j['choices'][0]['message'].get('tool_calls') or [{}])[0].get('function') or {}
    out['tool_call'] = tc.get('name') == 'multiply' and json.loads(tc.get('arguments') or '{}') == {'a': 6, 'b': 7}
    try:
        j = post('/chat/completions', {'model': MODEL, 'messages': [{'role': 'user', 'content': 'Report: host rank0 is healthy and runs 4 ranks. Answer as JSON.'}],
                                       'response_format': {'type': 'json_schema', 'json_schema': {'name': 'status', 'schema': SANITY_SCHEMA, 'strict': True}},
                                       'temperature': 0, 'seed': 42, 'max_tokens': 128, 'chat_template_kwargs': NOTHINK}, 300)
        d = json.loads(j['choices'][0]['message']['content'])
        out['json_schema'] = d.get('host') == 'rank0' and d.get('healthy') is True and d.get('ranks') == 4
        out['json_schema_text'] = j['choices'][0]['message']['content'][:120]
    except Exception as e:  # noqa: BLE001
        out['json_schema'] = False; out['json_schema_error'] = str(e)[:300]
    out['all_pass'] = all(out[k] for k in ('exact_json', 'normal_eos', 'tool_call', 'json_schema'))
    log(tag, out)
    return out


def phase_coding():
    jobs = [(n, [{'role': 'user', 'content': p}], 3072, True, None) for n, p in (coding_p(i, 'coding') for i in range(len(CODING)))]
    jobs.append(('cron_digest', cron_messages(SALT + 'cron'), 1024, False, CRON_TOOLS))
    results = {}; followups = {}

    def one(name, conv, max_tokens, think, tools):
        r = stream(name, conv, max_tokens, think=think, tools=tools)
        results[name] = strip(r)
        log('coding', name, 'done', {k: r[k] for k in ('wall_s', 'ttft_s', 'decode_tps', 'prompt_tokens', 'completion_tokens', 'finish_reason', 'error')})
        fq = 'Good. Now answer in one sentence: what is the single riskiest part of that answer?' if name != 'cron_digest' else 'Restate only the BK-77 finish time and archive count, nothing else.'
        f = with_prefix_delta(lambda: stream(name + '-followup', followup(conv, r['text'], fq), 96, think=False, tools=tools))
        followups[name] = strip(f)
        log('coding', name, 'followup', {k: f[k] for k in ('wall_s', 'ttft_s', 'prompt_tokens', 'prefix_hits_delta', 'completion_tokens', 'error')})

    t0 = time.perf_counter()
    with cf.ThreadPoolExecutor(4) as ex:
        list(ex.map(lambda j: one(*j), jobs))
    return {'wall_s': round(time.perf_counter() - t0, 1), 'requests': results, 'followups': followups,
            'summary': {'aggregate_completion_tps': round(sum(r['completion_tokens'] for r in results.values()) / (time.perf_counter() - t0), 2),
                        'followup_ttft_p50': statistics.median([f['ttft_s'] for f in followups.values() if f['ttft_s']]) if followups else None,
                        'followup_prefix_hits': {k: f['prefix_hits_delta'] for k, f in followups.items()}}}


def phase_with_shorts(tag, conv, max_tokens, ignore_eos, follow_q, timeout=1500):
    """One primary request plus three short requests fired 5/20/40 s into it; then a warm follow-up on the
    primary's conversation with three more shorts fired 1/5/10 s into the follow-up."""
    shorts = []

    def short_at(delay, i, phase_tag):
        time.sleep(delay)
        r = stream(f'{tag}-short-{phase_tag}-{i}', [{'role': 'user', 'content': short_q(i, f'{tag}{phase_tag}')}], 64, timeout=timeout)
        r['fired_at_s'] = delay; r['phase'] = phase_tag; shorts.append(strip(r))
        log(tag, 'short', phase_tag, i, {k: r[k] for k in ('wall_s', 'ttft_s', 'completion_tokens', 'error')})

    with cf.ThreadPoolExecutor(4) as ex:
        futs = [ex.submit(short_at, d, i, 'primary') for i, d in enumerate((5, 20, 40))]
        r = stream(tag, conv, max_tokens, ignore_eos=ignore_eos, timeout=timeout)
        log(tag, 'primary', {k: r[k] for k in ('wall_s', 'ttft_s', 'decode_tps', 'prefill_tps', 'prompt_tokens', 'completion_tokens', 'finish_reason', 'error')}, 'preview:', r['text_preview'][:60])
        for f_ in futs:
            f_.result()
    with cf.ThreadPoolExecutor(4) as ex:
        futs = [ex.submit(short_at, d, i + 3, 'followup') for i, d in enumerate((1, 5, 10))]
        f = with_prefix_delta(lambda: stream(tag + '-followup', followup(conv, r['text'], follow_q), 64, timeout=timeout))
        log(tag, 'followup', {k: f[k] for k in ('wall_s', 'ttft_s', 'prompt_tokens', 'prefix_hits_delta', 'completion_tokens', 'error')}, 'preview:', f['text_preview'][:60])
        for f_ in futs:
            f_.result()
    during = [x for x in shorts if x['phase'] == 'primary']
    return {'primary': strip(r), 'followup': strip(f), 'shorts_during_primary': summarize(during, ('wall_s', 'ttft_s')),
            'shorts_during_followup': summarize([x for x in shorts if x['phase'] == 'followup'], ('wall_s', 'ttft_s')), 'shorts': shorts}


def phase_longgen():
    conv = [{'role': 'user', 'content': f'Write an extremely long, continuous technical novel about a team bringing up a four-node inference cluster (working title {SALT}). No headings, no lists, just prose.'}]
    return phase_with_shorts('longgen', conv, LONGGEN_TOKENS, True, 'In one sentence, who was the main character?')


def phase_conc():
    """For each N in CONC_LEVELS: N simultaneous 512-token generations (short distinct prompts, ignore_eos so every
    stream produces the same amount of work). Aggregate tok/s, per-stream decode and first-token queueing at that level."""
    out = {}
    for n in CONC_LEVELS:
        def one(i):
            name, p = coding_p(i, f'conc{n}-')
            return stream(f'conc{n}-{i}', [{'role': 'user', 'content': f'Request {i}. {p}'}], 512, ignore_eos=True, timeout=900)
        t0 = time.perf_counter()
        with cf.ThreadPoolExecutor(n) as ex:
            rows = list(ex.map(one, range(n)))
        wall = time.perf_counter() - t0
        s = summarize(rows, ('wall_s', 'ttft_s', 'decode_tps'))
        s['wall_s_total'] = round(wall, 1)
        s['aggregate_completion_tps'] = round(sum(r['completion_tokens'] for r in rows if not r['error']) / wall, 2)
        out[str(n)] = s; out[str(n)]['rows'] = [strip(r) for r in rows]
        log('conc', n, 'aggregate tps', s['aggregate_completion_tps'], 'decode p50', s.get('decode_tps', {}).get('p50'), 'ttft p50/max', s.get('ttft_s', {}).get('p50'), s.get('ttft_s', {}).get('max'), 'errors', s['errors'])
    return out


def phase_cold():
    needle = f'the secret code word is ORCHID-{SALT[-4:]}'
    body = sized_prompt(COLD_TOKENS, SALT + 'cold', needle)
    conv = [{'role': 'user', 'content': body + '\n\nWhat is the secret code word? Reply with only the code word.'}]
    out = phase_with_shorts('cold', conv, 32, False, 'Now reply with only the number of the section that contains the code word, or "unknown".', timeout=2400)
    out['needle'] = needle.split()[-1]; out['needle_found'] = out['needle'].lower() in out['primary']['text_preview'].lower()
    log('cold needle', out['needle'], 'found' if out['needle_found'] else 'NOT FOUND')
    return out


def drain(limit_s=30):
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < limit_s:
        m = metrics()
        if m.get('running') == 0 and m.get('waiting') == 0:
            return round(time.perf_counter() - t0, 2), m
        time.sleep(0.5)
    return None, metrics()


def phase_cancel():
    out = {}
    # 1. abort mid-decode after ~100 streamed tokens
    r = stream('cancel-decode', [{'role': 'user', 'content': 'Write an extremely long essay about network fabrics.'}], 8192, ignore_eos=True, cancel_after_tokens=100)
    d, m = drain()
    s = stream('after-cancel-decode', [{'role': 'user', 'content': short_q(0, 'cancel')}], 32)
    out['mid_decode'] = {'aborted': strip(r), 'drain_s': d, 'metrics_after': m, 'next_short': strip(s)}
    log('cancel mid-decode', 'drain_s', d, 'next short ttft', s['ttft_s'], 'err', s['error'])
    # 2. abort during a ~100k-token cold prefill after 3 s
    body = sized_prompt(100000, SALT + 'cancel', 'nothing to see here')
    r = stream('cancel-prefill', [{'role': 'user', 'content': body + '\n\nSummarize in one sentence.'}], 64, cancel_after_s=3)
    d, m = drain(120)
    s = stream('after-cancel-prefill', [{'role': 'user', 'content': short_q(1, 'cancel')}], 32)
    out['mid_prefill'] = {'aborted': strip(r), 'drain_s': d, 'metrics_after': m, 'next_short': strip(s)}
    log('cancel mid-prefill', 'drain_s', d, 'next short ttft', s['ttft_s'], 'err', s['error'])
    out['pass'] = all(x['drain_s'] is not None and not x['next_short']['error'] for x in (out['mid_decode'], out['mid_prefill']))
    return out


# Soak rotation. Default mix = the batch-1..3 mix (keep it for --compare); SOAK_KINDS=... overrides, e.g. the varied-length mix
# 'short,coding,medium_gen,long_prompt,short,long_gen,long_prompt_96k,short' used for the 150-min soak. SOAK_WORKERS (4) sets concurrency.
SOAK_KINDS = [k for k in (os.environ.get('SOAK_KINDS') or 'short,coding,medium_gen,long_prompt,short').split(',') if k]
SOAK_WORKERS = int(os.environ.get('SOAK_WORKERS', '4'))
SOAK_LONGGEN_TOKENS = int(os.environ.get('SOAK_LONGGEN_TOKENS', '4096'))


def soak_job(kind, i, k):
    seed = f'{SALT}-soak-{i}-{k}'
    if kind == 'short':
        return stream(f'soak-{kind}-{i}-{k}', [{'role': 'user', 'content': short_q(i + k, f'soak{i}-{k}-')}], 64, timeout=900)
    if kind == 'coding':
        n, p = coding_p(i + k, f'soak{i}-{k}-')
        return stream(f'soak-{kind}-{i}-{k}', [{'role': 'user', 'content': p}], 1536, think=True, timeout=900)
    if kind == 'medium_gen':
        return stream(f'soak-{kind}-{i}-{k}', [{'role': 'user', 'content': 'Explain, at length and without lists, how RDMA differs from TCP.'}], 1024, ignore_eos=True, timeout=900)
    if kind == 'long_gen':  # sustained decode alongside the interactive traffic (thinking off, fixed length)
        return stream(f'soak-{kind}-{i}-{k}', [{'role': 'user', 'content': f'Write a very long continuous technical story about a cluster bring-up (working title {seed}). No headings, no lists.'}], SOAK_LONGGEN_TOKENS, ignore_eos=True, timeout=1500)
    size = 96000 if kind == 'long_prompt_96k' else 24000  # 96k exercises the fat-expert prefill kernel under concurrency
    body = sized_prompt(size, seed, f'the reference number is RX-{k}{i}7')
    return stream(f'soak-{kind}-{i}-{k}', [{'role': 'user', 'content': body + '\n\nWhat is the reference number? Reply with only it.'}], 32, timeout=1500)


def phase_soak():
    rows = []; samples = []; stop = threading.Event(); t0 = time.time()

    def worker(i):
        k = 0
        while not stop.is_set():
            kind = SOAK_KINDS[(k + i) % len(SOAK_KINDS)]
            r = soak_job(kind, i, k); r['kind'] = kind; r['worker'] = i; r['t_end'] = round(time.time() - t0, 1)
            rows.append(strip(r)); k += 1
            if r['error']:
                log('soak ERROR', r['name'], r['error'][:160])

    def sampler():
        while not stop.is_set():
            samples.append({'t': round(time.time() - t0, 1), 'metrics': metrics(), 'mem': mem(), 'requests_done': len(rows), 'errors': sum(1 for r in rows if r['error'])})
            log('soak sample', samples[-1]['t'], samples[-1]['metrics'], samples[-1]['mem'])
            stop.wait(60)

    ths = [threading.Thread(target=worker, args=(i,), daemon=True) for i in range(SOAK_WORKERS)] + [threading.Thread(target=sampler, daemon=True)]
    for t in ths:
        t.start()
    stop.wait(SOAK_MIN * 60); stop.set()
    for t in ths:
        t.join(timeout=1500)
    dur = time.time() - t0
    by_kind = {k: summarize([r for r in rows if r['kind'] == k], ('wall_s', 'ttft_s', 'decode_tps', 'prefill_tps')) for k in set(SOAK_KINDS)}
    half = dur / 2
    drift = {k: {'first_half_p50_wall': statistics.median([r['wall_s'] for r in rows if r['kind'] == k and r['t_end'] <= half and not r['error']] or [0]),
                 'second_half_p50_wall': statistics.median([r['wall_s'] for r in rows if r['kind'] == k and r['t_end'] > half and not r['error']] or [0])} for k in set(SOAK_KINDS)}
    return {'duration_s': round(dur, 1), 'requests': len(rows), 'errors': sum(1 for r in rows if r['error']),
            'error_samples': [r['error'] for r in rows if r['error']][:10],
            'aggregate_completion_tps': round(sum(r['completion_tokens'] for r in rows if not r['error']) / dur, 2),
            'aggregate_prompt_tps': round(sum(r['prompt_tokens'] for r in rows if not r['error']) / dur, 1),
            'by_kind': by_kind, 'drift': drift, 'long_prompt_needle_ok': sum(1 for r in rows if r['kind'].startswith('long_prompt') and 'RX-' in r['text_preview']),
            'long_prompt_total': sum(1 for r in rows if r['kind'].startswith('long_prompt') and not r['error']), 'kinds': SOAK_KINDS, 'workers': SOAK_WORKERS,
            'samples': samples, 'rows': rows}


# ---------------------------------------------------------------- driver
def compare(a, b):
    A, B = json.load(open(a)), json.load(open(b))
    la, lb = A['label'], B['label']

    def g(d, *path):
        for p in path:
            d = d.get(p) if isinstance(d, dict) else None
            if d is None:
                return None
        return d
    rows = [
        ('sanity all_pass', g(A, 'sanity', 'all_pass'), g(B, 'sanity', 'all_pass')),
        ('sanity json_schema', g(A, 'sanity', 'json_schema'), g(B, 'sanity', 'json_schema')),
        ('coding: 4-way wall s', g(A, 'coding', 'wall_s'), g(B, 'coding', 'wall_s')),
        ('coding: aggregate tok/s', g(A, 'coding', 'summary', 'aggregate_completion_tps'), g(B, 'coding', 'summary', 'aggregate_completion_tps')),
        ('coding: warm follow-up TTFT p50 s', g(A, 'coding', 'summary', 'followup_ttft_p50'), g(B, 'coding', 'summary', 'followup_ttft_p50')),
        ('cron 24k: TTFT s', g(A, 'coding', 'requests', 'cron_digest', 'ttft_s'), g(B, 'coding', 'requests', 'cron_digest', 'ttft_s')),
        ('longgen 12k: decode tok/s', g(A, 'longgen', 'primary', 'decode_tps'), g(B, 'longgen', 'primary', 'decode_tps')),
        ('longgen 12k: wall s', g(A, 'longgen', 'primary', 'wall_s'), g(B, 'longgen', 'primary', 'wall_s')),
        ('longgen: shorts p50 / p95 s', _pp(g(A, 'longgen', 'shorts_during_primary', 'wall_s')), _pp(g(B, 'longgen', 'shorts_during_primary', 'wall_s'))),
        ('longgen: warm follow-up TTFT s', g(A, 'longgen', 'followup', 'ttft_s'), g(B, 'longgen', 'followup', 'ttft_s')),
        ('cold prompt tokens', g(A, 'cold', 'primary', 'prompt_tokens'), g(B, 'cold', 'primary', 'prompt_tokens')),
        ('cold: TTFT (prefill) s', g(A, 'cold', 'primary', 'ttft_s'), g(B, 'cold', 'primary', 'ttft_s')),
        ('cold: prefill tok/s', g(A, 'cold', 'primary', 'prefill_tps'), g(B, 'cold', 'primary', 'prefill_tps')),
        ('cold: needle found', g(A, 'cold', 'needle_found'), g(B, 'cold', 'needle_found')),
        ('cold: shorts p50 / p95 s', _pp(g(A, 'cold', 'shorts_during_primary', 'wall_s')), _pp(g(B, 'cold', 'shorts_during_primary', 'wall_s'))),
        ('cold: warm follow-up TTFT s', g(A, 'cold', 'followup', 'ttft_s'), g(B, 'cold', 'followup', 'ttft_s')),
        ('cold: follow-up prefix-hit tokens', g(A, 'cold', 'followup', 'prefix_hits_delta'), g(B, 'cold', 'followup', 'prefix_hits_delta')),
        ('warmup: 12k prompt TTFT cold -> warm s', _warm(A), _warm(B)),
        ('cancel: pass', g(A, 'cancel', 'pass'), g(B, 'cancel', 'pass')),
        ('cancel: drain s (decode / prefill)', f"{g(A, 'cancel', 'mid_decode', 'drain_s')} / {g(A, 'cancel', 'mid_prefill', 'drain_s')}", f"{g(B, 'cancel', 'mid_decode', 'drain_s')} / {g(B, 'cancel', 'mid_prefill', 'drain_s')}"),
        ('soak: minutes', _r(g(A, 'soak', 'duration_s'), 60), _r(g(B, 'soak', 'duration_s'), 60)),
        ('soak: requests / errors', f"{g(A, 'soak', 'requests')} / {g(A, 'soak', 'errors')}", f"{g(B, 'soak', 'requests')} / {g(B, 'soak', 'errors')}"),
        ('soak: aggregate gen tok/s', g(A, 'soak', 'aggregate_completion_tps'), g(B, 'soak', 'aggregate_completion_tps')),
        ('soak: aggregate prompt tok/s', g(A, 'soak', 'aggregate_prompt_tps'), g(B, 'soak', 'aggregate_prompt_tps')),
        ('soak: short p50 / p95 s', _pp(g(A, 'soak', 'by_kind', 'short', 'wall_s')), _pp(g(B, 'soak', 'by_kind', 'short', 'wall_s'))),
        ('soak: coding p50 wall s', g(A, 'soak', 'by_kind', 'coding', 'wall_s', 'p50'), g(B, 'soak', 'by_kind', 'coding', 'wall_s', 'p50')),
        ('soak: 24k prompt p50 wall s', g(A, 'soak', 'by_kind', 'long_prompt', 'wall_s', 'p50'), g(B, 'soak', 'by_kind', 'long_prompt', 'wall_s', 'p50')),
        ('soak: medium_gen decode p50 tok/s', g(A, 'soak', 'by_kind', 'medium_gen', 'decode_tps', 'p50'), g(B, 'soak', 'by_kind', 'medium_gen', 'decode_tps', 'p50')),
        ('soak: min MemAvailable GiB', _minmem(A), _minmem(B)),
        ('sanity_end all_pass', g(A, 'sanity_end', 'all_pass'), g(B, 'sanity_end', 'all_pass')),
    ]
    w = max(len(r[0]) for r in rows)
    print(f"{'metric'.ljust(w)} | {la:>16} | {lb:>16}")
    print('-' * (w + 40))
    for name, x, y in rows:
        print(f'{name.ljust(w)} | {str(x):>16} | {str(y):>16}')


def _pp(d):
    return None if not d else f"{d['p50']} / {d['p95']}"


def _warm(d):
    rs = {r['name']: r for r in (d.get('warmup') or {}).get('requests', [])}
    return None if 'warm-12k' not in rs else f"{rs['warm-12k']['ttft_s']} -> {rs['warm-12k-again']['ttft_s']}"


def _r(x, div):
    return None if x is None else round(x / div, 1)


def _minmem(d):
    vals = [v['MemAvailable_GiB'] for s in (d.get('soak') or {}).get('samples', []) for v in s['mem'].values() if 'MemAvailable_GiB' in v]
    return min(vals) if vals else None


def main():
    if len(sys.argv) > 1 and sys.argv[1] == '--compare':
        return compare(sys.argv[2], sys.argv[3])
    global T_START
    T_START = time.time()
    models = json.loads(urllib.request.urlopen(URL + '/models', timeout=30).read())
    rec = {'label': LABEL, 'url': URL, 'salt': SALT, 'started': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), 'models': [m['id'] for m in models['data']],
           'soak_min': SOAK_MIN, 'cold_tokens_target': COLD_TOKENS, 'metrics_before': metrics(), 'memory_before': mem(), 'phases': PHASES}
    log('start', LABEL, URL, 'salt', SALT, 'phases', PHASES)
    for ph in PHASES:
        t0 = time.perf_counter(); log('phase', ph, 'begin')
        try:
            if ph == 'warmup':
                rec[ph] = phase_warmup()
            elif ph == 'sanity':
                rec[ph] = phase_sanity('sanity')
            elif ph == 'coding':
                rec[ph] = phase_coding()
            elif ph == 'longgen':
                rec[ph] = phase_longgen()
            elif ph == 'cold':
                rec[ph] = phase_cold()
            elif ph == 'conc':
                rec[ph] = phase_conc()
            elif ph == 'cancel':
                rec[ph] = phase_cancel()
            elif ph == 'soak':
                rec[ph] = phase_soak()
            elif ph == 'sanity_end':
                rec[ph] = phase_sanity('sanity_end')
        except Exception as e:  # noqa: BLE001
            rec[ph] = {'error': f'{type(e).__name__}: {str(e)[:400]}'}; log('phase', ph, 'ERROR', rec[ph]['error'])
        rec.setdefault('phase_wall_s', {})[ph] = round(time.perf_counter() - t0, 1)
        rec.setdefault('memory_by_phase', {})[ph] = mem()
        log('phase', ph, 'end', rec['phase_wall_s'][ph], 's')
        OUT.write_text(json.dumps(rec, indent=1))
    rec['metrics_after'] = metrics(); rec['memory_after'] = mem(); rec['finished'] = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
    b, a = rec['metrics_before'].get('requests_total'), rec['metrics_after'].get('requests_total')
    rec['own_requests'] = OWN_REQUESTS[0]
    rec['foreign_requests'] = None if a is None or b is None else max(0, int(a - b) - OWN_REQUESTS[0])  # engine successes not sent by this run
    if rec['foreign_requests']:
        log('WARNING: engine served', rec['foreign_requests'], 'requests that were not part of this run (foreign traffic during the benchmark)')
    OUT.write_text(json.dumps(rec, indent=1))
    log('done', OUT)


if __name__ == '__main__':
    main()
