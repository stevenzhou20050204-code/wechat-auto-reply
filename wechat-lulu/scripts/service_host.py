"""本机安装完成后自动等待登录、绑定账号并启动既有守护；不发送验收消息。"""
from __future__ import annotations
import argparse,base64,contextlib,datetime,hashlib,importlib.util,json,os,re,socket,ssl,subprocess,sys,time,uuid
from pathlib import Path

def load(path,default=None):
    try:return json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError,ValueError):return default
def atomic(path,obj):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True);temp=path.with_name(path.name+"."+uuid.uuid4().hex+".tmp")
    temp.write_text(json.dumps(obj,ensure_ascii=False,indent=2),encoding="utf-8");os.replace(temp,path)
def psq(value):return "'"+str(value).replace("'","''")+"'"
def ps(code,timeout=60):
    r=subprocess.run(["powershell.exe","-NoProfile","-NonInteractive","-EncodedCommand",base64.b64encode(code.encode("utf-16-le")).decode()],
        capture_output=True,text=True,encoding="utf-8",errors="replace",timeout=timeout,creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0))
    if r.returncode:raise RuntimeError("后台系统步骤失败，退出码 "+str(r.returncode))
    return r.stdout.strip()
def paused(p):
    state=Path(p["project_root"])/".wechat_codex"
    return any((state/n).exists() for n in ("STOP","UI-PAUSE")) or (Path(p["rdc_root"])/"STAR-WATCH-STOP").exists()
def write_setup(p,status,**extra):
    value={"status":status,"updatedAt":time.time(),"modelInvoked":False,"testMessagesSent":0,**extra}
    atomic(Path(p["install_root"])/"setup-status.json",value);return value
def task_action(p,key,start=True):
    name=p["task_names"][key]
    command="$t=Get-ScheduledTask -TaskName "+psq(name)+";if($t.Settings.Enabled){"
    command+=("if($t.State -ne 'Running'){Start-ScheduledTask -TaskName $t.TaskName};" if start else "")
    command+="'enabled'}else{'disabled'}"
    return ps(command)=="enabled"
def locked():
    if os.name!="nt":return True
    import ctypes
    u=ctypes.WinDLL("user32",use_last_error=True);u.OpenInputDesktop.restype=ctypes.c_void_p
    desk=u.OpenInputDesktop(0,False,0x0100)
    if not desk:return True
    u.SwitchDesktop.argtypes=[ctypes.c_void_p];ok=bool(u.SwitchDesktop(desk));u.CloseDesktop.argtypes=[ctypes.c_void_p];u.CloseDesktop(desk)
    return not ok
def environment(p):
    env=os.environ.copy();env.pop("OPENAI_API_KEY",None);env.pop("CODEX_API_KEY",None);env["PYTHONUTF8"]="1";env["WECHAT_CODEX_EXE"]=p["codex_exe"];env["CODEX_CLI_PATH"]=p["codex_exe"]
    if p.get("codex_home"):env["CODEX_HOME"]=p["codex_home"]
    env["LULU_PROJECT_ROOT"]=p["project_root"];env["PATH"]=str(Path(p["node"]).parent)+os.pathsep+env.get("PATH","")
    return env
def process_identities():
    raw=ps("@(Get-CimInstance Win32_Process | Where-Object {$_.Name -in 'Weixin.exe','WeChat.exe','xinghuan.exe','ssf2-v2r-pl.exe'} | Select-Object ProcessId,ExecutablePath)|ConvertTo-Json -Compress",40)
    obj=json.loads(raw) if raw else []
    return [obj] if isinstance(obj,dict) else obj
def signed_owned(p,kind):
    target=Path(p["wechat_exe" if kind=="wechat" else "xinghuan_exe"])
    return [r["ProcessId"] for r in process_identities() if r.get("ExecutablePath") and Path(r["ExecutablePath"])==target]
def star_account(p):
    root=Path(p["rdc_root"]);sys.path.insert(0,p["project_root"])
    spec=importlib.util.spec_from_file_location("lulu_star_account",root/"star-account.py");mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
    def guard():
        if paused(p) or locked():raise RuntimeError("暂停或锁屏，停止初始账号读取")
        status=load(Path(p["project_root"])/".wechat_codex/listener-status.json",{})
        remote=load(root/"connection-health.json",{})
        if status.get("active") or remote.get("activeCalls"):raise RuntimeError("当前繁忙，延后初始读取")
    ui=mod.NativeUI(lambda:signed_owned(p,"star"),guard)
    try:observed=ui.account()
    finally:ui.close()
    if not observed or observed=="logged_out":return False
    policy=load(root/"star-watch-policy.json",{})
    if policy.get("expectedAccount") and policy["expectedAccount"]!=observed:raise RuntimeError("星环账号与已绑定账号不同，不自动换号或覆盖")
    policy.update(policyVersion=2,intervalSeconds=3600,expectedAccount=observed,accountSwitchEnabled=False)
    atomic(root/"star-watch-policy.json",policy);return True
