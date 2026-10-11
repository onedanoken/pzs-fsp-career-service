"""Bounded in-process API metrics, without profile text or entity identifiers."""
import collections,threading
lock=threading.Lock()
samples=collections.defaultdict(lambda:collections.deque(maxlen=500))
counts=collections.Counter();failures=collections.Counter()

def record(route,method,status,elapsed_ms):
    key=method+' '+route
    with lock:
        counts[key]+=1;samples[key].append(elapsed_ms)
        if status>=500:failures[key]+=1

def report():
    with lock:
        result=[]
        for key,count in counts.items():
            values=sorted(samples[key]);size=len(values)
            result.append({'route':key,'requests':count,'server_errors':failures[key],
                           'sample_size':size,'p50_ms':round(values[int((size-1)*.5)],2),
                           'p95_ms':round(values[int((size-1)*.95)],2)})
        return {'scope':'current_process','routes':result}
