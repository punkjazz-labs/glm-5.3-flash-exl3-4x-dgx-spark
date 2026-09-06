# Scoped qualification summary

The selected configuration completed one bounded, isolated TP4 qualification
on 2026-09-05. The machine-readable projection is
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

## September 6 one-knob screens (provisional)

These short screens held the selected H16 eager recipe fixed and changed one
scheduler setting at a time. They used the same frozen runner and hard gates,
in the fixed-budget evaluation style described by [Karpathy's
autoresearch](https://github.com/karpathy/autoresearch). They are not part of
the selected qualification.

| Screen | Coding aggregate tok/s | Long decode tok/s | Cold prefill tok/s | 8-stream aggregate tok/s | Score |
|---|---:|---:|---:|---:|---:|
| Selected eager, mixed prefill off | 58.96 | 29.59 | 1,073.1 | 121.84 | 1.000 |
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
throughput loss. The selected default remains eager execution with mixed
prefill off.


## Matched 20-minute workload result

The selected baseline and native 1,024-token prefill threshold each completed
115 mixed requests, including 45 short replies, with zero errors, zero foreign
requests and 22/22 exact retrieval checks. Both passed every frozen gate.

| Mixed workload metric | Selected baseline | Native threshold 1024 |
|---|---:|---:|
| Short completion p95 | 19.573s | 7.030s |
| Slowest short completion | 24.989s | 13.969s |
| Short first-token p95 | 0.491s | 3.211s |
| Aggregate output tokens/s | 50.70 | 50.77 |
| Completed short output tokens | 695 | 694 |

Short completion p95 improved 64.1% with essentially unchanged mixed throughput.
First-token p95 worsened, so this is a completion-latency improvement rather than
a universal latency improvement. The matched standard phases showed coding
throughput -1.24%, cold prefill -3.92%, concurrent-eight output -1.03%, and long
decode +2.94%. The first short screen's +18.7% long-decode result did not repeat
at that magnitude. Its composite score must not be reported as a throughput gain.

The candidate passes the declared screen and advances to a fixed 150-minute
varied-load qualification: 280k cold context, 96k concurrent prompts, long
output, coding, retrieval, structured output and cancellation. One mixed pair
is not a repeated population-tail estimate or long-run reliability proof.
The selected default remains the original eager attention64 configuration
until qualification and operational handback checks finish.
