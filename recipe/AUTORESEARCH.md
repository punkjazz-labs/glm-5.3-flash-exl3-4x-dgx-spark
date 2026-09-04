# Autoresearch: squeezing the TP4 recipe for production

A small, Karpathy-style autoresearch loop: one configuration change per experiment, a fixed ten-minute production-mix
benchmark, a single scalar score, keep-if-better under hard reliability gates, everything logged to a TSV. It runs
detached on the head node (rank0) and relaunches the whole quartet for every experiment (about 11 minutes), so one
experiment costs roughly 22 minutes and a batch of eight fits in three hours.

## Rules

1. One knob per experiment, from a written queue (`experiments-*.tsv`: `name<TAB>VAR=val VAR=val`). A name starting
   with `live-` benches whatever is running without relaunching (the baseline).
2. The benchmark never changes inside a campaign: `warmup,sanity,coding,longgen,cold,conc,cancel` with
   `LONGGEN_TOKENS=4096 COLD_TOKENS=64000 CONC_LEVELS=4,8` (`glm_workload.py`, same code as the TP2/TP4 receipts).
3. Hard gates, all must pass or the experiment is rejected whatever its score: sanity block (exact JSON, EOS, tool
   call, json_schema), cancel drains, cold needle found, zero request or phase errors, and every rank keeps at least
   `MEM_FLOOR` (3 GiB) MemAvailable in every phase. The memory gate exists because 0.85 utilisation left 1-2 GiB and
   the driver logged `NV_ERR_NO_MEMORY` before the 13:58Z engine hang on 2026-09-02.
4. Score = geometric mean of metric/baseline over six production metrics, latencies inverted, so 1.10 means "10 %
   better across the board" and a win on one axis cannot hide a loss on another:
   coding aggregate tok/s (3 coding + 1 cron request at once), coding first-token p50, 12k-class generation decode
   tok/s, p50 latency of short requests fired into that generation, cold 64k prefill tok/s, aggregate tok/s at the
   highest concurrency level.
5. The first gated experiment is the baseline. The best gated score is tracked in `best.tsv`; at the end of the queue
   the loop relaunches the best configuration if it is not already live. Re-running the loop skips names already in
   `results.tsv`, so a queue can be extended and resumed.

## Running

```bash
# on rank0, ~/AI/tp4-recipe
nohup ./autoresearch.sh prod.env experiments-batch1.tsv ~/AI/autoresearch > ~/AI/autoresearch.out 2>&1 &
column -ts$'\t' ~/AI/autoresearch/results.tsv
python3 autoresearch_score.py ~/AI/autoresearch/<name>.json --baseline ~/AI/autoresearch/<baseline>.json
```

Knobs the launch scripts pass through: `GPU_MEM_UTIL MAX_NUM_SEQS DFLASH_TOKENS MAX_NUM_BATCHED_TOKENS
MIXED_PREFILL_CHUNK KV_CACHE_DTYPE MAX_MODEL_LEN EXTRA_ARGS`. Anything else needs a recipe change first.

## Campaign log

Filled in as batches complete (see README for the shipped result).

### 2026-09-02, production quartet (rank0, a393, 2d65, 2b4f), vLLM 0.1.dev20051 EXL3, mixed-prefill `off`

Final validation of the winner (full suite + 30-min soak): README, "Production tuning by autoresearch". A consistently
rescored table (same scorer, same baseline, 3 GiB floor) is `evidence/autoresearch/rescored.tsv`; note the draft-4 receipt
predates the foreign-traffic counter, so its `clean` gate there is vacuous.

Baseline for scoring: `mem080` (recipe defaults except `GPU_MEM_UTIL=0.80`). The recipe default 0.85 (`live-baseline`) fails the
memory gate with 0.85 GiB free on the tightest rank and is otherwise identical in throughput. Scores below are from
`autoresearch_score.py` with the 1 s latency floor; receipts in `evidence/autoresearch/`.

