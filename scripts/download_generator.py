"""Download only pinned, safe model weights; no remote Python code."""
import json,sys
from pathlib import Path
from huggingface_hub import snapshot_download
ROOT=Path(__file__).resolve().parents[1]
lock=json.loads((ROOT/'data'/'generation_model_lock.json').read_text(encoding='utf-8'))
snapshot_download(lock['repository'],revision=lock['revision'],local_dir=ROOT/lock['path'],local_dir_use_symlinks=False,allow_patterns=['*.json','*.safetensors','tokenizer*','vocab.json','merges.txt','LICENSE','README.md'])
print('Pinned generator downloaded')
