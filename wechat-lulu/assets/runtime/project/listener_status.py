"""状态快照短暂被读取占用时重试发布，不能让监听因此退出。"""
import json
import os
import threading
import time
from pathlib import Path


def publish_status(path, data, replace=None, sleep=None):
    """只重试 Windows 文件共享冲突；失败时保留上一份完整快照。"""
    path = Path(path)
    replace = replace or os.replace
    sleep = sleep or time.sleep
    temporary = path.with_name(path.name + f'.{os.getpid()}.{threading.get_ident()}.tmp')
    try:
        with temporary.open('w', encoding='utf-8') as stream:
            json.dump(data, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        delays = (.02, .05, .15)
        for attempt in range(len(delays) + 1):
            try:
                replace(temporary, path)
                return True
            except PermissionError as error:
                if getattr(error, 'winerror', None) not in (None, 5, 32, 33):
                    raise
                if attempt == len(delays):
                    return False
                sleep(delays[attempt])
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
