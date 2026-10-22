"""全部联系人持续监听；每日招呼在本地处理，其余按需唤起鹭鹭。"""
import json,os,time,hashlib,threading,re,queue
from pathlib import Path
from chatgpt_route import LocalRoute
from wechat_listener import PrivateChatListener
from wechat_sender import AutoReplySender,KnownNotSent
from lulu_policy import GREETING,DailyGreetings,LuluReply,ImportantInbox,shanghai_day
from lulu_bridge import LuluBridge,contact_reply_perspective
from local_text_bridge import ThreadSetupRetry
from clock_rules import clock_reply,clock_context
from media_context import MediaContext,recent_media_reference
from ui_permission import ui_pause_reason
import topic_policy
from pending_reply_policy import policy_enabled
from listener_status import publish_status
from stable_db import guarded_cache_read_step

class LuluSender(AutoReplySender):
    def __init__(self,db,allowed,path,greetings,send=None,stop_path=None):
        super().__init__(db,allowed,path,send=send,stop_path=stop_path);self.greetings=greetings
    def preflight_batch(self,user,event_ids,incoming_seq):
        record=super().preflight_batch(user,event_ids,incoming_seq)
        if event_ids and isinstance(incoming_seq,int) and policy_enabled(self.db):
            rows=self.db.get_messages(user,limit=20)
            maximum=max((int(row.get('sort_seq') or 0) for row in rows),default=0)
            latest=[row for row in rows if int(row.get('sort_seq') or 0)==incoming_seq and str(row.get('sender_id'))!='2']
            if maximum==incoming_seq and latest:
                self.greetings.supersede_reserved(user,shanghai_day(),event_ids,incoming_seq,
                    max(int(row.get('create_time') or 0) for row in latest))
        return record
    def deliver(self,user,text,event_ids,incoming_seq):
        if getattr(self.allowed,'dynamic',False):self.allowed.refresh()
        if user not in self.allowed or not event_ids:return '仅打印'
        if topic_policy.latest_incoming_blocked(self.db,user):return '未发送：政治敏感话题按设置静默'
        if self.stop_path.exists():raise RuntimeError('已手动停止，不创建招呼预约')
        if contact_reply_perspective(user)=='owner':return super().deliver(user,text,event_ids,incoming_seq)
        today=shanghai_day();status=self.greetings.status(user,today)
        if status=='reserved':raise RuntimeError('当日招呼发送结果未确认，禁止重复介绍或越过介绍发送')
        if status!='sent':
            text=LuluReply(GREETING,day=today)
            self.greetings.reserve(user,today,event_ids)
        elif getattr(text,'day',None):
            return '未发送：当天介绍已完成'
        try:result=super().deliver(user,text,event_ids,incoming_seq)
        except KnownNotSent:
            if getattr(text,'day',None):self.greetings.finish(user,text.day,'blocked')
            raise
        if getattr(text,'day',None):
            self.greetings.finish(user,text.day,'sent' if result=='已发送并确认' else 'skipped')
        return result

