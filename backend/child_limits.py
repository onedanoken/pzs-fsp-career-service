"""Resource limits installed inside a disposable parser/SQL child before work."""
import sys
_handle=None
def restrict(memory_mb=128,cpu_seconds=2):
    global _handle
    if sys.platform!='win32':
        import resource
        resource.setrlimit(resource.RLIMIT_AS,(memory_mb*1024*1024,)*2)
        resource.setrlimit(resource.RLIMIT_CPU,(cpu_seconds,)*2)
        return
    import ctypes
    from ctypes import wintypes as w
    class Basic(ctypes.Structure):
        _fields_=[('process_time',ctypes.c_int64),('job_time',ctypes.c_int64),('flags',w.DWORD),('minimum',ctypes.c_size_t),('maximum',ctypes.c_size_t),('processes',w.DWORD),('affinity',ctypes.c_size_t),('priority',w.DWORD),('scheduling',w.DWORD)]
    class IO(ctypes.Structure):_fields_=[(name,ctypes.c_uint64) for name in ['read_ops','write_ops','other_ops','read_bytes','write_bytes','other_bytes']]
    class Extended(ctypes.Structure):_fields_=[('basic',Basic),('io',IO),('process_memory',ctypes.c_size_t),('job_memory',ctypes.c_size_t),('peak_process',ctypes.c_size_t),('peak_job',ctypes.c_size_t)]
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.CreateJobObjectW.argtypes=[ctypes.c_void_p,w.LPCWSTR];kernel.CreateJobObjectW.restype=w.HANDLE
    kernel.SetInformationJobObject.argtypes=[w.HANDLE,ctypes.c_int,ctypes.c_void_p,w.DWORD];kernel.SetInformationJobObject.restype=w.BOOL
    kernel.AssignProcessToJobObject.argtypes=[w.HANDLE,w.HANDLE];kernel.AssignProcessToJobObject.restype=w.BOOL
    kernel.GetCurrentProcess.restype=w.HANDLE
    _handle=kernel.CreateJobObjectW(None,None)
    limits=Extended();limits.basic.flags=0x100|0x2;limits.basic.process_time=cpu_seconds*10_000_000;limits.process_memory=memory_mb*1024*1024
    if not _handle or not kernel.SetInformationJobObject(_handle,9,ctypes.byref(limits),ctypes.sizeof(limits)) or not kernel.AssignProcessToJobObject(_handle,kernel.GetCurrentProcess()):raise RuntimeError('Cannot establish child resource limits')
