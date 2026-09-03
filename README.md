# GLM-5.3-Flash EXL3 on four NVIDIA DGX Sparks (TP4), tuned for production

A complete, measured recipe for serving `Mia-AiLab/GLM-5.3-Flash-EXL3-TR3-4bpw` (GLM-5.3-Flash, 320B/18B-active MoE,
EXL3/TR3 4 bpw) with vLLM tensor parallelism across **four DGX Sparks (GB10)** over a ConnectX-7 RoCE fabric, with
1M-token context, DFlash2 speculative decoding, an OpenAI-compatible endpoint, a watchdog, and the benchmark and
autoresearch loop that produced the settings. Everything here was run on a real four-Spark cluster in September 2026;
every number links to a receipt in `evidence/`.

It builds on the MiaAI-Lab two-Spark recipe (`GLM-5.3-Flash-EXL3-2x-DGX-Sparks`): same image, same overlay patches,
same weights. This repository adds the four-node launch path, the production tuning and the evidence.

## What you get

| | untuned TP4 (recipe defaults) | tuned TP4 (settings) | **tuned + fat-expert kernel (this repo)** |
|---|---|---|---|
| single-stream decode, 12k prose generation | 35 tok/s (7 draft tokens, 30 % accepted) | 32 tok/s (3 draft tokens, 53 %) | 37 tok/s |
| 4 / 8 / 16 simultaneous generations, aggregate | queued above 4 | 89 / 128 / 131 tok/s | 99 / 132 / 131 tok/s |
| short question fired into a long generation, p50 | 330 s | 0.47 s | 0.47 s |
| 282k-token cold prompt: prefill | 731 tok/s | 1162 tok/s | 1324 tok/s |
| warm follow-up on that 282k context | 8.3 s | 1.6 s | 1.0 s |
| warm follow-up on a 12k conversation | 9.6 s | 8.6 s | 3.1 s |
| 30-min mixed soak, 4 workers: requests / errors | 66 / 0 | 188 / 0 | 195 / 0 |
| soak: aggregate generation tok/s | 18.4 | 53.7 | 56.7 |
| soak: short request p50 | 51 s | 0.54 s | 0.60 s |
| soak: min free host memory on a rank | 1.3 GiB | 6.4 GiB | 7.1 GiB |

Receipts: `evidence/workload-tp4.json` (untuned), `evidence/workload-tp4-final.json` (tuned settings, 493cb88 image) and
`evidence/workload-tp4-fat.json` (final: c190db1 image with the fat-expert kernel), same benchmark
(`recipe/glm_workload.py`), same prompt sizes, temperature 0, no foreign traffic during the final run.

## Hardware and wiring

- 4x NVIDIA DGX Spark (GB10, 128 GB unified memory each, sm_121a). The recipe was also validated with two MSI
  EdgeXpert GB10 nodes standing in for two Sparks (mixed vendors work; `RANK_DK` takes `sudo -n docker` per rank).
- ConnectX-7 fabric: this cluster uses a MikroTik CRS812 switch with a mix of 200G and 100G QSFP DACs. A switch-less
  ring also works (see alexellis' 4-Spark NVFP4 repo); what matters is that **every rank reaches every other rank on the
  fabric subnet** (`preflight` checks this) and that NCCL is pinned to the fabric port (`NETDEV`, `HCA`, per-rank RoCEv2
  GID resolved at launch).
- The management LAN is not used for anything but SSH from the head. On DGX Spark the "LAN" is often Wi-Fi: all four
  nodes here left the LAN together during an access-point blip while serving continued unaffected, so run the watchdog
  and the benchmarks on the head node, not from your laptop.
- Prefill on a 4-node TP job is fabric-bound: TP4 gained 29 % on a 282k prompt over TP2 (1162 vs 901 tok/s) while
  decode gained 45 %. The 100G links in this cluster did not change the result measurably.

## Pinned artefacts

| Item | Value |
|---|---|
| Model | `Mia-AiLab/GLM-5.3-Flash-EXL3-TR3-4bpw` @ `25a44fdbf16862a46b7cc9921142c6c81350af2f` (163.6 GiB) |
| Draft model | **off** (`SPEC_METHOD=none`) since the 2026-09-03 soaks, as a mitigation: long chunked prefills can stall all four ranks on this build (3/3 soaks with the draft on, once with it off, see below). Pinned for re-enabling: `incoai/GLM-5.3-Flash-DFlash2` @ `dc77ff1c99eeb2df044ee3d4f0094eb033fee410`, 3 tokens, draft TP 4 |
| Engine | vLLM `0.1.dev20051` + exllamav3 for sm_121a, as built by the MiaAI-Lab recipe Dockerfile |
| Upstream recipe commit | `493cb88` for the untuned/tuned receipts; `c190db1` (PR77 fat-expert prefill kernel, PR63 template fix) for the batch-3 receipts |
| Overlay + chat template | `overlay/` and `files/` of the same upstream commit, byte-identical on every rank (`ROOT`) |
| vLLM args | `--tensor-parallel-size 4 --nnodes 4 --quantization exl3 --kv-cache-dtype fp8 --max-model-len 1000000` + the knobs below (`recipe/node-launch.sh`) |

The image is not published by this repository. Build it on one node from the upstream recipe at the pinned commit
(`BUILD=1 ./start.sh`), `docker save | ssh docker load` it to the other ranks, and pin the image ID in `cluster.env`.
Check that the fat-expert kernel is in the image before enabling it:
```bash
docker run --rm --entrypoint sh <image> -c 'ls /opt/glm53/exl3-fat-kernel && grep -c EXL3_FAT /usr/local/lib/python3.12/dist-packages/vllm/model_executor/layers/quantization/exl3.py'
```

## Quickstart

On every rank: the upstream two-Spark recipe's prerequisites (Docker with the NVIDIA runtime, the image, both HF
snapshots in the HF cache, the overlay root, `/dev/infiniband`, a fabric address on the CX7 port). On the head, as the
same user, with an SSH key that reaches every rank:

