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
