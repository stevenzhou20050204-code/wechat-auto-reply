"""不依赖Codex联网的星环自愈；只在连续网络失败后操作指定星环程序。"""
import argparse,csv,ctypes,datetime,io,json,os,socket,ssl,struct,subprocess,sys,time,urllib.request,urllib.error
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

ROOT=Path(__file__).resolve().parent
EXE=Path(r'D:\xinghuan\xinghuan.exe')
PROJECT=Path(r'D:\wechatauto-replica')
STATE=ROOT/'star-network-health.json'
STOP=ROOT/'STAR-WATCH-STOP'

def hourly_due(old,now,interval=3600):
    return int(now//interval)>int(float(old.get('hourlyCheckedAt',0))//interval)

def account_check(record):
    """账号只取自星环自己的可见账号栏；看不清时不退出、不传入凭证。"""
    import importlib.util
    sys.path.insert(0,str(PROJECT))
    from passive_sender import require_desktop
    def guard():
        require_desktop()
        if STOP.exists() or (PROJECT/'.wechat_codex/UI-PAUSE').exists() or (PROJECT/'.wechat_codex/STOP').exists():raise RuntimeError('人工暂停，停止星环账号操作')
        listener=load(PROJECT/'.wechat_codex/listener-status.json',{}) or {}
        remote=load(ROOT/'connection-health.json',{}) or {}
        if listener.get('active',0) or remote.get('activeCalls',0):raise RuntimeError('正在回复或远程调用，推迟星环账号操作')
    spec=importlib.util.spec_from_file_location('star_account',ROOT/'star-account.py');module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    ui=module.NativeUI(owned_processes,guard)
    old=record.get('account',{})
    def checkpoint(value):record['account']=dict(value);save(record)
    try:
        value=module.ensure_account(ui,old=old,save=checkpoint)
        record['account']=value;save(record)
        return value.get('status')=='matched'
    finally:ui.close()

def load(path,default=None):
    try:return json.loads(Path(path).read_text(encoding='utf-8-sig'))
    except (OSError,ValueError):return default

def plan(healthy,failures,attempts,now,last_attempt=0,paused=False,busy=False,last_healthy=0):
    if paused:return 'paused'
    if healthy:return 'healthy'
    if failures<3:return 'waiting'
    if busy:return 'deferred_busy'
    # 已验证恢复后的新断线重新计时；同一持续故障仍限制重启频率。
    if (last_attempt>last_healthy and now-last_attempt<300) or len([value for value in attempts if value>last_healthy and now-value<3600])>=3:return 'cooldown'
    return 'recover'

def probe(url):
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({'http':'http://127.0.0.1:21000','https':'http://127.0.0.1:21000'}))
    try:
        with opener.open(urllib.request.Request(url,headers={'User-Agent':'LocalNetworkHealth/1.0'}),timeout=6) as response:return response.status<500
    except urllib.error.HTTPError as error:return 400<=error.code<500 and error.code not in (407,429)
    except (OSError,urllib.error.URLError):return False

def socks_backend_healthy():
    """另验RDC使用的SOCKS与后端TLS，不能只凭HTTP代理判断恢复。"""
    def exact(stream,size):
        result=b''
        while len(result)<size:
            chunk=stream.recv(size-len(result))
            if not chunk:raise OSError('代理提前断开')
            result+=chunk
        return result
    host='olvbkozcufcbptfogatw.supabase.co'
    try:
        with socket.create_connection(('127.0.0.1',21001),timeout=6) as stream:
            stream.sendall(b'\x05\x01\x00')
            if exact(stream,2)!=b'\x05\x00':return False
            encoded=host.encode('ascii');stream.sendall(b'\x05\x01\x00\x03'+bytes([len(encoded)])+encoded+struct.pack('!H',443))
            answer=exact(stream,4)
            if answer[:2]!=b'\x05\x00':return False
            size={1:4,4:16}.get(answer[3])
            if answer[3]==3:size=exact(stream,1)[0]
            if size is None:return False
            exact(stream,size+2)
            with ssl.create_default_context().wrap_socket(stream,server_hostname=host) as secured:
                return bool(secured.version())
    except (OSError,ValueError):return False

