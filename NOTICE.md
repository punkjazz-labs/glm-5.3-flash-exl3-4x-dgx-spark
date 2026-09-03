# Licences of what this recipe runs

This repository (the scripts, the benchmark, the receipts) is MIT. It does not ship any model or engine; it launches
third-party artefacts that carry their own terms:

| Artefact | Source | Licence |
|---|---|---|
| GLM-5.3-Flash (base model) | `zai-org/GLM-5.3-Flash` | MIT |
| EXL3/TR3 4 bpw checkpoint | `Mia-AiLab/GLM-5.3-Flash-EXL3-TR3-4bpw` (byte-identical re-host of `brandonmusic/GLM-5.3-Flash-tr3-4bpw`) | ShapleyMCG License 1.0 (source-available, not OSI open source) |
| DFlash2 speculative drafter | `incoai/GLM-5.3-Flash-DFlash2` | CC BY-NC-ND 4.0 (**non-commercial**) |
| Engine image, overlay patches, chat template | MiaAI-Lab `GLM-5.3-Flash-EXL3-2x-DGX-Sparks` (vLLM + exllamav3 built for sm_121a) | MIT (repository code) |

The configuration this recipe ships uses the DFlash2 drafter, so the served stack is non-commercial as it stands.
Set `SPEC_METHOD=none` (or `mtp`) to serve without it; the receipts in `evidence/autoresearch/` include a no-speculation
control so you can see what that costs (decode 22.6 vs 31-33 tok/s).