def detect_account(p):
    if not signed_owned(p,"wechat"):return None
    sys.path.insert(0,p["project_root"])
    from stable_db import make_db
    db=make_db()
    try:
        info=db.get_self_info()
        account=info.get("username") if isinstance(info,dict) else None
        return account if isinstance(account,str) and account and "@chatroom" not in account else None
    finally:
        close=getattr(db,"close",None)
        if callable(close):close()
def bind_config(p,account):
    root=Path(p["project_root"]);updates=[]
    for name in ("wechat_autoreply.json","wechat_autoreply.lulu.pending.json"):
        path=root/name;value=load(path)
        if not isinstance(value,dict):raise ValueError("配置缺失或损坏，不重建覆盖")
        if value.get("account_username") not in ("",account):raise ValueError("当前微信与原绑定不同，保留原配置")
        if value.get("account_username")==account:
            if value.get("enabled") is not True:raise ValueError("已绑定账号的自动回复被禁用，保持人工决定")
            continue
        value["account_username"]=account;value["enabled"]=True;updates.append((path,value))
    for path,value in updates:
        if paused(p):raise ValueError("人工暂停，停止绑定")
        atomic(path,value)

def active_ports(p):
    code="$r="+psq(str(Path(p["xinghuan_exe"]).parent))+";$pids=@((Get-CimInstance Win32_Process | Where-Object {$_.Name -in 'xinghuan.exe','ssf2-v2r-pl.exe' -and $_.ExecutablePath -and (Split-Path -Parent $_.ExecutablePath) -ieq $r}).ProcessId);@(Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue | Where-Object {$_.OwningProcess -in $pids} | Select-Object -ExpandProperty LocalPort -Unique)|ConvertTo-Json -Compress"
    raw=ps(code,40);values=json.loads(raw) if raw else []
    return ([values] if isinstance(values,int) else values)[:32]
def classify_port(port):
    try:
        with socket.create_connection(("127.0.0.1",port),timeout=1) as s:
            s.sendall(b"\x05\x01\x00");answer=s.recv(2)
            if answer==b"\x05\x00":return "socks"
    except OSError:pass
    try:
        with socket.create_connection(("127.0.0.1",port),timeout=2) as s:
            s.sendall(b"CONNECT chatgpt.com:443 HTTP/1.1\r\nHost: chatgpt.com:443\r\n\r\n")
            answer=s.recv(512)
            if answer.startswith(b"HTTP/") and b" 200 " in answer.split(b"\r\n",1)[0]:return "http"
    except OSError:pass
    return None
def bind_ports(p):
    selected={}
    for port in active_ports(p):
        kind=classify_port(port)
        if kind and kind not in selected:selected[kind]=port
    if "http" not in selected or "socks" not in selected:return False
    root=Path(p["install_root"]);old=load(root/"port-bindings.json")
    current={"http":selected["http"],"socks":selected["socks"]}
    if old and old!=current:raise RuntimeError("已部署代理端口发生改变，保留运行文件并报告，等待空闲修复")
    if not old:
        installed=load(root/"runtime-installed.json",{})
        for rel in installed:
            f=root/rel
            if f.suffix not in (".py",".cjs",".ps1"):continue
            before=f.read_bytes();s=before.decode("utf-8-sig")
            # 初次启用前更新已验证源文件的端口常量，启动后不热改运行源码。
            ports={"21000":str(current["http"]),"21001":str(current["socks"])}
            s=re.sub(r"(?<![0-9])(21000|21001)(?![0-9])",lambda m:ports[m[1]],s)
            data=s.encode("utf-8-sig" if f.suffix==".ps1" else "utf-8")
            if data!=before:f.write_bytes(data)
            installed[rel]=hashlib.sha256(f.read_bytes()).hexdigest()
        atomic(root/"runtime-installed.json",installed);atomic(root/"port-bindings.json",current)
    p["http_port"]=current["http"];p["socks_port"]=current["socks"];atomic(root/"profile.json",p)
    return True
def codex_ready(p):
    r=subprocess.run([p["codex_exe"],"login","status"],env=environment(p),capture_output=True,timeout=30,creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0))
    return r.returncode==0
def authorize_codex_once(p):
    root=Path(p["install_root"]);marker=root/"codex-onboarding.json"
    if marker.exists() or paused(p) or locked():return
    (root/"logs").mkdir(exist_ok=True)
    out=open(root/"logs/codex-login.stdout.log","ab");err=open(root/"logs/codex-login.stderr.log","ab")
    try:child=subprocess.Popen([p["codex_exe"],"login"],cwd=root,env=environment(p),stdout=out,stderr=err,creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0))
    finally:out.close();err.close()
    atomic(marker,{"status":"authorization_pending","pid":child.pid,"startedAt":time.time()})