def network_healthy():
    with ThreadPoolExecutor(max_workers=3) as pool:
        http=list(pool.map(probe,('https://mcp.desktopcommander.app/api/mcp-info','https://chatgpt.com/cdn-cgi/trace')))
        backend=pool.submit(socks_backend_healthy).result()
    return any(http) and backend

def owned_processes():
    from ctypes import wintypes
    kernel=ctypes.windll.kernel32;kernel.OpenProcess.restype=wintypes.HANDLE
    kernel.QueryFullProcessImageNameW.argtypes=[wintypes.HANDLE,wintypes.DWORD,wintypes.LPWSTR,ctypes.POINTER(wintypes.DWORD)]
    kernel.CloseHandle.argtypes=[wintypes.HANDLE]
    rows=csv.reader(io.StringIO(subprocess.check_output(['tasklist','/FI','IMAGENAME eq xinghuan.exe','/FO','CSV','/NH'],text=True,errors='replace',creationflags=subprocess.CREATE_NO_WINDOW)))
    result=[]
    for row in rows:
        if len(row)<2 or row[0].lower()!='xinghuan.exe':continue
        pid=int(row[1]);handle=kernel.OpenProcess(0x1000,False,pid)
        if not handle:continue
        try:
            buffer=ctypes.create_unicode_buffer(32768);size=wintypes.DWORD(len(buffer))
            if kernel.QueryFullProcessImageNameW(handle,0,buffer,ctypes.byref(size)) and Path(buffer.value)==EXE:result.append(pid)
        finally:kernel.CloseHandle(handle)
    return result

