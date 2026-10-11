"""Measure baseline startup and memory of owned disposable API processes.

No production data, account secrets or deployment modifications. This tiny
workload is evidence for a baseline budget, not a production capacity claim.
"""
import argparse,ctypes,json,os,platform,secrets,signal,socket,subprocess,sys,time
from pathlib import Path
import httpx

ROOT=Path(__file__).resolve().parents[1]


def peak_memory(pid):
    if os.name!='nt':
        try:
            line=next(x for x in Path(f'/proc/{pid}/status').read_text().splitlines() if x.startswith('VmHWM:'))
            return int(line.split()[1])*1024
        except (OSError,StopIteration):return None
    from ctypes import wintypes
    class Counters(ctypes.Structure):
        _fields_=[('cb',wintypes.DWORD),('PageFaultCount',wintypes.DWORD)]+[(name,ctypes.c_size_t) for name in ['PeakWorkingSetSize','WorkingSetSize','QuotaPeakPagedPoolUsage','QuotaPagedPoolUsage','QuotaPeakNonPagedPoolUsage','QuotaNonPagedPoolUsage','PagefileUsage','PeakPagefileUsage','PrivateUsage']]
    kernel=ctypes.WinDLL('kernel32',use_last_error=True);psapi=ctypes.WinDLL('psapi',use_last_error=True)
    kernel.OpenProcess.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD];kernel.OpenProcess.restype=wintypes.HANDLE
    kernel.CloseHandle.argtypes=[wintypes.HANDLE];kernel.CloseHandle.restype=wintypes.BOOL
    psapi.GetProcessMemoryInfo.argtypes=[wintypes.HANDLE,ctypes.POINTER(Counters),wintypes.DWORD];psapi.GetProcessMemoryInfo.restype=wintypes.BOOL
    handle=kernel.OpenProcess(0x1000|0x10,False,pid)
    if not handle:return None
    try:
        counters=Counters();counters.cb=ctypes.sizeof(counters)
        return int(counters.PeakWorkingSetSize) if psapi.GetProcessMemoryInfo(handle,ctypes.byref(counters),counters.cb) else None
    finally:kernel.CloseHandle(handle)


def trial():
    token=secrets.token_hex(6);temporary=ROOT/'tmp';temporary.mkdir(exist_ok=True)
    with socket.socket() as allocation:allocation.bind(('127.0.0.1',0));port=allocation.getsockname()[1]
    pid_file=temporary/f'budget-{token}.pid'
    environment=os.environ.copy();environment.update({'FSP_DB_PATH':str(temporary/f'budget-{token}.db'),'FSP_BUDGET_PID':str(pid_file),'FSP_DATABASE_URL':'','FSP_DEMO':'0','FSP_ENABLE_ML':'0','FSP_OLLAMA_URL':'','FSP_JOB_AUTOSTART':'1','FSP_BUDGET_PORT':str(port)})
    with (temporary/f'budget-{token}.log').open('w',encoding='utf-8') as log:
        started=time.perf_counter()
        # Windows venv executables can be launchers. The service records its own
        # PID so the measurement and shutdown target the actual interpreter.
        launch="import os,pathlib; pathlib.Path(os.environ['FSP_BUDGET_PID']).write_text(str(os.getpid())); import uvicorn; uvicorn.run('backend.main:app',host='127.0.0.1',port=int(os.environ['FSP_BUDGET_PORT']),access_log=False)"
        process=subprocess.Popen([sys.executable,'-c',launch],cwd=ROOT,env=environment,stdout=log,stderr=log,creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
        try:
            with httpx.Client(base_url=f'http://127.0.0.1:{port}',timeout=2,trust_env=False) as client:
                while True:
                    if process.poll() is not None:raise RuntimeError('Disposable API exited; inspect its temporary log')
                    try:
                        if client.get('/api/health').status_code==200:break
                    except httpx.HTTPError:pass
                    if time.perf_counter()-started>30:raise RuntimeError('Disposable API failed to start in 30 seconds')
                    time.sleep(.05)
                startup=time.perf_counter()-started
                samples=[]
                for _ in range(20):
                    begin=time.perf_counter();response=client.get('/api/health');response.raise_for_status();samples.append((time.perf_counter()-begin)*1000)
                client.get('/').raise_for_status()
                memory=peak_memory(int(pid_file.read_text()))
                version=client.get('/api/openapi.json').json()['info']['version']
                return {'api_version':version,'cold_start_seconds':round(startup,3),'peak_api_memory_mib':round(memory/1024**2,2) if memory is not None else None,'health_p95_ms':round(sorted(samples)[18],2)}
        finally:
            if process.poll() is None:
                if pid_file.exists():
                    try:os.kill(int(pid_file.read_text()),signal.SIGTERM)
                    except ProcessLookupError:pass
                else:process.terminate()
            try:process.wait(timeout=5)
            except subprocess.TimeoutExpired:process.kill();process.wait(timeout=5)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',default='artifacts/stage03-runtime.json');args=parser.parse_args()
    report={'scope':'Fresh isolated SQLite API, ML disabled; Windows localhost; 20 health requests and one static page per trial','platform':platform.platform(),'python':platform.python_version(),'logical_cpus':os.cpu_count(),'trials':[trial() for _ in range(3)],'limits':'Not PostgreSQL, loaded inference, production capacity or a minimum hardware certification'}
    output=ROOT/args.output;output.parent.mkdir(parents=True,exist_ok=True);output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False))