def authorize_rdc_once(p):
    root=Path(p["rdc_root"]);marker=Path(p["install_root"])/"rdc-onboarding.json"
    if marker.exists():return
    if paused(p) or locked():return
    # 首次仅启动官方设备授权流程，最终授权由本人在官方页面完成。
    env=environment(p);env.pop("DC_NONINTERACTIVE",None)
    env["NODE_OPTIONS"]='--require "'+str(root/"dc-network.cjs").replace("\\","/")+'"'
    env["DC_HEALTH_PATH"]=str(root/"connection-health.json")
    (root/"logs").mkdir(exist_ok=True)
    out=open(root/"logs/onboarding.stdout.log","ab");err=open(root/"logs/onboarding.stderr.log","ab")
    try:
        child=subprocess.Popen([str(root/"runtime/node.exe"),str(root/"app/node_modules/@wonderwhy-er/desktop-commander/dist/index.js"),"remote"],
            cwd=root,env=env,stdout=out,stderr=err,creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0))
    finally:out.close();err.close()
    atomic(marker,{"status":"owner_authorization_pending","pid":child.pid,"startedAt":time.time()})
def owner_authorization_required(health,now=None):
    if health.get("authenticationStatus")!="owner-required":return False
    now=time.time() if now is None else now
    value=health.get("updatedAt")
    if isinstance(value,str):
        try:value=datetime.datetime.fromisoformat(value.replace("Z","+00:00")).timestamp()
        except ValueError:return False
    return isinstance(value,(int,float)) and 0<=now-value<=45

def server_pong_fresh(health,now=None):
    now=time.time() if now is None else now
    def stamp(value):
        if isinstance(value,str):
            try:return datetime.datetime.fromisoformat(value.replace("Z","+00:00")).timestamp()
            except ValueError:return None
        return value if isinstance(value,(int,float)) else None
    updated=stamp(health.get("updatedAt"))
    if updated is None or not 0<=now-updated<=45:return False
    age=health.get("lastPongAgeMs")
    if isinstance(age,(int,float)):return age>=0 and now-updated+age/1000<=75
    pong=stamp(health.get("lastServerPongAt") or health.get("lastPongAt"))
    return pong is not None and 0<=now-pong<=75

def rdc_process_matches(p,health):
    pid=health.get("pid")
    if not isinstance(pid,int) or pid<=0:return False
    root=Path(p["rdc_root"])
    code="$p=Get-CimInstance Win32_Process -Filter 'ProcessId="+str(pid)+"';if($p -and $p.ExecutablePath -ieq "+psq(root/"runtime/node.exe")+" -and $p.CommandLine -like "+psq("*"+str(root/"app/node_modules/@wonderwhy-er/desktop-commander/dist/index.js")+"*")+" -and $p.CommandLine -match '\\sremote(?:\\s|$)'){'matched'}"
    return ps(code,30)=="matched"

def native_capability_ready(p):
    project=Path(p["project_root"]);path=project/".wechat_codex/image-generation-health.json"
    value=load(path,{})
    if path.exists() and time.time()-path.stat().st_mtime<4500:return value.get("ok") is True
    logs=Path(p["install_root"])/"logs";logs.mkdir(exist_ok=True)
    with (logs/"native-capability.stdout.log").open("wb") as out,(logs/"native-capability.stderr.log").open("wb") as err:
        subprocess.run([p["python"],"-B",str(project/"lulu_image_health.py"),"--engine-root",p["engine_root"]],cwd=project,env=environment(p),stdout=out,stderr=err,timeout=150,creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0))
    return load(path,{}).get("ok") is True

