"""用户玩游戏期间只允许后台处理，不恢复窗口或注入桌面输入。"""
import json
from pathlib import Path

DEFAULT_ROOT=Path(__file__).resolve().parent

class UiInteractionPaused(RuntimeError):
    """暂停的是界面操作，监听和未处理消息不能被当成已发送。"""

def ui_pause_reason(root=None):
    root=Path(root) if root is not None else DEFAULT_ROOT
    pause=root/'.wechat_codex'/'UI-PAUSE'
    if pause.exists():
        try:return pause.read_text(encoding='utf-8-sig')[:300] or '用户要求仅后台处理'
        except OSError:return '界面暂停文件不可读，保持暂停'
    config=root/'wechat_autoreply.json'
    if config.exists():
        try:
            if json.loads(config.read_text(encoding='utf-8-sig')).get('interaction_mode')=='background_only':return '用户要求仅后台处理'
        except (OSError,ValueError):return '界面权限配置不可读，保持暂停'
    return None

def ensure_ui_allowed(root=None):
    reason=ui_pause_reason(root)
    if reason:raise UiInteractionPaused('界面操作已暂停：'+reason)
