import json, time, urllib.request, sys
URL = sys.argv[1]
def post(p, stream=False, timeout=600):
    req = urllib.request.Request(URL + '/chat/completions', data=json.dumps(p).encode(), headers={'Content-Type': 'application/json'})
    return urllib.request.urlopen(req, timeout=timeout)
M = 'GLM-5.3-Flash-EXL3'
# 1. thinking mode + reasoning_effort via streaming (reasoning route uses reasoning_effort)
t0 = time.time(); reasoning = ''; content = ''; usage = {}
with post({'model': M, 'messages': [{'role': 'user', 'content': 'What is 17*23? Think briefly, then answer with just the number.'}], 'max_tokens': 400, 'temperature': 0, 'stream': True, 'stream_options': {'include_usage': True}, 'chat_template_kwargs': {'enable_thinking': True}, 'reasoning_effort': 'low'}) as r:
    for raw in r:
        line = raw.decode().strip()
        if not line.startswith('data: ') or line == 'data: [DONE]': continue
        j = json.loads(line[6:]); usage = j.get('usage') or usage
        for c in j.get('choices', []):
            d = c.get('delta') or {}; reasoning += d.get('reasoning') or d.get('reasoning_content') or ''; content += d.get('content') or ''
print('THINKING: reasoning_chars=%d content=%r tokens=%s wall=%.1fs' % (len(reasoning), content.strip()[:60], usage.get('completion_tokens'), time.time() - t0))
# 2. streaming tool call
tools = [{'type': 'function', 'function': {'name': 'get_weather', 'description': 'Get weather', 'parameters': {'type': 'object', 'properties': {'city': {'type': 'string'}}, 'required': ['city']}}}]
calls = {}
with post({'model': M, 'messages': [{'role': 'user', 'content': 'What is the weather in Milan? Use the tool.'}], 'tools': tools, 'max_tokens': 200, 'temperature': 0, 'stream': True, 'chat_template_kwargs': {'enable_thinking': False}}) as r:
    for raw in r:
        line = raw.decode().strip()
        if not line.startswith('data: ') or line == 'data: [DONE]': continue
        for c in json.loads(line[6:]).get('choices', []):
            for tc in (c.get('delta') or {}).get('tool_calls') or []:
                e = calls.setdefault(tc.get('index', 0), {'name': '', 'args': ''}); f = tc.get('function') or {}
                e['name'] += f.get('name') or ''; e['args'] += f.get('arguments') or ''
print('STREAM TOOL CALL:', calls)
# 3. long context needle at ~60k tokens
filler = ('The quick brown fox jumps over the lazy dog near the river bank. ' * 4000)
msg = filler[:len(filler)//2] + '\nSECRET-CODE: KESTREL-9134.\n' + filler[len(filler)//2:] + '\nWhat is the SECRET-CODE? Reply with only the code.'
t0 = time.time()
with post({'model': M, 'messages': [{'role': 'user', 'content': msg}], 'max_tokens': 20, 'temperature': 0, 'chat_template_kwargs': {'enable_thinking': False}}) as r:
    j = json.loads(r.read())
print('LONG CONTEXT: prompt_tokens=%s answer=%r wall=%.1fs' % (j['usage']['prompt_tokens'], j['choices'][0]['message']['content'].strip(), time.time() - t0))
