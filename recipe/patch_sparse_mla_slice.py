"""Apply the pinned, opt-in attention-only 64-token diagnostic to a fresh container.

The preimage hash guards compatibility with the inspected GLM NoPE backend.
Existing deployed serving containers are never patched by this installer.
"""
import ast
import hashlib
import json
import os
from pathlib import Path
import sys

ORIGINAL_SHA256 = 'd665ef2109b0183d48e3541ecd24e9fa8e1dc3e410983bc29b8d997af9d7cd01'
PATCHED_SHA256 = 'f1854c0cce9d749d5ce67dd5132f6541c9d01dec4c2bb414267815ed6fd620c6'
OLD_CALL = """        out = flashinfer_trtllm_batch_decode_with_kv_cache_mla(
            query=q.unsqueeze(1),
            kv_cache=kv_c_and_k_pe_cache.view(torch.uint8).unsqueeze(1),
            workspace_buffer=self._workspace_buffer,
            qk_nope_head_dim=self.qk_nope_head_dim,
            kv_lora_rank=self.kv_lora_rank,
            qk_rope_head_dim=self.kernel_qk_rope_head_dim,
            block_tables=topk_indices_physical.unsqueeze(1),
            seq_lens=topk_lengths,
            max_seq_len=sparse_topk_capacity,
            out=output.unsqueeze(1),
            bmm1_scale=self.scale,
            bmm2_scale=1.0,
            sparse_mla_top_k=sparse_topk_capacity,
            kv_scale_format=self.kv_scale_format,
        )
        out = out.squeeze(1)
"""
NEW_CALL = """        slice_tokens = int(os.getenv(_SLICE_ENV, "0"))
        if slice_tokens not in (0, _SLICE_TOKENS):
            raise ValueError(
                f"{_SLICE_ENV} must be 0 or {_SLICE_TOKENS}; got {slice_tokens}"
            )
        call_kwargs = dict(
            kv_cache=kv_c_and_k_pe_cache.view(torch.uint8).unsqueeze(1),
            workspace_buffer=self._workspace_buffer,
            qk_nope_head_dim=self.qk_nope_head_dim,
            kv_lora_rank=self.kv_lora_rank,
            qk_rope_head_dim=self.kernel_qk_rope_head_dim,
            max_seq_len=sparse_topk_capacity,
            bmm1_scale=self.scale,
            bmm2_scale=1.0,
            sparse_mla_top_k=sparse_topk_capacity,
            kv_scale_format=self.kv_scale_format,
        )
        if slice_tokens == 0:
            out = flashinfer_trtllm_batch_decode_with_kv_cache_mla(
                query=q.unsqueeze(1),
                block_tables=topk_indices_physical.unsqueeze(1),
                seq_lens=topk_lengths,
                out=output.unsqueeze(1),
                **call_kwargs,
            ).squeeze(1)
        else:
            if q.dtype != torch.bfloat16 or tuple(q.shape[1:]) != (16, 576) or self.num_heads != 16:
                raise RuntimeError(f"SM120 attention slicing requires BF16 query [T,16,576]; got dtype={q.dtype}, shape={tuple(q.shape)}, heads={self.num_heads}")
            if kv_c_and_k_pe_cache.shape[-2:] != (64, 656):
                raise RuntimeError("SM120 attention slicing requires packed KV pages [64,656]")
            if sparse_topk_capacity != _SLICE_REQUIRED_TOPK:
                raise RuntimeError(
                    "SM120 attention slicing requires physical sparse-top-k "
                    f"{_SLICE_REQUIRED_TOPK}; got {sparse_topk_capacity}"
                )
            workspace_bytes = (
                self._workspace_buffer.numel() * self._workspace_buffer.element_size()
            )
            if workspace_bytes < _SLICE_REQUIRED_WORKSPACE_BYTES:
                raise RuntimeError(
                    "SM120 attention slicing requires at least "
                    f"{_SLICE_REQUIRED_WORKSPACE_BYTES} workspace bytes; got "
                    f"{workspace_bytes}"
                )
            for begin in range(0, num_actual_toks, _SLICE_TOKENS):
                end = min(begin + _SLICE_TOKENS, num_actual_toks)
                flashinfer_trtllm_batch_decode_with_kv_cache_mla(
                    query=q[begin:end].unsqueeze(1),
                    block_tables=topk_indices_physical[begin:end].unsqueeze(1),
                    seq_lens=topk_lengths[begin:end],
                    out=output[begin:end].unsqueeze(1),
                    **call_kwargs,
                )
            out = output
"""
CONSTANTS = """_SLICE_ENV = "VLLM_SM120_SPARSE_MLA_SLICE_TOKENS"
_SLICE_TOKENS = 64
_SLICE_REQUIRED_TOPK = 2048
_SLICE_REQUIRED_WORKSPACE_BYTES = 33_685_504


"""


def patched_source(original):
    digest = hashlib.sha256(original.encode()).hexdigest()
    if digest == PATCHED_SHA256:
        return original
    if digest != ORIGINAL_SHA256:
        raise RuntimeError(f"unrecognized sparse MLA backend preimage: {digest}")
    result = original.replace("from typing import TYPE_CHECKING, cast", "import os\nfrom typing import TYPE_CHECKING, cast", 1)
    result = result.replace("def _kv_scale_format_for_model", CONSTANTS + "def _kv_scale_format_for_model", 1)
    result = result.replace(OLD_CALL, NEW_CALL, 1)
    ast.parse(result)
    if hashlib.sha256(result.encode()).hexdigest() != PATCHED_SHA256:
        raise RuntimeError("sparse MLA patch transformation hash mismatch")
    return result


def main():
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("/usr/local/lib/python3.12/dist-packages/vllm/v1/attention/backends/mla/flashinfer_mla_sparse_sm120.py")
    original = path.read_text()
    result = patched_source(original)
    if result != original:
        temporary = path.with_name(path.name + ".glm53-slice-tmp")
        temporary.write_text(result)
        temporary.chmod(path.stat().st_mode & 0o777)
        os.replace(temporary, path)
    print(json.dumps({"patch": "glm53-sparse-mla-slice", "sha256": PATCHED_SHA256, "slice_tokens": os.environ.get("VLLM_SM120_SPARSE_MLA_SLICE_TOKENS", "0"), "status": "PASS"}))


if __name__ == "__main__":
    main()
