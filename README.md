# GLM-5.3-Flash EXL3 TP4 sparse-attention recipe

This portable recipe captures one selected four-rank GB10 configuration: EXL3
with FP8 KV cache, DFlash2 at three speculative tokens, eager execution, and
a 64-row sparse-MLA attention slice. It launches a compatible, pinned upstream
image and runtime root; it is not a standalone vLLM distribution.

The selected run completed one bounded 153-minute mixed-load qualification.
It is not fresh-install, reboot, indefinite-stability, maximum-context, or
global-optimum evidence. See [EVIDENCE.md](EVIDENCE.md) and the
[machine-readable projection](qualification-attention64h16.json).

Older files under `evidence/` and the historical experiment queues describe
earlier configurations. Their successful short runs are not pooled into this
configuration's qualification; use the selected settings and commands below.

## Selected result

| Measurement | Result |
|---|---:|
| Accounted HTTP requests / errors | 484 / 0 |
| Mixed soak duration; requests; exact retrieval | 153.0 min; 371; 93 / 93 |
| Coding aggregate completion | 60.21 tok/s |
| Natural 4,096-token long-generation decode | 31.09 tok/s |
| Cold prefill, 283,572-token prompt | 1,054.4 tok/s |
| Peak concurrent aggregate completion | 133.72 tok/s |
| Mixed-soak aggregate completion | 34.62 tok/s |
| Mixed short-request wall / first-token p95 | 8.84 s / 1.535 s |

These are fields from one isolated qualification receipt. They are not a
cross-project benchmark and should not be compared with differently quantized
models, engines, prompt sets, context sizes, or soak durations.


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

The native threshold screen is the provisional balanced candidate for a
separate fixed 20-minute matched mixed-tail screen. The score is a weighted
tradeoff, not a throughput result. The 128 screen had a better tiny cold-tail
observation but worse cron latency. Tiny-tail samples were n=3, and none of
these screens proves a global optimum, a promotion, or long-run candidate
reliability. The matched screen requires zero errors and foreign requests, at
least 20 short probes, at least a 20% p95 gain, and no more than a 5% aggregate
throughput loss. The selected default remains eager execution with mixed
prefill off.

## What the patch addresses

Earlier unsliced graph and fully eager candidates both stalled. In the decisive
capture, one rank held a single resident
`sparse_mla_prefill_kernel<...,16,2048,64>` block at the same program counter
across three samples while TP peers waited in NCCL. Directional state had
incoming data and peer credit, but no outgoing GPU data publication from that
rank. That localizes the observed progress frontier to the GPU/kernel path; it
does not identify an internal race or prove a general FlashInfer defect.

The patch splits only the final guarded sparse-attention call into rows of at
most 64 tokens, preserving the outer 2,048-token scheduler batch, weights,
drafter, cache layout, fabric and upstream overlays. It accepts one source
SHA-256 and produces one patched SHA-256; it rejects any other backend or
geometry. Independent numerical checks covered 1, 64, 65, 129, 193 and 2,048
token rows, including ragged/empty rows, then the selected configuration
completed the bounded qualification.

