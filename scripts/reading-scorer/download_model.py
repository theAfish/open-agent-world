"""Download only fixed public files; never reads an HF token or uploads inputs."""
import hashlib
import json
from pathlib import Path
import urllib.request
import argparse

from huggingface_hub import hf_hub_download

MODEL_ID = "HuggingFaceTB/SmolLM2-135M"
REVISION = "93efa2f097d58c2a74874c7e644dbc9b0cee75a2"
WEIGHTS_HASH = "80521b40281d6ce74e35c9282c22539e75aa0ac8578892b2a59955ef78d55da1"
parser = argparse.ArgumentParser(description="Download the pinned optional local reading model")
parser.add_argument("--output", type=Path, required=True, help="Dedicated model directory outside the checkout")
DEST = parser.parse_args().output.resolve()
ROOT = DEST.parent
FILES = ["config.json", "generation_config.json", "tokenizer.json", "tokenizer_config.json",
         "special_tokens_map.json", "README.md", "model.safetensors"]


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


manifest = {"model_id": MODEL_ID, "model_revision": REVISION, "license": "Apache-2.0",
            "language_scope": "English primarily; other languages uncalibrated",
            "source": f"https://huggingface.co/{MODEL_ID}/tree/{REVISION}",
            "runtime": "transformers-cpu-float32", "files": {}}
for name in FILES:
    path = Path(hf_hub_download(MODEL_ID, name, revision=REVISION, token=False,
                               local_dir=DEST, cache_dir=ROOT / "download-cache"))
    digest = sha256(path)
    if name == "model.safetensors" and digest != WEIGHTS_HASH:
        raise RuntimeError("Model weight SHA256 does not match fixed official metadata")
    manifest["files"][name] = {"sha256": digest, "size": path.stat().st_size}
    print(json.dumps({"downloaded": name, "size": path.stat().st_size, "sha256": digest}), flush=True)
# The publisher's model card explicitly declares Apache-2.0. Preserve its full text.
license_url = "https://www.apache.org/licenses/LICENSE-2.0.txt"
license_path = DEST / "LICENSE-2.0.txt"
with urllib.request.urlopen(license_url, timeout=30) as response:
    license_path.write_bytes(response.read())
manifest["files"][license_path.name] = {"sha256": sha256(license_path), "size": license_path.stat().st_size}
manifest["license_declaration"] = f"https://huggingface.co/{MODEL_ID}/blob/{REVISION}/README.md"
(DEST / "scorer-manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps({"model_dir": str(DEST), "total_bytes": sum(f["size"] for f in manifest["files"].values())}), flush=True)
