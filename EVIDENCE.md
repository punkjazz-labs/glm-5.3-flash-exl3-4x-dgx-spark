# Scoped qualification summary

The historical H16/off configuration completed one bounded, isolated TP4 qualification
on 2026-09-05. Native1024 completed a separate bounded full qualification and verified live cutover on 7 September. The machine-readable projection is
[`qualification-attention64h16.json`](qualification-attention64h16.json). It
contains selected measured fields plus SHA-256 references to the private
qualification inputs, without copying those inputs. It records 484 of 484
accounted HTTP requests, a 153-minute mixed soak with 371 requests and zero
errors, and 93 of 93 exact retrieval checks. The runtime did not restart during
that window.

The sparse-attention patch is narrowly guarded: it accepts one upstream
backend source SHA-256 and produces one patched SHA-256; it requires BF16
`[T,16,576]` query geometry, 64-by-656 packed KV pages, physical top-k 2048,
and at least 33,685,504 bytes of decode workspace. It was numerically compared
with an independent FP32 reference for token counts 1, 64, 65, 129, 193 and
2048. The largest observed absolute difference was 0.00185609 and normalized
RMS difference 0.00170352.

This is evidence for the stated revision and workload only. It does not
establish fresh-install reproducibility, reboot persistence, indefinite
stability, compatibility with another image or driver, maximum-context
capacity, or a global performance optimum. Review the recorded numerical
findings, then run preflight, readiness checks and the documented workload on
your deployment. The independent numerical harness is not included in this
public bundle; the reported numerical results are not a runnable quickstart.

The new qualification projection excludes raw logs, node identities, network
state, absolute paths, generated request/output text, gateway receipts and
deployment records. The historical `evidence/workload-tp4-fat.json` test fixture
was already public and is retained unchanged for the local suite. It is not
evidence for the selected configuration. Its SHA-256 is
`562e15e71dc77bc9651dfcdc6b3d4c472ee290aacce7bd631f533a76ee672e64`.

## Selected Native1024 evidence

Native1024 retains eager H16 sparse attention, mixed prefill `off`, and
`MAX_NUM_BATCHED_TOKENS=2048`; its sole scheduler delta is
`EXTRA_ARGS=--enforce-eager --long-prefill-token-threshold 1024`. Its full run
recorded 484 HTTP 200 responses, 368 soak requests, 92/92 retrieval checks,
151.9 minutes, 62.89 coding tok/s, 33.56 long-decode tok/s, 1,010.5 cold-prefill tok/s on a 282,310-token prompt, 126.61 concurrent-8 tok/s, 34.43 mixed aggregate tok/s, and 5.511 s /
3.108 s short wall/TTFT p95. The workload SHA-256 is
`577f1182d263a88cee9ac0d3cd64487e170676b5730a39c3a310a3f14c7a743a`; the
controller, snapshot and final-audit SHA-256 values are respectively
`dd951c662d5ec24575916192be92a5ed3e00aa17007e71c37707d45a1a23f709`,
`f91c9950d76b86531c57cc6785ea7dbf714a051e41b1fbf84e1ee895053a185e`, and
`e07093ad05238636801e014f2018ba17c2b690715d98a000a39be7337ed7d888`.

Matched r1 reduced short wall p95 64.1% with aggregate output +0.14%; reverse
r2 reduced it 23.2% with aggregate output -1.22%. TTFT traded direction and
long decode improved in r1 but regressed 18.8% in r2. These observations do
not claim universal speed or a global optimum. The retained H16 projection
above remains the historical baseline; it is not a projection of the new native result.

## September 6 one-knob screens (provisional)

These short screens held the selected H16 eager recipe fixed and changed one
scheduler setting at a time. They used the same frozen runner and hard gates,
in the fixed-budget evaluation style described by [Karpathy's
autoresearch](https://github.com/karpathy/autoresearch). They are not part of
the selected qualification.

| Screen | Coding aggregate tok/s | Long decode tok/s | Cold prefill tok/s | 8-stream aggregate tok/s | Score |
|---|---:|---:|---:|---:|---:|
| Baseline H16, threshold 0 | 58.96 | 29.59 | 1,073.1 | 121.84 | 1.000 |
| Mixed prefill 64 | 54.49 | 32.41 | 1,049.4 | 121.80 | 1.678 |
| Mixed prefill 128 | 56.50 | 29.57 | 1,045.1 | 125.31 | 1.599 |
| Native long-prefill threshold 1024 | 61.56 | 35.12 | 1,032.5 | 125.65 | 1.541 |

The native threshold was the provisional balanced candidate selected for a
separate fixed 20-minute matched mixed-tail screen. The score is the geometric mean of six
baseline-relative metric ratios; latency ratios are inverted with a one-second
floor. It is not a throughput result. The 128 screen had a better tiny cold-tail
observation but worse cron latency. Tiny-tail samples were n=3, and none of
these screens proves a global optimum, a promotion, or long-run candidate
reliability. The matched screen requires zero errors and foreign requests, at
least 20 short probes, at least a 20% p95 gain, and no more than a 5% aggregate
throughput loss. Native1024 subsequently passed the live cutover gate and consumer checks.


## Matched 20-minute workload result

Both orders used 115 mixed requests, 45 short probes, 22/22 retrieval checks,
and zero workload errors or foreign requests. Independent whole-server audits
reconciled r2 baseline/native as 227/228 HTTP 200 responses, respectively.
The r1 claims here use the frozen runner accounting, not these r2 access-log audits.

| Mixed workload metric | r1 baseline | r1 native | r2 baseline | r2 native |
|---|---:|---:|---:|---:|
| Short completion p95 | 19.573s | 7.030s | 22.562s | 17.331s |
| Slowest short completion | 24.989s | 13.969s | 25.465s | 25.576s |
| Short first-token p95 | 0.491s | 3.211s | 5.595s | 3.087s |
| Aggregate output tokens/s | 50.70 | 50.77 | 50.81 | 50.19 |
| Completed short output tokens | 695 | 694 | 722 | 722 |

R1 reduced short completion p95 64.1% with output +0.14%; r2 reduced it 23.2%
with output -1.22%. TTFT worsened in r1 but improved in r2. Long decode improved
in r1 (+2.94%) and regressed in r2 (-18.8%). These mixed-order results support a
completion-tail objective, not a universal latency or throughput claim. The full
qualification, deliberate rollback drill, live cutover and consumer handback are complete.

## Operational scope

The dedicated native group matched the qualified image, backend, environment
and mount contents. Its cutover passed a deliberate after-supervisor rollback
test before the final successful deployment, with real gateway inference and
one active selected supervisor. The previous H16 group is retained as rollback.
This does not establish physical reboot survival or indefinite reliability.
The scheduler change does not address the separate xgrammar FSM error observed
in an earlier service-wide run.
