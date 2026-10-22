"""只在已确认账号不符时切换星环账号；凭证仅由当前用户DPAPI解密。"""
import ctypes,json,re,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parent
EXPECTED=''
if not EXPECTED:
    raise RuntimeError('请先配置目标账号；分发版禁止自动登录')
HOLD={'login_submitted','login_unconfirmed','authentication_rejected'}

def credential(account):
    import win32crypt
    try:
        raw=json.loads((ROOT/'star-account.dpapi.json').read_text(encoding='utf-8'))
        if raw.get('account')!=account:raise ValueError()
        import base64
        return win32crypt.CryptUnprotectData(base64.b64decode(raw['encrypted']),b'Xinghuan account recovery v1',None,None,0)[1].decode('utf-8')
    except Exception:raise RuntimeError('本机加密凭证不可用；不退出现有账号') from None

def ensure_account(ui,expected=EXPECTED,load_secret=credential,old=None,save=lambda value:None):
    """未知账号绝不退出；提交登录后不盲目重复，正确账号始终不换号。"""
    observed=ui.account()
    result={'expected':expected,'observed':observed,'status':'unknown','checkedAt':time.time()}
    if observed==expected:result['status']='matched';return result
    if observed is None:result['status']='unreadable';return result
    if observed!='logged_out' and ui.account()!=observed:result['status']='unreadable';return result
    if (old or {}).get('status') in HOLD:result['status']='login_unconfirmed';return result
    secret=load_secret(expected)
    try:
        if observed!='logged_out':
            result['status']='logout_reserved';save(result)
            ui.logout(observed)
        result['status']='login_preparing';save(result)
        ui.fill_login(expected,secret)
        result['status']='login_submitted';save(result)
        ui.submit_login()
        deadline=time.monotonic()+30
        while time.monotonic()<deadline:
            current=ui.account()
            if current==expected:result.update(status='matched',observed=current,switched=True);return result
            if ui.auth_challenge():result['status']='owner_required';return result
            if ui.auth_rejected():result['status']='authentication_rejected';return result
            time.sleep(1)
        result['status']='login_unconfirmed';return result
    finally:secret=None

