"""Prompt-injection and jailbreak detection with Meta's Llama Prompt Guard 2 (22M), run as ONNX.

A 22M-parameter classifier (BENIGN / MALICIOUS) that takes ~10-50 ms on CPU: cheap enough to screen
every user message and every piece of untrusted content (tool outputs, web snippets) before it
reaches an LLM. Long texts are scored in overlapping windows and the worst window wins.

Built with Llama. Model: gravitee-io/Llama-Prompt-Guard-2-22M-onnx (community ONNX export,
Llama 4 Community License).
"""
from functools import lru_cache
from pathlib import Path

import numpy as np

from connoisseur.log import get_logger
from connoisseur.settings import get_settings

log = get_logger(__name__)

MAX_TOKENS = 512
WINDOW_STRIDE = 384


class PromptGuard:
    def __init__(self, model_dir: Path):
        import onnxruntime as ort
        from tokenizers import Tokenizer

        s = get_settings()
        self.tokenizer = Tokenizer.from_file(str(model_dir / "tokenizer.json"))
        self.tokenizer.no_padding()
        self.tokenizer.no_truncation()
        options = ort.SessionOptions()
        options.intra_op_num_threads = 2
        self.session = ort.InferenceSession(
            str(model_dir / s.prompt_guard_file), options, providers=["CPUExecutionProvider"]
        )
        self.input_names = {i.name for i in self.session.get_inputs()}
        self.cls_id = self.tokenizer.token_to_id("[CLS]")
        self.sep_id = self.tokenizer.token_to_id("[SEP]")

    def _windows(self, text: str) -> list[list[int]]:
        ids = self.tokenizer.encode(text, add_special_tokens=False).ids
        body = MAX_TOKENS - 2
        starts = range(0, max(len(ids) - body, 0) + 1, WINDOW_STRIDE) if len(ids) > body else [0]
        windows = [[self.cls_id, *ids[i : i + body], self.sep_id] for i in starts]
        if len(ids) > body and (len(ids) - body) % WINDOW_STRIDE:
            windows.append([self.cls_id, *ids[-body:], self.sep_id])  # make sure the tail is covered
        return windows

    def malicious_probability(self, text: str) -> float:
        """Probability (0-1) that the text tries to override instructions or jailbreak the model."""
        if not text.strip():
            return 0.0
        worst = 0.0
        for window in self._windows(text):
            ids = np.array([window], dtype=np.int64)
            feeds = {"input_ids": ids, "attention_mask": np.ones_like(ids)}
            if "token_type_ids" in self.input_names:
                feeds["token_type_ids"] = np.zeros_like(ids)
            logits = self.session.run(None, {k: v for k, v in feeds.items() if k in self.input_names})[0][0]
            probs = np.exp(logits - logits.max())
            worst = max(worst, float(probs[1] / probs.sum()))
        return round(worst, 4)


@lru_cache
def get_prompt_guard() -> PromptGuard | None:
    """Download (once) and load the classifier. Returns None if it cannot be loaded."""
    s = get_settings()
    try:
        from huggingface_hub import snapshot_download

        model_dir = snapshot_download(
            s.prompt_guard_repo,
            revision=s.prompt_guard_revision,
            allow_patterns=[s.prompt_guard_file, "tokenizer.json", "config.json"],
            cache_dir=str(s.embedding_cache_dir) if s.embedding_cache_dir else None,
        )
        return PromptGuard(Path(model_dir))
    except Exception as exc:  # noqa: BLE001 - the app must still start; guardrails report the gap
        log.error("prompt_guard_unavailable", error=str(exc)[:300])
        return None