```bash
git clone <this repo> ~/AI/tp4-recipe && cd ~/AI/tp4-recipe
cp cluster.env.example cluster.env && $EDITOR cluster.env      # ranks, image, paths, knobs
cd recipe
./tp4-cluster.sh ../cluster.env preflight   # fabric reachability both ways, images, GPU clocks
./tp4-cluster.sh ../cluster.env launch      # node-local launcher on each rank (no SSH inside the containers)
./tp4-cluster.sh ../cluster.env wait        # ~11 min to /health 200 (weights from page cache; ~20 min cold)
./tp4-cluster.sh ../cluster.env status
curl -s http://<HEAD_IP>:8890/v1/models
nohup ./tp4-watchdog.sh ../cluster.env > ~/AI/tp4-watchdog.out 2>&1 &   # relaunch on engine death, rate-limited
```

Stop with `./tp4-cluster.sh ../cluster.env stop`. A four-rank TP job does not survive the loss of one rank; the watchdog
restarts the whole quartet (and, if `TP2_*` are set, falls back to the incumbent two-node deployment when it cannot).
Touch `~/AI/tp4-maintenance` to pause it.

Benchmark the running service (results as JSON, compare two receipts with `--compare`):
```bash
./workload-run.sh mylabel                    # full suite incl. 30-min soak (~50 min)
python3 glm_workload.py --compare ../evidence/workload-tp4-final.json ~/AI/workload-mylabel-*.json
python3 loop_test.py                         # 40 real prompts, thinking off and on: length-capped answers, self-correction
```

## Tuning

The settings in `cluster.env.example` come from an autoresearch loop (`recipe/AUTORESEARCH.md`): one knob per
experiment, a fixed ten-minute production-mix benchmark after a full relaunch, a geometric-mean score over six
production metrics against a baseline, hard reliability gates (contract checks, cancel drain, needle, zero errors,
memory floor, no foreign traffic during the run, GPU clocks), keep-if-better. Twenty-four experiments over two days; the
full log with every receipt is in `evidence/autoresearch/` and `evidence/autoresearch-b3/`.

