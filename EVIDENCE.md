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
