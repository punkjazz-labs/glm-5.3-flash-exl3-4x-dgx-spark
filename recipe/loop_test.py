#!/usr/bin/env python3
"""Thinking-off loop test (Tech2Wild 2026-09-02 finding: with thinking off, the EXL3 lane leaked self-correction into
answers and ran to the token cap on 2 of 120 prompts; NVFP4 never did). 40 real prompts, 8 categories x 5, run with
thinking off and on, temperature 0, fixed max_tokens; counts answers that hit the cap, self-correction markers in the
answer text, and per-category decode rates. Prompts are salted per run so nothing is served from the prefix cache.
Env: GLM_URL, GLM_OUT, GLM_MODES (nothink,think; also low|medium|high = thinking on with that reasoning_effort),
     GLM_MAX_TOKENS (2048), GLM_CONC (4), GLM_SALT.
Note: decode_tps is only meaningful with thinking off (the first content token arrives after the reasoning).
"""
import concurrent.futures as cf, json, os, re, statistics, sys, time, urllib.request

URL = os.environ.get('GLM_URL', 'http://127.0.0.1:8890/v1'); MODEL = 'GLM-5.3-Flash-EXL3'
OUT = os.environ.get('GLM_OUT', 'loop-test.json'); MODES = os.environ.get('GLM_MODES', 'nothink,think').split(',')
MAXT = int(os.environ.get('GLM_MAX_TOKENS', '2048')); CONC = int(os.environ.get('GLM_CONC', '4'))
SALT = os.environ.get('GLM_SALT') or time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())
MARK = re.compile(r"\b(wait,|wait\b|actually,|hold on|let me (re|correct|count|check|fix|redo)|i made an (error|mistake)|correction:|scratch that|no, that)", re.I)

P = {
 'coding': ['Write a Python function that parses ISO-8601 durations like P3DT4H into total seconds, with docstring and three doctests.',
            'Fix the bug: this binary search loops forever on some inputs. Return only the corrected function.\n\ndef bs(a, x):\n    lo, hi = 0, len(a)\n    while lo < hi:\n        mid = (lo + hi) // 2\n        if a[mid] < x: lo = mid\n        else: hi = mid\n    return lo',
            'Write a bash one-liner that lists the ten largest files under the current directory, human readable sizes.',
            'Implement a thread-safe token bucket rate limiter in Go. Keep it under 60 lines.',
            'Write a SQL query that returns each customer\'s most recent order (id, customer_id, placed_at) for a table orders(id, customer_id, placed_at).'],
 'reasoning': ['A bat and a ball cost 1.10 in total. The bat costs 1.00 more than the ball. How much is the ball? Answer with the number only, then one sentence.',
               'If all bloops are razzies and some razzies are lazzies, must some bloops be lazzies? Answer yes or no and justify in two sentences.',
               'A train leaves at 09:40 and arrives at 13:05 the same day. How long is the trip in minutes? Show the arithmetic in one line.',
               'Is 1,048,576 a power of two? Which one? Answer in one line.',
               'Three boxes are labelled apples, oranges, mixed; every label is wrong. You may draw one fruit from one box. Which box do you draw from and how do you relabel all three? Four sentences maximum.'],
 'json': ['Return only a JSON object with keys name, port, tls (boolean) describing an HTTPS server on port 8443 named edge.',
          'Convert to JSON, no prose: host node-a, rank 0, fabric 10.0.0.1, link 200G.',
          'Produce a JSON array of the first five prime numbers as integers. Nothing else.',
          'Return a JSON object mapping the days of the week to their number, Monday=1. Only JSON.',
          'Give a JSON object with a single key "status" whose value is "ok". Only the object.'],
 'html': ['Write a minimal HTML page with a title, one h1 and a two-column table of three rows. No CSS, no explanation.',
          'Produce an HTML form with fields name, email and a submit button. Return only HTML.',
          'Write an HTML unordered list of four fruits inside a nav element. Only the HTML.',
          'Return an HTML snippet: a paragraph containing a link to https://example.com with the text Example. Nothing else.',
          'Write an HTML page skeleton with a header, main and footer element, each containing one sentence. Only HTML.'],
 'prose': ['In exactly 120 words, explain what tensor parallelism is to a curious teenager.',
           'Write a 100-word explainer on why RDMA needs lossless Ethernet.',
           'Describe the sound of rain on a tin roof in no more than 80 words.',
           'Explain, in under 150 words, why unified memory helps run large models on a small desktop.',
           'Write 100 words on the difference between latency and throughput, no lists.'],
 'narrative': ['Write a 200-word story about a night-shift engineer who hears the cluster fans change pitch.',
               'Tell a 150-word fable in which a router learns patience.',
               'Write a 200-word scene: two sysadmins argue about cable colours during an outage.',
               'A 150-word monologue from a GPU that has just been given a new job.',
               'Write a 200-word story that ends with the line: the health check finally returned 200.'],
 'summarization': ['Summarize in three sentences: Tensor parallelism splits each layer across devices so every device holds a slice of every weight matrix; each forward pass needs collective communication after the sliced operations, so the interconnect bandwidth and latency set the ceiling. Pipeline parallelism instead assigns whole layers to devices and passes activations between stages; it needs less bandwidth but leaves devices idle unless micro-batching fills the pipeline. Expert parallelism places different experts of a mixture-of-experts layer on different devices and routes tokens to them.',
                   'Summarize in one sentence: RoCE v2 carries RDMA over routable UDP/IP, relying on priority flow control or congestion notification to avoid the packet loss that RDMA transports handle badly.',
                   'Give a two-sentence summary: Speculative decoding runs a small draft model to propose several tokens, then the large model verifies them in one forward pass; accepted tokens are free, rejected ones fall back to the large model\'s own token, so throughput rises when the draft agrees often and the verification cost stays close to one step.',
                   'Summarize in two sentences: Prefix caching stores the key-value tensors of previously seen prompt prefixes so that a new request sharing that prefix skips recomputation; the granularity of the cache blocks decides how much of a partially matching prompt can be reused.',
                   'One-sentence summary: A watchdog process that polls a health endpoint and relaunches the service on failure must rate-limit itself, otherwise a persistent fault becomes a relaunch storm.'],
 'format': ['List five Unix signals, one per line, uppercase, no numbering, nothing else.',
            'Answer with exactly three bullet points starting with a dash: reasons to pin a Docker image by digest.',
            'Reply with the single word READY.',
            'Give the capital of Italy in uppercase letters only.',
            'Output the numbers from 1 to 10 separated by commas on one line, nothing else.'],
}