| Knob | upstream default | tuned | measured effect on TP4 |
|---|---|---|---|
| `GPU_MEM_UTIL` | 0.87 (0.85 is the most GB10 accepts) | **0.75** | 0.85 leaves 0.85-2 GiB host memory per rank and preceded two engine deaths (NVRM `NV_ERR_NO_MEMORY`); 0.75 keeps ~9 GiB free with no throughput cost, KV pool still >3M tokens; 0.70 adds 4 GiB and nothing else |
| `MAX_NUM_SEQS` | 4 | **8** | 8 streams: 84 → 135 tok/s aggregate, worst first token 27 s → 1.0 s. 16 streams add nothing (131 tok/s) |
| `DFLASH_TOKENS` | 7 | **3** (draft now off) | acceptance 30 % → 53 %, decode 27 → 31-33 tok/s on prose; 2 and 5 are worse; no speculation = 22.6 tok/s, which is what production runs since the hang below |
| `MAX_NUM_BATCHED_TOKENS` | 7168 (TP2) | **2048** | 1024 / 4096 / 8192 tested: cold prefill unchanged, 8192 puts the coding first token at 20 s under load |
| `MIXED_PREFILL_CHUNK` | skip | **off** | skip never prefills while anything decodes: a short question waited 330 s behind a long answer. off: 0.5-0.7 s, decode of the running request keeps ~90 % of its speed |
| `EXL3_FAT_KERNEL` | 1 (upstream, since c190db1) | **1** (image built from c190db1 or later) | uncached 64k prefill 1156-1182 → 1356-1361 tok/s (+16-18 %); coding first token 11-22 s → 2.9 s because the 24k prefill fired with the coding requests finishes sooner; decode, short latency, 8-stream throughput unchanged; replicated |
| `SWAPPINESS` (host `vm.swappiness`, set at launch) | 60 | **60** | 0 was tested (Tech2Wild's pin): one engine death in two runs, rank 1 logging NVRM out-of-memory at serving time; no metric better. Kept at the distribution default |

Batch 3 on the new image (scores against the shipped settings on the old image; `evidence/autoresearch-b3/`):

| run | 64k prefill tok/s | coding first token p50 | 4k decode | 8-stream agg | score |
|---|---|---|---|---|---|
| old image, shipped settings | 1156-1182 | 11.6-22 s | 31-33 | 133-138 | 1.000 |
| **fat kernel, shipped settings** (two runs) | **1361 / 1356** | **2.9 / 2.8 s** | 31.8 / 34.2 | 129 / 138 | **1.280 / 1.309** |
| fat kernel, batched tokens 7168 | 1451 | 11.5 s | 38.2 | 134 | 1.061 |
| fat kernel, batched tokens 4096 | 1383 | 6.1 s | 32.9 | 129 | 1.138 |
| old image, utilisation 0.70, swappiness 0 | 1191 | 21 s | 31.3 | 132 | 0.895 |

The coding first-token median is bimodal with fresh prompts (it depends on which request lands behind the 24k prefill),
so read it together with the prefill column: the kernel moves both, the other rows move only that column.

What did not matter or did not work: chunk size for prefill speed (fabric-bound), `MAX_NUM_SEQS` beyond 8 on EXL3
(decode saturates), and any single-run difference under 3 %: the replicate of the best setting scored 1.054 against a
first run of 1.079.


## The hang that a long soak found (2026-09-03)

Ten-minute benches and a 30-minute soak passed; a 150-minute soak at the 8-sequence cap with 96k prompts in the mix hung
the engine three times out of three (78, 31, 35 min). Each time a 96k prompt had just been admitted: its chunked prefill
shares steps with 6-7 speculative-decode streams, all four TP ranks stop at the same forward pass, the GPUs spin at 96 %
utilisation and ~21 W, and five minutes later vLLM dies on `RPC call to sample_tokens timed out`. Nothing in dmesg, no
NVRM or Xid line. Turning the fat-expert kernel off changed nothing; turning the DFlash2 draft off (`SPEC_METHOD=none`)
made the identical soak pass 150 minutes with 472 requests and 0 errors, and then the next 282k cold prefill, running
alone with the draft off, stalled the same way (218624 tokens computed). So the stall is in long chunked prefill on this
build; the draft and concurrency raise its odds. The recipe ships with the draft off as the better-odds setting
(single-stream decode 22-23 tok/s instead of 32-37, everything else unchanged) and with the watchdog as the real safety
net: it relaunches the quartet in about 10 minutes. Whether the 493cb88 image has the same stall is being soaked next. `soak_report.py` summarises a soak receipt;
`SOAK_KINDS`, `SOAK_WORKERS` and `SOAK_MIN` reproduce the mix (`workload-run.sh <label> warmup,sanity,soak,sanity_end`).

## Reliability notes

- **Memory regime.** GB10 has one memory pool. At 0.85 utilisation a rank sits at 1-2 GiB `MemAvailable` and the driver
  logs out-of-memory bursts; the engine hung twice in that regime (`RPC call to sample_tokens timed out`). The
  memory-floor gate (3 GiB) and 0.75 are the answer. Drop the page cache before every launch (the launcher does).
- **Swappiness.** Default 60 has every rank holding 3-4 GiB of cold pages in swap during serving. Set `vm.swappiness=0` and persist it
  in `/etc/sysctl.d/` only if your own measurements say so: on this cluster it produced the one engine death of the
  campaign and no gain, so the recipe sets 60 at launch.
- **GPU clocks.** A GB10 can come out of a reboot pinned at 600-700 MHz (Tech2Wild, 2026-09-02). `preflight`, the watchdog
  and the benchmark's per-phase probe read the sm clock; a run with a rank under 1000 MHz fails the `clocks` gate.
- **Thinking off and runaway answers.** `loop_test.py`, 40 prompts, 2048-token cap, run on the tuned config with
  both images (`evidence/loop-test/`): with thinking off, 1-2 of 40 ran away (the same 150-word monologue prompt was
  still going at 1000+ words when it hit the cap in both runs), a few more were flagged by the self-correction regex
  but read as normal answers; the rate matches Tech2Wild's 2 in 120. `reasoning_effort: low` does not remove it (3 of
  40 capped). With thinking on there is no leakage, but 7-10 of 40 hit the cap because the reasoning alone exceeded
  2048 tokens (narrative and prose prompts returned no answer text). So: route latency-sensitive traffic with thinking
  off and a `max_tokens` you can afford to lose to a runaway, watch `finish_reason: length`, and give thinking-on
  routes a large cap.
