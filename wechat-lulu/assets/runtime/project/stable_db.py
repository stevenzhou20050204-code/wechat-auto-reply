"""串行读取共享缓存，防止 Windows 在别的查询占用文件时替换失败。"""
import ctypes,functools,os,threading,time
from contextlib import contextmanager

_gate=threading.RLock()

@contextmanager
def cache_gate():
    with _gate:
        if os.name!='nt':
            yield;return
        from ctypes import wintypes
        k=ctypes.windll.kernel32
        k.CreateMutexW.argtypes=[ctypes.c_void_p,wintypes.BOOL,wintypes.LPCWSTR];k.CreateMutexW.restype=wintypes.HANDLE
        k.WaitForSingleObject.argtypes=[wintypes.HANDLE,wintypes.DWORD]
        k.ReleaseMutex.argtypes=[wintypes.HANDLE];k.CloseHandle.argtypes=[wintypes.HANDLE]
        handle=k.CreateMutexW(None,False,'Local\\WeChatAutoReply-ReadCache-Acer')
        if not handle:raise OSError('无法创建微信缓存读取锁')
        acquired=False
        try:
            result=k.WaitForSingleObject(handle,15000)
            if result not in (0,0x80):raise TimeoutError('等待微信缓存读取锁超时')
            acquired=True;yield
        finally:
            if acquired:k.ReleaseMutex(handle)
            k.CloseHandle(handle)

def transient_read_error(error):
    return isinstance(error,PermissionError) and getattr(error,'winerror',None) in (None,5,32,33)

class TransientCacheReadError(PermissionError):
    """数据库读取短暂失败；保留上层监听状态，不能误当成发送或队列写入错误。"""
    def __init__(self,operation,error):
        super().__init__(f'微信缓存读取暂时不可用（{operation}）：{error}')
        self.operation=operation
        self.winerror=getattr(error,'winerror',None)

def guarded_cache_read_step(operation,on_unavailable):
    """只隔离经过数据库读取包装确认的共享冲突，其他故障照常抛出。"""
    try:
        operation()
        return True
    except TransientCacheReadError as error:
        on_unavailable(error)
        return False

class StableDB:
    """锁覆盖完整读取及连接关闭；跨进程与跨线程使用同一把缓存锁。"""
    def __init__(self,raw):self._raw=raw
    def __getattr__(self,name):
        value=getattr(self._raw,name)
        if not callable(value):return value
        @functools.wraps(value)
        def read(*args,**kwargs):
            with cache_gate():
                for attempt in range(4):
                    try:
                        result=value(*args,**kwargs)
                        # 库的通讯录接口会吞掉临时打开错误并返回None，不能遗漏这条恢复路径。
                        if name=='_contact_conn' and result is None and attempt<3:
                            time.sleep((.1,.2,.4)[attempt]);continue
                        return result
                    except PermissionError as error:
                        if not transient_read_error(error):raise
                        if attempt==3:raise TransientCacheReadError(name,error) from error
                        time.sleep((.1,.2,.4)[attempt])
        return read

def make_db(**kwargs):
    from wechatauto import WeChatDB
    with cache_gate():return StableDB(WeChatDB(**kwargs))