class NativeUI:
    def __init__(self,owned,guard):self.owned=owned;self.guard=guard;self.home=False;self.was_minimized=None;self.hwnd=None
    def frame(self):
        import win32gui,win32process,win32con
        from window_capture import _window_bitmap
        from local_ui_ocr import recognize
        self.guard();pids=set(self.owned());windows=[]
        def visit(hwnd,_):
            if win32process.GetWindowThreadProcessId(hwnd)[1] in pids and win32gui.GetWindowText(hwnd)=='星环加速器':windows.append(hwnd)
            return True
        win32gui.EnumWindows(visit,None)
        if len(windows)!=1:raise RuntimeError('星环主窗口不唯一，停止账号操作')
        hwnd=windows[0]
        if self.was_minimized is None:self.was_minimized=bool(win32gui.IsIconic(hwnd))
        self.hwnd=hwnd
        if win32gui.IsIconic(hwnd) or not win32gui.IsWindowVisible(hwnd):win32gui.ShowWindow(hwnd,win32con.SW_SHOWNOACTIVATE);time.sleep(.5)
        picture,rect=_window_bitmap(hwnd)
        return hwnd,rect,recognize(picture)
    def click(self,patterns):
        import win32gui,win32process,win32con,win32api
        hwnd,rect,rows=self.frame();matches=[row for row in rows if re.fullmatch(patterns,''.join(row[0].split()))]
        if len(matches)!=1:raise RuntimeError('星环操作按钮不唯一，停止本轮')
        _,x,y,w,h=matches[0];point=win32gui.ScreenToClient(hwnd,(rect[0]+x+w//2,rect[1]+y+h//2))
        self.guard()
        if tuple(win32gui.GetWindowRect(hwnd))!=tuple(rect) or win32process.GetWindowThreadProcessId(hwnd)[1] not in set(self.owned()):raise RuntimeError('星环窗口变化，不点击')
        position=win32api.MAKELONG(*point)
        win32gui.PostMessage(hwnd,win32con.WM_LBUTTONDOWN,win32con.MK_LBUTTON,position);win32gui.PostMessage(hwnd,win32con.WM_LBUTTONUP,0,position)
        time.sleep(.5)
    @staticmethod
    def read_account(rows):
        anchors=[r for r in rows if '您的账号' in ''.join(r[0].split()) or '您的帐号' in ''.join(r[0].split())]
        if len(anchors)!=1:return None
        _,ax,ay,aw,ah=anchors[0];phones=[]
        for text,x,y,w,h in rows:
            if abs((y+h/2)-(ay+ah/2))<=max(ah,h)*1.5 and x>=ax:
                phones.extend(re.findall(r'(?<!\d)1\d{10}(?!\d)',text.replace(' ','')))
        phones=set(phones);return next(iter(phones)) if len(phones)==1 else None
    def account(self):
        _,_,rows=self.frame();phone=self.read_account(rows)
        if phone:return phone
        text=' '.join(r[0] for r in rows)
        if '验证码' in text:return None
        if ('密码' in text and ('登录' in text or '登陆' in text)):return 'logged_out'
        if any(''.join(r[0].split())=='我的' for r in rows):
            self.home=any('点击连接' in r[0] or '点击断开' in r[0] for r in rows)
            self.click('我的');_,_,rows=self.frame();return self.read_account(rows)
        return None
    def logout(self,observed):
        if self.account()!=observed or observed==EXPECTED:raise RuntimeError('退出前账号变化或已正确，不退出')
        self.click('退出账号|退出登录')
        _,_,rows=self.frame()
        if any(re.search('确定.*退出|确认.*退出|是否.*退出',r[0]) for r in rows):self.click('确定|确认')
    def fill_field(self,pattern,value,password=False):
        import win32gui,win32process,win32con
        hwnd,rect,rows=self.frame()
        fields=[r for r in rows if re.search(pattern,''.join(r[0].split())) and not re.search('忘记|修改|记住',r[0])]
        if len(fields)!=1:raise RuntimeError('星环登录输入框不唯一，停止输入')
        _,x,y,w,h=fields[0];px,py=rect[0]+x+w//2,rect[1]+y+h//2
        self.click(re.escape(''.join(fields[0][0].split())))
        edits=[]
        def visit(child,_):
            if win32gui.GetClassName(child).lower()=='edit' and win32process.GetWindowThreadProcessId(child)[1] in set(self.owned()):
                l,t,r,b=win32gui.GetWindowRect(child)
                if l<=px<=r and t<=py<=b:edits.append(child)
            return True
        win32gui.EnumChildWindows(hwnd,visit,None)
        if len(edits)!=1:raise RuntimeError('登录原生输入控件未确认，未传入凭证')
        edit=edits[0]
        if password and not win32gui.GetWindowLong(edit,win32con.GWL_STYLE)&win32con.ES_PASSWORD:raise RuntimeError('密码控件标志未确认，未输入密码')
        self.guard()
        try:win32gui.SendMessageTimeout(edit,win32con.WM_SETTEXT,0,value,win32con.SMTO_ABORTIFHUNG,2000)
        except Exception:raise RuntimeError('星环输入控件操作未确认；凭证未写入日志') from None
        # 跨进程编辑控件不能靠GetWindowText读取；使用系统支持的WM_GETTEXT消息核验。
        from ctypes import wintypes
        native=ctypes.windll.user32.SendMessageTimeoutW
        native.argtypes=[wintypes.HWND,wintypes.UINT,wintypes.WPARAM,wintypes.LPARAM,wintypes.UINT,wintypes.UINT,ctypes.POINTER(ctypes.c_size_t)]
        native.restype=wintypes.LPARAM
        length=ctypes.c_size_t()
        if not native(edit,win32con.WM_GETTEXTLENGTH,0,0,win32con.SMTO_ABORTIFHUNG,2000,ctypes.byref(length)) or length.value!=len(value):raise RuntimeError('星环登录字段长度未确认')
        if not password:
            buffer=ctypes.create_unicode_buffer(len(value)+1);count=ctypes.c_size_t()
            if not native(edit,win32con.WM_GETTEXT,len(buffer),ctypes.addressof(buffer),win32con.SMTO_ABORTIFHUNG,2000,ctypes.byref(count)) or buffer.value!=value:raise RuntimeError('星环账号输入未确认')
    def fill_login(self,account,secret):
        self.fill_field('请输入.*(?:账号|帐号|手机|邮箱)|^(?:账号|帐号|手机号|手机号码|邮箱)(?:[:：])?$',account)
        self.fill_field('请输入.*密码|^密码(?:[:：])?$',secret,True)
    def submit_login(self):self.click('登录|登陆|立即登录|立即登陆')
    def auth_challenge(self):return any(re.search('验证码|设备确认|安全验证|扫码确认',r[0]) for r in self.frame()[2])
    def auth_rejected(self):return any(re.search('密码错误|密码不正确|账号.*不存在|登录失败',r[0]) for r in self.frame()[2])
    def close(self):
        try:
            if self.home:self.click('我的加速')
            if self.was_minimized and self.hwnd:
                import win32gui,win32con
                win32gui.ShowWindow(self.hwnd,win32con.SW_SHOWMINNOACTIVE)
        except Exception:pass
