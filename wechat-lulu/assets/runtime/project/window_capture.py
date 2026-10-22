"""读取指定微信窗口的画面，避免把遮挡它的其他程序当成微信。"""
import ctypes
from PIL import Image

def crop_window_image(image,window_rect,box):
    """把屏幕坐标转换为目标窗口坐标，越界时拒绝沿用旧布局。"""
    left,top,right,bottom=window_rect
    x0,y0,x1,y1=box
    if not (left<=x0<x1<=right and top<=y0<y1<=bottom):
        raise RuntimeError('微信截图区域已超出绑定窗口')
    if image.size!=(right-left,bottom-top):raise RuntimeError('微信截图尺寸与窗口不一致')
    return image.crop((x0-left,y0-top,x1-left,y1-top))

def _window_bitmap(hwnd):
    """仅捕获指定窗口自己的位图。"""
    import win32gui,win32ui
    rect=win32gui.GetWindowRect(hwnd)
    width,height=rect[2]-rect[0],rect[3]-rect[1]
    if width<=0 or height<=0:raise RuntimeError('微信窗口尺寸无效')
    source_dc=win32gui.GetWindowDC(hwnd)
    source=memory=bitmap=None
    try:
        source=win32ui.CreateDCFromHandle(source_dc);memory=source.CreateCompatibleDC()
        bitmap=win32ui.CreateBitmap();bitmap.CreateCompatibleBitmap(source,width,height)
        memory.SelectObject(bitmap)
        if not ctypes.windll.user32.PrintWindow(hwnd,memory.GetSafeHdc(),2):
            raise RuntimeError('微信窗口截图失败，禁止改读其他窗口')
        picture=Image.frombuffer('RGB',(width,height),bitmap.GetBitmapBits(True),'raw','BGRX',0,1)
        if max(high-low for low,high in picture.getextrema())<10:
            raise RuntimeError('微信窗口截图为空白，停止识别')
        return picture,rect
    finally:
        if memory is not None:memory.DeleteDC()
        if source is not None:source.DeleteDC()
        win32gui.ReleaseDC(hwnd,source_dc)
        if bitmap is not None:win32gui.DeleteObject(bitmap.GetHandle())

def capture_window(gui,box=None):
    """合成本窗口及直属弹层；失败不退回桌面或其他应用截图。"""
    import win32gui,win32process
    if not win32gui.IsWindow(gui.main_hwnd) or not win32gui.IsWindowVisible(gui.main_hwnd) or win32gui.IsIconic(gui.main_hwnd):raise RuntimeError('微信窗口当前不可截图')
    if tuple(win32gui.GetWindowRect(gui.render_hwnd))!=tuple(gui.render_rect):raise RuntimeError('微信窗口位置已变化，停止沿用旧截图坐标')
    picture,rect=_window_bitmap(gui.main_hwnd);popups=[]
    def visit(hwnd,_):
        if win32gui.IsWindowVisible(hwnd) and win32gui.GetWindow(hwnd,4)==gui.main_hwnd and win32process.GetWindowThreadProcessId(hwnd)[1]==gui.pid:
            position=win32gui.GetWindowRect(hwnd)
            if position[0]<rect[2] and position[2]>rect[0] and position[1]<rect[3] and position[3]>rect[1]:popups.append(hwnd)
        return True
    win32gui.EnumWindows(visit,None)
    for hwnd in reversed(popups):
        overlay,position=_window_bitmap(hwnd);picture.paste(overlay,(position[0]-rect[0],position[1]-rect[1]))
    return crop_window_image(picture,rect,box or gui.render_rect)