| experiment | change vs baseline | coding agg tok/s | coding TTFT p50 | 4k gen decode | shorts p50 | 64k prefill tok/s | 8-stream agg tok/s | 8-stream worst TTFT | min free GiB | gates | score |
|---|---|---|---|---|---|---|---|---|---|---|---|
| live-baseline | `GPU_MEM_UTIL=0.85` (recipe default) | 59.7 | 3.1 s | 26.4 | 0.48 s | 1010 | 77 | 34.5 s | 0.85 | mem | - |
| mem080 | baseline | 60.2 | 2.4 s | 27.9 | 0.42 s | 1157 | 84 | 27.4 s | 4.1 | pass | 1.000 |
| mem080-seqs8 | `MAX_NUM_SEQS=8` | 61.3 | 2.8 s | 26.7 | 0.46 s | 1166 | 103 | 1.3 s | 2.4 | mem | 1.004 |
| mem080-dflash5 | `DFLASH_TOKENS=5` | 60.8 | 2.8 s | 28.2 | 0.50 s | 1128 | 90 | 26.8 s | 1.7 | mem | 0.983 |
| mem080-dflash3 | `DFLASH_TOKENS=3` | 59.6 | 1.7 s | 31.1 | 0.45 s | 1133 | 87 | 25.3 s | 5.5 | pass | 1.079 |
| live-mem080-dflash3-rep | replicate of the above | 62.2 | 2.2 s | 30.7 | 0.41 s | 1167 | 91 | 24.4 s | 5.3 | pass | 1.054 |
| mem080-dflash2 | `DFLASH_TOKENS=2` | 63.8 | 3.1 s | 31.2 | 0.48 s | 1181 | 64 | 34.7 s | 5.1 | pass | 0.947 |
| mem080-dflash4 | `DFLASH_TOKENS=4` | 51.7 | 13.7 s | 12.6 | 1.81 s | 1087 | 47 | 63.7 s | 4.8 | cancel | invalid: foreign gateway traffic during the run (see below) |
| mem080-nospec | `SPEC_METHOD=none` (control) | 44.9 | 2.4 s | 22.6 | 0.52 s | 1202 | 66 | 31.7 s | 3.8 | pass | 0.889 |
| mem080-chunk4096 | `MAX_NUM_BATCHED_TOKENS=4096` | 57.1 | 2.5 s | 26.5 | 0.46 s | 1124 | 85 | 28.4 s | 3.1 | pass | 0.972 |
| mem080-chunk8192 | `MAX_NUM_BATCHED_TOKENS=8192` | 53.4 | 19.9 s | 26.7 | 0.47 s | 1137 | 88 | 24.7 s | 3.5 | pass | 0.686 |
| mem080-dflash3-chunk1024 | `DFLASH_TOKENS=3 MAX_NUM_BATCHED_TOKENS=1024` | 63.1 | 12.3 s | 33.3 | 0.42 s | 1100 | 93 | 24.2 s | 5.2 | pass | 0.796 |
| mem080-mix2048 | `MIXED_PREFILL_CHUNK=2048` | 62.6 | 2.9 s | 29.1 | 0.41 s | 1159 | 84 | 26.6 s | 2.1 | mem | 0.983 |
| **mem075-dflash3-seqs8** | `GPU_MEM_UTIL=0.75 DFLASH_TOKENS=3 MAX_NUM_SEQS=8` | 61.5 | 2.9 s | 32.9 | 0.46 s | 1185 | **135** | **1.0 s** | **8.9** | pass | **1.086** |

What the campaign established:

- **Speculative decoding is worth +37 % decode** on this stack (22.6 tok/s without, 31 tok/s with DFLASH at 3 draft
  tokens), and the draft length matters: 7 (the recipe default) accepts only 30 % of drafted tokens, 3 accepts 53 %
  and is faster on every axis; 2 is faster single-stream but collapses at 8 streams; 5 is no better than 7.
- **`MAX_NUM_SEQS=4` was the concurrency ceiling.** With 8 the engine batches eight streams at 135 tok/s aggregate
  and nobody queues; per-stream decode at 8 streams is 18 tok/s against 24-26 with four of them queued. Memory cost is
  about 2 GiB per rank, which is why the combination needs 0.75.
- **Prefill is chunk-size-insensitive** (1100-1200 tok/s at 1024, 2048, 4096 and 8192) but interactivity is not:
  8192-token steps push first tokens of concurrent requests to 15-25 s, and 1024 also hurt coding TTFT. Keep 2048.
- **The mixed-prefill cap at 2048 is indistinguishable from `off`** with a 2048-token chunk, as expected.
- **`GPU_MEM_UTIL` buys reliability, not speed.** 0.85 -> 0.80 -> 0.75 costs nothing measurable and raises the free
  memory floor from under 1 GiB to about 9 GiB. Free memory still varies 1.7-5.5 GiB between launches at identical
  settings, so the 3 GiB gate flips on noise; it is a guard against the 0.85 regime, not a precise measurement. The
  NVRM `NV_ERR_NO_MEMORY` bursts in dmesg happen during every engine start (memory profiling, graph capture) at any
  utilisation; batch 3 refined this: single messages at serving time did precede an engine death.
- **Run-to-run noise is about 3 %** on the score (draft-3 replicate 1.054 vs 1.079); differences inside that band are
  not decisions. Latency metrics under 1 s are pure jitter and enter the score floored at 1 s.
- **Benchmarks on the production endpoint can be contaminated.** The invalid draft-4 run coincided with a
  structured-output request the benchmark never sends (xgrammar 500 in the engine log); the runner now counts the
  engine's `request_success_total` against its own requests and a `clean` gate rejects runs with foreign traffic. A
  clean draft-4 re-run was queued but never picked up (the harness had the old queue file open); it is the one gap.

### 2026-09-02/03, batch 3: fat-expert prefill kernel, swappiness, replicates (`experiments-batch3.tsv`, `experiments-batch3b.tsv`)

Triggered by Tech2Wild's NVFP4-vs-EXL3 two-lane comparison (2026-09-02) and by upstream landing PR77 (E2 fat-expert prefill
kernel, +20-24 % uncached prefill on TP2) after the image this recipe pinned. Three things changed in the harness first:
every prompt now carries a per-request salt (the coding and short prompts were fixed strings before, so their first-token
numbers in batches 1-2 were partly prefix-cache hits: the salted coding first-token p50 is 11-22 s on the old image, not
2-3 s, because fresh coding prompts queue behind the 24k cron prefill fired with them); the host probe records the GPU sm
clock per phase and the scorer gates on it (`clocks`: no rank under 1000 MHz while busy); `vm.swappiness` became a launch
knob (`SWAPPINESS`, applied by `node-launch.sh`) and the rank logs rotate per launch so a crash log survives the relaunch.
Receipts: `evidence/autoresearch-b3/`. Baseline for scoring: `live-swap0-shipped-rep` (shipped settings, old image,
swappiness 0 on the hosts).