def connect_once(record):
    import win32gui,win32process,win32con,win32api
    sys.path.insert(0,str(PROJECT))
    from window_capture import _window_bitmap
    from local_ui_ocr import recognize
    pids=set(owned_processes());windows=[]
    def visit(hwnd,_):
        if win32process.GetWindowThreadProcessId(hwnd)[1] in pids and win32gui.GetWindowText(hwnd)=='星环加速器':windows.append(hwnd)
        return True
    win32gui.EnumWindows(visit,None)
    if len(windows)!=1:raise RuntimeError('星环窗口不唯一，需要人工处理')
    hwnd=windows[0]
    # 不激活窗口、不移动系统鼠标；游戏暂停、锁屏或认证界面不操作。
    from passive_sender import require_desktop
    require_desktop()
    if STOP.exists() or (PROJECT/'.wechat_codex/UI-PAUSE').exists() or (PROJECT/'.wechat_codex/STOP').exists():raise RuntimeError('存在人工或界面暂停，停止连接操作')
    if win32gui.IsIconic(hwnd) or not win32gui.IsWindowVisible(hwnd):win32gui.ShowWindow(hwnd,win32con.SW_SHOWNOACTIVATE)
    time.sleep(.8)
    picture,rect=_window_bitmap(hwnd);rows=recognize(picture)
    texts=[''.join(row[0].split()) for row in rows]
    if any(word in text for text in texts for word in ('验证码','重新登录','账号密码','立即续费')):raise RuntimeError('星环需要本人登录或处理订阅，不自动操作')
    if any('点击断开' in text for text in texts):return 'already_connected'
    matches=[row for row in rows if ''.join(row[0].split())=='点击连接']
    if len(matches)!=1:raise RuntimeError('未识别到唯一的点击连接按钮；保留程序等待人工处理')
    if tuple(win32gui.GetWindowRect(hwnd))!=tuple(rect) or win32process.GetWindowThreadProcessId(hwnd)[1] not in set(owned_processes()):raise RuntimeError('星环窗口发生变化，禁止沿用坐标')
    _,x,y,w,h=matches[0];point=win32gui.ScreenToClient(hwnd,(rect[0]+x+w//2,rect[1]+y+h//2));client=win32gui.GetClientRect(hwnd)
    if not 0<=point[0]<client[2] or not 0<=point[1]<client[3]:raise RuntimeError('连接按钮不在目标窗口内')
    if STOP.exists() or (PROJECT/'.wechat_codex/UI-PAUSE').exists() or (PROJECT/'.wechat_codex/STOP').exists():raise RuntimeError('连接前检测到暂停')
    record.update(clickPhase='reserved',clickPid=win32process.GetWindowThreadProcessId(hwnd)[1]);save(record)
    position=win32api.MAKELONG(*point)
    win32gui.PostMessage(hwnd,win32con.WM_LBUTTONDOWN,win32con.MK_LBUTTON,position)
    win32gui.PostMessage(hwnd,win32con.WM_LBUTTONUP,0,position)
    record['clickPhase']='posted';save(record)
    return 'connection_click_posted'

def save(value):
    value['updatedAt']=datetime.datetime.now(datetime.timezone.utc).isoformat()
    temp=STATE.with_suffix('.tmp');temp.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');temp.replace(STATE)

def journal(event,**fields):
    """保留每次恢复结果；常规健康写状态即可，不记录凭证或网络正文。"""
    path=ROOT/'logs/star-network-recovery.jsonl';path.parent.mkdir(exist_ok=True)
    with path.open('a',encoding='utf-8') as stream:
        stream.write(json.dumps({'at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'event':event,**fields},ensure_ascii=False)+'\n')

def recover(record):
    from ctypes import wintypes
    sys.path.insert(0,str(PROJECT))
    from passive_sender import require_desktop
    require_desktop()
    kernel=ctypes.windll.kernel32;kernel.OpenProcess.restype=wintypes.HANDLE
    kernel.TerminateProcess.argtypes=[wintypes.HANDLE,wintypes.UINT];kernel.CloseHandle.argtypes=[wintypes.HANDLE]
    for pid in owned_processes():
        if STOP.exists() or (PROJECT/'.wechat_codex/UI-PAUSE').exists() or (PROJECT/'.wechat_codex/STOP').exists():raise RuntimeError('恢复前暂停')
        handle=kernel.OpenProcess(0x1001,False,pid)
        if handle:
            try:
                buffer=ctypes.create_unicode_buffer(32768);size=wintypes.DWORD(len(buffer))
                if kernel.QueryFullProcessImageNameW(handle,0,buffer,ctypes.byref(size)) and Path(buffer.value)==EXE:kernel.TerminateProcess(handle,0)
            finally:kernel.CloseHandle(handle)
    time.sleep(1)
    info=subprocess.STARTUPINFO();info.dwFlags|=subprocess.STARTF_USESHOWWINDOW;info.wShowWindow=0
    subprocess.Popen([str(EXE)],cwd=str(EXE.parent),startupinfo=info,creationflags=subprocess.CREATE_NO_WINDOW,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    time.sleep(5)
    if not account_check(record):raise RuntimeError('星环账号未核对为指定账号，停止自动连接并保留现场')
    # 应用启动和线路接通需要时间；每次点击后先等待，不能把尚在连接当作失败。
    deadline=time.monotonic()+20
    while True:
        try:
            record['connectionAction']=connect_once(record);break
        except RuntimeError as error:
            if '人工' in str(error) or '登录' in str(error) or '暂停' in str(error) or time.monotonic()>=deadline:raise
            time.sleep(1)
    deadline=time.monotonic()+55
    while time.monotonic()<deadline:
        if network_healthy():return True
        time.sleep(3)
    return False

def run():
    parser=argparse.ArgumentParser();parser.add_argument('--check-only',action='store_true');parser.add_argument('--force',action='store_true');args=parser.parse_args()
    now=time.time();old=load(STATE,{}) or {};attempts=[value for value in old.get('attempts',[]) if now-value<3600]
    policy=load(ROOT/'star-watch-policy.json',{}) or {};interval=max(3600,int(policy.get('intervalSeconds',3600)))
    # RDC旧进程或手工重复唤醒也不能绕过每小时检查的间隔。
    if not args.force and not args.check_only and not hourly_due(old,now,interval):return
    paused=STOP.exists() or (PROJECT/'.wechat_codex/UI-PAUSE').exists() or (PROJECT/'.wechat_codex/STOP').exists()
    healthy=network_healthy();round_failures=0 if healthy else 1
    # 三次确认放在本小时检查内，不能把旧的每分钟三轮改成等待三小时。
    if not healthy and not paused:
        for _ in range(2):
            time.sleep(2)
            if STOP.exists():break
            if network_healthy():healthy=True;round_failures=0;break
            round_failures+=1
    failures=0 if healthy else old.get('consecutiveFailures',0)+1
    listener=load(PROJECT/'.wechat_codex/listener-status.json',{}) or {};rdc=load(ROOT/'connection-health.json',{}) or {}
    busy=bool(listener.get('active',0) or rdc.get('activeCalls',0))
    last_healthy=now if healthy else old.get('lastHealthyAt',0)
    action=plan(healthy,round_failures,attempts,now,old.get('lastAttempt',0),paused,busy,last_healthy)
    record={'updatedAt':datetime.datetime.now(datetime.timezone.utc).isoformat(),'networkHealthy':healthy,'status':action,'consecutiveFailures':failures,'attempts':attempts,'lastAttempt':old.get('lastAttempt',0),'lastHealthyAt':last_healthy,'modelInvoked':False,'windowActivated':False,'systemMouseMoved':False,'connectionClickLiveVerified':old.get('connectionClickLiveVerified',False),'lastRecovery':old.get('lastRecovery'),'lastFailure':old.get('lastFailure'),'probes':'HTTP proxy plus RDC SOCKS backend TLS'}
    record.update(policyVersion=2,intervalSeconds=interval,hourlyCheckedAt=now,nextCheckAt=(int(now//interval)+1)*interval,roundFailures=round_failures,account=old.get('account',{}))
    record['accountCheckDeferred']=bool(paused or busy)
    if args.check_only:print(json.dumps(record,ensure_ascii=True));return
    if not paused and not busy and owned_processes():
        try:
            if not account_check(record):record['status']='account_needs_attention';save(record);return
        except Exception as error:
            record['status']='account_needs_attention';record['error']=str(error)[:200];save(record);return
    if action=='recover':
        record['lastAttempt']=now;record['attempts'].append(now);save(record)
        journal('recovery_started',failures=failures)
        try:
            record['networkHealthy']=recover(record);record['status']='recovered' if record['networkHealthy'] else 'recovery_unconfirmed'
            if record['networkHealthy']:
                record['consecutiveFailures']=0
                record['lastHealthyAt']=time.time()
                record['connectionClickLiveVerified']=record['connectionClickLiveVerified'] or record.get('connectionAction')=='connection_click_posted'
                record['lastRecovery']={'at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'connectionAction':record.get('connectionAction'),'clickPid':record.get('clickPid'),'networkVerified':True}
            if not record['networkHealthy']:record['lastFailure']={'at':time.time(),'reason':'重启和一次连接后，网络仍未确认'}
            journal('recovery_finished',status=record['status'],connectionAction=record.get('connectionAction'),networkHealthy=record['networkHealthy'])
        except Exception as error:
            record['status']='needs_attention';record['error']=str(error)[:240];record['lastFailure']={'at':time.time(),'reason':record['error']}
            journal('recovery_error',reason=record['error'])
    save(record)

def main():
    """任务调度与人工验收共享互斥锁，避免同时重启或点击。"""
    from ctypes import wintypes
    kernel=ctypes.windll.kernel32
    kernel.CreateMutexW.argtypes=[wintypes.LPVOID,wintypes.BOOL,wintypes.LPCWSTR];kernel.CreateMutexW.restype=wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes=[wintypes.HANDLE,wintypes.DWORD]
    kernel.ReleaseMutex.argtypes=[wintypes.HANDLE];kernel.CloseHandle.argtypes=[wintypes.HANDLE]
    handle=kernel.CreateMutexW(None,False,'Local\\XinghuanNetworkRecovery-Acer')
    if not handle:raise OSError('无法创建星环自愈互斥锁')
    acquired=False
    try:
        acquired=kernel.WaitForSingleObject(handle,0) in (0,128)
        if acquired:
            try:run()
            except Exception as error:
                record=load(STATE,{}) or {}
                record.update(status='needs_attention',error=str(error)[:200],modelInvoked=False,hourlyCheckedAt=time.time(),intervalSeconds=3600)
                save(record);journal('hourly_check_error',reason=record['error'])
    finally:
        if acquired:kernel.ReleaseMutex(handle)
        kernel.CloseHandle(handle)

if __name__=='__main__':main()