An independently pinned report describes a similar H16 prefill-kernel wedge,
but it is not maintainer confirmation of the same cause here. Its workaround
is not public source-equivalent. The public Jasl Triton code is also not a
drop-in replacement: its packed FP8/scales and head handling differ from this
GLM `fp8_ds_mla` cache, so it would need a GLM adapter and new numerical and
all-rank qualification. See [Mark Sunner's pinned evidence](https://github.com/marksunner/glm52-dgx-spark-deadlock-evidence/tree/32133d5ef0e4dde00d1da15d639c8a71c92f75f8)
and [Jasl revision `2dd63d85`](https://github.com/jasl/vllm/tree/2dd63d85f4133cf98721ecf6a8d373e4c1dc356f).

## Pinned inputs and effective runtime

| Input | Pin |
|---|---|
| EXL3 checkpoint | [`Mia-AiLab/GLM-5.3-Flash-EXL3-TR3-4bpw`](https://huggingface.co/Mia-AiLab/GLM-5.3-Flash-EXL3-TR3-4bpw) @ `25a44fdbf16862a46b7cc9921142c6c81350af2f` |
| DFlash2 drafter | [`incoai/GLM-5.3-Flash-DFlash2`](https://huggingface.co/incoai/GLM-5.3-Flash-DFlash2) @ `dc77ff1c99eeb2df044ee3d4f0094eb033fee410` |
| Upstream image source / overlays | [MiaAI-Lab recipe](https://github.com/MiaAI-Lab/GLM-5.3-Flash-EXL3-2x-DGX-Sparks/tree/c190db1ae17ba8dff20129ed1f308d10c63cf37d) @ `c190db1ae17ba8dff20129ed1f308d10c63cf37d` |
| Effective engine build | `vllm-0.1.dev20051+g487ecf187-tp4-d6e0b989` |
| Runtime libraries | FlashInfer 0.6.17; Torch 2.13.0+cu130; CUDA runtime 13.0.96; NCCL 2.30.7; Triton 3.7.1 |
| Driver / CUDA observation | NVIDIA 580.173.02; CUDA driver API 13000; CUDA runtime 13.0.96 |
| Selected chat template | `recipe/chat_template-20260904.jinja` SHA-256 `bdc5009ef6024a700f2ab2b8caefb14d083f504cf8d2ce70caa7e459b01cc331` |
| Sparse MLA patch | `recipe/patch_sparse_mla_slice.py`: source `d665ef…cd01`; patched `f1854c…620c6` |

The selected template is an explicit local replacement, not the unmodified
template from upstream `c190db1`. Qualification used independently built image
digests on each rank; their source and patched-backend hashes matched. Use a
compatible image digest on each of your ranks, then verify the hashes at
startup. Do not infer that one image digest was used everywhere in the source
qualification.

The root model is [GLM-5.3-Flash](https://huggingface.co/zai-org/GLM-5.3-Flash).
Read [NOTICE.md](NOTICE.md) before serving: DFlash2 makes the selected stack
non-commercial under its published terms.

## Prepare, configure and launch

Follow [REQUIREMENTS.md](REQUIREMENTS.md). Build or obtain the upstream image
at the pinned revision, retain its matching `overlay/`, and cache the pinned
model snapshots on each rank. Configure a site-specific file from the exported
repository root:

```bash
cp cluster.env.example cluster.env
$EDITOR cluster.env
CFG="$(pwd)/cluster.env"
source "$CFG"
```

Install the selected template into each rank's configured runtime root before
launch. Repeat this step on every rank, using that rank's actual `ROOT`:

```bash
install -m 0644 recipe/chat_template-20260904.jinja "$ROOT/files/chat_template.jinja"
shasum -a 256 "$ROOT/files/chat_template.jinja"
```

On the controller, from the exported repository root:

```bash
./recipe/tp4-cluster.sh "$CFG" preflight
./recipe/tp4-cluster.sh "$CFG" launch
./recipe/tp4-cluster.sh "$CFG" wait
./recipe/tp4-cluster.sh "$CFG" ready
python3 recipe/functional.py http://YOUR_HEAD_IP:8890/v1
CFG="$CFG" SOAK_MIN=150 SOAK_WORKERS=4 LONGGEN_TOKENS=4096 \
  SOAK_LONGGEN_TOKENS=4096 COLD_TOKENS=280000 CONC_LEVELS=4,8 \
  SOAK_KINDS=short,coding,medium_gen,long_prompt,short,long_gen,long_prompt_96k,short \
  ./recipe/workload-run.sh attention64h16 \
  warmup,sanity,coding,longgen,cold,conc,cancel,soak,sanity_end
```

`tp4-cluster.sh` sources its config before locating its own directory, while
`workload-run.sh` and `tp4-watchdog.sh` change into `recipe/` first. Use an
absolute `CFG` path for all three. `functional.py` posts to
`/chat/completions`, so it requires the `/v1` endpoint root.

`cluster.env.example` enables the selected patch. Its controller streams the
installer into each fresh container. Do not set
`VLLM_SM120_SPARSE_MLA_SLICE_TOKENS=64` for another image revision: the patch
rejects a different preimage.

## Operation and recovery

The exported watchdog checks all rank containers and real inference rather
than trusting `/health` alone. It restarts the retained four-container group,
then requires all-rank readiness. It rate-limits recovery and pauses on a
recovery failure or limit; it does not create an image from changed inputs.

```bash
CFG="$(pwd)/cluster.env"
nohup ./recipe/tp4-watchdog.sh "$CFG" > tp4-watchdog.out 2>&1 &
```

Use `touch "$MAINT"` after sourcing the configuration to pause a configured
watchdog before planned work, and use the same absolute `CFG` with
`./recipe/tp4-cluster.sh "$CFG" stop` to remove a test group. Define an
automatic fallback only after qualifying it independently. The included
`autoresearch_score.py` preserves the frozen receipt scorer used to assess
workload outputs; do not compare results without its hard gates.

Run the complete local suite without GPUs or model access:

```bash
python3 -m unittest discover -s tests -v
```

These 12 tests check scoring rejection and cancellation deadlines. They do
not establish model readiness, numerical equivalence, or sustained reliability.

## Rejected graph mode and fabric boundary

Graph mode is not selected: its full trial completed the frozen model suite,
but had a worse mixed short-request tail and failed its service handback. The
eager choice favors the measured interactive tail; it does not claim that
graphs are generally slower. Coordinated PFC/ECN and dual-HCA/four-channel
configuration improved the fabric baseline, but the surviving captured
progress frontier was in the H16 prefill kernel with clean monitored transport
deltas. That evidence does not support replacing the switch as a remedy.

## Related sources and non-equivalent alternatives

- The original [MiaAI-Lab TP2 recipe at `c190db1`](https://github.com/MiaAI-Lab/GLM-5.3-Flash-EXL3-2x-DGX-Sparks/tree/c190db1ae17ba8dff20129ed1f308d10c63cf37d) is implementation provenance, not a validated four-rank switched-RoCE replacement.
- [NCCL issue #2353](https://github.com/NVIDIA/nccl/issues/2353) was reviewed and withdrawn as a candidate: this run used 2.30.7 and did not show that issue's required `ncclLocalOpAppend` / sleeping-proxy signature. [NCCL #2334](https://github.com/NVIDIA/nccl/issues/2334) remains only a topology/workload analogy.
- The fabric campaign used RouterOS and switch-marvell 7.23.5 with coordinated PFC/ECN. The vendor [RouterOS changelog](https://mikrotik.com/download/changelogs) documents release provenance; no cable or link-rate change is credited, and fabric changes did not by themselves explain the surviving kernel frontier.
- [tonyd2wild's pinned NVFP4 forensic note](https://github.com/tonyd2wild/GLM-5.3-Flash-NVFP4-1M-KV-4x-DGX-Spark/blob/8fd2fcd27c04c7fa93e770000b818657f338875d/docs/SM121-CRASH-FORENSICS-2026-08-27.md) retracts an earlier hardware-memory-ceiling diagnosis. Its NVFP4/Marlin stack is not comparable to this EXL3/vLLM result.
- [cfontes' original NVFP4 card](https://huggingface.co/cfontes/glm-5.3-flash-dflash2-tp4/tree/9417f8e107bf373750fd9b1cadaf9a8a70d530d9) and [later drafter card](https://huggingface.co/cfontes/GLM-5.3-Flash-DFlash2-TP4-Spark/tree/fe78d4cebbaebb3744acdde6311df91b3377e2fb) use different weights; the later card reports no served acceptance gain in its rechecks.
- [Pinned SGLang TP4 recipe](https://github.com/joesinvestments/GLM-5.3-Flash-FP8-4x-DGX-Spark/tree/880efbc7793d06a21908afd590d34cd59ca2e00b) is a credible native-FP8 alternative, but differs in engine, weights, speculation, context limit and validation duration. It needs a fresh matched qualification.
- [NVIDIA DGX Spark clustering](https://docs.nvidia.com/dgx/dgx-spark/spark-clustering.html), [vLLM](https://github.com/vllm-project/vllm), and [FlashInfer](https://github.com/flashinfer-ai/flashinfer) describe the upstream platform components.

The subsequent matched 20-minute comparison reduced short completion p95 from
19.573s to 7.030s with mixed output throughput essentially unchanged (50.70 vs
50.77 tokens/s). First-token p95 increased. This earns longer qualification;
it does not change the selected default. See [the detailed result](EVIDENCE.md#matched-20-minute-workload-result).