class LuluReplies:
    def __init__(self,db,allowed,engine_root,state,greetings,**_):
        self.db=db;self.allowed=allowed;self.engine_root=Path(engine_root);self.state=Path(state);self.greetings=greetings
        self.inbox=ImportantInbox(self.state/'lulu');self.bridges={};self.locks={};self.lock=threading.Lock();self.needs_reconnect=False
        self.available=queue.LifoQueue();self.next_bridge=0
    def take_bridge(self):
        try:bridge=self.available.get_nowait()
        except queue.Empty:bridge=None
        if bridge is not None and bridge.needs_reconnect:
            bridge.close();self.bridges.pop(bridge._lulu_key,None);bridge=None
        if bridge is None:
            with self.lock:key=str(self.next_bridge);self.next_bridge+=1
            bridge=LuluBridge(self.engine_root,self.state/'lulu'/('worker-'+key));bridge._lulu_key=key
            self.bridges[key]=bridge
        return bridge
    def reply(self,user,incoming,display_name='',counter_context=None,media_messages=None):
        if getattr(self.allowed,'dynamic',False):self.allowed.refresh()
        if user not in self.allowed:raise RuntimeError('不是当前账号的授权私聊联系人')
        if (self.state/'STOP').exists():raise RuntimeError('已手动停止')
        policy_active=topic_policy.enabled()
        rows=list(reversed(self.db.get_messages(user,limit=8)))
        recent=[{'role':'owner_or_assistant' if str(row.get('sender_id'))=='2' else 'contact','text':str(row.get('content',''))[:1000]} for row in rows if row.get('type') in ('文本','text',1,'1')]
        if policy_active and topic_policy.sensitive(incoming,recent):return LuluReply('')
        name=self.db.get_nickname(user) or display_name or user;day=shanghai_day()
        owner_perspective=contact_reply_perspective(user)=='owner'
        status='sent' if owner_perspective else self.greetings.status(user,day)
        if re.search('紧急|急事|很重要|重要消息|出事|救命|马上回电|尽快回电',incoming):self.inbox.add(user,name,incoming,incoming[:200])
        if status=='reserved':raise RuntimeError('当日介绍的结果尚未确认')
        if status!='sent' and not media_messages and (not policy_active or topic_policy.safe_first_message(incoming)):return LuluReply(GREETING,day=day)
        if policy_active and incoming in ('[语音消息]','[文件或卡片消息]','[表情消息]'):return LuluReply('')
        if incoming=='[语音消息]':return LuluReply('鹭鹭暂时听不了语音，方便发文字告诉我吗？')
        if incoming=='[文件或卡片消息]':return LuluReply('文件或卡片收到啦，需要我转告主人什么事情呢？')
        if incoming=='[表情消息]':return LuluReply('鹭鹭收到你的表情啦～')
        answer=clock_reply(incoming,[{'role':'self' if r['role']=='owner_or_assistant' else 'fan','text':r['text']} for r in recent])
        if answer and (status=='sent' or not policy_active):return LuluReply(answer)
        if not media_messages:media_messages=recent_media_reference(rows,incoming)
        images=[];descriptions=[]
        if media_messages:
            prepared=MediaContext(self.db,self.state/'media',self.allowed).prepare(user,media_messages)
            images=prepared['images'];descriptions=prepared['descriptions']
            if not images:return LuluReply('' if policy_active else '这张图或视频暂时没加载出来，可以重发一下，或者告诉鹭鹭里面的内容吗？')
        payload={'incoming':incoming[:2000],'recent':recent,'current_time':clock_context(),'media':descriptions,'capabilities':{'assistant':'鹭鹭','web_search':True,'image_generation':True,'payment_execution':False}}
        payload['topic_policy']={'sensitive_politics':'silent' if policy_active else 'default','greeting_only':status!='sent','greeting':GREETING}
        with self.lock:contact_lock=self.locks.setdefault(user,threading.Lock())
        with contact_lock:
            bridge=None
            try:
                for attempt in range(2):
                    if (self.state/'STOP').exists():raise RuntimeError('已手动停止')
                    try:
                        bridge=self.take_bridge()
                        result=bridge.generate(user,payload,name,images=images)
                        break
                    except ThreadSetupRetry:
                        if bridge is not None:
                            bridge.close();self.bridges.pop(bridge._lulu_key,None);bridge=None
                        if attempt:raise
            finally:
                if bridge is not None:self.available.put(bridge)
        if result['important']:self.inbox.add(user,name,incoming,result['summary'] or incoming[:200])
        if result.get('decision')=='silent':return LuluReply('')
        if status!='sent':return LuluReply(GREETING,day=day)
        with (self.state/'lulu'/'generation-metrics.jsonl').open('a',encoding='utf-8') as stream:
            stream.write(json.dumps({'at':time.time(),'contact_hash':hashlib.sha256(user.encode()).hexdigest(),'seconds':result['seconds'],'usage':result.get('usage'),'searches':result['searches'],'generated_image':bool(result['image_path']),
                'image_requested':result.get('image_requested',False),'image_tool_enabled':result.get('image_tool_enabled',False),'image_attempts':result.get('image_attempts',0),'image_errors':result.get('image_errors',[])},ensure_ascii=False)+'\n')
        return LuluReply(result['text'],image_path=result['image_path'],important=result['important'],summary=result['summary'])
    def close(self):
        for bridge in list(self.bridges.values()):bridge.close()

