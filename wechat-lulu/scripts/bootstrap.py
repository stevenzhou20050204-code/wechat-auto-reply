"""Windows鹭鹭自动部署：安装依赖、路径绑定、任务注册；不测试发送或调用模型。"""
from __future__ import annotations
import argparse, base64, contextlib, hashlib, json, os, platform, re, shutil
import subprocess, sys, time, urllib.request, uuid, zipfile
from pathlib import Path
SKILL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL / "scripts"))
from lulu_skill import verified_sources

def load(path, default=None):
    try: return json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except FileNotFoundError: return default

def atomic(path, value):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp, path)

def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def psquote(value):
    return "'" + str(value).replace("'", "''") + "'"

def run(args, cwd=None, timeout=1800, env=None, log=None):
    result = subprocess.run([str(a) for a in args], cwd=cwd, env=env, stdin=subprocess.DEVNULL,
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if log:
        Path(log).parent.mkdir(parents=True, exist_ok=True)
        Path(log).write_text(result.stdout + "\n" + result.stderr, encoding="utf-8")
    if result.returncode:
        raise RuntimeError("步骤执行失败，退出码 " + str(result.returncode) + ("；本地日志 " + str(log) if log else ""))
    return result.stdout.strip()

def powershell(code, **kwargs):
    encoded = base64.b64encode(code.encode("utf-16-le")).decode("ascii")
    return run(["powershell.exe", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded], **kwargs)

def no_links(path):
    for p in (Path(path), *Path(path).parents):
        if p.is_symlink() or (hasattr(p, "is_junction") and p.is_junction()):
            raise ValueError("安装路径不能包含符号链接或目录联接")

def validate_root(root):
    root = Path(root).absolute()
    no_links(root)
    for p in (SKILL, Path.home(), Path(root.anchor)):
        if root == p or root in p.parents or p == SKILL and p in root.parents:
            raise ValueError("安装目标不能是用户根目录、磁盘根目录、skill或其父目录")
    marker = root / ".lulu-bootstrap-owner.json"
    if root.exists() and any(root.iterdir()) and not marker.is_file():
        raise ValueError("目标目录已有非本安装器管理的文件，拒绝覆盖")
    if marker.exists():
        value = load(marker)
        if value.get("owner") != "wechat-lulu-bootstrap-v2" or Path(value["root"]).resolve() != root.resolve():
            raise ValueError("安装器目录归属不匹配")
    return root

def claim(root):
    root = validate_root(root); root.mkdir(parents=True, exist_ok=True)
    marker = root / ".lulu-bootstrap-owner.json"
    if not marker.exists(): atomic(marker, {"owner": "wechat-lulu-bootstrap-v2", "root": str(root)})
    return root

def task_names(root):
    tag = hashlib.sha256(str(root.resolve()).casefold().encode()).hexdigest()[:10]
    return {k: "WechatLulu-" + tag + "-" + k for k in ("bootstrap", "setup", "listener", "health", "rdc", "network")}

def make_profile(root):
    return {"version": 2, "install_root": str(root), "project_root": str(root/"project"),
        "engine_root": str(root/"engine"), "rdc_root": str(root/"rdc"),
        "deployment_state": str(root/"engine/LULU-DEPLOYMENT-STATE.json"),
        "task_names": task_names(root), "timezone": "Asia/Shanghai",
        "check_hours": {"start": 8, "end": 22}, "health_stale_seconds": 4500,
        "http_port": 21000, "socks_port": 21001, "codex_home":os.environ.get("CODEX_HOME",str(Path.home()/".codex")), "xinghuan_exe": "", "wechat_exe": "",
        "python": str(root/"project/.venv/Scripts/python.exe"),
        "pythonw": str(root/"project/.venv/Scripts/pythonw.exe")}

def foreign_listener(root):
    if os.name != "nt": return False
    code = "$r=" + psquote(str(root)) + ";@((Get-CimInstance Win32_Process) | Where-Object {$_.Name -in 'python.exe','pythonw.exe' -and $_.CommandLine -like '*run_two_contacts.py*' -and $_.CommandLine -notlike ('*'+$r+'*')}).Count"
    return int(powershell(code, timeout=30) or "0") > 0

def portable_source(rel, data, profile):
    if not rel.endswith((".py", ".ps1", ".cjs")): return data
    text = data.decode("utf-8-sig")
    # 文件内读取同一份本机profile，避免用户名、盘符或任务名写死。
    prelude = "$luluProfile=Get-Content -LiteralPath (Join-Path (Split-Path -Parent $PSScriptRoot) 'profile.json') -Raw -Encoding UTF8 | ConvertFrom-Json\n"
    if rel.startswith("engine/") and rel.endswith(".ps1"):
        if text.startswith("param(") or text.startswith("param ("):
            pos = text.index("\n") + 1; text = text[:pos] + prelude + text[pos:]
        else: text = prelude + text
        text = re.sub(r"(?m)^\$project=.*$", "$project=$luluProfile.project_root", text)
        text = re.sub(r"(?m)^    \$root='.*DesktopCommanderRemote'$", "    $root=$luluProfile.rdc_root", text)
        for name, key in (("task","listener"),("taskName","listener")):
            text = re.sub(r"(?m)^\$" + name + r"='[^']*'$", "$"+name+"=$luluProfile.task_names."+key, text)
        text = re.sub(r"(?m)^    \$rdcHealthPath='.*'$", "    $rdcHealthPath=Join-Path $luluProfile.rdc_root 'connection-health.json'", text)
        text = re.sub(r"(?m)^    \$rdcManagerPath='.*'$", "    $rdcManagerPath=Join-Path $luluProfile.rdc_root 'status.json'", text)
        text = re.sub(r"(?m)^        \$starRoot='.*'$", "        $starRoot=$luluProfile.rdc_root", text)
        for old, key in (("Desktop Commander Remote - Acer","rdc"),("Xinghuan Network Recovery - Acer","network")):
            text = text.replace("'"+old+"'", "$luluProfile.task_names."+key)
        text = text.replace("WechatTwoContactAutoReply-Acer", task_names(Path(profile["install_root"]))["listener"])
        text=text.replace(" -or ($chinaNow.Hour -eq 22 -and $chinaNow.Minute -gt 0)", "")
        # 个人名单由实际配置与目录验证，通用安装不能强制旧机器上的联系人。
        text = "\n".join(line for line in text.split("\n") if not (
            "CONFIGURE_CONTACT_" in line or "排除标签示例" in line or "排除昵称示例" in line or
            "已恢复的联系人甲固定账号" in line or "$starHealth.account.observed -ne" in line))
    if rel == "project/start_wechat_listener.ps1":
        text = re.sub(r"(?m)^\$controller=.*$", "$controller=Join-Path (Split-Path -Parent $PSScriptRoot) 'engine/WechatAutoReplyControl.ps1'", text)
    if rel.startswith("rdc/") and rel.endswith(".ps1"):
        if text.startswith("param("):
            pos=text.index("\n")+1;text=text[:pos]+prelude+text[pos:]
        else:text=prelude+text
        if rel=="rdc/StarNetworkWatchControl.ps1":text=re.sub(r"(?m)^\$taskName='[^']*'$", "$taskName=$luluProfile.task_names.network", text)
        for old, key in (("Desktop Commander Remote - Acer","rdc"),("Xinghuan Network Recovery - Acer","network")):
            text = text.replace("'"+old+"'", "$luluProfile.task_names."+key)
        text = text.replace("DesktopCommanderRemote-Acer", task_names(Path(profile["install_root"]))["rdc"])
    if rel == "rdc/star-network-watch.py":
        text = re.sub(r"(?m)^EXE=.*$", "DEPLOY=json.loads((ROOT.parent/'profile.json').read_text(encoding='utf-8-sig'))\nEXE=Path(DEPLOY['xinghuan_exe'])", text)
        text = re.sub(r"(?m)^PROJECT=.*$", "PROJECT=Path(DEPLOY['project_root'])", text)
        text = text.replace("XinghuanNetworkRecovery-Acer",profile["task_names"]["network"])
    if rel == "rdc/star-account.py":
        text = text.replace("EXPECTED=''\nif not EXPECTED:\n    raise RuntimeError('请先配置目标账号；分发版禁止自动登录')",
            "EXPECTED=json.loads((ROOT/'star-watch-policy.json').read_text(encoding='utf-8-sig')).get('expectedAccount','')")
        needle = "def ensure_account(ui,expected=EXPECTED,load_secret=credential,old=None,save=lambda value:None):"
        text = text.replace(needle, needle+"\n    if not expected:return {'status':'owner_required','reason':'目标账号尚未从本机登录界面绑定'}")
    ports={"21000":str(profile.get("http_port",21000)),"21001":str(profile.get("socks_port",21001))}
    text=re.sub(r"(?<![0-9])(21000|21001)(?![0-9])",lambda m:ports[m[1]],text)
    return text.encode("utf-8-sig" if rel.endswith(".ps1") else "utf-8")

def stage_runtime(root, profile):
    files = verified_sources()
    for rel, source in files.items():
        dest = root / rel; dest.parent.mkdir(parents=True, exist_ok=True)
        content = portable_source(rel, source.read_bytes(), profile)
        if dest.exists() and dest.read_bytes() == content: continue
        if dest.exists() and (root/"bootstrap-state.json").exists():
            # 重跑不会覆盖已部署/已人工调整的源文件。
            old = load(root/"runtime-installed.json", {}).get(rel)
            if old is None or sha(dest) != old: raise ValueError("已安装源码被修改，保留并停止: " + rel)
        temp = dest.with_name(dest.name+".installing"); temp.write_bytes(content); os.replace(temp,dest)
    installed = {rel: sha(root/rel) for rel in files}
    atomic(root/"runtime-installed.json", installed)
    for name in ("service_host.py",):
        shutil.copyfile(SKILL/"scripts"/name, root/name)
    shutil.copyfile(SKILL/"scripts/register_tasks.ps1", root/"register_tasks.ps1")
    config = root/"project/wechat_autoreply.json"
    if not config.exists(): atomic(config, load(SKILL/"assets/config.template.json"))
    pending=root/"project/wechat_autoreply.lulu.pending.json"
    if not pending.exists(): atomic(pending, load(config))
    if not (root/"rdc/star-watch-policy.json").exists():
        atomic(root/"rdc/star-watch-policy.json", {"policyVersion":2,"intervalSeconds":3600,"expectedAccount":"","accountSwitchEnabled":False})
    if not Path(profile["deployment_state"]).exists():
        atomic(profile["deployment_state"], {"bootstrapVersion":2,"monitoring":{}})
    atomic(root/"profile.json", profile)

def download(url, target, expected=None):
    if not url.startswith("https://"): raise ValueError("只允许HTTPS软件源")
    target=Path(target); target.parent.mkdir(parents=True,exist_ok=True)
    if target.exists() and expected and sha(target)==expected:return target
    part=target.with_name(target.name+".part")
    request=urllib.request.Request(url,headers={"User-Agent":"WechatLuluBootstrap/2"})
    with urllib.request.urlopen(request,timeout=40) as src, part.open("wb") as dst:
        if not src.url.startswith("https://"):raise ValueError("下载被重定向至不安全链接")
        shutil.copyfileobj(src,dst)
    if expected and sha(part)!=expected:raise ValueError("下载文件SHA256不一致")
    os.replace(part,target);return target

def signed(path, publisher):
    data=powershell("$s=Get-AuthenticodeSignature -LiteralPath "+psquote(path)+";[pscustomobject]@{status=[string]$s.Status;subject=$s.SignerCertificate.Subject}|ConvertTo-Json -Compress",timeout=60)
    value=json.loads(data)
    if value.get("status")!="Valid" or publisher not in value.get("subject",""):raise ValueError("软件签名核验失败")
    return value

def unzip_checked(archive, dest):
    dest=Path(dest).resolve();dest.mkdir(parents=True,exist_ok=True)
    with zipfile.ZipFile(archive) as z:
        for f in z.infolist():
            p=(dest/f.filename).resolve()
            if dest not in p.parents and p!=dest:raise ValueError("软件压缩包路径越界")
            if (f.external_attr>>16)&0o170000==0o120000:raise ValueError("压缩包不能包含符号链接")
        z.extractall(dest)

def ensure_node(root, catalog):
    node=root/"tools/node/node.exe"
    if node.is_file():return node
    index=json.loads(urllib.request.urlopen(catalog["node"]["index"],timeout=30).read())
    major=catalog["node"]["major"]
    versions=[x for x in index if x.get("lts") and x["version"].startswith("v"+str(major)+".") and "win-x64-zip" in x["files"]]
    if not versions:raise RuntimeError("没有兼容的Node LTS发行版")
    version=versions[0]["version"];filename="node-"+version+"-win-x64.zip"
    base="https://nodejs.org/dist/"+version+"/"
    sums=urllib.request.urlopen(base+"SHASUMS256.txt",timeout=30).read().decode()
    checksum=next(line.split()[0] for line in sums.splitlines() if line.endswith("  "+filename))
    archive=download(base+filename,root/"downloads"/filename,checksum)
    expanded=root/"tools/node-unpack";unzip_checked(archive,expanded)
    source=expanded/("node-"+version+"-win-x64")
    (root/"tools/node").mkdir(parents=True,exist_ok=True)
    shutil.copytree(source,root/"tools/node",dirs_exist_ok=True)
    return node

def npm(root, node, args, log):
    cli=node.parent/"node_modules/npm/bin/npm-cli.js"
    if not cli.exists():raise RuntimeError("Node分发包缺少npm")
    env=os.environ.copy();env["PATH"]=str(node.parent)+os.pathsep+env.get("PATH","")
    env["PUPPETEER_SKIP_DOWNLOAD"]="true";env["DO_NOT_TRACK"]="1"
    return run([node,cli,*args],env=env,log=root/"logs"/log)

def find_codex(root):
    candidates=[]
    explicit=os.environ.get("WECHAT_CODEX_EXE") or os.environ.get("CODEX_CLI_PATH")
    if explicit:candidates.append(Path(explicit))
    for command in ("codex.exe",):
        path=shutil.which(command)
        if path:candidates.append(Path(path))
    base=Path(os.environ.get("LOCALAPPDATA",""))/"OpenAI/Codex/bin"
    if base.exists():candidates+=sorted(base.glob("*/codex.exe"),key=lambda p:p.stat().st_mtime,reverse=True)
    local=root/"tools/codex/node_modules/@openai"
    if local.exists():candidates+=list(local.glob("codex*/**/codex.exe"))
    return next((p for p in candidates if p.is_file()),None)

def python_dependencies(root, profile):
    python=Path(profile["python"])
    if not python.exists():run([sys.executable,"-m","venv",root/"project/.venv"],timeout=180)
    stamp=root/"dependency-state.json"
    fingerprint=hashlib.sha256((root/"project/pyproject.toml").read_bytes()+(root/"project/requirements-lulu-ocr.txt").read_bytes()).hexdigest()
    if load(stamp,{}).get("pythonFingerprint")!=fingerprint:
        run([python,"-m","pip","install","--disable-pip-version-check","--upgrade","pip","setuptools","wheel"],log=root/"logs/pip-tools.log")
        run([python,"-m","pip","install","--disable-pip-version-check","-e",str(root/"project")+"[guia]","-r",root/"project/requirements-lulu-ocr.txt"],log=root/"logs/pip-runtime.log")
        run([python,"-m","pip","check"],log=root/"logs/pip-check.log")
        atomic(stamp,{"pythonFingerprint":fingerprint})

def dependencies(root, profile, catalog):
    python_dependencies(root,profile)
    node=ensure_node(root,catalog);profile["node"]=str(node)
    codex=find_codex(root)
    if codex is None:
        npm(root,node,["install","--prefix",str(root/"tools/codex"),catalog["codex"]["npm"],"--ignore-scripts","--no-audit","--no-fund"],"npm-codex.log")
        codex=find_codex(root)
    if codex is None:raise RuntimeError("Codex原生程序安装后仍不可用")
    profile["codex_exe"]=str(codex)
    help_text=run([codex,"app-server","--help"],timeout=30)
    profile["codex_app_server_proxy"]=bool(re.search(r"(?m)^\s+proxy(?:\s|$)",help_text))
    if not profile["codex_app_server_proxy"]:
        installed=load(root/"runtime-installed.json",{})
        for rel in installed:
            f=root/rel
            if f.suffix!=".py":continue
            before=f.read_text(encoding="utf-8-sig")
            after=re.sub(r"([\"'])app-server\1\s*,\s*([\"'])proxy\2", "\"app-server\"", before)
            if after!=before:f.write_text(after,encoding="utf-8");installed[rel]=sha(f)
        atomic(root/"runtime-installed.json",installed)
    network=root/"rdc/network-runtime"
    if any(load(network/"node_modules"/name/"package.json",{}).get("version")!=version for name,version in catalog["rdc"]["network"].items()):
        npm(root,node,["install","--prefix",str(network),*[name+"@"+version for name,version in catalog["rdc"]["network"].items()],"--ignore-scripts","--no-audit","--no-fund"],"npm-rdc-network.log")
    package=root/"rdc/app/node_modules/@wonderwhy-er/desktop-commander"
    if load(package/"package.json",{}).get("version")!=catalog["rdc"]["version"]:
        npm(root,node,["install","--prefix",str(root/"rdc/app"),catalog["rdc"]["npm"]+"@"+catalog["rdc"]["version"],"--ignore-scripts","--no-audit","--no-fund"],"npm-rdc.log")
        npm(root,node,["rebuild","--prefix",str(root/"rdc/app"),"@vscode/ripgrep","--no-audit","--no-fund"],"npm-ripgrep.log")
    manifest=load(SKILL/"assets/rdc-patches/manifest.json")
    if load(package/"package.json")["version"]!=manifest["version"]:raise RuntimeError("RDC补丁版本不匹配")
    for f in manifest["files"]:
        source=SKILL/"assets/rdc-patches"/f["path"]
        if sha(source)!=f["sha256"]:raise ValueError("RDC补丁校验失败")
        target=package/"dist/remote-device"/f["path"];shutil.copyfile(source,target)
    (root/"rdc/runtime").mkdir(exist_ok=True)
    shutil.copyfile(node,root/"rdc/runtime/node.exe")
    run([node,"--require",root/"rdc/dc-network.cjs","-e","console.log('NETWORK_RUNTIME_OK');process.exit(0)"],timeout=30,log=root/"logs/network-runtime-check.log")
    atomic(root/"profile.json",profile)

def application_paths():
    code=r"""
$paths=@()
foreach($name in 'Weixin.exe','WeChat.exe','xinghuan.exe'){
 foreach($base in 'HKCU:\Software\Microsoft\Windows\CurrentVersion\App Paths\','HKLM:\Software\Microsoft\Windows\CurrentVersion\App Paths\'){
  $k=Get-ItemProperty -LiteralPath ($base+$name) -ErrorAction SilentlyContinue
  if($k){$paths+=$k.'(default)'}
 }
}
$paths+=@((Get-CimInstance Win32_Process | Where-Object {$_.Name -in 'Weixin.exe','WeChat.exe','xinghuan.exe'}).ExecutablePath)
foreach($base in 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*','HKLM:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*','HKLM:\Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*'){
 foreach($k in @(Get-ItemProperty -Path $base -ErrorAction SilentlyContinue)){
  if($k.DisplayName -match '^(微信|WeChat|Weixin|星环)'){
   if($k.InstallLocation){foreach($n in 'Weixin.exe','WeChat.exe','xinghuan.exe'){$paths+=Join-Path $k.InstallLocation $n}}
   if($k.DisplayIcon){$paths+=$k.DisplayIcon.Split(',')[0].Trim('"')}
  }
 }
}
@($paths | Where-Object {$_ -and (Test-Path -LiteralPath $_)} | Sort-Object -Unique)|ConvertTo-Json -Compress
"""
    raw=powershell(code,timeout=40)
    found=json.loads(raw) if raw else []
    return [Path(p) for p in ([found] if isinstance(found,str) else found)]

def applications(root, profile, catalog):
    found=application_paths()
    for kind,names in (("wechat",{"weixin.exe","wechat.exe"}),("star",{"xinghuan.exe"})):
        p=next((x for x in found if x.name.casefold() in names),None)
        key="wechat_exe" if kind=="wechat" else "xinghuan_exe"
        if p is None and kind=="wechat":
            winget=shutil.which("winget.exe")
            if not winget:
                powershell("Install-PackageProvider -Name NuGet -Scope CurrentUser -Force | Out-Null;Install-Module -Name Microsoft.WinGet.Client -Scope CurrentUser -Force -Repository PSGallery | Out-Null;Import-Module Microsoft.WinGet.Client;Repair-WinGetPackageManager -Force -Latest",timeout=900,log=root/"logs/winget-setup.log")
                winget=shutil.which("winget.exe")
                if not winget:
                    candidate=Path(os.environ.get("LOCALAPPDATA",""))/"Microsoft/WindowsApps/winget.exe"
                    if candidate.exists():winget=str(candidate)
                if not winget:raise RuntimeError("Windows Package Manager自动恢复未完成，保留安装现场")
            run([winget,"install","--id",catalog[kind]["winget"],"--exact","--silent","--accept-package-agreements","--accept-source-agreements","--disable-interactivity"],log=root/"logs/wechat-install.log")
            p=next((x for x in application_paths() if x.name.casefold() in names),None)
        if p is None and kind=="star":
            spec=catalog["star"]
            installer=download(spec["url"],root/"downloads"/spec["filename"],spec.get("sha256"))
            signed(installer,spec["signer"])
            target=root/"applications/xinghuan"
            run([installer,"/VERYSILENT","/SUPPRESSMSGBOXES","/NORESTART","/CURRENTUSER","/DIR="+str(target)],log=root/"logs/star-install.log")
            options=[target/"xinghuan.exe",*application_paths()]
            p=next((x for x in options if x.name.casefold() in names and x.exists()),None)
        if p is None:raise RuntimeError("软件安装后未能唯一识别路径: "+kind)
        signed(p,catalog[kind]["signer"]);profile[key]=str(p.resolve())
    atomic(root/"profile.json",profile)

def register_resume_task(root):
    profile=load(root/"profile.json");name=profile["task_names"]["bootstrap"]
    pythonw=Path(sys.executable).with_name("pythonw.exe")
    arguments=subprocess.list2cmdline(["-X","utf8","-B",str(SKILL/"scripts/bootstrap.py"),"install","--root",str(root)])
    code="$name="+psquote(name)+";$exe="+psquote(pythonw)+";$arguments="+psquote(arguments)+";$e=Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue;if($e){if($e.Actions[0].Execute -ine $exe -or $e.Actions[0].Arguments -ne $arguments){throw 'bootstrap task identity conflict'}}else{$u=[Security.Principal.WindowsIdentity]::GetCurrent().Name;$a=New-ScheduledTaskAction -Execute $exe -Argument $arguments;$t=New-ScheduledTaskTrigger -AtLogOn -User $u;$p=New-ScheduledTaskPrincipal -UserId $u -LogonType Interactive -RunLevel Limited;$s=New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew -ExecutionTimeLimit ([timespan]::Zero);Register-ScheduledTask -TaskName $name -Action $a -Trigger $t -Principal $p -Settings $s|Out-Null}"
    powershell(code,timeout=60)

def open_login_apps(profile):
    from service_host import signed_owned
    for key,kind in (("wechat_exe","wechat"),("xinghuan_exe","star")):
        if not signed_owned(profile,kind):subprocess.Popen([profile[key]],creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0))

