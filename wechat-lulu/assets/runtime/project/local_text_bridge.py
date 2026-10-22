"""常驻 Codex 回复进程；文字与视觉输入均有界，不恢复旧聊天事实。"""
import json,os,time
from pathlib import Path
from codex_bridge import CodexBridge,BridgeError,RpcError

class ThreadSetupRetry(BridgeError):
    """账号读取或会话创建被明确拒绝；尚未提交生成，可以重建连接。"""

def routing_setup_error(error):
    """只识别服务器已返回的路由发现失败，不重试状态未知的生成。"""
    message=str(error).lower()
    return (isinstance(error,RpcError) and error.code == -32603
            and 'workspace routing discovery' in message
            and ('failed' in message or 'timed out' in message))

class LocalTextBridge(CodexBridge):
    def __init__(self,engine_root,state_root):
        import sys
        sys.path.insert(0,str(engine_root))
        import reply_engine
        self.engine_root=Path(engine_root)
        self.instructions=(self.engine_root/'reply-instructions.txt').read_text(encoding='utf-8')
        self.usage=None
        work=self.engine_root/'reply-runtime';work.mkdir(exist_ok=True)
        try:
            super().__init__(state_path=Path(state_root)/'text-bridge.json',cwd=work,model='gpt-6-luna',
                command=[reply_engine.resolve_native_cli(),'app-server'],request_timeout=20,turn_timeout=50)
        except RpcError as error:
            # 父类已关闭失败进程并释放锁；此时还没有提交任何模型轮次。
            if routing_setup_error(error):raise ThreadSetupRetry(str(error)) from error
            raise
    def _read_state(self):return {}
    def _save_state(self):pass
    def _thread_params(self):
        # 每轮使用最新语气规则；修改规则不需要重新启动模型或恢复旧上下文。
        self.instructions=(self.engine_root/'reply-instructions.txt').read_text(encoding='utf-8')
        params=super()._thread_params()
        params.update(baseInstructions=self.instructions,developerInstructions='只输出JSON，字段decision为reply或hold，text为最多60字的单条微信回复。')
        params['config'].update({'features.skip_host_skill_discovery':True,'project_doc_max_bytes':0,'features.workspace_dependencies':False,'features.goals':False,'features.sleep_tool':False})
        return params
    def _ensure_thread(self,contact_id):
        params=self._thread_params();params.update(ephemeral=True,environments=[])
        try:
            result=self.request('thread/start',params)
        except RpcError as error:
            if routing_setup_error(error):
                raise ThreadSetupRetry(str(error)) from error
            raise
        thread_id=result.get('thread',{}).get('id')
        if not thread_id:raise BridgeError('未获得独立生成会话')
        self.effective_model=result.get('model');self.effective_effort=result.get('reasoningEffort')
        expected=getattr(self,'reasoning_effort','low')
        if self.effective_effort not in (None,expected):raise BridgeError('未应用 '+expected+' 推理强度')
        return thread_id
    def _collect(self,events,thread_id,turn_id,initial_turn):
        owner=self
        class UsageQueue:
            def get(self,*args,**kwargs):
                event=events.get(*args,**kwargs)
                if isinstance(event,dict) and event.get('method')=='thread/tokenUsage/updated':
                    owner.usage=event.get('params',{}).get('tokenUsage')
                return event
        return super()._collect(UsageQueue(),thread_id,turn_id,initial_turn)
    def generate(self,contact_id,payload,display_name,images=None):
        self.usage=None;start=time.monotonic()
        kwargs={'input_items':[{'type':'localImage','path':str(Path(path).resolve()),'detail':'auto'} for path in images]} if images else {}
        raw=self.reply(contact_id,json.dumps(payload,ensure_ascii=False,separators=(',',':')),display_name,**kwargs)
        try:result=json.loads(raw)
        except ValueError as exc:raise BridgeError('模型未返回规定格式，停止发送') from exc
        text=result.get('text');decision=result.get('decision')
        if decision not in ('reply','hold') or not isinstance(text,str) or len(text)>60 or any(ord(c)<32 for c in text):raise BridgeError('回复格式无效，停止发送')
        if decision=='reply' and not text.strip():raise BridgeError('回复为空，停止发送')
        result.update(seconds=round(time.monotonic()-start,2),usage=self.usage,prompt_characters=len(json.dumps(payload,ensure_ascii=False)))
        return result