| experiment | change vs shipped (0.75 / 8 seqs / draft 3 / 2048) | coding agg tok/s | coding TTFT p50 | 4k gen decode | shorts p50 | 64k prefill tok/s | 8-stream agg tok/s | min free GiB | gates | score |
|---|---|---|---|---|---|---|---|---|---|---|
| swap0-shipped | hosts at swappiness 0 (persisted before the batch) | 61.3 | 12.8 s | 31.0 | 0.51 s | 1182 | 138 | 9.4 | **engine died** at the 100k cancel-prefill | - |
| live-swap0-shipped-rep | same, benched on the relaunch | 64.7 | 11.6 s | 32.5 | 0.47 s | 1156 | 134 | 10.5 | pass | 1.000 |
| swap60-shipped | `SWAPPINESS=60` | 62.2 | 22.1 s | 31.4 | 0.55 s | 1169 | 133 | 8.0 | pass | 0.887 |
| **fat-shipped-swap60** | c190db1 image + root, `EXL3_FAT_KERNEL=1`, swappiness 60 | 63.9 | **2.9 s** | 31.8 | 0.50 s | **1361** | 129 | 10.0 | pass | **1.280** |
| **fat-shipped-swap60-rep** | replicate | 63.0 | **2.8 s** | 34.2 | 0.53 s | **1356** | 138 | 9.9 | pass | **1.309** |
| fat-mnbt4096-swap60 | + `MAX_NUM_BATCHED_TOKENS=4096` | 40.5 | 6.1 s | 31.3 | 0.47 s | 1397 | 126 | 8.6 | clean (12 foreign requests) | invalid |
| fat-mnbt7168-swap60 | + `MAX_NUM_BATCHED_TOKENS=7168` (upstream TP2 default) | 62.6 | 11.5 s | 38.2 | 0.49 s | 1451 | 134 | 9.4 | pass | 1.061 |
| swap0-mem070 | old image, `SWAPPINESS=0 GPU_MEM_UTIL=0.70` | 62.1 | 21.1 s | 31.3 | 0.48 s | 1191 | 132 | 14.3 | pass | 0.895 |
| swap60-mem080-dflash3 | old image, `GPU_MEM_UTIL=0.80 MAX_NUM_SEQS=4` (third replicate) | 36.9 | 1.1 s | 13.8 | 2.15 s | 1129 | 32 | 4.7 | cancel, clean (13 foreign) | invalid |
| fat-mnbt4096-swap60-rep | clean re-run of the 4096 row | 63.6 | 6.1 s | 32.9 | 0.51 s | 1383 | 129 | 8.1 | pass | 1.138 |

What batch 3 established:

- **The E2 fat-expert kernel is the largest single improvement of the whole campaign** and costs nothing: uncached 64k
  prefill 1156-1182 -> 1356-1361 tok/s (+16-18 %), and because the 24k cron prefill in the coding mix finishes sooner,
  the fresh coding requests fired with it get their first token in 2.9 s instead of 11-22 s. Decode, short latency,
  8-stream throughput and free memory are unchanged. Replicated (1.280, 1.309). Adopted: `prod.env` now pins the c190db1
  image and root with `EXL3_FAT_KERNEL=1`.
- **Chunk size, re-tested on the new kernel:** 7168 prefills 7 % faster still (1451 tok/s) but returns the coding first
  token to 11.5 s; 4096 (clean re-run) prefills 2 % faster and doubles the coding first token to 6.1 s (1.138). 2048 stays.
- **Swappiness 0 is not adopted.** Tech2Wild pins it; here it produced one engine death in two runs on the shipped
  configuration (rank 1 logged NVRM `NV_ERR_NO_MEMORY` four times at serving time, during the 64k and the 100k prefill,
  minutes before the engine stopped answering), and no run at swappiness 0 was faster than its swappiness-60 twin on any
  metric except the noisy coding TTFT. The persisted setting was returned to 60 after the batch. With 60 the campaign,
  the validation and this batch's five other relaunches ran without a death.
- **NVRM `NV_ERR_NO_MEMORY` in dmesg: bursts at every engine start (graph capture) are noise; single messages at serving
  time are not.** The dmesg timeline of the evening shows dozens-to-hundreds of messages on every rank in the two minutes
  after each launch, and the only serving-time messages of the night are the four on rank 1 before the death. The
  earlier note in batch 1 ("not a serving-time signal") was wrong in that generality.
- **Coding first-token p50 is bimodal with salted prompts** (2.9 / 6 / 11.5 / 21 / 22 s across otherwise equivalent
  runs): it depends on which fresh coding request lands behind the 24k prefill in the same batch, and it moves the score
  by up to 11 % on its own. Read the score together with the columns; the fat-kernel gain shows on prefill and TTFT
  together, the swappiness rows differ only on this column.
- **0.70 utilisation buys 4 GiB more free memory and nothing else** (0.895, all inside noise except that column). 0.75 stays.
- **Foreign traffic now recurs about hourly** (12 and 13 gateway requests during two runs); the `clean` gate caught
  both, and the queue file can be appended while the loop runs (same inode) to re-run a contaminated experiment.

### 2026-09-03, 150-min varied-length soak on the adopted configuration (`workload-run.sh TP4-soak150`)

Goal: a long uninterrupted window on the shipped c190db1 / fat-kernel / swappiness-60 quartet with a wider request mix
than the 30-min validation soak: `SOAK_KINDS=short,coding,medium_gen,long_prompt,short,long_gen,long_prompt_96k,short`,
`SOAK_WORKERS=8` (the production seq cap), `SOAK_MIN=150`. The two new kinds are a 4k fixed-length generation with
thinking off and a 96k needle prompt, so the fat-expert prefill runs under concurrency next to interactive traffic.