def reconcile(profile_path):
    p=load(profile_path);root=Path(p["install_root"])
    if paused(p):return write_setup(p,"paused")
    if locked():return write_setup(p,"waiting_for_unlock")
    if not signed_owned(p,"star"):return write_setup(p,"waiting_for_star_login")
    if not star_account(p):return write_setup(p,"waiting_for_star_login")
    # 登录正确后由现有严格UI守护单轮连接，后续仍为每小时检查。
    if not bind_ports(p):
        task_action(p,"network");return write_setup(p,"waiting_for_star_connection")
    if not codex_ready(p):
        authorize_codex_once(p)
        return write_setup(p,"codex_authorization_required")
    if not native_capability_ready(p):return write_setup(p,"native_capability_needs_attention")
    session=Path.home()/".desktop-commander-device/device.json"
    if not session.exists():authorize_rdc_once(p)
    task_action(p,"rdc")
    if owner_authorization_required(load(Path(p["rdc_root"])/"connection-health.json",{})):
        return write_setup(p,"rdc_authorization_required",rpcVerified=False)
    account=detect_account(p)
    if account is None:return write_setup(p,"waiting_for_wechat_login",rdcAuthorizationPending=not session.exists())
    bind_config(p,account)
    task_action(p,"listener")
    task_action(p,"health")
    status=load(Path(p["project_root"])/".wechat_codex/listener-status.json",{})
    listener_ready=status.get("mode")=="lulu-assistant" and time.time()-float(status.get("updatedAt",0))<30
    rdc=load(Path(p["rdc_root"])/"connection-health.json",{})
    server_fresh=server_pong_fresh(rdc)
    joined=rdc.get("channelState")=="joined" or rdc.get("joined") is True
    if listener_ready and server_fresh and joined and rdc_process_matches(p,rdc):return write_setup(p,"ready",listenerRunning=True,rdcServerPongFresh=True,rpcVerified=False)
    return write_setup(p,"starting",listenerRunning=listener_ready,rdcServerPongFresh=server_fresh,rdcAuthorizationPending=not session.exists(),rpcVerified=False)
def doctor(profile_path):
    p=load(profile_path)
    if not p:return {"status":"not_installed"}
    return {"profile":str(profile_path),"installation":load(Path(p["install_root"])/"bootstrap-state.json",{}),
        "setup":load(Path(p["install_root"])/"setup-status.json",{}),"paused":paused(p),
        "listener":{k:v for k,v in load(Path(p["project_root"])/".wechat_codex/listener-status.json",{}).items() if k in ("pid","updatedAt","mode","contactCount","replyQueue","interaction")},
        "rdc":{k:v for k,v in load(Path(p["rdc_root"])/"connection-health.json",{}).items() if k in ("pid","updatedAt","channelState","lastPongAgeMs","activeCalls","startupStage")},"modelInvoked":False,"testMessagesSent":0,"rpcPerformed":False}
def hourly_due(root,key,hours=None):
    now=datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8)))
    if hours and now.hour not in hours:return False
    path=root/("hourly-"+key+".json");slot=now.strftime("%Y-%m-%dT%H")
    if load(path,{}).get("slot")==slot:return False
    atomic(path,{"slot":slot,"startedAt":time.time()});return True
def main():
    a=argparse.ArgumentParser();a.add_argument("--profile",type=Path,required=True);a.add_argument("--service",choices=["setup","listener","rdc","network","health"],required=True);args=a.parse_args()
    p=load(args.profile);root=Path(p["install_root"]);logs=root/"logs";logs.mkdir(exist_ok=True)
    if sys.stdout is None:sys.stdout=(logs/(args.service+".python.log")).open("a",encoding="utf-8",buffering=1)
    if sys.stderr is None:sys.stderr=(logs/(args.service+".python.err")).open("a",encoding="utf-8",buffering=1)
    lock=open(root/("service-"+args.service+".lock"),"a+b")
    import msvcrt
    lock.write(b"0");lock.flush();lock.seek(0)
    try:msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)
    except OSError:return
    try:
        if args.service=="setup":
            while True:
                try:result=reconcile(args.profile)
                except Exception as exc:result=write_setup(p,"needs_attention",errorType=type(exc).__name__)
                if result["status"] in ("ready","paused"):break
                time.sleep(20)
            return
        if args.service=="listener":
            if paused(p) or load(Path(p["project_root"])/"wechat_autoreply.json",{}).get("enabled") is not True:return
            command=["powershell.exe","-NoProfile","-NonInteractive","-File",str(Path(p["engine_root"])/"WechatAutoReplySupervise.ps1")]
        elif args.service=="rdc":
            if paused(p):return
            if not (Path.home()/".desktop-commander-device/device.json").exists():return
            command=["powershell.exe","-NoProfile","-NonInteractive","-File",str(Path(p["rdc_root"])/"Supervise.ps1")]
        elif args.service=="network":
            if paused(p) or not hourly_due(root,"network"):return
            command=[p["python"],"-B",str(Path(p["rdc_root"])/"star-network-watch.py")]
        else:
            if not hourly_due(root,"health",range(8,23)):return
            command=["powershell.exe","-NoProfile","-NonInteractive","-File",str(Path(p["engine_root"])/"WechatLuluHealth.ps1"),"-NoRepair"]
        with (logs/(args.service+".stdout.log")).open("ab") as out,(logs/(args.service+".stderr.log")).open("ab") as err:
            subprocess.run(command,cwd=root,env=environment(p),stdout=out,stderr=err,creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0),check=True)
    finally:
        lock.seek(0);msvcrt.locking(lock.fileno(),msvcrt.LK_UNLCK,1);lock.close()
if __name__=="__main__":
    import re
    main()
