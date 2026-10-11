"""Optional CPU-only neural provider, localhost only, no personal input required.

Ollama-compatible /api/generate envelope lets the same application adapter use
Ollama on a small host or this reproducible Transformers reference provider.
"""
import json,os,time
from pathlib import Path
from http.server import HTTPServer,BaseHTTPRequestHandler
import torch
from transformers import AutoTokenizer,AutoModelForCausalLM
ROOT=Path(__file__).resolve().parents[1]
lock=json.loads((ROOT/'data'/'generation_model_lock.json').read_text(encoding='utf-8'))
path=ROOT/lock['path'];torch.set_num_threads(int(os.environ.get('FSP_GENERATOR_THREADS','2')))
tokenizer=AutoTokenizer.from_pretrained(path,local_files_only=True,trust_remote_code=False)
model=AutoModelForCausalLM.from_pretrained(path,local_files_only=True,trust_remote_code=False,torch_dtype=torch.float32).eval()
# FP32 reference path: avoid quantization changing small-model instruction quality.
PORT=int(os.environ.get('FSP_GENERATOR_PORT','11435'))
class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args):pass
    def do_GET(self):
        body=json.dumps({'status':'ready','provider':'transformers','revision':lock['revision'],'quantization':'fp32'}).encode();self.send_response(200);self.end_headers();self.wfile.write(body)
    def do_POST(self):
        if self.path!='/api/generate':self.send_error(404);return
        length=int(self.headers.get('Content-Length','0'))
        if not 0<length<=20000:self.send_error(413);return
        try:
            body=json.loads(self.rfile.read(length));prompt=body['prompt']
            if not isinstance(prompt,str) or len(prompt)>10000:raise ValueError()
            messages=[{'role':'system','content':'Return one valid JSON object only. No markdown. No answers. Follow the requested schema.'},{'role':'user','content':prompt}]
            inputs=tokenizer(tokenizer.apply_chat_template(messages,tokenize=False,add_generation_prompt=True),return_tensors='pt')
            started=time.monotonic()
            with torch.inference_mode():output=model.generate(**inputs,max_new_tokens=min(600,int(body.get('options',{}).get('num_predict',400))),do_sample=False,pad_token_id=tokenizer.eos_token_id)
            text=tokenizer.decode(output[0][inputs['input_ids'].shape[-1]:],skip_special_tokens=True)
            response=json.dumps({'response':text,'model':lock['repository'],'revision':lock['revision'],'provider':'transformers','duration_seconds':round(time.monotonic()-started,2)},ensure_ascii=False).encode()
            self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(response)));self.end_headers();self.wfile.write(response)
        except (ValueError,KeyError):self.send_error(422)
print('Neural generator ready on http://127.0.0.1:'+str(PORT),flush=True)
HTTPServer(('127.0.0.1',PORT),Handler).serve_forever()
