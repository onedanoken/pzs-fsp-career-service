"""Local Qwen3 inference. Models are pinned by scripts/download_models.py.
No network calls during inference; no generated achievements.
"""
import hashlib
import json
import logging
import os
os.environ.setdefault("USE_TF","0")
os.environ.setdefault("USE_FLAX","0")
import threading
import numpy as np
from .db import ROOT, connection

log=logging.getLogger(__name__)

class ModelEngine:
    def __init__(self):
        self.lock=threading.RLock()
        self.state="not_installed"
        self.error=None
        self.embed_model=None
        self.rank_model=None
        self.model_key="none"
        self.device="cpu"

    def status(self):
        return {"state":self.state,"error":self.error,"embedding":"Qwen3-Embedding-0.6B",
                "reranker":"Qwen3-Reranker-0.6B","device":self.device,"model_key":self.model_key,
                "quantization":"dynamic-int8" if self.device=="cpu" else "none",
                "available":self.state=="ready"}

    def prepare(self):
        if os.environ.get("FSP_ENABLE_ML","0")!="1":
            self.state="disabled"
            return
        lockfile=ROOT/"data"/"model_lock.json"
        if not lockfile.exists():
            return
        with self.lock:
            if self.state=="ready":
                return
            self.state="loading"
            try:
                import torch
                from transformers import AutoModel,AutoTokenizer,AutoModelForCausalLM
                torch.set_num_threads(int(os.environ.get("FSP_TORCH_THREADS","8")))
                meta=json.loads(lockfile.read_text(encoding="utf-8"))
                embedding_path=ROOT/meta["embedding"]["path"]
                reranker_path=ROOT/meta["reranker"]["path"]
                self.model_key=meta["embedding"]["revision"][:10]+":"+meta["reranker"]["revision"][:10]+":v1"
                self.device="cuda" if torch.cuda.is_available() else "cpu"
                dtype=torch.float16 if self.device=="cuda" else torch.float32
                self.embed_tokenizer=AutoTokenizer.from_pretrained(str(embedding_path),padding_side="left",local_files_only=True)
                self.embed_model=AutoModel.from_pretrained(str(embedding_path),local_files_only=True,torch_dtype=dtype).eval()
                self.rank_tokenizer=AutoTokenizer.from_pretrained(str(reranker_path),padding_side="left",local_files_only=True)
                self.rank_model=AutoModelForCausalLM.from_pretrained(str(reranker_path),local_files_only=True,torch_dtype=dtype).eval()
                if self.device=="cpu":
                    self.embed_model=torch.quantization.quantize_dynamic(self.embed_model,{torch.nn.Linear},dtype=torch.qint8)
                    self.rank_model=torch.quantization.quantize_dynamic(self.rank_model,{torch.nn.Linear},dtype=torch.qint8)
                    self.model_key+=":int8"
                else:
                    self.embed_model.to(self.device)
                    self.rank_model.to(self.device)
                self.no_id=self.rank_tokenizer.convert_tokens_to_ids("no")
                self.yes_id=self.rank_tokenizer.convert_tokens_to_ids("yes")
                self.state="ready"
            except Exception as exc:
                self.state="error"
                self.error=type(exc).__name__+": "+str(exc)[:180]
                log.exception("Local model initialization failed")

    def key(self,text,kind):
        return hashlib.sha256((self.model_key+":"+kind+":"+text).encode()).hexdigest()

    def embed(self,texts,query=False):
        if self.state!="ready":
            return None
        import torch
        import torch.nn.functional as F
        kind="query" if query else "document"
        keys=[self.key(t,kind) for t in texts]
        result={}
        with connection() as conn:
            for key in keys:
                row=conn.execute("SELECT vector FROM embeddings WHERE cache_key=?",(key,)).fetchone()
                if row:
                    result[key]=np.frombuffer(row["vector"],dtype=np.float32).copy()
        missing=list(dict.fromkeys((key,text) for key,text in zip(keys,texts) if key not in result))
        with self.lock:
            for start in range(0,len(missing),4):
                part=missing[start:start+4]
                inputs=[("Instruct: Retrieve IT profiles or vacancies that match the requested role, tasks and skills.\nQuery: "+t) if query else t for _,t in part]
                encoded=self.embed_tokenizer(inputs,padding=True,truncation=True,max_length=384,return_tensors="pt").to(self.device)
                with torch.inference_mode():
                    last=self.embed_model(**encoded,use_cache=False).last_hidden_state[:,-1]
                    vectors=F.normalize(last.float(),p=2,dim=1).cpu().numpy()
                with connection() as conn:
                    for (key,_),vector in zip(part,vectors):
                        vector=vector.astype(np.float32)
                        result[key]=vector
                        conn.execute("INSERT OR REPLACE INTO embeddings VALUES(?,?,?)",(key,vector.tobytes(),len(vector)))
        return np.array([result[k] for k in keys])

    def rerank(self,query,documents):
        if self.state!="ready":
            return None
        import torch
        keys=[self.key(query+"\n"+d,"rerank") for d in documents]
        scores={}
        with connection() as conn:
            for key in keys:
                row=conn.execute("SELECT score FROM reranks WHERE cache_key=?",(key,)).fetchone()
                if row:
                    scores[key]=row["score"]
        missing=[(k,d) for k,d in zip(keys,documents) if k not in scores]
        prefix="<|im_start|>system\nDecide whether the profile satisfies the vacancy. Answer only yes or no.<|im_end|>\n<|im_start|>user\n"
        suffix="<|im_end|>\n<|im_start|>assistant\n<think>\n\n</think>\n\n"
        prefix_ids=self.rank_tokenizer.encode(prefix,add_special_tokens=False)
        suffix_ids=self.rank_tokenizer.encode(suffix,add_special_tokens=False)
        with self.lock:
            for start in range(0,len(missing),4):
                part=missing[start:start+4]
                bodies=["<Instruct>: Match an IT vacancy with a profile using role, tasks and skills. Missing evidence is unknown.\n<Query>: "+query+"\n<Document>: "+d for _,d in part]
                encoded=self.rank_tokenizer(bodies,truncation=True,max_length=384-len(prefix_ids)-len(suffix_ids),add_special_tokens=False)
                sequences=[prefix_ids+ids+suffix_ids for ids in encoded["input_ids"]]
                batch=self.rank_tokenizer.pad({"input_ids":sequences},padding=True,return_tensors="pt").to(self.device)
                with torch.inference_mode():
                    logits=self.rank_model(**batch,logits_to_keep=1,use_cache=False).logits[:,-1,[self.no_id,self.yes_id]]
                    values=logits.float().softmax(dim=-1)[:,1].cpu().tolist()
                with connection() as conn:
                    for (key,_),value in zip(part,values):
                        scores[key]=value
                        conn.execute("INSERT OR REPLACE INTO reranks VALUES(?,?)",(key,value))
        return [scores[k] for k in keys]

engine=ModelEngine()
