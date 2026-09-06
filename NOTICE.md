# Third-party artefacts and licences

This repository contains launcher and benchmark code only. It does not
redistribute model weights, container images, upstream overlays, or CUDA
libraries. Obtain and comply with the terms of every dependency below.

| Artefact | Pinned source | Licence / terms to verify |
|---|---|---|
| GLM-5.3-Flash base model | `zai-org/GLM-5.3-Flash` | MIT, according to its model card |
| EXL3/TR3 checkpoint | `Mia-AiLab/GLM-5.3-Flash-EXL3-TR3-4bpw` @ `25a44fdbf16862a46b7cc9921142c6c81350af2f` | ShapleyMCG License 1.0 |
| DFlash2 drafter | `incoai/GLM-5.3-Flash-DFlash2` @ `dc77ff1c99eeb2df044ee3d4f0094eb033fee410` | CC BY-NC-ND 4.0 |
| Engine image and overlay | `MiaAI-Lab/GLM-5.3-Flash-EXL3-2x-DGX-Sparks` @ `c190db1ae17ba8dff20129ed1f308d10c63cf37d` | inspect its repository and image terms |

The selected configuration enables DFlash2. Its non-commercial terms apply to
the served stack. This notice is informational and is not legal advice.