Run 1 (07:53Z-09:17Z, receipt lost, log in `evidence/soak-crash-20260903T0912Z/`):

- 78 minutes clean: 8 running / 0-2 waiting, KV 8-13 %, clocks 2431-2548 MHz busy, MemAvailable flat (r0 7.5 GiB,
  r1-r3 13.1 GiB), swap flat, no NVRM lines, ~815 requests, 0 errors.
- 09:12:34Z the rank-0 TP0 worker stopped returning from a step that mixed the 96k prompt's chunked prefill (24256 of
  ~96k computed, 1984 tokens scheduled) with five spec-decode requests (4 tokens each). Generation throughput fell
  9.9 -> 2.5 -> 0 tok/s over 20 s, the engine logged "No available shared memory broadcast block found in 60 seconds"
  four times, and at 09:17:33Z raised `TimeoutError: RPC call to sample_tokens timed out` and died. All four GPUs sat at
  96 % utilisation with 2.5 GHz clocks for the whole five minutes (a spin, not work). Ranks 1-3 logged nothing until
  rank 0's TCPStore vanished; no NVRM, Xid, OOM or thermal message on any node. About a hundred earlier 96k requests
  of the same run had completed, so the trigger is not "a 96k prompt" but a specific batch composition or a race.
- The watchdog noticed at 09:18:07Z and ran stop -> preflight, and **preflight failed on the clock gate**: with the
  containers removed every GB10 idles at 208 MHz / 0 % utilisation, and the session-4 gate had no utilisation condition.
  It fell back to TP2 as designed and paused. Fixed the same hour: preflight and the watchdog judge only a busy GPU
  (`CLOCK_MIN_UTIL`, 20 %), which is also the only state in which a reboot-capped clock is detectable.

Run 2 (`TP4-soak150b`, 09:51Z-10:22Z, identical mix, after the cutover back to TP4): same hang after 31 min, again ~80 s
after a 96k prompt was admitted (`Avg prompt throughput: 9688 tokens/s` is the 96k prompt being scheduled), this time
with the 96k request at 89280 computed tokens and six decode requests in the batch. The fixed watchdog recovered TP4
on its own: death 10:27:24Z, recovering 10:28:25Z, preflight PASS on idle GPUs, HEALTHY after 509 s. Evidence in
`evidence/soak-crash-20260903T1022Z/`.

Run 3 (`TP4-soak150-fat0`, 10:53Z-11:28Z): the same quartet relaunched with `EXL3_FAT_KERNEL=0` (watchdog paused), same
mix. Hung after 35 min, 23 s after a 96k prompt was admitted. **The fat-expert kernel is not the cause.** Evidence in
`evidence/soak-crash-20260903T1128Z/`. Production was relaunched on `prod.env` (kernel on) afterwards.

What the three hangs have in common, and what they rule out:

| | run 1 | run 2 | run 3 |
|---|---|---|---|
| fat-expert kernel | on | on | **off** |
| minutes to hang | 78 | 31 | 35 |
| 96k prompt admitted -> stall | 13 s | 81 s | 23 s |
| 96k request computed tokens at death | 24256 | 89280 | n/a (engine stopped before the dump was captured) |
| other requests in the batch | 5 decode, 4 spec tokens each | 6 decode | 6-7 decode |
| GPUs during the stall | 4 x 96 % util, 21-23 W, clocks 2.5 GHz | same | same |
| last forward logged | all four ranks at the same call count | same | same |
| dmesg / NVRM / Xid | none | none | none |

- Every hang follows a 96k prompt within 90 s, while the 24k prompts of the same mix never hung in this or any earlier
  soak, and the 280k cold prefill of the validation suite (run alone, no decode traffic) never hung either. The trigger
  is a long chunked prefill sharing steps with 6-7 speculative-decode streams.
- The stop is symmetric: all four TP ranks log their last forward at the same call count, so it is a collective that
  never completes (or a step that never reaches its collective), not one rank dying first. Nothing is logged by any rank
  until the engine's own 300 s RPC timeout fires.
- Roughly 100 (run 1), 25 (run 2) and 30 (run 3) earlier 96k requests of the same runs completed, so it is a race with a
  per-request probability of a few percent, not a deterministic failure at a chunk boundary.
- Not yet isolated: the speculative decoder (DFlash2, 3 draft tokens) and the spinwait change of the c190db1 image
  (PR96). The next A/B is `SPEC_METHOD` off with the same mix, then the 493cb88 image; a targeted probe (96k prompts
  fired into 7 decode streams for 20 min) would make each A/B cheaper than a 150-min soak.
- Operationally the watchdog now turns this into a ~10-minute outage with automatic recovery; the gateway's own retry
  covers the rest. Until the cause is fixed, the recipe carries the caveat: prompts above ~64k at full concurrency can
  stall the engine.

Run 4 (`TP4-soak150-spec0`, 11:50Z-14:26Z, `evidence/soak-spec0-20260903/`): the same quartet relaunched with
`SPEC_METHOD=none` (fat kernel on, everything else `prod.env`), same mix, same 8 workers. **150 minutes clean**: 472
own requests (plus 33 gateway requests that arrived during the run), 0 errors, 115/115 needles found in the 24k and 96k
prompts, closing sanity block pass, MemAvailable flat on every rank (r0 11.4 -> 11.3 GiB, r1-r3 15.3 -> 15.1 GiB), swap
flat, busy clocks never below 2431 MHz, no NVRM line. `soak_report.py` on the receipt:

| kind | n | wall p50 / p95 s | TTFT p50 / p95 s | decode p50 tok/s | prefill p50 tok/s |
|---|---|---|---|---|---|
| short (64 tok) | 176 | 3.96 / 100.8 | 0.53 / 77.8 | 11.9 | - |
| coding (1536 tok, thinking) | 60 | 198 / 429 | 2.8 / 23.9 | 8.0 | - |
| medium_gen (1024 tok) | 61 | 130 / 422 | 0.32 / 35.1 | 7.9 | - |
| long_prompt (24k needle) | 58 | 18.5 / 130 | 18.1 / 125.6 | 12.8 | 1331 |
| long_gen (4096 tok) | 60 | 607 / 759 | 0.36 / 4.4 | 6.8 | - |
| long_prompt_96k (96k needle) | 57 | 84.2 / 213 | 83.2 / 207 | - | 1163 |

The tails are the mix, not a defect: with two 96k prefills in flight the 2048-token step budget is mostly prefill, decode
streams drop to 7-12 tok/s each and short requests queue behind the prefills (waiting reached 6). That is what the
production cap of 8 sequences looks like when several of them are 96k prompts; it is the price of chunked prefill at
this chunk size, and the reason `MIXED_PREFILL_CHUNK` stays `off` (the alternatives were measured in batch 1).

**Verdict: the DFlash2 speculative decoder is the trigger.** Fat kernel on/off made no difference (runs 1-3), the draft
off survived 150 minutes with the same prompts, workers and image. The failing step is a long chunked prefill scheduled
together with 6-7 draft-verified decode requests. What is *not* established: whether a shorter draft (`DFLASH_TOKENS=1`)
or a larger step budget changes the odds, and whether the 493cb88 image behaves the same (it was never soaked with 96k
prompts). Filed as a caveat, not a fix.

**Production decision (adopted in `prod.env`, live and watchdog-supervised since 14:30Z): `SPEC_METHOD=none`.** From the
batch-2 control (`mem080-nospec`, same image family): single-stream decode 22.6 instead of 28-37 tok/s, coding-mix
aggregate 45 instead of 60 tok/s, 8-stream aggregate 66 instead of 84 tok/s, first-token and prefill unchanged, ~1 GiB
more free memory per rank. The draft is pinned but inactive; re-enable with `SPEC_METHOD=dflash` after an upstream
fix and the same 150-minute soak.

**Correction, 15:01Z the same day.** The full validation suite run on the spec-off production (`TP4-spec0-full`, for a
like-for-like table) died in its cold phase: the 282k prefill, **running alone with the draft off** (scheduler dump:
`num_running_reqs=1`, 218624 of 282k tokens computed, 1792 scheduled), stalled ~165 s in and the engine died 300 s later
on the same `RPC call to sample_tokens timed out` (`evidence/soak-crash-20260903T1456Z/`). The watchdog recovered it
unattended (15:02Z -> healthy 15:12Z). So the draft is not the cause: **long chunked prefill on the c190db1 build can
stall on its own**, with a per-prefill probability that the draft and concurrency raise. Yesterday's two 282k passes and
this morning's ~150 completed 96k prompts were the other side of that coin. `SPEC_METHOD=none` stays in production as the
better-odds setting until the overnight runs answer the real question: run 5 is the 493cb88 image with the draft on
(never soaked this hard), run 6 is `DFLASH_TOKENS=1` on c190db1. If 493cb88 survives, the stall is a c190db1 regression
(PR77 fat kernel is already excluded; PR86 indexer workspace and PR96 spinwait are the candidates) and production goes
back to it with the draft, giving up the +17 % prefill and the 2.9 s coding first token for 32-37 tok/s decode and no
stall. If it hangs too, the problem is older than the E2 series and needs a vLLM-level fix; the watchdog is then the
production answer and prompts above ~100k should be kept off the shared engine.

