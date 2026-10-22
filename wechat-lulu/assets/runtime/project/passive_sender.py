"""不写微信内存的单次界面发送；姓名、草稿和新消息均核对后只点一次。"""
import ctypes,time,json,hashlib,os,uuid
from pathlib import Path

def require_desktop():
    user=ctypes.windll.user32
    user.OpenInputDesktop.restype=ctypes.c_void_p
    handle=user.OpenInputDesktop(0,False,1)
    if not handle:raise RuntimeError('当前桌面不可操作')
    try:
        buffer=ctypes.create_unicode_buffer(128);needed=ctypes.c_uint()
        if not user.GetUserObjectInformationW(ctypes.c_void_p(handle),2,buffer,ctypes.sizeof(buffer),ctypes.byref(needed)) or buffer.value!='Default':
            raise RuntimeError('当前不是已解锁的普通桌面，停止输入')
    finally:user.CloseDesktop(ctypes.c_void_p(handle))

def normalized(text):return ''.join(str(text).split())

def same_editor_text(observed,expected):
    """Windows复制会转换换行；只统一行结束符，不忽略任何正文字符或空行。"""
    if not isinstance(observed,str) or not isinstance(expected,str):return False
    def lines(value):return value.replace('\r\n','\n').replace('\r','\n')
    return lines(observed)==lines(expected)