def one(mode, cat, i, prompt):
    body = {'model': MODEL, 'messages': [{'role': 'user', 'content': f'[ref {SALT}-{mode}-{cat}{i}] {prompt}'}], 'max_tokens': MAXT,
            'temperature': 0, 'seed': 42, 'stream': True, 'stream_options': {'include_usage': True},
            'chat_template_kwargs': {'enable_thinking': mode != 'nothink'}}
    if mode in ('low', 'medium', 'high'):
        body['reasoning_effort'] = mode
    req = urllib.request.Request(URL + '/chat/completions', data=json.dumps(body).encode(), headers={'Content-Type': 'application/json'})
    t0 = time.perf_counter(); ttft = None; text = []; reasoning = 0; finish = None; usage = {}
    try:
        with urllib.request.urlopen(req, timeout=1500) as r:
            for line in r:
                line = line.decode().strip()
                if not line.startswith('data:') or line == 'data: [DONE]':
                    continue
                j = json.loads(line[5:])
                if j.get('usage'):
                    usage = j['usage']
                for c in j.get('choices', []):
                    d = c.get('delta', {})
                    if d.get('content'):
                        if ttft is None: ttft = time.perf_counter() - t0
                        text.append(d['content'])
                    if d.get('reasoning_content') or d.get('reasoning'):
                        reasoning += len(d.get('reasoning_content') or d.get('reasoning'))
                    finish = c.get('finish_reason') or finish
        err = None
    except Exception as e:  # noqa: BLE001
        err = str(e)[:200]
    wall = time.perf_counter() - t0; ans = ''.join(text); ct = usage.get('completion_tokens') or 0
    marks = MARK.findall(ans)
    return {'mode': mode, 'cat': cat, 'i': i, 'finish_reason': finish, 'completion_tokens': ct, 'reasoning_chars': reasoning,
            'ttft_s': round(ttft, 2) if ttft else None, 'wall_s': round(wall, 1), 'decode_tps': round(ct / (wall - ttft), 1) if ttft and wall > ttft and ct else None,
            'capped': finish == 'length', 'self_correction_marks': len(marks), 'answer_words': len(ans.split()), 'error': err,
            'preview': ans[:160], 'tail': ans[-160:]}


def main():
    rec = {'url': URL, 'salt': SALT, 'max_tokens': MAXT, 'started': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), 'modes': {}}
    for mode in MODES:
        jobs = [(mode, cat, i, p) for cat, ps in P.items() for i, p in enumerate(ps)]
        t0 = time.perf_counter()
        with cf.ThreadPoolExecutor(CONC) as ex:
            rows = list(ex.map(lambda a: one(*a), jobs))
        wall = time.perf_counter() - t0
        by = {}
        for cat in P:
            rs = [r for r in rows if r['cat'] == cat]
            dec = [r['decode_tps'] for r in rs if r['decode_tps']]
            by[cat] = {'capped': sum(r['capped'] for r in rs), 'marks': sum(r['self_correction_marks'] for r in rs), 'errors': sum(1 for r in rs if r['error']),
                       'decode_tps_med': round(statistics.median(dec), 1) if dec else None, 'tokens_med': statistics.median(r['completion_tokens'] for r in rs)}
        capped = [f"{r['cat']}{r['i']}" for r in rows if r['capped']]; marked = [f"{r['cat']}{r['i']}x{r['self_correction_marks']}" for r in rows if r['self_correction_marks']]
        rec['modes'][mode] = {'n': len(rows), 'wall_s': round(wall, 1), 'capped': len(capped), 'capped_items': capped, 'with_marks': len(marked), 'marked_items': marked,
                              'errors': sum(1 for r in rows if r['error']), 'by_category': by, 'rows': rows}
        print(f"{mode}: n={len(rows)} capped={len(capped)} {capped} with_self_correction_marks={len(marked)} {marked} errors={rec['modes'][mode]['errors']} wall={wall:.0f}s", flush=True)
        for cat, b in by.items():
            print(f"   {cat:14s} capped={b['capped']} marks={b['marks']} decode_med={b['decode_tps_med']} tokens_med={b['tokens_med']}", flush=True)
    rec['finished'] = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
    json.dump(rec, open(OUT, 'w'), indent=1); print('wrote', OUT)


if __name__ == '__main__':
    main()