Numbers the spec-off suite produced before it died (same suite as yesterday's table): coding first token behind the 24k
prefill 0.3 / 1.8 / 1.8 s (fresh prompts; fat+draft 2.9 s), coding 4-way per-stream decode 15.2 tok/s (3072-token answers
202 s instead of 146 s), 12k generation decode 22.7 tok/s (36.8), warm 12k follow-up 9.6 s with **no prefix-cache hits**
(3.1 s with hits yesterday: to be understood), 24k cron prefill 18.8 s TTFT (19.7).

Run 5 (`TP4-soak150-old493`, 15:44Z-18:19Z, `evidence/soak-old493-20260903/`): the **493cb88 image and root** (yesterday's
"old image": no E2 fat kernel, no PR86 indexer workspace, no PR96 spinwait) with the **draft on, 3 tokens**, otherwise
`prod.env` (0.75 / 8 / 2048 / mixed off / swappiness 60), same mix, same 8 workers. **150 minutes clean**: 468 own
requests (+15 gateway), 0 errors, 115/115 needles, sanity pass before and after, MemAvailable flat (r0 8.9 GiB,
r1-r3 13.3-14.3 GiB), busy clocks >= 2431 MHz, draft acceptance 0.43 over the run, 57 completed 96k prompts. The same
mix hung c190db1 with the draft on three times within 78 minutes and once with it off (a lone 282k prefill).

| kind | n | wall p50 / p95 s | TTFT p50 / p95 s | decode p50 tok/s | prefill p50 tok/s |
|---|---|---|---|---|---|
| short | 176 | 2.07 / 85.4 | 0.61 / 79.0 | 12.0 | - |
| coding | 60 | 175 / 363 | 0.92 / 82.2 | 9.8 | - |
| medium_gen | 57 | 150 / 404 | 0.58 / 7.3 | 6.8 | - |
| long_prompt (24k) | 58 | 22.5 / 87.7 | 22.1 / 85.0 | 13.0 | 1090 |
| long_gen (4096) | 60 | 676 / 851 | 0.57 / 75.6 | 6.2 | - |
| long_prompt_96k | 57 | 87.3 / 180 | 85.7 / 179 | 14.8 | 1128 |

Per-stream decode under this mix is prefill-bound (6-15 tok/s on either image, with or without the draft), so the
mix does not show the draft's single-stream gain; the validation suite does. What run 5 establishes is the location of
the stall: **it is a regression between 493cb88 and c190db1** (upstream PRs 77, 63, 86, 96 and whatever else landed in
that window), not an inherent property of the model, the draft, the fabric or the four-node TP job. The fat-expert
kernel (PR77) was cleared by run 3, so PR86 (indexer workspace opt-in) and PR96 (spinwait) are the candidates, plus the
vLLM build itself if it changed between the two images.

Run 6 (`TP4-soak150-tok1`, 18:29Z-20:26Z, c190db1 with the draft on and `DFLASH_TOKENS=1`, fat kernel on,
`evidence/soak-crash-20260903T1951Z/`): froze at 19:51Z, 82 minutes in (zero token growth, 21 W at 96 % utilisation,
`No available shared memory broadcast block` every minute from 19:52Z, client timeouts from 20:05Z). A shorter draft
does not change the outcome. **New in this run: the engine never died.** In the five earlier stalls the core raised
`RPC call to sample_tokens timed out` after 300 s and `/health` went dark; this time the core sat in the shared-memory
wait with no timeout for 35+ minutes while `/health` kept returning 200, so neither the chain nor the watchdog would
have noticed. The workload was killed by hand at 20:26Z (the chain's results file therefore says "survived" for tok1;
it did not). The watchdog now carries a liveness check for exactly this: requests running, token counters flat for
four intervals, and a probe completion failing twice in a row -> recovery. **Decision: `prod.env` returns to the 493cb88 image and root with the draft on
(3 tokens), no fat kernel**, the configuration of run 5; the overnight chain's final restore step launches it and resumes
the watchdog. c190db1 stays staged on every rank.

Validation suite on the final configuration (`TP4-final493`, 20:59Z-21:44Z on the live quartet, 493cb88 + draft 3,
`evidence/workload-TP4-final493-20260903T205905Z.json`; 2 foreign requests), against yesterday's two runs of the same suite:

| metric | old image, yesterday (`TP4-final`) | fat kernel + draft, yesterday (`TP4-fat`) | **493cb88 + draft, tonight** |
|---|---|---|---|
| 282k cold prefill | 1162 tok/s, 242 s | 1324 tok/s, 213 s | 1162 tok/s, 243 s |
| coding first token, 3 fresh prompts behind the 24k prefill | 0.5 / 2.2 / 2.2 s | 0.5 / 2.0 / 3.5 s | 0.5 / 3.9 / 21.5 s (the bimodal case landed) |
| coding 4-way aggregate | 62.6 tok/s | 63.2 tok/s | 59.7 tok/s |
| 12k generation decode | 32.2 tok/s | 36.8 tok/s | **41.0 tok/s** |
| warm follow-up on the 12k conversation | 8.6 s | 3.1 s | 6.6 s |
| cold: warm follow-up | 1.6 s | 1.0 s | 1.9 s |
| 30-min soak | 188 req, 0 err | 195 req, 0 err | 184 req, 0 err |
| soak short p50 / p95 | 0.54 / 9.3 s | 0.60 / 18.1 s | 1.14 / 7.3 s |
| soak aggregate gen | 53.7 tok/s | 56.7 tok/s | 52.2 tok/s |
| min MemAvailable | 6.4 GiB | 7.1 GiB | 8.3 GiB |

Same image and knobs as yesterday's first column; the 12k decode came out 41 instead of 32 tok/s (draft acceptance on that
prose run; the suite's single generation is a one-sample measurement), everything else within the usual spread. The
c190db1 prefill and warm-follow-up advantages are what the bisect is trying to win back.

### 2026-09-03/04, bisect of the c190db1 stall (`recipe/bisect/`, `evidence/bisect-20260903/`)

What actually differs between the 493cb88 and c190db1 images as our launcher runs them: the exl3.py overlay (PR77, 778
-> 1623 lines) with its compiled extension, and the PR86 indexer-workspace patch applied at build time to
`vllm/v1/attention/backends/mla/indexer.py` (a wrapper that is meant to return the stock expression unless
`GLM53_INDEXER_WORKSPACE=rightsize`). The spinwait change (PR96) is inert here: our launcher never runs
`patch_spinwait.py` and both images carry the stock `busy_loop_s = 1`. vLLM is the same build in both. Two derived
images were built in seconds on every rank from the staged c190db1 (`Dockerfile.noidx`: pristine `indexer.py` from the
493cb88 image; `Dockerfile.oldexl3`: the 493cb88 `exl3.py` over the new extension) and soaked with the same mix:

| run | image | fat kernel | draft | outcome |
|---|---|---|---|---|
| 8 | **c190db1 minus PR86** (`noidx`) | on | on, 3 | **150 min clean**: 488 req, 0 err, 121/121 needles, 96k prefill 1280 tok/s, acceptance 0.45, memory flat |
| 9 | c190db1 with the 493cb88 exl3.py, PR86 still applied (`oldexl3`) | n/a | on, 3 | stall at ~64 min (liveness probe failed 01:45Z, engine dead 01:47Z, 218k-token dump as before) |

**Verdict (withdrawn 2026-09-04 by run 11, see below): the PR86 indexer-workspace patch is the regression.** With it reverted, c190db1 keeps the E2 fat-expert
prefill kernel, the DFlash2 draft and the PR63 template fix and does not stall; with it present, even the old overlay
stalls. Why a patch that "returns the stock expression unless opted in" hangs a four-node TP job mid-prefill is upstream's
question (the wrapper changes how the sparse-indexer gather workspace is sized or cached on the prefill path; our stalls
were all inside long chunked prefills, at chunk boundaries, with all four ranks stopping on the same forward).

**Production decision (2026-09-04): `prod.env` pins `glm53-exl3-c190db1-noidx:candidate`** (tag; per-rank IDs differ
because each rank built it), the c190db1 root, `EXL3_FAT_KERNEL=1`, `SPEC_METHOD=dflash`, `DFLASH_TOKENS=3`. The
validation suite on it follows below. The 493cb88 image and root stay on every rank as the fallback.

Validation suite on the adopted build (`TP4-noidx-full`, 02:08Z-02:54Z 2026-09-04, `evidence/workload-TP4-noidx-full-20260904T020828Z.json`,
4 foreign requests), next to the two other configurations measured with the same suite:

| metric | 493cb88 + draft (last night's production) | c190db1 + draft, stock (stalls) | **c190db1 minus PR86 + draft (production now)** |
|---|---|---|---|
| 282k cold prefill | 1162 tok/s, 243 s | 1324 tok/s, 213 s | **1270 tok/s, 222 s** |
| coding first token, 3 fresh prompts behind the 24k prefill | 0.5 / 3.9 / 21.5 s | 0.5 / 2.0 / 3.5 s | **0.5 / 2.1 / 3.5 s** |
| coding 4-way aggregate | 59.7 tok/s | 63.2 tok/s | 62.5 tok/s |
| 12k generation decode | 41.0 tok/s | 36.8 tok/s | 32.6 tok/s (one `ignore_eos` sample; the 32-41 spread across runs is draft acceptance on the degenerate tail, see below) |
| warm follow-up on the 12k conversation | 6.6 s (5376 prefix tokens hit) | 3.1 s (8960) | 9.7 s (1792; hits = where the re-tokenised answer first diverges, see below) |
| cold: warm follow-up | 1.9 s | 1.0 s | 1.0 s |
| 30-min soak | 184 req, 0 err | 195 req, 0 err | **188 req, 0 err** |
| soak short p50 / p95 | 1.14 / 7.3 s | 0.60 / 18.1 s | 0.60 / 16.6 s |
| min MemAvailable | 8.3 GiB | 7.1 GiB | 8.4 GiB |
| 150-min varied soak at the seq cap with 96k prompts | clean | stalls 5/5 | **clean (run 8)** |

The adopted build keeps c190db1's prefill and first-token behaviour, the draft's decode, and passes the long soak. Two
numbers in this table are noisy by construction, and the 2026-09-04 variance probe below explains both: the 12k warm
follow-up re-prefills everything after the first token at which the model's own 12k `ignore_eos` output stops
re-tokenising identically (its first emitted end-of-turn), quantised to 1792-token KV blocks, so 3-10 s is 3-10k tokens
of re-prefill at ~1200 tok/s; and the single 12k generation's decode rate is the draft acceptance rate of whatever
degenerate text that `ignore_eos` run drifted into (0.32 -> 30 tok/s, 0.45 -> 36, code 0.88 -> 39).


### 2026-09-04, run 11: the confirmation that withdrew the bisect verdict

Before asking upstream to revert PR86 the verdict was checked. Code reading first: the c190db1 vs 493cb88 image diff of
`vllm/v1/attention/backends/mla/indexer.py` is exactly the PR86 patch; `GLM53_INDEXER_WORKSPACE` is unset in our
containers, so the patched `get_max_prefill_buffer_size` returns the stock `max_model_len * 40`; it is called once at
init (`models/glm5next/nvidia/attention.py:302`); the builder guard is skipped in stock mode; and the vLLM cache volume
holds no torch.compile cache that a source change could key. The patched file was therefore a runtime no-op, the
verdict rested on 5/5 stalls with it vs one clean run without it (about a 5 % chance of a fluke), and one more run was
needed. Run 11 (fresh boot of `glm53-exl3-c190db1-noidx:candidate`, identical mix, 07:06Z start): **stall at 24 min**,
engine dead 07:30:56Z after the 300 s RPC timeout, the fatal step scheduling chunk offset 84864 of a 96k prompt (1984
tokens) beside three decodes with 3 draft tokens each; ranks 1-3 at 96 % utilisation and ~21 W until removed. The
verdict is withdrawn on PR #115 and the prepared upstream change (fork branch `indexer-workspace-opt-in-apply`) was not
submitted.

Fatal-step scheduler state across all six engine dumps so far: always a chunk of a long prompt's chunked prefill,
`num_computed_tokens` 24256 / 53809 / 84864 / 89280 (96k prompts) and 218624 (lone 282k), chunk 1984 or 1792, with 0-6
draft-carrying decodes in the same step. Stall times on the c190db1 family: 78, 31, 35, 82, 64, 24 min (7 of 8 runs; run
8 clean). Next: `recipe/freeze-catch.sh` (host-side py-spy on every rank at the shm-broadcast warning, 4 minutes before
the RPC timeout) armed under a further soak (`~/AI/repro.sh`), to read the stuck frames.

Runs 12a/12b (armed): 12a stalled **3 minutes** into the soak (07:47Z); the catcher fired on the shm-broadcast warning
and dumped every rank in 46 s, but `py-spy --nonblocking` cannot produce native frames, so only thread states came
back: on all four ranks the `VLLM::Worker_TP` thread was in state R (spinning on the CPU, not blocked in the kernel), the
engine-core and API processes in S. py-spy `--native` from the host fails on this platform ("Failed to get stack
traces"); gdb from the host works (all threads, symbols), so the catcher now runs gdb every round (`thread 1` + all
threads). 12b under that catcher: **150 min clean**, 507 requests, 0 errors, 125/125 needles, 96k prefill 1293 tok/s,
spec acceptance 0.45, running max 8, min free 8.8 GiB (`evidence/workload-TP4-soak150-repro-20260904.json`; 20 foreign
requests reached the quartet directly during the run). Tally on the c190db1 family: 9 runs, 7 stalls at 3-82 min, 2
clean; the stall is a per-run race. The catcher stays in the recipe (`~/AI/repro.sh` on rank0 re-arms it) for the
next stall.

### 2026-09-04, the two noisy suite numbers, measured (`recipe/variance_probe.py`, `evidence/variance-20260904/`)

Run on the production quartet (noidx build, 4 h up) with the gateway moved to the MSI pair, 06:14Z-06:48Z, one request
at a time. Every generation asks for `return_token_ids`; the follow-up history is tokenised with `/tokenize` and compared
token by token with the sequence the engine actually produced, so the first divergence is a number, not a guess.

**Warm follow-up TTFT.** TTFT = (prompt tokens - prefix-cache hits) / ~1150 tok/s, and hits = the first divergence
rounded down to a 1792-token KV block (the hybrid model's block; the suite's historical hit counts 1792/3584/5376/8960
are all multiples of it).

| conversation | answer tokens | first divergence | hits | follow-up TTFT | re-prefill rate | what diverged |
|---|---|---|---|---|---|---|
| 12k `ignore_eos` novel #1 | 12288 | 9070 | 8960 | 3.0 s | 1133 tok/s | the model emitted `<|user|>` (end of turn) at 9070 and kept going; the text sent back has no such token |
| 12k `ignore_eos` novel #2 | 12288 | 7906 | 7168 | 4.3 s | 1220 tok/s | same text on both sides, tokenised differently (`-up novel ends well`) |
| natural 6000-token essay | 6000 | none | 5376 | 0.9 s | (tail block + question) | whole answer round-trips; only the partial last block is re-prefilled |
| natural 3764-token essay | 3764 | none | 3584 | 0.6 s | | same |
| 6000-token essay that degenerated (`He did not answer. He (`) | 6000 | 3914 | 3584 | 2.3 s | 1067 tok/s | non-canonical tokenisation inside the loop text |
| thinking on, 3072 tokens of reasoning | 3072 | at `<think>` | 0 | 0.24 s (58-token prompt) | | the template renders history as `<think></think>` + content; the reasoning is never reusable |

So the suite's 3-10 s is the `ignore_eos` artefact: the follow-up pays for everything after the point where the model
would have stopped, and that point moves from run to run. Real conversations with thinking off reuse the whole previous
answer minus at most 1791 tokens; with thinking on they reuse the user turns only (template design, not an engine bug).
The engine's re-prefill rate is steady. Sending the same follow-up a second time hit 8960 again for novel #1 and 10752
for novel #2: the re-prefilled tail is not always retained, minor and not pursued.

**Single-stream decode.** Identical prompt, temperature 0, seed 42, 4096 or 12288 tokens with `ignore_eos`, engine idle.
Clocks 2411-2548 MHz on all ranks throughout, ~42 W busy, <= 76 C, no swap, free memory flat.

| sample | tokens | draft acceptance | decode | 512-token windows |
|---|---|---|---|---|
| novel 12288 #1 | 12288 | 0.408 | 34.1 tok/s | 30.5-38.5 |
| novel 12288 #2 | 12288 | 0.412 | 34.6 tok/s | 31.2-39.7 |
| novel 4096 #1 | 4096 | 0.323 | 30.4 tok/s | 28.4-33.4 |
| novel 4096 #2 | 4096 | 0.451 | 35.8 tok/s | 30.9-39.5 |
| novel 4096 #3 | 4096 | 0.389 | 33.1 tok/s | 31.0-35.1 |
| novel 4096 #4 | 4096 | 0.602 | 21.4 tok/s | contaminated: a foreign 36k-token request ran alongside (someone reached the quartet directly while the gateway was on the pair); discarded |
| Rust B-tree, code | 4096 | 0.884 | 38.9 tok/s | 32.1-45.6 |

Decode is monotone in the acceptance rate of the DFlash2 draft: 0.32 -> 30, 0.39 -> 33, 0.41 -> 34, 0.45 -> 36, 0.88 -> 39 tok/s.
The same prompt at temperature 0 does not produce the same text past its natural end (the `ignore_eos` tail drifts
into different degenerate loops with different acceptance), which is exactly the suite's 12k sample, hence 32-41 across
runs. On real prose the draft accepts ~0.4 and decode sits at 33-35 tok/s; on code ~0.8-0.9 and 39-49 tok/s (the
thinking-on Go fix decoded at 49 tok/s). Nothing here is hardware or engine variance, and the lever for more decode is
acceptance (draft length, draft model), not clocks.