def send_input_once(gui,x,y,right=False):
    """使用Windows正常命中路径发送一次点击，不改渲染层的透明样式。"""
    from wechatauto.guia import MOUSE_INPUT
    u=gui._input._user32
    if not u.SetCursorPos(int(x),int(y)):raise RuntimeError('发送点击的鼠标位置未确认')
    time.sleep(.06)
    for flags in ((0x8008,0x8010) if right else (0x8002,0x8004)):
        packet=MOUSE_INPUT();packet.type=0
        packet.u.mi.dx=int(x*65535//gui._input.screen_w);packet.u.mi.dy=int(y*65535//gui._input.screen_h)
        packet.u.mi.dwFlags=flags
        if u.SendInput(1,ctypes.byref(packet),ctypes.sizeof(MOUSE_INPUT))!=1:
            raise RuntimeError('系统未完整接收单次鼠标点击，结果未知，禁止补点')
        time.sleep(.06)


def header_name(text):
    value=normalized(text)
    # 窄窗返回箭头会被 OCR 合并进标题；仅去掉一个已知界面符号。
    return value[1:] if value[:1] in ('〈','‹','<','＜') else value

def search_result_band(width,height):
    """只读取搜索结果所在的左栏，排除右侧联系人资料中的重复姓名。"""
    return (0,int(height*.085),int(width*.55),int(height*.21))

def image_preview_hash(picture):
    """图片草稿核对忽略绿色闪烁光标，保留其余每个像素。"""
    import cv2,numpy as np
    array=np.array(picture.convert('RGB'));r,g,b=(array[:,:,i].astype(np.int16) for i in range(3))
    mask=((g>110)&(g>r*1.5)&(g>b*1.3)).astype(np.uint8)
    count,labels,stats,_=cv2.connectedComponentsWithStats(mask,8)
    background=np.median(array[:,-max(5,array.shape[1]//20):,:].reshape(-1,3),axis=0).astype(np.uint8)
    for index,(x,y,w,h,area) in enumerate(stats[1:],1):
        if w<=5 and h>=10 and h>=w*3:array[labels==index]=background
    return hashlib.sha256(array.tobytes()).hexdigest()

def detect_chat_pane(picture):
    """从左右面板的持续背景明度差定位分界，不把侧栏名称当聊天标题。"""
    import numpy as np
    array=np.asarray(picture.convert('RGB')).mean(axis=2)
    profile=np.median(array,axis=0);width=len(profile);step=max(4,width//160)
    if width<120:return None
    candidates=[]
    for x in range(max(step,int(width*.20)),min(width-step,int(width*.76))):
        jump=float(np.median(profile[x:x+step])-np.median(profile[x-step:x]))
        if jump>=6:candidates.append((jump,x))
    if not candidates:return None
    best=max(value[0] for value in candidates);points=[x for score,x in candidates if score>=best*.9]
    if max(points)-min(points)>step*4:return None
    return (min(points)+max(points))//2

def search_focus_visible(gui):
    """确认左上搜索框的绿色细边，不依赖可能已消失的占位文字。"""
    import cv2,numpy as np
    box=(0,0,int(gui.render_w*.65),int(gui.render_h*.10))
    array=np.asarray(gui._grab_screen(gui._rel_to_screen(box))).astype(np.int16);r,g,b=(array[:,:,i] for i in range(3))
    mask=((g>110)&(g>r*1.2)&(g>b*1.08)).astype(np.uint8)
    _,_,stats,_=cv2.connectedComponentsWithStats(mask,8)
    return any(w>gui.render_w*.15 and 12<=h<=gui.render_h*.07 and .015<area/(w*h)<.4 for x,y,w,h,area in stats[1:])

def contact_search_matches(gui,name):
    """只在联系人/功能分组内找精确名称，排除更靠前的网络建议。"""
    band=(0,int(gui.render_h*.08),int(gui.render_w*.55),int(gui.render_h*.95))
    local=hasattr(gui,'_grab_screen')
    if local:
        from local_ui_ocr import window_rows
        # 导航不靠置信分数授权；仍只点分组内的精确姓名，最后单独核对聊天标题。
        rows=window_rows(gui,band,minimum_score=.60)
    else:rows=gui.ocr_zoomed(band,scale=3)
    headings={'联系人','功能','最常使用','聊天记录','聊天文件','群聊','公众号','收藏','小程序','网络搜索结果','搜索网络结果'}
    sections=sorted([row for row in rows if normalized(row[0]) in headings],key=lambda row:row[2])
    gui._search_debug={'headings':[row[0] for row in sections],'candidates':[]}
    matches=[];navigation_candidates=[]
    for heading in sections:
        if normalized(heading[0]) not in ('联系人','功能','最常使用'):continue
        start=heading[2]+heading[4]+5;end=min((row[2]-5 for row in sections if row[2]>heading[2]),default=band[3])
        area=(int(gui.render_w*.145),start,band[2],end)
        if local:
            candidates=[row for row in window_rows(gui,area,minimum_score=.60) if normalized(row[0])==normalized(name)]
            if candidates:matches.extend(candidates);continue
        for scale in (3,5):
            observed=gui.ocr_zoomed(area,scale=scale)
            gui._search_debug['candidates'].extend(row[0] for row in observed)
            navigation_candidates.extend(observed)
            candidates=[row for row in observed if normalized(row[0])==normalized(name)]
            if candidates:matches.extend(candidates);break
        else:
            matches.extend(row for row in contrast_ocr(gui,area,scale=3) if normalized(row[0])==normalized(name))
    if not matches and navigation_candidates and len(normalized(name))>=5:
        # 只用于打开候选；发送前仍须数据库唯一绑定和完整聊天标题精确核对。
        from difflib import SequenceMatcher
        candidate=max(navigation_candidates,key=lambda row:SequenceMatcher(None,normalized(row[0]),normalized(name)).ratio())
        same_row=all(abs(row[1]-candidate[1])<=30 and abs(row[2]-candidate[2])<=max(30,candidate[4]*2) for row in navigation_candidates)
        if same_row and SequenceMatcher(None,normalized(candidate[0]),normalized(name)).ratio()>=.70:return [candidate]
    return matches

def glyph_matches(actual,template):
    import numpy as np
    a=np.asarray(actual.convert('L'))<128;b=np.asarray(template.convert('L'))<128
    if not a.any() or not b.any() or any(abs(x-y)>1 for x,y in zip(a.shape,b.shape)):return False
    # 不缩放文字；只补齐绿色边缘取整造成的一像素尺寸差，仍严格比较字形。
    height=max(a.shape[0],b.shape[0]);width=max(a.shape[1],b.shape[1])
    a=np.pad(a,((0,height-a.shape[0]),(0,width-a.shape[1])))
    b=np.pad(b,((0,height-b.shape[0]),(0,width-b.shape[1])))
    for dy in (-1,0,1):
        for dx in (-1,0,1):
            moved=np.zeros_like(b);height,width=b.shape
            moved[max(0,dy):min(height,height+dy),max(0,dx):min(width,width+dx)]=b[max(0,-dy):min(height,height-dy),max(0,-dx):min(width,width-dx)]
            score=2*np.logical_and(a,moved).sum()/max(1,a.sum()+moved.sum())
            if score>=.985:return True
    return False

def choose_window(windows,name=None):
    # 微信登录小窗也叫“微信”；只接受具有聊天工作区尺寸的主窗。
    # 最小化窗口在枚举时使用还原尺寸，仍能经原有快捷键恢复。
    mains=[item for item in windows if item['title'] in ('微信','WeChat') and item.get('width',0)>300 and item.get('height',0)>300]
    if not mains:
        mains=[item for item in windows if item['title']=='Weixin' and item.get('height',0)>300 and item.get('width',0)>300 and item.get('class','').endswith('QWindowIcon')]
    if len(mains)!=1:raise RuntimeError('无法唯一确认微信主窗口')
    main=mains[0]
    chats=[item for item in windows if name and item['title']==name and item['pid']==main['pid']]
    if len(chats)>1:raise RuntimeError('存在重复的授权聊天窗口')
    return (chats[0] if chats else main),bool(chats)

def native_windows(db):
    from ctypes import wintypes
    u=ctypes.windll.user32
    # tasklist 只枚举进程；窗口是否可见不影响选择，因此支持托盘和最小化。
    pids=set(db._find_weixin_pids());rows=[]
    callback_type=ctypes.WINFUNCTYPE(wintypes.BOOL,wintypes.HWND,wintypes.LPARAM)
    def visit(hwnd,_):
        pid=wintypes.DWORD();u.GetWindowThreadProcessId(hwnd,ctypes.byref(pid))
        if pid.value in pids:
            title=ctypes.create_unicode_buffer(512);u.GetWindowTextW(hwnd,title,512)
            cls=ctypes.create_unicode_buffer(256);u.GetClassNameW(hwnd,cls,256)
            rect=wintypes.RECT();u.GetWindowRect(hwnd,ctypes.byref(rect))
            width,height=rect.right-rect.left,rect.bottom-rect.top
            if u.IsIconic(hwnd):
                try:
                    import win32gui
                    left,top,right,bottom=win32gui.GetWindowPlacement(hwnd)[4]
                    width,height=right-left,bottom-top
                except Exception:
                    # 还原尺寸不可读时不猜测主窗，保持拒绝发送。
                    width=height=0
            if title.value:rows.append({'hwnd':int(hwnd),'pid':pid.value,'title':title.value,'class':cls.value,'width':width,'height':height,'visible':bool(u.IsWindowVisible(hwnd))})
        return True
    callback=callback_type(visit);u.EnumWindows(callback,0)
    return rows

def owned_point(gui,x,y,allow_popup=False):
    from ctypes import wintypes
    u=gui._input._user32;u.WindowFromPoint.restype=wintypes.HWND;u.GetAncestor.restype=wintypes.HWND
    hit=u.WindowFromPoint(wintypes.POINT(int(x),int(y)))
    root=u.GetAncestor(hit,2) if hit else 0
    if root==gui.main_hwnd:return True
    if allow_popup and root:
        pid=wintypes.DWORD();u.GetWindowThreadProcessId(root,ctypes.byref(pid))
        u.GetWindow.restype=wintypes.HWND
        return pid.value==gui.pid and u.GetWindow(root,4)==gui.main_hwnd
    return False

def clamp_window_rect(rect,work):
    """只缩小越出显示器工作区的窗口，不把任务栏后面的位图当可点击界面。"""
    left,top,right,bottom=rect;wl,wt,wr,wb=work
    if wl<=left<right<=wr and wt<=top<bottom<=wb:return None
    width=min(right-left,wr-wl);height=min(bottom-top,wb-wt)
    return max(wl,min(left,wr-width)),max(wt,min(top,wb-height)),width,height

def fit_work_area(hwnd):
    import win32gui,win32api,win32con
    from ctypes import wintypes
    monitor=win32api.MonitorFromWindow(hwnd,win32con.MONITOR_DEFAULTTONEAREST)
    work=win32api.GetMonitorInfo(monitor)['Work']
    # 自动隐藏任务栏不缩减Monitor.Work，但其弹出区域依然会截获发送按钮的点击。
    class AppBarData(ctypes.Structure):
        _fields_=[('cbSize',wintypes.DWORD),('hWnd',wintypes.HWND),('uCallbackMessage',wintypes.UINT),('uEdge',wintypes.UINT),('rc',wintypes.RECT),('lParam',wintypes.LPARAM)]
    bar=AppBarData();bar.cbSize=ctypes.sizeof(bar)
    if ctypes.windll.shell32.SHAppBarMessage(5,ctypes.byref(bar)):
        work=exclude_taskbar(work,(bar.rc.left,bar.rc.top,bar.rc.right,bar.rc.bottom),bar.uEdge)
    target=clamp_window_rect(win32gui.GetWindowRect(hwnd),work)
    if target is None:return False
    if ctypes.windll.user32.IsZoomed(hwnd):win32gui.ShowWindow(hwnd,win32con.SW_RESTORE)
    win32gui.SetWindowPos(hwnd,0,*target,win32con.SWP_NOZORDER|win32con.SWP_NOACTIVATE)
    time.sleep(.15);return True

def exclude_taskbar(work,bar,edge):
    """保留系统报告的任务栏区域，不修改自动隐藏等系统设置。"""
    wl,wt,wr,wb=work;left,top,right,bottom=bar
    if max(wl,left)>=min(wr,right) or max(wt,top)>=min(wb,bottom):return tuple(work)
    if edge==0:wl=max(wl,right)
    elif edge==1:wt=max(wt,bottom)
    elif edge==2:wr=min(wr,left)
    elif edge==3:wb=min(wb,top)
    return (wl,wt,wr,wb) if wl<wr and wt<wb else tuple(work)

def wake_main_window(target,input_driver):
    """托盘隐藏须经微信自己的快捷键恢复，否则 Qt 仍把界面当作隐藏。"""
    from ui_permission import ensure_ui_allowed
    ensure_ui_allowed()
    u=input_driver._user32
    if u.IsWindowVisible(target['hwnd']) and not u.IsIconic(target['hwnd']):return False
    require_desktop()
    input_driver._raw_key(0x11,down=True);input_driver._raw_key(0x12,down=True)
    try:input_driver.key(0x57)
    finally:
        input_driver._raw_key(0x12,down=False);input_driver._raw_key(0x11,down=False)
    time.sleep(.35)
    return True

def contrast_ocr(gui,box,scale=3):
    """搜索结果绿字和分组灰字先增大对比度，再沿用本机 OCR。"""
    import numpy as np
    from PIL import Image
    picture=gui._grab_screen(gui._rel_to_screen(box)).convert('RGB')
    array=np.asarray(picture).astype(np.int16);r,g,b=(array[:,:,i] for i in range(3))
    ink=(array.mean(axis=2)<185)|((g>110)&(g>r*1.2)&(g>b*1.15))
    binary=Image.fromarray(np.where(ink,0,255).astype(np.uint8),'L').convert('RGB')
    binary=binary.resize((binary.width*scale,binary.height*scale),Image.Resampling.LANCZOS)
    if hasattr(gui,'main_hwnd'):
        from local_ui_ocr import recognize
    else:
        from wechatauto.guia import ScreenOCR
        recognize=ScreenOCR.recognize
    return [(text,box[0]+x//scale,box[1]+y//scale,w//scale,h//scale) for text,x,y,w,h in recognize(binary)]

def create_gui(db,name=None):
    from ui_permission import ensure_ui_allowed
    ensure_ui_allowed()
    from wechatauto import WeChatGUI
    from wechatauto.guia import WinInput
    from ctypes import wintypes
    target,standalone=choose_window(native_windows(db),name)
    u=ctypes.windll.user32
    require_desktop()
    if not standalone:wake_main_window(target,WinInput())
    u.ShowWindow(target['hwnd'],9 if u.IsIconic(target['hwnd']) else 5)
    fit_work_area(target['hwnd'])
    time.sleep(.15)
    class NoProcessWriteGUI(WeChatGUI):
        def _get_uia(self,refresh=False):return None
        def wx_click(self,x,y,right=False):
            # 原mouse_event立即按下松开会被Qt漏接；这里只产生一次经检查的SendInput点击。
            return send_input_once(self,x,y,right=right)
        def _minimize_blockers(self):return 0
        def _load_layout(self):return True
        def _grab_screen(self,box=None):
            from window_capture import capture_window
            return capture_window(self,box)
        def ocr(self,rel_box):
            from local_ui_ocr import window_rows
            return window_rows(self,rel_box)
        def ocr_zoomed(self,rel_box,scale=3):
            # 发送子进程不再回退到可能连续阻塞的WinRT；仍只识别本窗口位图。
            from local_ui_ocr import recognize
            from PIL import Image
            picture=self._grab_screen(self._rel_to_screen(rel_box))
            picture=picture.resize((picture.width*scale,picture.height*scale),Image.Resampling.LANCZOS)
            return [(text,rel_box[0]+x//scale,rel_box[1]+y//scale,w//scale,h//scale) for text,x,y,w,h in recognize(picture)]
        def ensure_visible(self):
            # 不调用可能阻塞的 AttachThreadInput，也不复用旧坐标或旧可见性缓存。
            ensure_ui_allowed()
            u=self._input._user32
            for _ in range(3):
                require_desktop()
                if not getattr(self,'_passive_standalone',False):wake_main_window({'hwnd':self.main_hwnd},self._input)
                u.ShowWindow(self.main_hwnd,9 if u.IsIconic(self.main_hwnd) else 5)
                fit_work_area(self.main_hwnd)
                u.SetWindowPos(self.main_hwnd,-1,0,0,0,0,0x2|0x1|0x40)
                u.SetForegroundWindow(self.main_hwnd);u.BringWindowToTop(self.main_hwnd)
                time.sleep(.15)
                self.render_hwnd=self._find_render_window(self.main_hwnd) or self.main_hwnd
                self._update_render_rect()
                if u.GetForegroundWindow()==self.main_hwnd and u.IsWindowVisible(self.main_hwnd) and not u.IsIconic(self.main_hwnd):return True
                x=self.origin_x+int(self.render_w*.40);y=self.origin_y+10
                if owned_point(self,x,y):self._input.send_input_click(x,y)
            return False
        def open_chat(self,name,exact=True):
            ensure_ui_allowed()
            current,standalone=choose_window(native_windows(self._cached_db),name)
            if standalone:
                self.use_window(current['hwnd']);self._passive_standalone=True
                return self.ensure_visible()
            if not self.ensure_visible():return False
            if PassiveSender(self._cached_db).header_matches(self,name):return True
            # 已在本机验证Ctrl+1从通讯录返回聊天页，避免在通讯录搜索框内停留。
            self._input.key(0x31,ctrl=True);time.sleep(.2)
            # 已在联系人列表时先点唯一的精确名称；打开后仍必须通过聊天标题复核。
            # 裁去头像，防止OCR把头像符号拼到短昵称前面。
            list_left=int(self.render_w*.10) if self.layout_profile=='portrait' else int(self.render_h*.13)
            from local_ui_ocr import window_rows
            list_box=(list_left,int(self.render_h*.09),int(self.render_w*.55),int(self.render_h*.95))
            matches=[]
            for delay in (0,.35):
                if delay:time.sleep(delay)
                # 长列表整页检测容易漏掉三个字的姓名，重叠分块保留原像素字号。
                rows=[];step=max(120,int(self.render_h*.22));overlap=max(24,int(self.render_h*.04))
                for top in range(list_box[1],list_box[3],step):
                    area=(list_box[0],max(list_box[1],top-overlap),list_box[2],min(list_box[3],top+step+overlap))
                    rows.extend(window_rows(self,area,minimum_score=.60))
                matches=[]
                for row in rows:
                    if normalized(row[0])==normalized(name) and not any(abs(row[2]-prior[2])<max(row[4],prior[4]) for prior in matches):matches.append(row)
                if matches:break
            if len(matches)==1:
                _,x,y,w,h=matches[0];sx=self.origin_x+x+w//2;sy=self.origin_y+y+h//2
                if owned_point(self,sx,sy):
                    self.wx_click(sx,sy);time.sleep(.4)
                    current,standalone=choose_window(native_windows(self._cached_db),name)
                    if standalone:self.use_window(current['hwnd'])
                    self._passive_standalone=standalone
                    # 此处只检查精确标题，不能把“有消息内容”当作目标打开成功。
                    if self.ensure_visible() and PassiveSender(self._cached_db).header_matches(self,name):return True
            return self._search_chat(name)
        def _search_chat(self,name):
            # Ctrl+F的弹层不属于主窗口位图，截图会单独合成其直属弹层。
            ensure_ui_allowed()
            self._input.key(0x31,ctrl=True);time.sleep(.2)
            self._input.key(0x46,ctrl=True);time.sleep(.25)
            from local_ui_ocr import window_rows
            focused=False
            for delay in (0,.15,.3):
                if delay:time.sleep(delay)
                if search_focus_visible(self):focused=True;break
            if not focused:
                # 看到占位词不等于已聚焦；只点击这次观察到的唯一搜索框，再验证绿色边框。
                labels=window_rows(self,(0,0,int(self.render_w*.65),int(self.render_h*.10)),minimum_score=.60)
                placeholders=[row for row in labels if normalized(row[0]) in ('搜索','Q搜索')]
                if len(placeholders)==1:
                    _,x,y,w,h=placeholders[0];sx=self.origin_x+x+w//2;sy=self.origin_y+y+h//2
                    if owned_point(self,sx,sy):self.wx_click(sx,sy);time.sleep(.2);focused=search_focus_visible(self)
                if not focused:raise RuntimeError('微信搜索输入焦点未确认，禁止粘贴姓名')
            PassiveSender(self._cached_db).guard(self)
            self._input.key(0x41,ctrl=True);self.set_clipboard(name);self._input.key(0x56,ctrl=True)
            time.sleep(.25)
            # 再核对焦点和搜索框选区，避免快捷键偶失效后把姓名当成聊天草稿。
            if not search_focus_visible(self):raise RuntimeError('粘贴后微信搜索焦点变化，停止导航')
            import pyperclip
            marker='wechat-search-'+uuid.uuid4().hex
            self.set_clipboard(marker);self._input.key(0x41,ctrl=True);self._input.key(0x43,ctrl=True);time.sleep(.1)
            if pyperclip.paste()!=name:raise RuntimeError('搜索框内容与目标姓名不一致')
            self._input.key(0x23)
            time.sleep(.15)
            # 只认首个联系人卡片；缩小截图后放大 5 倍，避免绿字被全页 OCR 漏掉。
            matches=[]
            for delay in (0,.4,.8):
                if delay:time.sleep(delay)
                matches=contact_search_matches(self,name)
                if matches:break
            if len(matches)!=1:raise RuntimeError('搜索没有唯一名称候选（数量='+str(len(matches))+'）：'+str(getattr(self,'_search_debug',{})))
            _,x,y,w,h=matches[0];sx=self.origin_x+x+w//2;sy=self.origin_y+y+h//2
            if not owned_point(self,sx,sy,allow_popup=True):raise RuntimeError('搜索结果被非目标窗口遮挡')
            self.wx_click(sx,sy);time.sleep(.35)
            current,standalone=choose_window(native_windows(self._cached_db),name)
            if standalone:self.use_window(current['hwnd'])
            self._passive_standalone=standalone
            return self.ensure_visible()
    gui=NoProcessWriteGUI(hwnd=target['hwnd']);gui._cached_db=db;gui._passive_standalone=standalone
    if not gui.ensure_visible():raise RuntimeError('微信未恢复到可操作前台')
    return gui

class PassiveSender:
    def __init__(self,db,stop_path=None,contact_guard=None,progress_hook=None):
        self.db=db;self.stop_path=Path(stop_path or Path(__file__).parent/'.wechat_codex'/'STOP')
        self.journal_path=Path(__file__).parent/'.wechat_codex'/'send-last.json'
        self.progress={};self.contact_guard=contact_guard;self.progress_hook=progress_hook
    def contact_journal(self,user):
        return self.journal_path.parent/'send-journals'/(hashlib.sha256(user.encode()).hexdigest()+'.json')
    def load_journal(self,user):
        target=self.contact_journal(user)
        if not target.exists():target=self.journal_path
        if not target.exists():return {}
        record=json.loads(target.read_text(encoding='utf-8'))
        return record if record.get('contact_hash')==hashlib.sha256(user.encode()).hexdigest() else {}
    def mark(self,phase):
        self.progress.update(phase=phase,at=time.time(),pid=os.getpid())
        if self.progress_hook:self.progress_hook(phase,self.progress)
        self.journal_path.parent.mkdir(parents=True,exist_ok=True)
        # 每联系人保留自己的阶段；新一次准备失败不能抹掉另一份未发草稿的凭证。
        contact=self.journal_path.parent/'send-journals'/(self.progress['contact_hash']+'.json')
        contact.parent.mkdir(parents=True,exist_ok=True)
        encoded=json.dumps(self.progress,ensure_ascii=False)
        for target in (contact,self.journal_path):
            temporary=target.with_suffix('.tmp')
            with temporary.open('w',encoding='utf-8') as file:
                file.write(encoded);file.flush();os.fsync(file.fileno())
            temporary.replace(target)
        with (self.journal_path.parent/'send-history.jsonl').open('a',encoding='utf-8') as file:
            file.write(encoded+'\n');file.flush();os.fsync(file.fileno())
    def guard(self,gui=None):
        from ui_permission import ensure_ui_allowed
        ensure_ui_allowed()
        if self.stop_path.exists():raise RuntimeError('手动停止开关已开启')
        require_desktop()
        if gui is not None:
            user32=ctypes.windll.user32;process=ctypes.c_uint()
            # Qt切换控件时前台句柄可能短暂为空；只等待稳定，不抢回其他程序的焦点。
            for attempt in range(5):
                foreground=user32.GetForegroundWindow();user32.GetWindowThreadProcessId(foreground,ctypes.byref(process))
                if foreground==gui.main_hwnd and process.value==gui.pid:break
                if attempt<4:time.sleep(.05)
            # 已核对的草稿在点击前被其他应用抢焦点时，只恢复一次；不重新粘贴或发送。
            recoverable={'prepared','replace_reserved','paste_reserved','pasted','draft_verified','pasting_image','image_draft_verified'}
            if (process.value and process.value!=gui.pid and self.progress.get('phase') in recoverable
                    and not getattr(self,'_focus_recoveries',0)):
                self._focus_recoveries=1
                self.guard()
                if not gui.ensure_visible():raise RuntimeError('恢复微信焦点失败，保留草稿')
                if not self.header_matches(gui,self.progress.get('name','')):raise RuntimeError('恢复焦点后聊天标题不匹配，保留草稿')
                box=self.editor_box(gui);prior=self.progress.get('editor_box')
                if not prior or len(prior)!=len(box) or max(abs(a-b) for a,b in zip(prior,box))>3:raise RuntimeError('恢复焦点后输入框布局变化，保留草稿')
                if not gui.focus_input(box):raise RuntimeError('恢复焦点后输入框未聚焦，保留草稿')
                self.guard()
                foreground=user32.GetForegroundWindow();user32.GetWindowThreadProcessId(foreground,ctypes.byref(process))
                self.progress['focus_recoveries']=1
            if process.value!=gui.pid:raise RuntimeError('前台焦点不属于微信，停止输入（目标PID='+str(gui.pid)+'，实际PID='+str(process.value)+'）')
            if foreground!=gui.main_hwnd:raise RuntimeError('前台不是本次绑定的聊天窗口，停止输入')
            if ctypes.windll.user32.IsIconic(gui.main_hwnd) or not ctypes.windll.user32.IsWindowVisible(gui.main_hwnd):raise RuntimeError('本次绑定窗口已最小化或隐藏')
    def reconcile_pending(self,user):
        """只读取已落盘的发送结果；确认成功可解锁新批次，绝不重点击。"""
        prior=self.load_journal(user)
        if prior.get('phase') not in ('send_reserved','clicked'):return prior
        text=prior.get('text');before=prior.get('incoming_seq')
        if not isinstance(text,str) or not isinstance(before,int):return prior
        query=getattr(self.db,'get_new_messages',None)
        rows=query(user,max(0,before-1),limit=500) if query else self.db.get_messages(user,limit=500)
        # 图片的未知结果不凭“有一张新图”推测来源；保留预约，交给主人核对。
        if prior.get('kind')=='image':return prior
        matches=[m for m in rows if str(m.get('sender_id'))=='2' and int(m.get('sort_seq') or 0)>before and same_editor_text(m.get('content'),text)]
        if not matches:return prior
        self.progress=dict(prior);self.progress['sent_seq']=min(int(m['sort_seq']) for m in matches)
        self.progress['confirmation']='reconciled from target self message; no repeated click'
        import sqlite3
        account=hashlib.sha256(self.db.get_self_info()['username'].encode()).hexdigest()[:20]
        ledger=self.journal_path.parent/account/'deliveries.sqlite3'
        if ledger.exists():
            with sqlite3.connect(ledger) as conn:
                conn.executemany("UPDATE deliveries SET status='sent' WHERE event_id=? AND contact_id=?",[(event,user) for event in prior.get('event_ids',())])
        self.mark('sent_confirmed')
        return self.progress
    def supersede_pending(self,user,event_ids=(),incoming_seq=None):
        """仅新来信可以放弃旧未知批次；先完整留证，绝不重发旧正文。"""
        prior=self.reconcile_pending(user)
        if prior.get('phase') not in ('send_reserved','clicked'):return prior
        from pending_reply_policy import policy_enabled,distinct_newer_batch
        if not distinct_newer_batch(prior,user,event_ids,incoming_seq) or not policy_enabled(self.db):return prior
        if self.stop_path.exists():raise RuntimeError('手动停止开关已开启，不取代旧回复')
        rows=self.db.get_messages(user,limit=20)
        latest=max((int(row.get('sort_seq') or 0) for row in rows),default=0)
        at_latest=[row for row in rows if int(row.get('sort_seq') or 0)==incoming_seq]
        if latest!=incoming_seq or not at_latest or any(row.get('sender_id') is None or str(row.get('sender_id'))=='2' for row in at_latest):
            raise RuntimeError('旧回复取代前会话已更新或主人已回复，保留旧记录')
        encoded=json.dumps(prior,ensure_ascii=False,sort_keys=True)
        digest=hashlib.sha256(encoded.encode()).hexdigest()
        folder=self.journal_path.parent/'send-resolutions'/hashlib.sha256(user.encode()).hexdigest()
        folder.mkdir(parents=True,exist_ok=True)
        target=folder/(digest+'.json')
        resolution={'original':prior,'original_sha256':digest,'at':time.time(),
            'resolution':'superseded_by_distinct_new_incoming','successor_event_ids':list(event_ids),
            'successor_incoming_seq':incoming_seq,'old_delivery_status_unchanged':True,
            'authorization':'owner pending_reply_policy=latest_new_message'}
        # 原文件不可覆盖；中断后可核对同一快照接续，账本和旧消息指纹不改。
        try:
            with target.open('x',encoding='utf-8') as stream:
                json.dump(resolution,stream,ensure_ascii=False);stream.flush();os.fsync(stream.fileno())
        except FileExistsError:
            saved=json.loads(target.read_text(encoding='utf-8'))
            if saved.get('original_sha256')!=digest or saved.get('original')!=prior:
                raise RuntimeError('旧发送留证文件不一致，停止取代')
        self.progress=dict(prior)
        self.progress.update(previous_phase=prior['phase'],resolution=resolution['resolution'],
            resolution_file=str(target),successor_event_ids=list(event_ids),successor_incoming_seq=incoming_seq)
        if self.stop_path.exists():raise RuntimeError('取代前检测到手动停止，保留旧预约')
        self.mark('superseded_by_new_incoming')
        return dict(self.progress)
    def header_matches(self,gui,name):
        gui._update_render_rect()
        left=0 if gui.layout_profile=='portrait' or getattr(gui,'_passive_standalone',False) else int(gui.render_w*.14)
        # 聊天图片会形成很强的竖边，不能用消息正文的亮度差定位会话标题。
        height=min(190,max(100,int(gui.render_h*.12)))
        box=(left,0,gui.render_w,min(105,max(75,int(gui.render_h*.085))))
        if hasattr(gui,'_grab_screen') and not getattr(gui,'_passive_standalone',False) and gui.layout_profile!='portrait':
            from local_ui_ocr import window_rows
            frame=(0,0,gui.render_w,height)
            labels=window_rows(gui,frame,minimum_score=.60)
            anchors=[row for row in labels if normalized(row[0])=='+' and gui.render_w*.18<row[1]<gui.render_w*.70 and row[2]>height*.15]
            if len(anchors)!=1:return False
            _,x,y,w,h=anchors[0]
            # 左上添加按钮是本轮画面中的固定界面控件；标题须在其右方且同一高度。
            box=(x+w+max(3,w//4),max(0,y-h),gui.render_w,min(height,y+h*2))
        if hasattr(gui,'main_hwnd'):
            from local_ui_ocr import window_rows
            labels=window_rows(gui,box)
            matches=[row for row in labels if header_name(row[0])==normalized(name)]
            if not matches:
                # 生僻姓名在整条标题中的分数偏低；原图和独立放大图都须精确一致。
                labels=window_rows(gui,box,minimum_score=.60)
                matches=[row for row in labels if header_name(row[0])==normalized(name)]
                if len(matches)==1:
                    from local_ui_ocr import recognize
                    from PIL import Image
                    picture=gui._grab_screen(gui._rel_to_screen(box))
                    picture=picture.resize((picture.width*3,picture.height*3),Image.Resampling.LANCZOS)
                    confirm=[row for row in recognize(picture,minimum_score=.60) if header_name(row[0])==normalized(name)]
                    if len(confirm)!=1:return False
            if len(matches)!=1:return False
        else:labels=[];matches=[]
        if not matches:
            labels=gui.ocr_zoomed(box,scale=3)
            matches=[row for row in labels if header_name(row[0])==normalized(name)]
        if len(matches)!=1:
            labels=contrast_ocr(gui,box,scale=3)
            matches=[row for row in labels if header_name(row[0])==normalized(name)]
        if len(matches)!=1 and hasattr(gui,'main_hwnd'):
            from local_ui_ocr import window_rows
            matches=[row for row in window_rows(gui,box) if header_name(row[0])==normalized(name)]
        if len(matches)!=1:return False
        gui._sidebar_ratio=max(0,(matches[0][1]-24)/gui.render_w)
        gui.right_pane_left=max(0,matches[0][1]-24)
        gui._passive_pane_left=gui.right_pane_left
        return True
    def editor_box(self,gui):
        # 根据实际全宽灰色分隔线定位输入区域，不使用固定输入坐标。
        left=getattr(gui,'_passive_pane_left',gui.right_pane_left)+15;right=gui.render_w-15
        top=int(gui.render_h*.50);bottom=int(gui.render_h*.91)
        if right-left<150:raise RuntimeError('窗口布局不支持安全发送')
        picture=gui._grab_screen(gui._rel_to_screen((left,top,right,bottom))).convert('RGB')
        pixels=picture.load();width,height=picture.size;lines=[]
        for y in range(height):
            hit=sum(1 for x in range(0,width,6) if 205<=sum(pixels[x,y])/3<=242 and max(pixels[x,y])-min(pixels[x,y])<8)
            if hit/max(1,(width+5)//6)>.80:lines.append(y)
        if not lines:raise RuntimeError('未确认聊天输入区域的分隔线')
        y0=top+max(lines)+5;y1=int(gui.render_h*.94)
        if y1-y0<35:raise RuntimeError('输入区域过小')
        return (left,y0,right,y1)
    def draft_present(self,gui,box):
        picture=gui._grab_screen(gui._rel_to_screen(box)).convert('RGB')
        pixels=picture.load();points=[];green=[]
        for y in range(0,picture.height,2):
            for x in range(0,picture.width,2):
                r,g,b=pixels[x,y]
                if (r+g+b)/3<185:
                    if g>100 and g>r*1.5 and g>b*1.3:green.append((x,y))
                    else:points.append((x,y))
        # OCR 偶尔把绿色输入光标识别成字；先确认截图中存在非光标墨迹。
        # 宽绿色图像仍是草稿，不把绿色内容一概当成光标。
        green_content=bool(green and max(p[0] for p in green)-min(p[0] for p in green)>5)
        if not points and not green_content:return False
        labels=gui.ocr(box)
        if labels:
            # 微信4.1.15在失焦空框显示浅灰语音提示，不能当作人工草稿。
            hint=normalized(''.join(row[0] for row in labels))
            if hint=='按住鼠标语音输入文字' and points and all(min(pixels[x,y])>=120 and max(pixels[x,y])-min(pixels[x,y])<8 for x,y in points) and not green_content:return False
            return True
        if green_content:return True
        # 句号、短横线等只有几个像素，也属于人工草稿；只豁免细长光标。
        if not points:return False
        width=max(p[0] for p in points)-min(p[0] for p in points)+1
        height=max(p[1] for p in points)-min(p[1] for p in points)+1
        return not (width<=3 and height>=10)
    def newest(self,user):
        messages=self.db.get_messages(user,limit=20)
        if not messages:raise RuntimeError('无法核对会话新消息')
        return max(int(m.get('sort_seq') or 0) for m in messages)
    def clear_hover(self,gui):
        # 鼠标悬停会覆盖空白框，显示语音提示或鼠标高亮；移到微信顶端后再截图。
        self.guard(gui)
        x=gui.origin_x+int(gui.render_w*.80);y=gui.origin_y+10
        if not gui._input._user32.SetCursorPos(x,y):
            raise RuntimeError('无法移开鼠标，不能可靠检查输入框')
        # SendInput 移动同时更新远程桌面的鼠标高亮，避免旧高亮残留在输入框。
        from wechatauto.guia import MOUSE_INPUT
        movement=MOUSE_INPUT();movement.type=0
        movement.u.mi.dx=int(x*65535//gui._input.screen_w);movement.u.mi.dy=int(y*65535//gui._input.screen_h)
        movement.u.mi.dwFlags=0x8000|0x0001
        if gui._input._user32.SendInput(1,ctypes.byref(movement),ctypes.sizeof(MOUSE_INPUT))!=1:raise RuntimeError('鼠标移动未确认')
        time.sleep(.25)
    def verify_text(self,gui,box,text):
        # 完整复制已聚焦编辑框，避免长文滚动裁切及整框OCR耗尽发送时限。
        # 随机标记确认复制确实发生；正文改动不能再由宽松OCR结果覆盖。
        import pyperclip,uuid
        self.guard(gui)
        marker='WECHAT-VERIFY-'+uuid.uuid4().hex
        previous=pyperclip.paste();pyperclip.copy(marker)
        try:
            gui._input.key(0x41,ctrl=True);gui._input.key(0x43,ctrl=True);time.sleep(.2)
            copied=pyperclip.paste()
            matched=same_editor_text(copied,text)
            if matched:self.progress['text_verification']='clipboard_exact_with_line_endings'
            return matched
        finally:pyperclip.copy(previous)
    def send_button(self,gui):
        band=(int(gui.render_w*.78),int(gui.render_h*.88),gui.render_w,gui.render_h)
        labels=[row for row in gui.ocr_zoomed(band,scale=3) if normalized(row[0])=='发送']
        if len(labels)==1:return labels[0]
        # 小号白字在绿色按钮上容易漏读；只对唯一绿色矩形做黑白高对比 OCR。
        import cv2,numpy as np
        from PIL import Image
        from local_ui_ocr import recognize
        picture=gui._grab_screen(gui._rel_to_screen(band)).convert('RGB');array=np.asarray(picture)
        r,g,b=(array[:,:,i].astype(np.int16) for i in range(3))
        mask=((g>135)&(g>r*1.5)&(g>b*1.3)).astype(np.uint8)
        count,_,stats,_=cv2.connectedComponentsWithStats(mask,8)
        candidates=[]
        for x,y,w,h,area in stats[1:]:
            if 25<=w<=gui.render_w*.30 and 14<=h<=gui.render_h*.10 and area/(w*h)>.55:candidates.append((int(x),int(y),int(w),int(h)))
        if len(candidates)!=1:raise RuntimeError('未找到唯一绿色发送按钮')
        x,y,w,h=candidates[0];crop=array[y:y+h,x:x+w]
        white=(crop[:,:,0]>215)&(crop[:,:,1]>215)&(crop[:,:,2]>215)
        glyph=Image.fromarray(np.where(white,0,255).astype(np.uint8),'L').convert('RGB')
        padded=Image.new('RGB',(w+12,h+12),'white');padded.paste(glyph,(6,6))
        # 本机同一个按钮放大五倍会误读为“发区”，三倍则能读出“发送”。
        # 不用宽松文字匹配；在几个缩放档位中要求全文精确一致。
        confirmed=False
        for scale in (3,5):
            rows=recognize(padded.resize(((w+12)*scale,(h+12)*scale),Image.Resampling.LANCZOS))
            if normalized(''.join(row[0] for row in rows))=='发送':confirmed=True;break
        if not confirmed:
            # 两个模板均已在本机观察到的“发送”按钮上人工核对；仍要求严格字形匹配。
            templates=[Path(__file__).parent/name for name in ('send-button-glyph.png','send-button-glyph-4.1.15.13.png')]
            if not any(template.exists() and glyph_matches(glyph,Image.open(template)) for template in templates):
                picture.save(self.journal_path.parent/'send-button-failed.png')
                raise RuntimeError('发送按钮文字和本机已核对字形均未确认')
        return ('发送',band[0]+x,band[1]+y,w,h)
    def prepare(self,user,allow_draft=False,expected_seq=None):
        if self.progress_hook:self.progress_hook('prepare_start')
        self.guard()
        hits=[c for c in self.db.search_contact(user) if c.get('username')==user]
        if len(hits)!=1 or '@chatroom' in user:raise RuntimeError('联系人不是唯一私聊账号')
        name=hits[0].get('remark') or hits[0].get('nick_name')
        exact=[c for c in self.db.search_contact(name) if (c.get('remark') or c.get('nick_name'))==name]
        if len(exact)!=1 or exact[0]['username']!=user:raise RuntimeError('显示名称重复，停止发送')
        gui=create_gui(self.db,name=name)
        self.guard()
        if not gui.ensure_visible():raise RuntimeError('微信窗口不可操作')
        if not self.header_matches(gui,name):
            self.guard()
            if not gui.open_chat(name,exact=True):raise RuntimeError('未打开唯一授权联系人')
        if not self.header_matches(gui,name):raise RuntimeError('聊天标题不匹配')
        self.guard(gui)
        before=self.newest(user)
        if expected_seq is not None and before!=expected_seq:raise RuntimeError('生成回复后会话已更新，不发送旧回复')
        self.clear_hover(gui)
        box=self.editor_box(gui)
        draft=self.draft_present(gui,box)
        if draft and not allow_draft:raise RuntimeError('已有草稿，不覆盖')
        self.guard(gui)
        cx=(box[0]+box[2])//2;cy=box[1]+min(24,(box[3]-box[1])//4)
        if not owned_point(gui,gui.origin_x+cx,gui.origin_y+cy):raise RuntimeError('输入位置不属于绑定聊天窗口')
        if not gui.focus_input(box):raise RuntimeError('输入框未聚焦')
        self.clear_hover(gui)
        if not self.header_matches(gui,name):raise RuntimeError('输入前聊天标题识别失败')
        refreshed=self.editor_box(gui)
        if max(abs(a-b) for a,b in zip(refreshed,box))>3:raise RuntimeError('输入前窗口布局发生变化')
        box=refreshed
        if self.draft_present(gui,box) and not allow_draft:raise RuntimeError('输入前检测到已有草稿')
        if self.newest(user)!=before:raise RuntimeError('输入前收到更新消息')
        return gui,name,before,box
    def backup_authorized_draft(self,gui,user,name,before,box):
        """按用户授权备份当前未发送草稿，不能备份或覆盖别的会话。"""
        import win32clipboard
        self.guard(gui)
        if not self.header_matches(gui,name) or self.newest(user)!=before:raise RuntimeError('备份草稿前会话变化')
        root=self.journal_path.parent/'draft-backups'/hashlib.sha256(user.encode()).hexdigest()/uuid.uuid4().hex
        root.mkdir(parents=True,exist_ok=False)
        gui._grab_screen(gui._rel_to_screen(box)).save(root/'preview.png')
        marker='DRAFT-BACKUP-'+uuid.uuid4().hex;gui.set_clipboard(marker)
        gui._input.key(0x41,ctrl=True);gui._input.key(0x43,ctrl=True);time.sleep(.2)
        saved={'contact_hash':hashlib.sha256(user.encode()).hexdigest(),'name':name,'incoming_seq':before,'at':time.time(),'reason':'explicit user authorized draft regeneration'}
        win32clipboard.OpenClipboard()
        try:
            if win32clipboard.IsClipboardFormatAvailable(13):
                text=win32clipboard.GetClipboardData(13)
                if text!=marker:(root/'draft.txt').write_text(text,encoding='utf-8');saved['text_saved']=True
            if win32clipboard.IsClipboardFormatAvailable(8):
                (root/'draft.dib').write_bytes(win32clipboard.GetClipboardData(8));saved['image_saved']=True
        finally:win32clipboard.CloseClipboard()
        self.guard(gui)
        if not self.header_matches(gui,name) or self.newest(user)!=before:raise RuntimeError('备份草稿后会话变化')
        (root/'metadata.json').write_text(json.dumps(saved,ensure_ascii=False),encoding='utf-8')
        return str(root)

    def __call__(self,user,text,expected_seq=None,event_ids=(),adopt_draft=False,replace_unsent_drafts=False):
        prior=self.supersede_pending(user,event_ids,expected_seq)
        if prior.get('phase') in ('send_reserved','clicked'):
            raise RuntimeError('上次点击结果未知，禁止自动再次发送')
        # 先观察现有框，核对草稿所有权后才创建本次记录，避免覆盖恢复依据。
        gui,name,before,box=self.prepare(user,allow_draft=True,expected_seq=expected_seq)
        draft=self.draft_present(gui,box)
        replace=False;draft_backup=None
        if draft:
            if replace_unsent_drafts and not adopt_draft:
                draft_backup=self.backup_authorized_draft(gui,user,name,before,box);replace=True
            elif adopt_draft:
                if not self.verify_text(gui,box,text):raise RuntimeError('已观察草稿发生变化，不发送')
            else:
                old=prior.get('text')
                if prior.get('phase') not in ('pasted','draft_verified') or not isinstance(old,str):
                    raise RuntimeError('已有草稿没有本程序的有效凭证，不覆盖')
                if any(str(m.get('sender_id'))=='2' and int(m.get('sort_seq') or 0)>prior.get('incoming_seq',0) for m in self.db.get_messages(user,limit=20)):
                    raise RuntimeError('旧草稿后主人已回复，不覆盖或续发')
                if not self.verify_text(gui,box,old):raise RuntimeError('草稿已被修改，不覆盖')
                replace=old!=text
        elif prior.get('phase') in ('pasted','draft_verified'):
            # 旧批次从未预约点击，且有独立的新来信时，丢失的旧草稿不应永远挡住新回复。
            # 同一来信不重贴；旧事件指纹仍保留，不能借新批次重发旧事件。
            if expected_seq!=before or not event_ids or before<=prior.get('incoming_seq',0) or set(event_ids)&set(prior.get('event_ids',())):
                raise RuntimeError('原草稿已不在框中且结果未处理，禁止重发')
            self.clear_hover(gui)
            if self.draft_present(gui,box):raise RuntimeError('空白输入框复核发生变化')
            self.progress=dict(prior);self.progress['resolution']='preclick draft absent; distinct newer incoming event'
            self.mark('draft_discarded')
        self.progress={'attempt_id':uuid.uuid4().hex,'contact_hash':hashlib.sha256(user.encode()).hexdigest(),
            'text_hash':hashlib.sha256(text.encode()).hexdigest(),'text':text,'event_ids':list(event_ids),
            'name':name,'incoming_seq':before,'editor_box':box,'render_rect':gui.render_rect,
            'window_hwnd':gui.main_hwnd,'render_hwnd':gui.render_hwnd,'standalone':getattr(gui,'_passive_standalone',False)}
        if prior.get('attempt_id'):self.progress['previous_attempt']=prior['attempt_id']
        if draft_backup:self.progress['draft_backup']=draft_backup
        if draft and (adopt_draft or not replace):
            self.mark('draft_verified')
            return self.finish(gui,name,user,text,before)
        self.mark('prepared')
        self.guard(gui);gui.set_clipboard(text);self.guard(gui)
        if replace:
            self.mark('replace_reserved');gui._input.key(0x41,ctrl=True)
        self.mark('paste_reserved');self.guard(gui);gui._input.key(0x56,ctrl=True);self.mark('pasted');time.sleep(.35)
        if not self.verify_text(gui,box,text):raise RuntimeError('草稿内容未获得精确确认；保留未知结果')
        self.mark('draft_verified')
        return self.finish(gui,name,user,text,before)
    def finish(self,gui,name,user,text,before):
        self.guard(gui)
        if not self.header_matches(gui,name) or self.newest(user)!=before:raise RuntimeError('发送前上下文发生变化')
        self.clear_hover(gui)
        geometry=tuple(gui.render_rect)
        button=self.send_button(gui)
        if not self.header_matches(gui,name) or self.newest(user)!=before:raise RuntimeError('点击前上下文发生变化')
        self.guard(gui);_,x,y,w,h=button
        if tuple(gui.render_rect)!=geometry:raise RuntimeError('按钮识别后窗口位置发生变化')
        if not owned_point(gui,gui.origin_x+x+w//2,gui.origin_y+y+h//2):raise RuntimeError('发送位置不属于绑定聊天窗口')
        if self.contact_guard is not None:self.contact_guard(user);self.guard(gui)
        self.mark('send_reserved')
        gui.wx_click(gui.origin_x+x+w//2,gui.origin_y+y+h//2)
        self.mark('clicked')
        deadline=time.monotonic()+12
        while time.monotonic()<deadline:
            try:messages=self.db.get_new_messages(user,max(0,before-1),limit=200)
            except PermissionError as error:
                from stable_db import transient_read_error
                if not transient_read_error(error):raise
                self.progress['confirmation_read_error']=str(error)
                time.sleep(.4);continue
            for message in messages:
                if int(message.get('sort_seq') or 0)>before and str(message.get('sender_id'))=='2' and same_editor_text(message.get('content'),text):
                    self.progress['sent_seq']=int(message['sort_seq']);self.mark('sent_confirmed');return True
            time.sleep(.4)
        raise RuntimeError('发送结果未知，不自动重试')
    def finish_owned_draft(self,user,text,before):
        # 只接续已记为 pasted 的本次草稿，不再次粘贴，不接续点击结果未知的记录。
        journal=self.load_journal(user)
        if journal.get('phase') not in ('pasted','draft_verified') or journal.get('incoming_seq')!=before or journal.get('contact_hash')!=hashlib.sha256(user.encode()).hexdigest() or journal.get('text_hash')!=hashlib.sha256(text.encode()).hexdigest():
            raise RuntimeError('不是本次已填入的草稿，禁止继续发送')
        self.progress=journal
        hits=[c for c in self.db.search_contact(user) if c.get('username')==user]
        if len(hits)!=1:raise RuntimeError('联系人不唯一')
        name=hits[0].get('remark') or hits[0].get('nick_name')
        gui=create_gui(self.db,name=name)
        self.guard()
        if not gui.ensure_visible() or not self.header_matches(gui,name) or self.newest(user)!=before:raise RuntimeError('草稿所对应的会话发生变化')
        self.clear_hover(gui);box=self.editor_box(gui)
        if not gui.focus_input(box):raise RuntimeError('草稿输入框未聚焦')
        self.clear_hover(gui)
        if not self.header_matches(gui,name) or not self.verify_text(gui,box,text):raise RuntimeError('原草稿内容不匹配，不覆盖')
        self.mark('draft_verified')
        return self.finish(gui,name,user,text,before)

    def prepare_image(self,user,path,expected_seq=None,event_ids=(),replace_unsent_drafts=False):
        """只填入一张已生成图片，完整核对后由独立步骤决定是否点击。"""
        from PIL import Image
        import io,win32clipboard
        path=Path(path).resolve()
        if not path.is_file() or path.stat().st_size>30_000_000:raise RuntimeError('图片不存在或过大')
        with Image.open(path) as picture:
            if picture.width*picture.height>40_000_000:raise RuntimeError('图片像素过大')
            buffer=io.BytesIO();picture.convert('RGB').save(buffer,format='BMP');dib=buffer.getvalue()[14:]
        prior=self.supersede_pending(user,event_ids,expected_seq)
        if prior.get('phase') in ('send_reserved','clicked'):raise RuntimeError('上次发送结果未知，禁止继续')
        gui,name,before,box=self.prepare(user,allow_draft=replace_unsent_drafts,expected_seq=expected_seq)
        draft_backup=None
        if self.draft_present(gui,box):
            if not replace_unsent_drafts:raise RuntimeError('已有草稿，不覆盖')
            draft_backup=self.backup_authorized_draft(gui,user,name,before,box)
            self.guard(gui);gui._input.key(0x41,ctrl=True);gui._input.key(0x08);time.sleep(.2)
            self.clear_hover(gui);box=self.editor_box(gui)
            if self.draft_present(gui,box) or self.newest(user)!=before:raise RuntimeError('草稿替换前空框不能确认')
        self.progress={'attempt_id':uuid.uuid4().hex,'contact_hash':hashlib.sha256(user.encode()).hexdigest(),'kind':'image',
            'text':'[鹭鹭生成的图片]','image_path':str(path),'image_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
            'event_ids':list(event_ids),'name':name,'incoming_seq':before,'window_hwnd':gui.main_hwnd,'render_hwnd':gui.render_hwnd,
            'render_rect':list(gui.render_rect),'editor_box':list(box)}
        if draft_backup:self.progress['draft_backup']=draft_backup
        self.mark('prepared')
        self.guard(gui)
        if self.newest(user)!=before or not self.header_matches(gui,name) or self.draft_present(gui,box):raise RuntimeError('填图前会话或草稿发生变化')
        win32clipboard.OpenClipboard()
        try:
            win32clipboard.EmptyClipboard();win32clipboard.SetClipboardData(8,dib)
        finally:win32clipboard.CloseClipboard()
        self.mark('pasting_image');gui._input.key(0x56,ctrl=True);time.sleep(.8)
        self.clear_hover(gui);self.guard(gui)
        if not self.header_matches(gui,name) or self.newest(user)!=before:raise RuntimeError('填图后会话变化')
        refreshed=self.editor_box(gui)
        if not self.draft_present(gui,refreshed):raise RuntimeError('未检测到图片草稿，禁止发送')
        # 图片必须形成有尺寸的缩略图；不能把一个光标或文字提示当作成功。
        view=gui._grab_screen(gui._rel_to_screen(refreshed)).convert('RGB')
        import numpy as np
        array=np.asarray(view);dark=(array.mean(axis=2)<185);ys,xs=np.where(dark)
        if len(xs)<100 or xs.max()-xs.min()<25 or ys.max()-ys.min()<25:raise RuntimeError('图片草稿缩略图尚未确认')
        self.progress['editor_box']=list(refreshed);self.progress['preview_sha256']=image_preview_hash(view);self.progress['preview_version']=2;self.mark('image_draft_verified')
        return gui,name,before,refreshed

    def send_image(self,user,path,expected_seq=None,event_ids=(),replace_unsent_drafts=False):
        options={'replace_unsent_drafts':True} if replace_unsent_drafts else {}
        gui,name,before,box=self.prepare_image(user,path,expected_seq=expected_seq,event_ids=event_ids,**options)
        return self.finish_image(gui,name,user,before,box)

    def finish_image(self,gui,name,user,before,box):
        """只发送本次已核对的图片草稿，不重新粘贴。"""
        button=self.send_button(gui);self.guard(gui)
        if self.newest(user)!=before or not self.header_matches(gui,name):raise RuntimeError('图片点击前会话变化')
        current=gui._grab_screen(gui._rel_to_screen(box)).convert('RGB')
        fingerprint=image_preview_hash(current) if self.progress.get('preview_version')==2 else hashlib.sha256(current.tobytes()).hexdigest()
        if fingerprint!=self.progress['preview_sha256']:raise RuntimeError('图片草稿画面发生变化，禁止发送')
        _,x,y,w,h=button
        if not owned_point(gui,gui.origin_x+x+w//2,gui.origin_y+y+h//2):raise RuntimeError('图片发送按钮不属于绑定窗口')
        if self.contact_guard is not None:self.contact_guard(user);self.guard(gui)
        self.mark('send_reserved');gui.wx_click(gui.origin_x+x+w//2,gui.origin_y+y+h//2);self.mark('clicked');self.clear_hover(gui)
        deadline=time.monotonic()+12
        while time.monotonic()<deadline:
            rows=self.db.get_new_messages(user,max(0,before-1),limit=200)
            candidates=[m for m in rows if str(m.get('sender_id'))=='2' and int(m.get('sort_seq') or 0)>before and m.get('type') in ('图片','image',3,'3')]
            newer=[m for m in rows if int(m.get('sort_seq') or 0)>before]
            if len(candidates)==1 and len(newer)==1 and not self.draft_present(gui,self.editor_box(gui)):
                self.progress['sent_seq']=int(candidates[0]['sort_seq']);self.mark('sent_confirmed');return True
            time.sleep(.4)
        raise RuntimeError('图片发送结果未知，禁止自动重试')

    def resume_image_draft(self,user,path):
        """接续有凭证且从未预约点击的图片，仅用于已确认的草稿恢复。"""
        prior=self.reconcile_pending(user);path=Path(path).resolve()
        if prior.get('phase')!='image_draft_verified' or prior.get('kind')!='image':raise RuntimeError('没有可接续的已核对图片草稿')
        if prior.get('image_sha256')!=hashlib.sha256(path.read_bytes()).hexdigest():raise RuntimeError('生成图片发生变化')
        self.progress=prior
        # 图片已在编辑框中，不再次点击它；点击缩略图会改变选中外观。
        self.guard();gui=create_gui(self.db,name=prior['name']);name=prior['name'];before=prior['incoming_seq']
        if not self.header_matches(gui,name) or self.newest(user)!=before:raise RuntimeError('图片草稿会话发生变化')
        self.clear_hover(gui);box=self.editor_box(gui)
        if list(box)!=prior['editor_box']:raise RuntimeError('图片草稿输入区域发生变化')
        return self.finish_image(gui,name,user,before,box)
