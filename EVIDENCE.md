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

## September 7 bounded capacity and transport screens

The selected native1024 eight-sequence recipe remains the qualified default. The following later measurements use the same model/backend lane and frozen workload, but do not replace its qualification.

| Screen | Matched observation | Status |
|---|---|---|
| `MAX_NUM_SEQS=16`, initial capacity screen | C16 aggregate completion `+36.26%` | Screen only |
| `MAX_NUM_SEQS=16`, forward mixed pair | C16 `187.52` vs `133.18` tok/s (`+40.80%`) | Default guard failed: short-completion p95 `+49.13%` |
| `MAX_NUM_SEQS=16`, reverse mixed pair | C16 `188.19` vs `132.19` tok/s (`+42.36%`) | Capacity evidence only; does not repair the forward default-guard failure |
| Native prefill threshold 1536 | Cold prefill `+3.23%` | Rejected: below the predeclared 5% target |
| Native prefill threshold 2048 | Cold prefill `+4.39%` | Rejected: below the predeclared 5% target |
| DFlash draft length 4 | Declared target did not reach 5% in its fresh-control screen | Rejected |
| Outer batch 4096 | Mixed aggregate output `+0.42%` | Rejected: below the predeclared 5% target |
| QPS4 plus split-data transport | Long generation `28.87` vs `31.82` tok/s (`-9.27%`) | Rejected; short screen only, **NO SOAK** |

