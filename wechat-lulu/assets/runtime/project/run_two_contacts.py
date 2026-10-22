"""持续监听已授权并绑定的账号；安静时只做本地查询，无模型调用。"""
import argparse,hashlib,json,os,sys,time,threading
from pathlib import Path
from chatgpt_route import LocalRoute
from local_text_bridge import LocalTextBridge,ThreadSetupRetry
from wechat_listener import PrivateChatListener
from wechat_sender import AutoReplySender,ListenerInstanceLock,load_auto_reply_config
from stable_db import make_db
from clock_rules import clock_reply,clock_context
from action_rules import payment_request_reply
from size_rules import self_size_reply

class ContextReplies:
    def __init__(self,db,allowed,engine_root,state_root,**_):
        self.db=db;self.allowed=allowed;self.engine_root=Path(engine_root);self.state_root=Path(state_root)
        sys.path.insert(0,str(self.engine_root))
        from dialogue_memory import compact_context
        self.compact=compact_context;self.bridges={};self.locks={username:threading.Lock() for username in allowed};self.needs_reconnect=False
    def reply(self,username,incoming,display_name='',counter_context=None,media_messages=None):
        if username not in self.allowed:raise RuntimeError('联系人不在当前授权名单')
        if (self.state_root/'STOP').exists():raise RuntimeError('已手动停止')
        permission=json.loads((self.engine_root/'reply-permissions.json').read_text(encoding='utf-8'))
        if permission.get('openai_selected_history') is not True:raise RuntimeError('历史摘要发送授权不存在')
        rows=list(reversed(self.db.get_messages(username,limit=8)))
        recent=[{'role':'self' if str(row.get('sender_id'))=='2' else 'fan','text':row.get('content','')} for row in rows if row.get('type') in ('文本','text',1,'1')]
        if counter_context is None:
            answer=self_size_reply(incoming,recent)
            if answer is not None:
                self._metric(username,{'decision':'reply','seconds':0,'prompt_characters':0,'route':'local-owner-size-choice'})
                return answer
        if not media_messages and counter_context is None and permission.get('openai_selected_media') is True and clock_reply(incoming,recent) is None:
            from media_context import recent_media_reference
            media_messages=recent_media_reference(rows,incoming)
        images=[];media_description=None
        if media_messages:
            if permission.get('openai_selected_media') is not True:raise RuntimeError('图片及视频关键帧生成授权不存在')
            from media_context import MediaContext
            media=MediaContext(self.db,self.state_root/'media',self.allowed).prepare(username,media_messages)
            images=media['images'];media_description=media['descriptions']
            if not images:
                self._metric(username,{'decision':'reply','route':'local-media-unavailable','seconds':0,'prompt_characters':0})
                return '这段视频我这边没加载出来，你说下内容？' if any(m['kind']=='video' for m in media_messages) else '这张图我这边没加载出来，你说下内容？'
        if counter_context is None and not media_messages:
            answer=clock_reply(incoming,recent)
            if answer is not None:return answer
            answer=payment_request_reply(incoming,recent)
            if answer is not None:
                self._metric(username,{'decision':'reply','seconds':0,'prompt_characters':0,'route':'local-payment-ack'})
                return answer
        payload=self.compact(incoming,recent,contact=self.allowed[username])
        payload['current_time']=clock_context()
        payload['capabilities']={'text_reply':True,'payment_execution':False}
        if media_description is not None:payload['media']=media_description
        if counter_context is not None:payload['counterattack']=counter_context
        # 一个常驻进程支持联系人独立会话；每轮上下文仍保持有界。
        with self.locks[username]:
            bridge=self.bridges.get(username)
            if bridge is not None and bridge.needs_reconnect:
                bridge.close();self.bridges.pop(username,None);bridge=None
            for attempt in range(2):
                if (self.state_root/'STOP').exists():raise RuntimeError('已手动停止')
                try:
                    if bridge is None:
                        bridge=LocalTextBridge(self.engine_root,self.state_root/hashlib.sha256(username.encode()).hexdigest()[:20])
                        self.bridges[username]=bridge
                    result=bridge.generate(username,payload,self.allowed[username],**({'images':images} if images else {}))
                    break
                except ThreadSetupRetry as error:
                    # 仅重试服务器明确拒绝的准备阶段，绝不重试已开始的生成或发送。
                    if bridge is not None:bridge.close()
                    self.bridges.pop(username,None);bridge=None
                    if attempt:raise
                    self._metric(username,{'decision':'retry','route':'thread-setup-reconnect','error':str(error)})
        self._metric(username,result)
        if result['decision']=='reply':return result['text']
        # hold 仍给对方一个短句，避免正常工作的监听看起来无响应。
        return result['text'].strip() or '这个我得自己看一下，稍等'
    def _metric(self,username,result):
        metric={key:result.get(key) for key in ('seconds','usage','prompt_characters','decision')}
        for key in ('route','error'):
            if key in result:metric[key]=result[key]
        metric.update(at=time.time(),contact_hash=hashlib.sha256(username.encode()).hexdigest())
        with (self.state_root/'generation-metrics.jsonl').open('a',encoding='utf-8') as file:file.write(json.dumps(metric,ensure_ascii=False)+'\n')
    def close(self):
        for bridge in self.bridges.values():bridge.close()

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--engine-root',required=True,type=Path)
    parser.add_argument('--run-seconds',type=float)
    parser.add_argument('--read-only-test',action='store_true')
    args=parser.parse_args()
    root=Path(__file__).resolve().parent;state=root/'.wechat_codex';state.mkdir(exist_ok=True)
    stop=state/'STOP'
    if stop.exists():raise RuntimeError('停止开关开启；只能由人类明确启动后恢复')
    instance=ListenerInstanceLock(state/'listener.lock').acquire()
    listener=None;route=None
    try:
        from wechatauto import WeChatDB
        # 微信库会重新包装 stdout；显式恢复实时日志，不等退出才刷新。
        if hasattr(sys.stdout,'reconfigure'):sys.stdout.reconfigure(encoding='utf-8',line_buffering=True,write_through=True)
        db=make_db();allowed=load_auto_reply_config(root/'wechat_autoreply.json',db)
        if not allowed:raise RuntimeError('自动回复名单未启用')
        if getattr(allowed,'dynamic',False):
            from lulu_runtime import run_assistant
            return run_assistant(db,allowed,args.engine_root,state,read_only=args.read_only_test,run_seconds=args.run_seconds)
        sys.path.insert(0,str(args.engine_root))
        from counter_policy import CounterPolicy
        counter=None if args.read_only_test else CounterPolicy(args.engine_root,allowed)
        account=hashlib.sha256(db.get_self_info()['username'].encode()).hexdigest()[:20]
        if not args.read_only_test:
            route=LocalRoute().start()
            os.environ['HTTPS_PROXY']=os.environ['HTTP_PROXY']='http://127.0.0.1:21002'
        sender=None if args.read_only_test else AutoReplySender(db,allowed,state/account/'deliveries.sqlite3')
        replies=None
        def factory(**kwargs):
            nonlocal replies
            if args.read_only_test:raise RuntimeError('本地读取测试不调用 Codex')
            if replies is None:replies=ContextReplies(db,allowed,args.engine_root,state,**kwargs)
            return replies
        listener=PrivateChatListener(db,bridge_factory=factory,state_root=state,allowed_contacts=allowed,
            auto_sender=sender,merge_seconds=2,poll_interval=1,max_workers=len(allowed),local_rules=False,counter_policy=counter,
            output=lambda value:print(value,flush=True),media_enabled=not args.read_only_test)
        listener.initialize();started=time.monotonic();last_status=0
        print('仅监听'+ '、'.join(allowed.values())+'；持续运行直到手动停止；安静时零模型调用',flush=True)
        if counter is not None:print('分级反击已启用：轻度/直接辱骂本地回复；严重辱骂调用 Codex；五分钟内三次直接辱骂升级',flush=True)
        while not stop.exists() and (args.run_seconds is None or time.monotonic()-started<args.run_seconds):
            listener.poll_once()
            if time.monotonic()-last_status>=5:
                data={'pid':os.getpid(),'updatedAt':time.time(),'mode':'read-only-test' if args.read_only_test else 'continuous','contacts':list(allowed.values()),'received':listener.received,'sent':listener.sent,'errors':listener.errors,'lastError':listener.last_error,'pending':len(listener._pending),'active':len(listener._active),'modelStarted':replies is not None and bool(replies.bridges)}
                data['counterattack']={'available':counter is not None,'enabled':counter is not None and json.loads(counter.path.read_text(encoding='utf-8')).get('enabled') is True,'severeRoute':'Codex','moderateRoute':'local'}
                destination=state/'listener-status.json';temporary=destination.with_suffix('.tmp')
                temporary.write_text(json.dumps(data,ensure_ascii=False),encoding='utf-8');temporary.replace(destination);last_status=time.monotonic()
            time.sleep(1)
        listener._stop.set()
    finally:
        if listener is not None:listener.close()
        if route is not None:route.close()
        instance.close()

if __name__=='__main__':
    if hasattr(sys.stdout,'reconfigure'):sys.stdout.reconfigure(encoding='utf-8')
    raise SystemExit(main())
