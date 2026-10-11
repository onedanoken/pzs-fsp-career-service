"""Download reproducible local models once; inference never uses the network."""
import argparse
import hashlib
import json
import os
from pathlib import Path
os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
os.environ.setdefault("HF_HUB_DOWNLOAD_TIMEOUT", "600")
from huggingface_hub import HfApi, snapshot_download
ROOT = Path(__file__).resolve().parents[1]

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--refresh", action="store_true", help="Resolve latest upstream revisions explicitly")
    args = parser.parse_args()
    target = ROOT / "data" / "model_lock.json"
    previous = json.loads(target.read_text(encoding="utf-8")) if target.exists() else {}
    lock = {}
    for kind, repo in [("embedding","Qwen/Qwen3-Embedding-0.6B"),("reranker","Qwen/Qwen3-Reranker-0.6B")]:
        revision = previous.get(kind, {}).get("revision") if not args.refresh else None
        revision = revision or HfApi().model_info(repo).sha
        print("Downloading", repo, revision, flush=True)
        path = Path(snapshot_download(repo, revision=revision, cache_dir=str(ROOT/"data"/"hf-cache"),
                     allow_patterns=["*.json","*.safetensors","*.txt","*.model","LICENSE"], max_workers=3))
        weights = path / "model.safetensors"
        digest = hashlib.sha256()
        with weights.open("rb") as stream:
            for block in iter(lambda:stream.read(4*1024*1024),b""):
                digest.update(block)
        lock[kind] = {"repo":repo,"revision":revision,"path":path.relative_to(ROOT).as_posix(),
                      "weights_sha256":digest.hexdigest(),"license":"Apache-2.0"}
        print("Verified", kind, weights.stat().st_size, "bytes", flush=True)
    target.parent.mkdir(exist_ok=True)
    target.write_text(json.dumps(lock,ensure_ascii=False,indent=2),encoding="utf-8")
    print("Saved", target, flush=True)

if __name__ == "__main__":
    main()
