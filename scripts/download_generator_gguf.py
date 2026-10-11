"""Fetch the approved immutable GGUF artifact, never remote executable code."""
import argparse
import hashlib
import json
import os
import re
import time
import urllib.request
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]

def download(cloud=False):
    lock=json.loads((ROOT/'data/generation_gguf_lock.json').read_text(encoding='utf-8'))
    if lock['repository']!='Qwen/Qwen2.5-0.5B-Instruct-GGUF' or lock['filename']!='qwen2.5-0.5b-instruct-q5_k_m.gguf' or not re.fullmatch(r'[a-f0-9]{40}',lock['revision']) or not re.fullmatch(r'[a-f0-9]{64}',lock['weights_sha256']):raise ValueError('Unexpected pinned artifact')
    folder=ROOT/'deploy/runtime/models' if cloud else ROOT/'data/generator-gguf'
    folder.mkdir(parents=True,exist_ok=True);target=folder/lock['filename'];partial=target.with_suffix('.partial')
    def digest(path):
        checksum=hashlib.sha256()
        with path.open('rb') as stream:
            for block in iter(lambda:stream.read(1024*1024),b''):checksum.update(block)
        return checksum.hexdigest()
    if target.exists() and target.stat().st_size==lock['size_bytes'] and digest(target)==lock['weights_sha256']:return target
    url='https://huggingface.co/'+lock['repository']+'/resolve/'+lock['revision']+'/'+lock['filename']
    started=time.monotonic();total=0
    try:
        with urllib.request.urlopen(url,timeout=20) as response,partial.open('wb') as stream:
            while True:
                block=response.read(1024*1024)
                if not block:break
                total+=len(block)
                if total>lock['size_bytes'] or time.monotonic()-started>180:raise RuntimeError('Model download budget exceeded')
                stream.write(block)
        if partial.stat().st_size!=lock['size_bytes'] or digest(partial)!=lock['weights_sha256']:raise RuntimeError('Pinned model digest mismatch')
        os.replace(partial,target);return target
    finally:
        if partial.exists():partial.unlink()

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--cloud',action='store_true');args=parser.parse_args()
    path=download(args.cloud);print(json.dumps({'artifact':path.name,'sha256_verified':True,'bytes':path.stat().st_size}))