- **Foreign traffic.** A benchmark on a live gateway is worthless if other clients hit the engine; the benchmark compares
  the engine's success counter with the requests it sent and the `clean` gate rejects the run.
- **Fresh prompts or you measure the cache.** Every prompt in the benchmark carries a per-request salt; the prefix cache
  is measured only where a row says warm. (An earlier version of this benchmark reused fixed coding prompts.)
- **First-token latency under load is a scheduler policy, not a TP question.** See `MIXED_PREFILL_CHUNK` above.

## How this compares (same model, other quants, other kits)

| Setup | single-stream decode | cold prefill | 211-282k replay | notes |
|---|---|---|---|---|
| this repo, EXL3 TP4, 4 Sparks | 37 tok/s (prose) | 1324 tok/s @ 282k | 1.0 s | 1M context, 8 streams 132-138 tok/s |
| EXL3 TP2, 2 Sparks (Tech2Wild's lane, Reederey87 kit, k=7) | 62 counting / 20 prose | 1752 @ 211k | 0.8 s | 1M context, 1.4M-token KV pool |
| MiaAI-Lab EXL3 TP2, 2 Sparks (their README, E2 kernel) | 65 structured / 27 prose | +20 % over pre-E2 at 8k-300k | | 1M context |
| Tech2Wild NVFP4 TP2, 2 Sparks | 64 counting / 19 prose | 2763 @ 211k | 9.2 s | 262k context; more tokens per joule |
| tonyd2wild NVFP4 TP4, 4 Sparks + 200G switch | 54 (mixed) | 1863 warm @ 114k | 2.6 s | 64 seqs, 530 tok/s aggregate |

NVFP4 wins fresh prefill and scales further with concurrency on GB10; EXL3 wins cached context, 1M context on the same
memory, and mixed load. For agents that re-send long context every turn, that is the EXL3 case, which is why this
recipe is EXL3. Sources: Tech2Wild, "NVFP4 vs EXL3 on DGX Spark" (2026-09-02) and the repositories credited below.

## Files

| File | Purpose |
|---|---|
| `cluster.env.example` | All cluster identities and serving knobs. Copy to `cluster.env`. |
| `recipe/node-launch.sh` | Node-local rank launcher (runs on each rank through `bash -s`): verifies snapshot/image/fabric address, resolves the RoCEv2 GID, drops page cache, writes the in-container start script, `docker run`. |
| `recipe/tp4-cluster.sh` | Controller on the head: `preflight`, `launch`, `wait`, `status`, `stop`, `logs`. Environment overrides beat the config file (`GPU_MEM_UTIL=0.8 ./tp4-cluster.sh cfg launch`). |
| `recipe/tp4-watchdog.sh` | Supervisor: relaunch on engine death, TP2 fallback, rate limit, pause file, clock check. |
| `recipe/tp4-cutover.sh`, `recipe/tp4-rollback.sh` | Optional TP2 → TP4 cutover with benchmark gate and automatic rollback to the retained TP2 containers. |
| `recipe/glm_workload.py`, `recipe/workload-run.sh` | The production-mix benchmark (coding + cron mix, long generation, cold 280k prompt, concurrency ladder, cancellation, soak). |
| `recipe/glm_benchmark.py`, `recipe/compare_baseline.py` | The upstream-style contract + throughput suite and the pass/fail comparison used by the cutover. |
| `recipe/loop_test.py` | Thinking-off loop test (40 prompts, 8 categories, both modes). |
| `recipe/autoresearch.sh`, `recipe/autoresearch_score.py`, `recipe/experiments-batch*.tsv`, `recipe/AUTORESEARCH.md` | The tuning loop, the scorer and the queues that produced the settings. |
| `recipe/91-tp4-linklocal-address.template` | NetworkManager dispatcher that re-adds a rank's fabric addresses at link-up (identity-guarded). |
| `evidence/` | Every receipt referenced above (JSON + logs), including the frozen TP2 baseline and the autoresearch campaign. |

## Credits

MiaAI-Lab for the EXL3 two-Spark recipe, image and overlay this runs on; Brandon Music (ShapleyMcg) for the EXL3/TR3
checkpoint; turboderp for exllamav3; IncoAI for DFlash2; Z.AI for GLM-5.3-Flash; Tech2Wild/tonyd2wild for the NVFP4
recipes and the two-lane comparison whose measurement lessons (clocks, fresh prompts, run it three times) are applied
here; alexellis for the switch-less four-Spark wiring. Licences of the artefacts: `NOTICE.md`. This repository: MIT.
