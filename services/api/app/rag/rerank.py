"""CPU cross-encoder reranker: BAAI bge-reranker-v2-m3 (multilingual, Apache-2.0), int8 ONNX export from
onnx-community/bge-reranker-v2-m3-ONNX. Runs on CPU so the 6 GB GPU stays free for the Ollama model.

Optional: if onnxruntime / tokenizers / the model files are missing, `available()` is False and callers keep
the fused ranking. Disable explicitly with RERANKER=off.
"""
from __future__ import annotations

import logging
import os
import threading
import time

import numpy as np

log = logging.getLogger(__name__)
REPO = "onnx-community/bge-reranker-v2-m3-ONNX"
MODEL_FILE = "onnx/model_int8.onnx"
MAX_LEN = 192

_lock = threading.Lock()
_state: dict = {}


def _load():
    if "session" in _state or "error" in _state:
        return _state.get("session")
    with _lock:
        if "session" in _state or "error" in _state:
            return _state.get("session")
        if os.environ.get("RERANKER", "on").lower() in ("off", "0", "false", "no"):
            _state["error"] = "disabled"
            return None
        try:
            import onnxruntime as ort
            from huggingface_hub import hf_hub_download
            from tokenizers import Tokenizer

            offline = os.environ.get("HF_HUB_OFFLINE") == "1"
            model = hf_hub_download(REPO, MODEL_FILE, local_files_only=offline)
            tok = Tokenizer.from_file(hf_hub_download(REPO, "tokenizer.json", local_files_only=offline))
            tok.enable_truncation(MAX_LEN)
            tok.enable_padding(pad_id=tok.token_to_id("<pad>") or 1, pad_token="<pad>")
            opts = ort.SessionOptions()
            opts.intra_op_num_threads = min(8, max(1, (os.cpu_count() or 4) // 2))
            sess = ort.InferenceSession(model, opts, providers=["CPUExecutionProvider"])
            _state.update(session=sess, tok=tok, inputs={i.name for i in sess.get_inputs()})
        except Exception as e:  # noqa: BLE001 - optional component
            log.warning("reranker unavailable: %s", e)
            _state["error"] = str(e)
            return None
    return _state["session"]


def available() -> bool:
    return _load() is not None


def score(query: str, passages: list[str], batch: int = 10) -> list[float] | None:
    """Relevance in [0, 1] (sigmoid of the cross-encoder logit) for each (query, passage)."""
    return score_pairs([(query, p) for p in passages], batch)


def score_pairs(pairs: list[tuple[str, str]], batch: int = 10) -> list[float] | None:
    sess = _load()
    if sess is None or not pairs:
        return None
    tok, names = _state["tok"], _state["inputs"]
    out: list[float] = []
    t0 = time.perf_counter()
    for i in range(0, len(pairs), batch):
        enc = tok.encode_batch(pairs[i:i + batch])
        feed = {"input_ids": np.array([e.ids for e in enc], dtype=np.int64),
                "attention_mask": np.array([e.attention_mask for e in enc], dtype=np.int64)}
        if "token_type_ids" in names:
            feed["token_type_ids"] = np.zeros_like(feed["input_ids"])
        logits = sess.run(None, feed)[0].reshape(-1)
        out += (1 / (1 + np.exp(-logits))).tolist()
    log.debug("reranked %d pairs in %.0f ms", len(pairs),(time.perf_counter() - t0) * 1000)
    return out