def wait_initial_network(root,profile):
    from service_host import paused,locked,star_account,bind_ports
    while True:
        if paused(profile):
            atomic(root/"bootstrap-state.json",{"status":"paused","environmentReady":False,"updatedAt":time.time()});return False
        if not locked():
            try:
                if star_account(profile):
                    if bind_ports(profile):
                        proxy="http://127.0.0.1:"+str(profile["http_port"])
                        os.environ["HTTP_PROXY"]=os.environ["HTTPS_PROXY"]=proxy
                        return True
                    run([profile["python"],"-B",root/"rdc/star-network-watch.py"],timeout=180,log=root/"logs/initial-network.log")
            except (OSError,RuntimeError,ValueError):pass
        atomic(root/"bootstrap-state.json",{"status":"waiting_for_star_login_or_connection","environmentReady":False,"updatedAt":time.time(),"modelInvoked":False,"testMessagesSent":0})
        time.sleep(20)

def install(root, prepare=False, plan=False):
    if os.name!="nt" and not plan:raise RuntimeError("自动部署只支持Windows")
    root=validate_root(root)
    if not plan and any((root/p).exists() for p in ("project/.wechat_codex/STOP","project/.wechat_codex/UI-PAUSE","rdc/STAR-WATCH-STOP")):
        return {"status":"paused","changed":False,"reason":"人工暂停优先，未开始安装"}
    if plan:return {"mode":"plan","root":str(root),"steps":["verify_sources","python_venv","node_lts","codex_native","rdc_and_recovery_patches","wechat","xinghuan","bind_paths","register_tasks","wait_for_login","bind_account","start_and_verify"],"modelInvoked":False,"messagesSent":0}
    if foreign_listener(root):return {"status":"existing_deployment","changed":False,"reason":"检测到其他目录的监听器，保留原部署"}
    root=claim(root)
    lock=open(root/".bootstrap.lock","a+b")
    import msvcrt
    lock.seek(0);lock.write(b"0");lock.flush();lock.seek(0)
    try:msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)
    except OSError:lock.close();raise RuntimeError("另一个安装流程正在运行")
    try:
        current=load(root/"profile.json")
        profile=current or make_profile(root)
        state_before=load(root/"bootstrap-state.json",{})
        if current and state_before.get("environmentReady") and (prepare or state_before.get("tasksRegistered")):
            return {"status":"existing_environment","profile":str(root/"profile.json"),"next":"doctor_or_resume","changed":False}
        stage_runtime(root,profile)
        atomic(root/"bootstrap-state.json",{"status":"installing","environmentReady":False,"updatedAt":time.time()})
        catalog=load(SKILL/"assets/software-sources.json")
        if not prepare:
            register_resume_task(root)
            applications(root,profile,catalog)
            open_login_apps(profile)
            python_dependencies(root,profile)
            if not wait_initial_network(root,profile):return {"status":"paused","environmentReady":False}
        dependencies(root,profile,catalog)
        atomic(root/"profile.json",profile)
        if not prepare:
            run(["powershell.exe","-NoProfile","-NonInteractive","-File",root/"register_tasks.ps1","-ProfilePath",root/"profile.json"],log=root/"logs/task-install.log")
        state={"status":"environment_ready" if prepare else "waiting_for_login","environmentReady":True,"tasksRegistered":not prepare,"updatedAt":time.time(),"modelInvoked":False,"testMessagesSent":0}
        atomic(root/"bootstrap-state.json",state)
        if not prepare:
            powershell("Disable-ScheduledTask -TaskName "+psquote(profile["task_names"]["bootstrap"])+"|Out-Null",timeout=30)
            code="$p=Get-Content -Raw -LiteralPath "+psquote(root/"profile.json")+"|ConvertFrom-Json;Start-ScheduledTask -TaskName $p.task_names.setup"
            powershell(code,timeout=30)
        return state
    except Exception as exc:
        atomic(root/"bootstrap-state.json",{"status":"needs_attention","environmentReady":False,"error":str(exc),"updatedAt":time.time(),"modelInvoked":False,"testMessagesSent":0})
        raise
    finally:
        lock.seek(0);msvcrt.locking(lock.fileno(),msvcrt.LK_UNLCK,1);lock.close()

def main():
    if sys.stdout is None:sys.stdout=open(os.devnull,"w",encoding="utf-8")
    if sys.stderr is None:sys.stderr=open(os.devnull,"w",encoding="utf-8")
    p=argparse.ArgumentParser(description=__doc__);p.add_argument("command",choices=["install","plan","doctor","resume"])
    p.add_argument("--root",type=Path,default=Path(os.environ.get("LOCALAPPDATA",str(Path.home())))/"WechatLulu")
    p.add_argument("--plan",action="store_true");p.add_argument("--prepare-only",action="store_true")
    a=p.parse_args()
    try:
        if a.command in ("doctor","resume"):
            sys.path.insert(0,str(SKILL/"scripts"));from service_host import doctor,reconcile
            result=(doctor if a.command=="doctor" else reconcile)(a.root/"profile.json")
        else:result=install(a.root,a.prepare_only,a.plan or a.command=="plan")
        print(json.dumps(result,ensure_ascii=False,indent=2))
        return 0
    except Exception as e:
        print(json.dumps({"status":"needs_attention","error":str(e),"modelInvoked":False,"testMessagesSent":0},ensure_ascii=False));return 1
if __name__=="__main__":raise SystemExit(main())
