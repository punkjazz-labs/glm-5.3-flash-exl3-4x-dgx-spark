#!/usr/bin/env python3
"""Bounded real inference probe for service readiness and recovery."""
import json
import sys
import time
import urllib.request

url = sys.argv[1].rstrip('/')
payload = {'model': 'GLM-5.3-Flash-EXL3', 'messages': [{'role': 'user', 'content': 'Reply only READY.'}], 'max_tokens': 8, 'temperature': 0, 'chat_template_kwargs': {'enable_thinking': False}}
t0 = time.monotonic()
request = urllib.request.Request(url + '/v1/chat/completions', data=json.dumps(payload).encode(), headers={'Content-Type': 'application/json'})
with urllib.request.urlopen(request, timeout=float(sys.argv[2]) if len(sys.argv) > 2 else 90) as response:
    data = json.load(response)
choice = data['choices'][0]
assert choice['message']['content'].strip() == 'READY', 'wrong readiness response'
assert choice['finish_reason'] == 'stop', 'incomplete readiness response'
print(json.dumps({'ready': True, 'elapsed_s': round(time.monotonic() - t0, 3), 'fingerprint': data.get('system_fingerprint')}))