def run_assistant(db,allowed,engine_root,state,read_only=False,run_seconds=None):
    state=Path(state);account=hashlib.sha256(db.get_self_info()['username'].encode()).hexdigest()[:20]
    ledger=state/account/'deliveries.sqlite3';greetings=DailyGreetings(ledger);route=None;listener=None;replies=None
    try:
        if not read_only:
            route=LocalRoute().start();os.environ['HTTP_PROXY']=os.environ['HTTPS_PROXY']='http://127.0.0.1:21002'
        sender=None if read_only else LuluSender(db,allowed,ledger,greetings)
        def factory(**kwargs):
            nonlocal replies
            if read_only:raise RuntimeError('只读检查不调用模型')
            if replies is None:replies=LuluReplies(db,allowed,engine_root,state,greetings)
            return replies
        cutover_path=state/'serial-cutover.json';cutover=None;resume_since=None;resume_max_age=600
        if not read_only and cutover_path.exists():
            cutover=json.loads(cutover_path.read_text(encoding='utf-8-sig'))
            if cutover.get('status')=='pending':
                if cutover.get('account')!=db.get_self_info()['username']:raise RuntimeError('排队部署接续账号不匹配')
                resume_since=cutover.get('since')
                if cutover.get('maintenance_authorized') is True:resume_max_age=cutover.get('max_resume_age_seconds',600)
        listener=PrivateChatListener(db,bridge_factory=factory,state_root=state,auto_sender=sender,allowed_contacts=allowed,
            merge_seconds=2,poll_interval=1,max_workers=1,serial_replies=True,persist_queue=not read_only,resume_since=resume_since,resume_max_age=resume_max_age,dispatch_allowed=lambda:not ui_pause_reason(),local_rules=False,counter_policy=None,media_enabled=not read_only,baseline_page_size=2,accept_all_message_types=True,output=lambda value:print(value,flush=True))
        listener.initialize();started=time.monotonic();last_status=0;last_publish_warning=0
        cache_read_failures=0;last_cache_read_error=None;last_cache_read_warning=0
        if resume_since is not None:
            cutover.update(status='consumed',consumedAt=time.time(),pid=os.getpid())
            cutover_path.write_text(json.dumps(cutover,ensure_ascii=False),encoding='utf-8')
        print('鹭鹭助手：全部真实联系人私聊；每日首次招呼本地处理；后续按需联网、看图和画图。',flush=True)
        def report_cache_read_failure(error):
            nonlocal cache_read_failures,last_cache_read_error,last_cache_read_warning
            cache_read_failures+=1
            last_cache_read_error={'at':time.time(),'operation':error.operation,'message':str(error)}
            if time.monotonic()-last_cache_read_warning>=60:
                print('微信缓存读取暂时被占用；保留队列、水位和发送预约，下轮重新读取，不重发未知结果。',flush=True)
                last_cache_read_warning=time.monotonic()
        def poll_and_publish():
            nonlocal last_status,last_publish_warning
            listener.poll_once()
            if time.monotonic()-last_status>=5:
                data={'pid':os.getpid(),'updatedAt':time.time(),'mode':'read-only-test' if read_only else 'lulu-assistant','scope':'all_private_contacts','assistant':'鹭鹭','model':'gpt-6.1-sol','reasoningEffort':'medium','greetingReservations':greetings.health_counts(),'contactCount':len(allowed),'received':listener.received,'sent':listener.sent,'errors':listener.errors,'lastError':listener.last_error,'pending':len(listener._pending),'active':len(listener._active),'modelStarted':bool(replies and replies.bridges),'webSearch':True,'imageGeneration':True,'dailyGreeting':GREETING,'counterattack':{'enabled':False}}
                data['replyQueue']=listener.queue_status()
                data['pendingReplyPolicy']='latest_new_message' if policy_enabled(db) else 'preserve_pending'
                data['imageGenerationRoutingVersion']=3
                data['contactExclusions']=allowed.policy_status()
                data['contentPolicy']={'sensitivePolitics':'silent' if topic_policy.enabled() else 'default'}
                pause_reason=ui_pause_reason()
                data['interaction']={'mode':'background_only' if pause_reason else 'desktop_ui','uiPaused':bool(pause_reason),'reason':pause_reason}
                data['statusPublisherVersion']=2
                data['cacheReadGuardVersion']=1
                data['contactStyleRoutingVersion']=3
                data['cacheRead']={'transientFailures':cache_read_failures,'lastTransientError':last_cache_read_error,'blocked':False}
                if not publish_status(state/'listener-status.json',data) and time.monotonic()-last_publish_warning>=60:
                    print('状态文件暂时被其他进程占用；监听继续，下轮重新发布完整快照。',flush=True)
                    last_publish_warning=time.monotonic()
                last_status=time.monotonic()
        while not (state/'STOP').exists() and (run_seconds is None or time.monotonic()-started<run_seconds):
            guarded_cache_read_step(poll_and_publish,report_cache_read_failure)
            time.sleep(1)
    finally:
        if listener is not None:listener.close()
        if route is not None:route.close()
