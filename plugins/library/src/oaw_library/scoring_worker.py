"""Optional, offline, CPU-only existing-text scorer. JSONL over stdin/stdout.

Run with a separate Python environment and a locally prepared model directory:
    python scoring_worker.py --model-dir PATH --threads 1
No model or package downloads take place in this worker. Library imports are lazy
so importing the module does not add ML dependencies to the OAW backend.
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import importlib.metadata
import json
import math
import os
from pathlib import Path
import queue
import re
import sys
import threading
import time
from typing import Any

ALGORITHM_VERSION = "oaw-existing-text-v1"
PROTOCOL_VERSION = 1
MAX_TEXT_CHARACTERS = 100_000


class ScoringCancelled(Exception):
    pass


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def logprob_from_logits(logits: list[float], target_id: int) -> float:
    """Small independent reference implementation used by numerical probes."""
    if not logits or not 0 <= target_id < len(logits) or not all(math.isfinite(x) for x in logits):
        raise ValueError("Invalid logits or target token")
    maximum = max(logits)
    return logits[target_id] - maximum - math.log(sum(math.exp(value - maximum) for value in logits))


def token_windows(count: int, context_tokens: int, stride: int):
    """Yield (context start, first target, exclusive end); each target once."""
    if not 2 <= context_tokens <= 1024 or not 1 <= stride < context_tokens:
        raise ValueError("Require 2 <= context_tokens <= 1024 and 1 <= stride < context_tokens")
    for first in range(1, count, stride):
        end = min(count, first + stride)
        yield max(0, end - context_tokens), first, end


def character_boundaries(text: str) -> tuple[list[int], list[int]]:
    """Map Python codepoint boundaries to UTF-16 code units and UTF-8 bytes."""
    utf16, utf8 = [0], [0]
    for character in text:
        utf16.append(utf16[-1] + len(character.encode("utf-16-le")) // 2)
        utf8.append(utf8[-1] + len(character.encode("utf-8")))
    return utf16, utf8


class LocalScorer:
    def __init__(self, model_dir: Path, threads: int = 1):
        self.model_dir = model_dir.resolve()
        self.threads = max(1, min(2, threads))
        self.model = self.tokenizer = self.torch = None
        self.manifest: dict[str, Any] | None = None
        self.probe_passed = False
        self.load_seconds = None

    def load(self, cancelled: threading.Event):
        if self.model is not None:
            return
        if cancelled.is_set():
            raise ScoringCancelled()
        started = time.perf_counter()
        manifest_path = self.model_dir / "scorer-manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        files = manifest.get("files", {})
        for required in ("model.safetensors", "tokenizer.json", "config.json"):
            if required not in files:
                raise ValueError(f"Manifest is missing {required}")
        if manifest.get("license") != "Apache-2.0":
            raise ValueError("This optional scorer requires its explicitly licensed model manifest")
        for name, expected in files.items():
            path = (self.model_dir / name).resolve()
            if not path.is_relative_to(self.model_dir) or not re.fullmatch(r"[0-9a-f]{64}", expected.get("sha256", "")):
                raise ValueError("Invalid manifest file path or SHA256")
            if not path.is_file() or path.stat().st_size != expected["size"] or file_sha256(path) != expected["sha256"]:
                raise ValueError(f"Local model integrity check failed: {name}")
        config = json.loads((self.model_dir / "config.json").read_text(encoding="utf-8"))
        if config.get("model_type") not in {"llama", "qwen2"} or config.get("auto_map"):
            raise ValueError("Unsupported model architecture; remote model code is disabled")
        if cancelled.is_set():
            raise ScoringCancelled()
        os.environ.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", HF_HUB_DISABLE_TELEMETRY="1",
                          TOKENIZERS_PARALLELISM="false", OMP_NUM_THREADS=str(self.threads),
                          MKL_NUM_THREADS=str(self.threads), OPENBLAS_NUM_THREADS=str(self.threads))
        import torch
        from tokenizers import Tokenizer
        from transformers import AutoModelForCausalLM
        torch.set_num_threads(self.threads)
        torch.set_num_interop_threads(1)
        # Third-party progress output must never corrupt the JSONL protocol.
        with contextlib.redirect_stdout(sys.stderr):
            model = AutoModelForCausalLM.from_pretrained(
                self.model_dir, local_files_only=True, trust_remote_code=False,
                use_safetensors=True, dtype=torch.float32, attn_implementation="sdpa",
            ).to("cpu").eval()
        tokenizer = Tokenizer.from_file(str(self.model_dir / "tokenizer.json"))
        manifest_fingerprint = hashlib.sha256(json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        self.manifest = {
            **manifest, "manifest_sha256": manifest_fingerprint,
            "algorithm_version": ALGORITHM_VERSION, "protocol_version": PROTOCOL_VERSION,
            "device": "cpu", "dtype": "float32", "threads": self.threads,
            "special_tokens": "none", "normalization": "none; source offsets retained by tokenizer",
            "versions": {name: importlib.metadata.version(name) for name in
                         ("torch", "transformers", "tokenizers", "safetensors", "huggingface-hub")},
        }
        self.model, self.tokenizer, self.torch = model, tokenizer, torch
        self.load_seconds = time.perf_counter() - started
        if cancelled.is_set():
            raise ScoringCancelled()

    def score(self, text: str, context_tokens: int, stride: int,
              cancelled: threading.Event, progress=None) -> dict[str, Any]:
        if not isinstance(text, str) or len(text) > MAX_TEXT_CHARACTERS:
            raise ValueError(f"text must be a string with at most {MAX_TEXT_CHARACTERS} codepoints")
        # Reject malformed lone surrogates before loading the model.
        utf16, utf8 = character_boundaries(text)
        list(token_windows(0, context_tokens, stride))
        self.load(cancelled)
        started = time.perf_counter()
        encoded = self.tokenizer.encode(text, add_special_tokens=False)
        ids = encoded.ids
        if self.tokenizer.decode(ids, skip_special_tokens=False) != text:
            raise ValueError("Tokenizer does not exactly round-trip this source text")
        tokens: list[dict[str, Any]] = []
        for index, (token_id, (start, end)) in enumerate(zip(ids, encoded.offsets)):
            if not 0 <= start < end <= len(text):
                raise ValueError("Tokenizer returned an unmappable source offset")
            tokens.append({
                "index": index, "token_id": token_id, "text": text[start:end],
                "start": start, "end": end, "utf16_start": utf16[start], "utf16_end": utf16[end],
                "byte_start": utf8[start], "byte_end": utf8[end],
                "logprob": None, "bits": None, "status": "no_context" if index == 0 else "pending",
            })
        torch = self.torch
        for begin, first, end in token_windows(len(ids), context_tokens, stride):
            if cancelled.is_set():
                raise ScoringCancelled()
            inputs = torch.tensor([ids[begin:end]], dtype=torch.long)
            with torch.inference_mode():
                output = self.model(input_ids=inputs, use_cache=False)
                # Logits at i-1 predict the existing source token at i.
                rows = output.logits[0, first - begin - 1:end - begin - 1].float()
                targets = inputs[0, first - begin:end - begin]
                values = (rows.gather(-1, targets.unsqueeze(-1)).squeeze(-1)
                          - torch.logsumexp(rows, dim=-1)).tolist()
            del output, rows, targets, inputs
            if cancelled.is_set():
                raise ScoringCancelled()
            for index, logprob in enumerate(values, first):
                if not math.isfinite(logprob) or logprob > 1e-5:
                    raise ValueError("Model returned an invalid token logprob")
                tokens[index].update(logprob=logprob, bits=-logprob / math.log(2), status="scored",
                                     context_start_token=begin, context_token_count=index - begin)
            if progress:
                progress({"scored_tokens": end - 1, "total_tokens": max(0, len(ids) - 1)})
        return {"text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                "offset_unit": "unicode_codepoint", "tokens": tokens,
                "token_count": len(ids), "scored_count": max(0, len(ids) - 1),
                "context_tokens": context_tokens, "stride": stride, "first_token_policy": "unscored",
                "model": self.manifest, "load_seconds": self.load_seconds,
                "score_seconds": time.perf_counter() - started}

    def probe(self, cancelled: threading.Event) -> dict[str, Any]:
        text = "The lithium ion moves through the crystal. 锂离子沿通道迁移。🙂 e\u0301"
        result = self.score(text, 256, 128, cancelled)
        torch = self.torch
        ids = [token["token_id"] for token in result["tokens"]]
        inputs = torch.tensor([ids], dtype=torch.long)
        with torch.inference_mode():
            expected_loss = float(self.model(input_ids=inputs, labels=inputs, use_cache=False).loss)
            index = min(8, len(ids) - 1)
            logits = self.model(input_ids=inputs[:, :index], use_cache=False).logits[0, -1].tolist()
        independent = logprob_from_logits(logits, ids[index])
        observed_loss = -sum(t["logprob"] for t in result["tokens"][1:]) / (len(ids) - 1)
        loss_error = abs(expected_loss - observed_loss)
        shifted_error = abs(independent - result["tokens"][index]["logprob"])
        if loss_error > 2e-5 or shifted_error > 2e-5:
            raise ValueError(f"Teacher-forcing probe failed: loss={loss_error}, shift={shifted_error}")
        self.probe_passed = True
        return {"ready": True, "checks": {"mean_nll_vs_model_loss_abs_error": loss_error,
                "prefix_next_token_logprob_abs_error": shifted_error, "exact_text_roundtrip": True},
                "sample": result, "language_scope": self.manifest.get("language_scope")}


def serve(model_dir: Path, threads: int):
    scorer = LocalScorer(model_dir, threads)
    output_lock, state_lock = threading.Lock(), threading.Lock()
    jobs: queue.Queue = queue.Queue(maxsize=1)
    requests: dict[str, threading.Event] = {}
    stopping = threading.Event()

    def emit(value):
        with output_lock:
            print(json.dumps(value, ensure_ascii=False, allow_nan=False), flush=True)

    def work():
        while not stopping.is_set():
            try:
                request, cancelled = jobs.get(timeout=0.1)
            except queue.Empty:
                continue
            identity = request["id"]
            try:
                if cancelled.is_set():
                    raise ScoringCancelled()
                if request["op"] == "probe":
                    result = scorer.probe(cancelled)
                else:
                    result = scorer.score(request.get("text"), request.get("context_tokens", 256),
                                          request.get("stride", 128), cancelled,
                                          lambda value: emit({"id": identity, "type": "progress", **value}))
                if cancelled.is_set():
                    raise ScoringCancelled()
                emit({"id": identity, "type": "result", "result": result})
            except ScoringCancelled:
                emit({"id": identity, "type": "cancelled"})
            except Exception as error:
                emit({"id": identity, "type": "error", "error": type(error).__name__, "message": str(error)})
            finally:
                with state_lock:
                    requests.pop(identity, None)
                jobs.task_done()

    worker = threading.Thread(target=work, name="local-reading-scorer", daemon=True)
    worker.start()
    shutdown_requested = False
    emit({"type": "hello", "protocol_version": PROTOCOL_VERSION, "execution": "local-cpu",
          "ready": False, "requires_probe": True})
    try:
        for line in sys.stdin:
            identity = None
            try:
                if len(line) > 1_000_000:
                    raise ValueError("Request exceeds JSONL size limit")
                request = json.loads(line)
                if not isinstance(request, dict):
                    raise ValueError("Request must be an object")
                identity = request.get("id")
                if not isinstance(identity, str) or not 1 <= len(identity) <= 128:
                    raise ValueError("Request requires a string id (1-128 characters)")
                op = request.get("op")
                if op == "status":
                    emit({"id": identity, "type": "result", "result": {"loaded": scorer.model is not None,
                          "ready": scorer.probe_passed, "execution": "local-cpu", "model": scorer.manifest}})
                elif op == "cancel":
                    with state_lock:
                        event = requests.get(request.get("target_id"))
                        if event:
                            event.set()
                    emit({"id": identity, "type": "result", "result": {"cancel_requested": event is not None}})
                elif op == "shutdown":
                    shutdown_requested = True
                    emit({"id": identity, "type": "result", "result": {"shutting_down": True}})
                    break
                elif op in {"score", "probe"}:
                    if op == "score" and not scorer.probe_passed:
                        raise ValueError("Run a successful probe before scoring")
                    event = threading.Event()
                    with state_lock:
                        if identity in requests:
                            raise ValueError("Duplicate active request id")
                        if requests:
                            raise ValueError("Scorer is busy; submit one scoring request at a time")
                        requests[identity] = event
                    emit({"id": identity, "type": "accepted"})
                    jobs.put_nowait((request, event))
                else:
                    raise ValueError("Unknown operation")
            except Exception as error:
                emit({"id": identity, "type": "error", "error": type(error).__name__, "message": str(error)})
    finally:
        if not shutdown_requested:
            # A one-shot caller may close stdin after its request; finish accepted work.
            jobs.join()
        stopping.set()
        with state_lock:
            for event in requests.values():
                event.set()
        worker.join(timeout=30)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-dir", type=Path, required=True)
    parser.add_argument("--threads", type=int, choices=(1, 2), default=1)
    args = parser.parse_args()
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    # NumPy's Windows DLL initialization can deadlock when its first import is in
    # the scoring thread while the main thread blocks in stdin. Initialize it here.
    os.environ["OPENBLAS_NUM_THREADS"] = str(args.threads)
    try:
        import numpy  # noqa: F401
    except ImportError:
        pass  # The normal lazy loader reports missing optional dependencies as JSON.
    serve(args.model_dir, args.threads)


if __name__ == "__main__":
    main()