The capacity rows describe aggregate work at offered concurrency 16. They are not a claim that a single interactive stream is 40% faster. The forward pair fails the interactive-default guard, so native1024 remains selected. The QPS candidate changed only `NCCL_IB_QPS_PER_CONNECTION=4` and `NCCL_IB_SPLIT_DATA_ON_QPS=1`; its observed decode difference is not causal attribution to either setting. The recipe already uses both HCAs, cross-NIC routing and four channels. [NVIDIA describes the two PCIe paths](https://docs.nvidia.com/dgx/dgx-spark/spark-clustering.html); NCCL documents [QPs per connection](https://docs.nvidia.com/deeplearning/nccl/user-guide/docs/env.html#nccl-ib-qps-per-connection) and [split-data behavior](https://docs.nvidia.com/deeplearning/nccl/user-guide/docs/env.html#nccl-ib-split-data-on-qps). A supplied [two-Spark result](https://x.com/Khen_na_/status/2096709963263418515) motivated this fresh test; its DeepSeek/direct-cable result is not a local GLM TP4 measurement.

### Qualified optional batch capacity

The fresh r2 protocol and independent review passed: 16 actual workers, all frozen gates, unchanged four-rank source and identity across 34 snapshots, complete HTTP accounting, exact native restoration, real gateway inference, and the signed runtime gate. Native1024 with `MAX_NUM_SEQS=8` remains the serving default; the 16-sequence setting is an opt-in batch/capacity choice.

| Full r2 observation | Result |
|---|---:|
| Varied soak duration / requests | 153.1 min / 493 |
| Accounted HTTP 200 / non-200 / foreign requests | 634 / 0 / 0 |
| Exact long-prompt retrieval | 120 / 120 |
| Minimum available memory across ranks | 17.70 GiB |
| Coding aggregate completion | 59.33 tok/s |
| Natural 4,096-token long decode | 30.42 tok/s |
| Cold prefill, 281,750 prompt tokens | 1,010.8 tok/s |
| C4 / C8 / C12 / C16 aggregate completion | 89.20 / 130.14 / 164.14 / 181.37 tok/s |
| Mixed aggregate completion / prompt throughput | 46.70 / 782.90 tok/s |
| Mixed short completion / first-token p95, n=185 | 181.912 / 169.948 s |
| Mixed long-generation decode p50 | 3.42 tok/s |

The 16-worker mixed saturation has unacceptable latency for the interactive default. Its 46.70 tok/s mixed output is not a matched improvement over the native default's 34.43 tok/s result: that qualification offered four workers. The earlier matched capacity pairs used four mixed workers plus fixed C16 bursts. Their C16 median per-request decode fell from 17.60 to 12.945 tok/s forward and 17.13 to 12.615 tok/s in reverse, even while aggregate completion increased. More capacity does not mean faster individual decoding.

The first full16 attempt was invalid and aborted: its transient launcher expanded Bash rank arrays before the child shell sourced configuration, leaving empty SSH hosts for memory probes. The launcher was corrected and its real memory transport checked before r2. R1 contributes no qualification evidence and is not pooled with r2. These results are bounded to the pinned stack and workload; they do not establish reboot survival, indefinite reliability, maximum context, or a global optimum.

## Bounded 64k profiler observation

A healthy 64k-prefill workload captured four host steps per rank over 3.878–4.133 seconds. Within each rank, NCCL all-reduce accounted for 33.3–37.0% of **summed CUDA-kernel durations**, EXL3 MoE/GEMM for 33.9–35.7%, and sparse MLA for 4.8–5.1%. These shares use kernel-duration sums as their denominator, not request wall time or fabric utilization.

This is an early window of a workload that includes overlapping short probes, not a whole-64k or steady-decode profile. GPU annotation mirrors are excluded from host-step counts and spans. Kernels can overlap, and NCCL duration includes peer synchronization and waiting; these measurements do not isolate the switch or wire, or predict an end-to-end speedup.

## Comparable-source boundary

[Tech2Wild's EXL3 recipe](https://github.com/tonyd2wild/GLM-5.3-Flash-EXL3-on-2x-NVIDIA-DGX-Spark/tree/dc91a125fc60349ce99498d65dac5bc772a43c54) is TP2. Its public TP4 recipe uses a different [NVFP4/Marlin stack](https://github.com/tonyd2wild/GLM-5.3-Flash-NVFP4-1M-KV-4x-DGX-Spark/tree/8fd2fcd27c04c7fa93e770000b818657f338875d), so neither its headline nor topology belongs in an EXL3/native1024 comparison. [`antirez/ds4`](https://github.com/antirez/ds4/tree/b6af0adf8ca97c89145c9f9c15be70c9fd6c4507) documents a single-GPU DGX Spark GLM path and in-host CUDA multi-GPU mode; its pinned sources do not establish Spark-to-Spark RDMA tensor parallelism. These are implementation references, not local performance evidence.

## Capacity evidence provenance

The full r2 workload window was 7 September 2026, 18:31:07–21:17:48 UTC; native/gateway restoration completed at 21:28:29 UTC. The frozen workload and scorer were unchanged. Raw receipts remain private because they contain node state and generated output; these are their exact SHA-256 commitments:

| Receipt | SHA-256 |
|---|---|
| seq16-full-r2-full-workload.json | `7a2ba45ad411aa45678c0924b97c009b730a56a4a824f7877c9aae28619ca778` |
| seq16-full-r2-controller.json | `392716943650d7fe1e3a4455321605bdb95aaaf6fc833a855e8d33a78be54272` |
| seq16-full-r2-final-release-qualification-review.json | `408932a76169e678324b15e5754e56214fcb3e4fcdc57391501f9bb24525d507` |
| final-capacity-decision.json | `58bfd2dbac59dab7bd63cb5a0f675887edc02ab61bf96222b02b3d811d49654c` |

The 16-worker protocol deliberately differs from the historical four-worker qualification; its worker count, exact phases, frozen hard gates, identity, request accounting and service handback were independently reviewed. No failed receipt was relabeled to pass. The early profiler remains a diagnostic window and is not pooled with unprofiled benchmark timings.


## September 9 matched RigMark screening

This later screen does not replace the selected Native1024 qualification. It used
[RigMark revision c671b52a97f3cc01919c18d8d1f8e4f01243290c](https://github.com/alexellis/rigmark/tree/c671b52a97f3cc01919c18d8d1f8e4f01243290c),
protocol 1.1.0, with the reviewed direct bench.py / report.py path. The source
archive and per-file hashes were pinned. The scoped static review found no
package installation, shell execution, dynamic execution, generated-code
execution, credential persistence, or non-Git subprocess launch in that direct
path. It is not a general malware guarantee for an endpoint, interpreter, or
dependency. audit_code.py, which can execute generated code in Docker, was not
used.

Both arms used three decode runs per code, prose, and structured prompt; one
cold/replay prefill pair at 8k, 32k, and 64k; and two C1/C2/C4 concurrency
rounds with a 256-token cap. Reasoning was disabled. Both arms accounted for
35 HTTP requests and passed 9/9 basic decode-output checks. A visible completion
or structured response is not a semantic coding-correctness test, and a capped
concurrency response is a capacity observation rather than a completed task.

| Metric | Retained queue setting | 2 ms queue setting | Change |
|---|---:|---:|---:|
| Code decode | 49.616 tok/s | 49.629 tok/s | +0.026% |
| C4 aggregate | 93.098 tok/s | 87.487 tok/s | -6.0% |
| Coding TTFT | 0.454 s | 0.608 s | +33.9% |
| 8k cold prefill | 1,052.709 tok/s | 1,029.454 tok/s | -2.2% |

The queue-spin and KDA ideas came from [JSpark3 at 70210556801c1c187e625b7700f4085763ed1840](https://github.com/jakejharris/jspark3/tree/70210556801c1c187e625b7700f4085763ed1840). The local KDA prototype retained TP4 geometry; TP3 padding was not imported.

The 2 ms setting is rejected for this recipe: it did not meet the declared
material decode-gain screen, C4 regressed, and coding TTFT rose. These results
are comparable within this RigMark screen only; they are not comparable with
the earlier mixed-soak table.

A TP4 BF16 f/g projection-batching prototype measured isolated class-level
CUDA timings only: 17.184 to 14.048 microseconds at one token, 17.344 to
13.824 at three, and 106.480 to 96.800 at 2,048. The corresponding 34-layer
arithmetic estimates are 0.107, 0.120, and 0.329 ms per forward. It is not
adopted: this is not end-to-end throughput, latency, quality, memory, or
reliability evidence.

The public [GX10 source](https://github.com/mmastrac/glm-5.3-flash-4x-gx10/tree/a30d4b24f2341ec2d36a516c38c3ba1fd967a884)
and [TP4 NVFP4/Marlin source](https://github.com/tonyd2wild/GLM-5.3-Flash-NVFP4-1M-KV-4x-DGX-Spark/tree/8fd2fcd27c04c7fa93e770000b818657f338875d)
remain hypothesis sources. Their weights/quantization, template, runtime,
scheduler, graph mode, speculative configuration, context, and workload differ,
so their figures do not establish a speedup for this EXL3/native recipe.

### NVFP4 matched result

Pinned artifacts: [NVFP4 weights at 240131d6a447c8d89acd428c5ddfc85598651744](https://huggingface.co/RedHatAI/GLM-5.3-Flash-NVFP4/tree/240131d6a447c8d89acd428c5ddfc85598651744), [DFlash2 draft at bf582e4eacc1810f76656d1811693ff6c6737d2a](https://huggingface.co/incoai/GLM-5.3-Flash-DFlash2/tree/bf582e4eacc1810f76656d1811693ff6c6737d2a), and Tony's image manifest `sha256:4def0ef644cb2e9814136dcffd5e385e21bc594f48f3b292234051904abe85a6`. The candidate used Marlin, FP8 KV (24 GiB), DFlash k7, a 16,384-token batch budget, 64 sequences and FULL_AND_PIECEWISE graphs. The retained EXL3 control used k3, batch 2,048, eight sequences and eager H16/slice64 Native1024.

The ordinary Tony NVFP4/Marlin recipe completed the frozen short benchmark.
It passed exact-text, JSON and forced tool-call argument smoke checks, plus
retrieval from a prompt containing 25,000 filler repetitions, followed by 9/9 basic RigMark decode-output checks and exactly 35/35
expected benchmark POST responses with HTTP 200. Independent access-log
accounting found no extra POSTs or non-200 responses in that window.
Exact retained-container/source/environment restoration, post-trial inference
checks and the signed runtime gate passed. The qualified EXL3 default was
returned to production after the screen; NVFP4 was not promoted.

| Metric (median) | EXL3 TP4 | NVFP4 TP4 | Change |
|---|---:|---:|---:|
| code decode | 49.616 tok/s | 68.021 tok/s | +37.1% |
| prose decode | 28.569 tok/s | 28.399 tok/s | -0.6% |
| structured decode | 55.892 tok/s | 91.291 tok/s | +63.3% |
| Code TTFT | 0.454 s | 0.342 s | -24.7% |
| 8k cold prefill | 1052.709 tok/s | 1452.279 tok/s | +38.0% |
| 8k replay TTFT | 1.098 s | 2.465 s | +124.5% |
| 32k cold prefill | 1086.398 tok/s | 1256.012 tok/s | +15.6% |
| 32k replay TTFT | 0.768 s | 1.986 s | +158.6% |
| 64k cold prefill | 1086.988 tok/s | 1393.382 tok/s | +28.2% |
| 64k replay TTFT | 1.158 s | 2.353 s | +103.2% |
| C1 aggregate | 40.400 tok/s | 43.261 tok/s | +7.1% |
| C1 stream TTFT | 0.421 s | 0.339 s | -19.5% |
| C2 aggregate | 65.780 tok/s | 70.445 tok/s | +7.1% |
| C2 stream TTFT | 0.567 s | 0.481 s | -15.2% |
| C4 aggregate | 93.098 tok/s | 81.345 tok/s | -12.6% |
| C4 stream TTFT | 0.772 s | 3.674 s | +375.9% |

The tool smoke checked the selected function and parsed arguments; it did not
assert the API finish reason or qualify the complete tool protocol.

This is a whole-recipe comparison, including weights, quantization, chat
templates, runtime, graph mode, scheduler and speculative settings. Both used
the same pinned RigMark source, prompt digest, comparison identifier, seed,
request settings, sample counts and no-thinking mode. It is not a quality
comparison or a long reliability qualification. The control was an already
warm retained process; the candidate was newly started and passed the stated
smokes. The benchmark added no extra unscored warmup in either arm.

C4 was variable: NVFP4 returned 62.941 then 99.749 aggregate tok/s, with
median stream TTFT 6.697 then 0.652 s. The EXL3 rounds were 97.046 and
89.150 tok/s. Keep both measured rounds: the candidate's second result alone
is not its headline. The first C4 interval overlaps a warning that the TileLang
`mhc_pre_big_fuse_with_norm_tilelang` kernel compiled during inference. That
supports a compilation contribution, without attributing the entire delay
or establishing steady-state capacity from two rounds.

**Decision: retain the qualified EXL3 default.** NVFP4 is a promising candidate
for coding and prefill, but this screen does not meet the no-more-than-5-percent
throughput-regression guard, and replay TTFT worsened. These data do not support
"double all metrics" or production promotion. The next focused experiment is
warmup coverage and a matched warmed C4/replay comparison; changing speculative
length is a separate hypothesis, not a proven fix. No new soak was run.

The earlier failed attempts are retained separately: the first omitted an MTP
file referenced by the pinned index; the second reached API readiness but hit
a quoting error in the external smoke harness before inference. Neither is
counted as a measured model failure. Both restored the retained runtime.

### Screening receipt hashes

These SHA-256 references identify private raw receipts; they do not make the
private logs, generated outputs, host identities or deployment records public.

| Receipt | SHA-256 |
|---|---|
| EXL3 RigMark control | `fc48b69a3c33157086fc5f5d685c43e6561d2efbbf63667df02b94d3c17190b3` |
| 2ms RigMark candidate | `d97aa316e8407223b78f7e71fb86c19b955f0436684fb0e754a1de0c101c4179` |
| NVFP4 RigMark candidate | `6e4c27c9ffbe3bae22d614ee0d4f263f960d24c0a1ce54007e26fc5a3a8f2ef2` |
| NVFP4 final controller | `ea4d226814535d9650b2421cd6c4a26c4899c1e6c3c3f1f578ec4c9022aca97d` |
| NVFP4 timed POST proof | `d867b55405d43327c56143a789b5dac6c011423a0e4f8409698602e6f813a364` |
| KDA actual-class numerical/microbenchmark | `9bc6c7fb43772879c044227060cc3355e4ef5273e358d3230d91ef21fffb064c` |
