# Runtime requirements

This recipe is a launcher around a compatible upstream runtime. Before using
it, prepare the following on every rank:

1. Four compatible GB10-class nodes with NVIDIA drivers, Docker plus the
   NVIDIA container runtime, a reachable IPv4 RoCEv2 fabric, passwordless SSH
   from the controller, and `/dev/infiniband` available to Docker.
2. A recorded compatible image digest on every rank, built from the upstream
   source pinned in [README.md](README.md#pinned-inputs-and-effective-runtime). The source
   qualification recorded independently built per-rank local image IDs, not one
   common registry digest; all ranks had the same accepted sparse-MLA source and
   patched hashes. The patch refuses every other preimage.
3. The same upstream checkout on all ranks at `c190db1`, with its `overlay/`
   directory. Install this export's pinned
   `recipe/chat_template-20260904.jinja` as `files/chat_template.jinja` on
   every runtime root; the selected template is not the unmodified upstream
   template. `node-launch.sh` mounts and executes the required upstream overlay
   patches; this export does not substitute or recreate those dependencies.
4. Local Hugging Face cache snapshots for the pinned EXL3 checkpoint and
   DFlash2 drafter. The launcher runs offline and fails closed when either
   selected snapshot is absent.
5. A per-site `cluster.env` based on `cluster.env.example`, with actual
   interfaces, HCA names, rank addresses, image digests and paths.

The selected dual-HCA setup requires an IPv4 RoCEv2 GID for both named HCAs in
`NCCL_IB_ADDR_RANGE`. Align host and switch PFC/ECN and the data/control QoS
priorities before preflight. The launcher verifies GID availability and
connectivity, but does not install network QoS policy.

For the example address range, use primary addresses `10.0.0.1` through
`10.0.0.4` and secondary addresses `10.1.0.1` through `10.1.0.4`, adapting
both to your network. On Spark, the two logical HCAs can address the same
physical QSFP port through separate PCIe links. The selected data class uses
DSCP 26 / PCP 3 with PFC priority 3 and ECN; control uses DSCP 48 / PCP 6.

Run `./recipe/tp4-cluster.sh cluster.env preflight` before launch. The
preflight confirms all-rank reachability and image presence; it cannot prove
that a different image, driver, fabric or model revision will reproduce the
reported result.

## TP4 slice compatibility and startup

The selected recipe requires `VLLM_SM120_SPARSE_MLA_SLICE_TOKENS=64` with
TP4's BF16 query layout `[T,16,576]`. The node launcher rejects `TP_SIZE != 4`
with slicing enabled before any Docker or host configuration action. The
backend guard also checks dtype, head count, packed KV layout, sparse top-k
and workspace size; its combined error is not necessarily a dtype defect.
The controller remains four-rank only. The node launcher's generic `TP_SIZE`
and `NNODES` parameters do not qualify a TP2 derivative. Use a separately
qualified TP2 recipe rather than weakening the H16 guard.

Value `0` disables slicing and is retained for controlled diagnostics. It is
**not the supported Native1024 configuration**: the workaround was selected
following large-prefill progress failures. Neither value is evidence of
universal startup reliability. The installer changes an attention-forward
call, not NCCL communicator initialization. A final PYNCCL log line followed
by silence does not establish that slicing caused an initialization stall.
A retained September 9 restoration with slicing **64** logged TP communicator
initialization at 11:49:13 and EP initialization at 11:54:14, then checkpoint
loading and successful readiness. Compare timestamped logs from all ranks,
container state and elapsed time before attributing a similar pause. The
launcher's normal wait budget is 1,800 seconds; do not wait through a reported
rank failure.

## Recorded Native1024 build identities

The following local Docker **image IDs** were recorded before and after the
September 6–7 full qualification. They are content identities of the retained
builds, **not pullable registry manifest digests**. We have not published a
single qualification image or established that rebuilding upstream `c190db1`
reproduces these bytes. A mutable `:exl3` tag, a common service fingerprint,
or the sparse-MLA preimage check alone cannot establish that the complete
runtime is identical. A registry manifest digest and local Docker image ID
are different identifiers; compare each to the corresponding field.

| Rank | Recorded local Docker image ID |
|---|---|
| 0 | `sha256:cca6a44bd97e784147e85e05b2cc79c256a7367a447aafcb3dca84ddc326d835` |
| 1 | `sha256:453a412090e08f4073a3d0c416a69fc94cc9e5d0a745785e60f5557962f32371` |
| 2 | `sha256:c07e5325c6b1f099942668b99eafbc9e044d7341cb0bb3ea53c48bcff5661122` |
| 3 | `sha256:7dfd75aabf1ac5e2a2e17068a1cd9bf96aa11af5cf7534e3811cc5130549f7c5` |

These IDs come from the candidate **before/after qualification** snapshots,
not an earlier baseline or a later challenger. The controller receipt SHA-256
is `dd951c662d5ec24575916192be92a5ed3e00aa17007e71c37707d45a1a23f709`.
The [README pins](README.md#pinned-inputs-and-effective-runtime) identify the
model, drafter, upstream root, template and library versions. All four ranks
recorded sparse-MLA backend SHA-256
`f1854c0cce9d749d5ce67dd5132f6541c9d01dec4c2bb414267815ed6fd620c6`.
That is a hash of one backend file, not the whole image or native extension.


<details>
<summary>Mounted overlay and template SHA-256 manifest (same on all four ranks)</summary>

| File | SHA-256 |
|---|---|
| `ablit_runtime.py` | `1b4b97902189e4aacf3d2c2b2edfacc0c9dadc6c5885c782b4d65827fd631e65` |
| `chat_template.jinja` | `bdc5009ef6024a700f2ab2b8caefb14d083f504cf8d2ce70caa7e459b01cc331` |
| `patch_ablit.py` | `cd7fef3e0a236605941d7f34217f2de4026de6e1b256ffafe772bb421c2f5e40` |
| `patch_glm5_drafter_group.py` | `1835bfbd64fbb5f063a1c9d5ea2d70cc3558312f45d4470a58d00c2e24b3806e` |
| `patch_glm_video_placeholders.py` | `60c1ae1df640cf9d332299d6bbc4378a1e7ca929cad146f47d6dc68c4a4f67ee` |
| `patch_hybrid_prefix_hit.py` | `bce9e20e67dc71d968cd8519a9eadd4f889d35e3ec7584ba8e9f8430ccdcee52` |
| `patch_kpool_tail_slotmap.py` | `601e17ace90ce6945d29edf0b994b91fa6c9cbb389774fd0f1252f31a19c26bd` |
| `patch_scheduler_decode_floor.py` | `0e117f2c8210d674e79d98a34a26e8b5dc6f956bfb566e3cd3a830b37f6e76de` |
| `patch_suppress_stops_in_reasoning.py` | `14602ea4350bad1eb8a6e76de3e17e2d5ef1229340bcd199351e20334f5e15d7` |
| `patch_xgrammar_termination.py` | `e6e5928eaf74dbb6bf0e9511105987c9f74848278c2cba7c28c506ca58ee90f5` |

</details>

The original qualification receipt did not separately capture a compiled
EXL3 extension checksum or complete source-to-image build attestation. Those
are missing provenance, not values inferred from the sparse-MLA checksum.

The selected configuration explicitly enables the **E2 EXL3 fat-expert
path** with `EXL3_FAT_KERNEL=1`. Retained qualification logs report
`configured_tier=kernel`, `effective_tier=kernel` and active
`exl3_fat_gemm` / `exl3_fat_gemm_scatter` symbols and counters. The launch
configuration did not enable `GLM53_ADAPTIVE_K` or `GLM53_DENSE_FP8`.
FP8 KV cache is distinct from dense-weight FP8. Mia's later E3 opt-in
measurements are a different recipe, not the origin of our 33.56 tok/s result.

The September 5 fleet inventory recorded driver `580.173.02`, kernels
`6.17.0-1029-nvidia` on ranks 0/1 and `6.17.0-1031-nvidia` on ranks 2/3.
This is a dated inventory, not a simultaneous kernel capture for every later
benchmark. A September 7 qualification hardware sample observed SM clocks
2457 / 2496 / 2502 / 2483 MHz, with reported maximum 3003 MHz on each rank.
A reported maximum is not a clock-lock setting. The published launcher and
preflight do not lock clocks; preflight observes clocks under load. These
samples do not establish a kernel-version regression or a universal clock
recommendation.

## Exact long-generation measurement

The full qualification's primary long-generation request was:

```json
{
  "model": "GLM-5.3-Flash-EXL3",
  "messages": [
    {
      "role": "user",
      "content": "Write an extremely long, continuous technical novel about a team bringing up a four-node inference cluster (working title 20260906T230407Z). No headings, no lists, just prose."
    }
  ],
  "max_tokens": 4096,
  "temperature": 0,
  "seed": 42,
  "stream": true,
  "stream_options": {
    "include_usage": true
  },
  "chat_template_kwargs": {
    "enable_thinking": false
  },
  "ignore_eos": true
}
```

`reasoning_effort: low` alone does **not** disable thinking in the selected
runtime/template. Set `chat_template_kwargs.enable_thinking=false` explicitly
and record whether reasoning output was emitted. Benchmark the physical
endpoint: a gateway profile may override request defaults.

The primary response recorded 4,096 completion tokens, zero reasoning
characters, 122.246 seconds wall time and 0.218 seconds TTFT. The frozen
runner measures decode as `(completion_tokens - 1) / (finish - first_token)`:
**33.56 tok/s**. Using completion tokens divided by request wall time gives
**33.51 tok/s**. TTFT accounting therefore cannot explain a multi-fold gap
for this particular request. Stream chunks are not token counts.

The full `longgen` phase also injects three short requests at 5/20/40 seconds,
then performs a follow-up with another three short probes. A primary-only
request, another essay prompt, a different output length, or reasoning-enabled
output is not the same phase. The qualification followed warmup, sanity and
coding; a short isolated diagnostic is not a rerun of that full qualification.
The original working-title salt above identifies the request; reuse can hit
prefix cache. Use fresh salts and report cached prompt tokens when comparing
cold behavior.

For a short diagnostic on an already ready, otherwise idle endpoint, the
existing frozen runner can be restricted to these phases (no soak):

```bash
GLM_URL=http://YOUR_HEAD_IP:8890/v1 GLM_LABEL=issue1-short \
  GLM_OUT=issue1-short.json LONGGEN_TOKENS=4096 \
  GLM_PHASES=warmup,sanity,longgen,sanity_end \
  python3 recipe/glm_workload.py
```

This command generates a fresh salt and includes the phase's short probes.
It sends real requests; run it only on an endpoint you intend to benchmark.
Its partial-phase receipt is diagnostic and cannot pass full qualification.
The frozen runner SHA-256 is
`d902aa45bbadb5e3f55cc25b21c1c00f44991ad82da058e8a55c7d56c1e98638`;
`deadline_http.py` is
`ba5ac22bca48473fa23f8da60741d9790ae6ec12df36eb9be6fed40ccda0a8af`.

## Reporting a reproduction gap

Please share a small, sanitized text/JSON comparison rather than a replacement
image or executable reproduction bundle:

- Exact request body and model endpoint type; completion-token count, wall
  time, TTFT, reasoning-output count, cached prompt tokens and per-run results.
- Per-rank Docker image ID **and** registry RepoDigests when present, upstream
  checkout revision and dirty status, mounted overlay/template SHA-256 values,
  installed EXL3 implementation/native-extension hashes and sparse-MLA hash.
  Record effective serve arguments and only relevant recipe/NCCL environment
  values; avoid whole environment dumps containing credentials.
- Timestamped startup and request-window logs from all ranks, including E2
  kernel-path diagnostics, draft acceptance and verification-step timing if
  available; kernel, driver, library versions and clocks/utilization under
  the same workload.
- Existing four-rank GPU NCCL all-reduce results across small and large sizes,
  topology and per-HCA traffic/error counter deltas, if available. A single
  `ib_write_bw` result measures a different operation and cannot by itself
  rule out a collective-path problem or identify a switch fault.

[Issue #1](https://github.com/punkjazz-labs/glm-5.3-flash-exl3-4x-dgx-spark/issues/1)
is an unresolved external reproduction report. These identities and checks
narrow differences; they do not establish its root cause or claim a fix.
