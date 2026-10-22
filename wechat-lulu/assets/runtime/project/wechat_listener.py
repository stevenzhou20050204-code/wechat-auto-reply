"""监听微信私聊；只向用户明确授权的联系人自动回复。"""

from __future__ import annotations

import argparse
from collections import defaultdict, deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, asdict
from datetime import datetime
import hashlib
import math
from pathlib import Path
import threading
import time
from typing import Callable, Optional
from clock_rules import clock_reply
from media_context import media_descriptor


POLL_INTERVAL = 1.0
MERGE_SECONDS = 5.0
PAGE_SIZE = 200
ACTIVE_RECHECK_SECONDS = 15.0
IDLE_RECHECK_SECONDS = 30.0
MAX_IDLE_CHECKS = 2


def is_group_chat(username):
    return "@chatroom" in str(username or "")


def clean_text(text):
    return str(text or "").replace("\x00", "").strip()


def local_rule_reply(text):
    """只匹配完整短句；复杂内容和多条合并消息交给 Codex。"""
    rules = {
        "在吗": "在，怎么了？", "在不在": "在，怎么了？", "在嘛": "在，怎么了？",
        "收到": "好", "好的": "好", "好": "嗯嗯", "谢谢": "没事",
        "谢了": "没事", "晚安": "晚安", "早": "早", "早上好": "早上好",
    }
    return rules.get(clean_text(text).rstrip("？?！!。~～"))


@dataclass(frozen=True)
class ReplyRecord:
    contact_id: str
    display_name: str
    incoming: str
    reply: str
    source: str
    delivery: str = "仅打印"


@dataclass(frozen=True)
class ReplyBatch:
    text: str
    event_ids: tuple
    incoming_seq: int
    media: tuple = ()
    order: tuple = ()


class PrivateChatListener:
    """数据库、桥接工厂和输出均可注入，离线验证不需要启动微信。"""

    def __init__(self, db, bridge_factory=None, state_root=None, model=None,
                 merge_seconds=MERGE_SECONDS, poll_interval=POLL_INTERVAL,
                 output=print, on_reply=None, clock=time.monotonic,
                 wall_clock=time.time, max_workers=4, auto_sender=None, allowed_contacts=None, local_rules=True, counter_policy=None, media_enabled=False, baseline_page_size=PAGE_SIZE, accept_all_message_types=False, serial_replies=False, resume_since=None,dispatch_allowed=None,resume_max_age=600,persist_queue=False):
        self.db = db
        self.bridge_factory = bridge_factory
        self.model = model
        self.merge_seconds = float(merge_seconds)
        self.poll_interval = float(poll_interval)
        if self.merge_seconds < 0 or self.poll_interval <= 0:
            raise ValueError("合并时间不能为负，扫描间隔必须大于 0")
        self.output = output
        self.on_reply = on_reply
        self.clock = clock
        self.wall_clock = wall_clock
        started = wall_clock()
        self.started_at = int(started)
        self.auto_send_since = math.ceil(started)
        self.resume_since = resume_since
        if resume_since is not None:
            if not isinstance(resume_max_age,int) or not 600<=resume_max_age<=21600:raise ValueError('维护接续期限无效')
            if not isinstance(resume_since,int) or not 0<=started-resume_since<=resume_max_age:raise ValueError('维护接续起点无效或已过期')
            self.started_at=self.auto_send_since=resume_since
        self.serial_replies=bool(serial_replies)
        self.dispatch_allowed=dispatch_allowed or (lambda:True)
        self.auto_sender = auto_sender
        self.allowed_contacts = (None if allowed_contacts is None else allowed_contacts if getattr(allowed_contacts,'dynamic',False) else frozenset(allowed_contacts))
        self.baseline_page_size=baseline_page_size
        self.accept_all_message_types=accept_all_message_types
        self.local_rules = bool(local_rules)
        self.counter_policy = counter_policy
        self.media_enabled=bool(media_enabled)
        info = db.get_self_info()
        self.self_id = str(info.get("username") or getattr(db, "wxid", ""))
        if not self.self_id:
            raise RuntimeError("无法确认当前微信账号；停止监听以避免账号上下文混用")
        self.self_name = info.get("nick_name") or self.self_id
        account = hashlib.sha256(self.self_id.encode("utf-8")).hexdigest()[:20]
        root = Path(state_root or Path(__file__).resolve().parent / ".wechat_codex")
        self.state_path = root / account / "threads.json"
        self.bridge_cwd = self.state_path.parent / "workspace"
        self.watermarks = {}
        self._boundary_seen = {}
        self._initial_contacts = set()
        self._baseline_retry = set()
        self._session_fingerprints = {}
        self._last_checked = {}
        self._active_until = {}
        self._read_retry = set()
        self._names = {}
        self._bridge = None
        self._bridge_lock = threading.Lock()
        self._output_lock = threading.Lock()
        self._lock = threading.RLock()
        self._idle = threading.Condition(self._lock)
        self._pending = {}
        self._pending_events = defaultdict(list)
        self._pending_media = defaultdict(list)
        self._pending_order = {}
        self._pending_started = {}
        self._order_counter = 0
        self._batches = defaultdict(deque)
        self._active = set()
        self._serial_running = False
        self._executor = ThreadPoolExecutor(max_workers=1 if self.serial_replies else max_workers,
                                            thread_name_prefix="wechat-reply")
        self._stop = threading.Event()
        self._scheduler = None
        self._initialized = False
        self._closed = False
        self.replies = 0
        self.errors = 0
        self.received = 0
        self.sent = 0
        self.last_error = None
        self._last_heartbeat = self.clock()
        self._inflight_batches = {}
        self._model_resume_at = 0.0
        self._model_pause_reason = None
        self._queue_journal = None
        if persist_queue:
            if not self.serial_replies:raise ValueError('持久队列仅支持全局串行回复')
            from reply_queue_journal import ReplyQueueJournal
            self._queue_journal=ReplyQueueJournal(self.state_path.parent/'reply-queue.json',self.self_id)
            saved=self._queue_journal.load()
            self._model_resume_at=float(saved.get('resume_at',0))
            self._model_pause_reason=saved.get('pause_reason')
            for row in saved['batches']:
                value=row['batch']
                batch=ReplyBatch(value['text'],tuple(value['event_ids']),value['incoming_seq'],tuple(value['media']),tuple(value['order']))
                self._batches[row['contact_id']].append(batch)
                self._order_counter=max(self._order_counter,int(batch.order[2]))

    def _save_queue_locked(self):
        if self._queue_journal is None:return
        rows=[]
        for user,batches in self._batches.items():
            rows.extend({'contact_id':user,'batch':asdict(batch)} for batch in batches)
        rows.extend({'contact_id':user,'batch':asdict(batch)} for user,batch in self._inflight_batches.items())
        for user,(messages,_) in self._pending.items():
            events=self._pending_events[user]
            ids=tuple(e[0] for e in events) if events and all(e[1] for e in events) else ()
            batch=ReplyBatch('\n'.join(messages),ids,max((e[2] for e in events),default=0),tuple(self._pending_media[user]),self._pending_order[user])
            rows.append({'contact_id':user,'batch':asdict(batch)})
        rows.sort(key=lambda row:row['batch']['order'])
        self._queue_journal.save(rows,self._model_resume_at,self._model_pause_reason)


    def _print(self, text):
        with self._output_lock:
            self.output(text)

    def _private(self, username):
        if self.allowed_contacts is not None and username not in self.allowed_contacts:
            return False
        # 公众号和微信内置服务不属于联系人私聊。
        return bool(username and username != self.self_id
                    and not is_group_chat(username)
                    and not username.startswith("gh_")
                    and username not in {"filehelper", "weixin", "newsapp",
                                         "fmessage", "medianote", "floatbottle",
                                         "qmessage", "qqmail", "tmessage"})

    def get_display_name(self, username):
        if username not in self._names:
            try:
                self._names[username] = self.db.get_nickname(username) or username
            except Exception:
                return username
        return self._names[username]

    @staticmethod
    def _seq(message):
        return int(message.get("sort_seq") or 0)

    @staticmethod
    def _key(message):
        # 分片之间 local_id 可能相同，加入发送方、时间、正文辅助去重。
        return tuple(str(message.get(k, "")) for k in (
            "local_id", "server_id", "sender_id",
            "create_time", "type", "content"))

    @staticmethod
    def _session_key(session):
        # 秒级时间相同时，摘要、未读数量和发送者仍会反映会话变化。
        return tuple(str(session.get(k, "")) for k in (
            "last_time", "summary", "unread", "last_msg_sender",
            "last_sender", "last_sender_display_name"))

    def _bootstrap(self, username, initial=False):
        """启动时记录历史水位；新会话读取启动后全部消息，不能只取最后一条。"""
        resume=initial and self.resume_since is not None
        limit = self.baseline_page_size if initial and not resume else PAGE_SIZE
        messages = self.db.get_messages(username, limit=limit)
        if not messages:
            self.watermarks[username] = 0
            self._boundary_seen[username] = set()
            return
        maximum = max(self._seq(m) for m in messages)
        # 必须覆盖最大 sort_seq 的全部记录，防止重叠查询重放启动历史。
        while len(messages) == limit and (
            self._seq(messages[-1]) == maximum or
            ((not initial or resume) and int(messages[-1].get("create_time") or 0) >= self.started_at)
        ):
            limit *= 2
            messages = self.db.get_messages(username, limit=limit)
            maximum = max((self._seq(m) for m in messages), default=0)
        if not initial or resume:
            for message in sorted(messages, key=self._seq):
                if int(message.get("create_time") or 0) >= self.started_at:
                    self._accept_message(username, message)
        self.watermarks[username] = maximum
        self._boundary_seen[username] = {
            self._key(m) for m in messages if self._seq(m) == maximum
        }

    def initialize(self):
        if self._initialized:
            return len(self.watermarks)
        # SQLite LIMIT -1 表示不限制数量，避免超过 500 个会话后漏监听。
        sessions = self.db.get_sessions(limit=-1)
        self._session_fingerprints = {
            str(s.get("username") or ""): self._session_key(s) for s in sessions
        }
        self._initial_contacts = {
            str(s.get("username") or "") for s in sessions
            if self._private(str(s.get("username") or ""))
        }
        for username in sorted(self._initial_contacts):
            try:
                self._bootstrap(username, initial=True)
                self._last_checked[username] = self.clock()
            except Exception as exc:
                self._baseline_retry.add(username)
                self._print(f"初始化 {username} 水位失败，将重试：{exc}")
        self._initialized = True
        dynamic=self.auto_sender is not None and getattr(self.auto_sender.allowed,'dynamic',False)
        sending = ('鹭鹭自动回复全部真实联系人私聊' if dynamic else "自动发送仅限：" + "、".join(self.auto_sender.allowed.values())
                   if self.auto_sender is not None else "当前全部只打印拟回复")
        self._print(f"当前微信账号：{self.self_name}\n微信全私聊监听已启动："
                    f"{len(self._initial_contacts)} 个私聊；群聊已排除\n"
                    f"每人独立合并 {self.merge_seconds:g} 秒；复杂消息使用 {'gpt-6.1-sol / medium' if dynamic else 'Codex low'}\n"
                    f"{sending}；"+('群聊、公众号、服务及本人已排除；' if dynamic else '其他联系人只打印；')+'按 Ctrl+C 停止')
        if self.serial_replies:self._print('回复全局排队：按最早来信顺序逐个生成和发送；同人排队期间追加消息合并；同一时间只处理一份回复。')
        return len(self.watermarks)

    def _accept_message(self, username, message):
        if not self._private(username):
            return
        sender = message.get("sender_username") or ""
        sender_id = message.get("sender_id")
        if str(sender_id) == "2":
            return
        media=media_descriptor(message) if self.media_enabled else None
        other={'语音':'[语音消息]','voice':'[语音消息]',34:'[语音消息]','34':'[语音消息]',
            '表情':'[表情消息]','emoji':'[表情消息]',47:'[表情消息]','47':'[表情消息]',
            '文件/分享/名片':'[文件或卡片消息]','file':'[文件或卡片消息]',49:'[文件或卡片消息]','49':'[文件或卡片消息]'}.get(message.get('type')) if self.accept_all_message_types else None
        if message.get("type") not in ("文本", "text", 1, "1") and media is None and other is None:
            return
        # real_sender_id 的名称索引会错配；私聊以会话 ID 为准，2 才是自己。
        if not sender_id and (not sender or sender == self.self_id):
            return
        content = ('[图片]' if media['kind']=='image' else '[视频]') if media else other or clean_text(message.get("content"))
        if not content:
            return
        try:
            stamp = datetime.fromtimestamp(int(message.get("create_time") or 0)).strftime("%H:%M:%S")
        except (ValueError, OSError, OverflowError):
            stamp = "时间未知"
        self._print(f"[{stamp}] {self.get_display_name(username)}：{content}")
        self.received += 1
        self._active_until[username] = self.clock() + ACTIVE_RECHECK_SECONDS
        event_id = hashlib.sha256(repr((username, self._seq(message), self._key(message))).encode("utf-8")).hexdigest()
        self.queue_message(username, content, event_id=event_id,
                           created_at=int(message.get("create_time") or 0), seq=self._seq(message),media=media)

    def _read_updates(self, username):
        """重叠一个 sort_seq 并去重，覆盖同秒到达和重复序号的消息。"""
        limit = PAGE_SIZE
        while True:
            previous = self.watermarks[username]
            messages = self.db.get_new_messages(username, max(0, previous - 1), limit=limit)
            for message in sorted(messages, key=self._seq):
                seq = self._seq(message)
                current = self.watermarks[username]
                key = self._key(message)
                if seq < current or (seq == current and key in self._boundary_seen[username]):
                    continue
                if seq > current:
                    self.watermarks[username] = seq
                    self._boundary_seen[username] = set()
                self._boundary_seen[username].add(key)
                self._accept_message(username, message)
            if len(messages) < limit:
                return
            # 达到批次上限且整批同序号时扩大窗口，否则下一批从新水位继续。
            if self.watermarks[username] == previous:
                limit *= 2
            else:
                limit = PAGE_SIZE

    def poll_once(self):
        if not self._initialized:
            self.initialize()
        sessions = self.db.get_sessions(limit=-1)
        now = self.clock()
        # 先处理变化的会话；安静会话按最久未检查的顺序分批核对。
        idle_candidates = sorted((
            str(s.get("username") or "") for s in sessions
            if self._private(str(s.get("username") or ""))
            and now - self._last_checked.get(str(s.get("username") or ""), -IDLE_RECHECK_SECONDS) >= IDLE_RECHECK_SECONDS
        ), key=lambda name: self._last_checked.get(name, float("-inf")))
        idle_checks = set(idle_candidates[:MAX_IDLE_CHECKS])
        for session in sessions:
            username = str(session.get("username") or "")
            if not self._private(username):
                continue
            fingerprint = self._session_key(session)
            changed = self._session_fingerprints.get(username) != fingerprint
            if (username in self.watermarks and not changed
                    and username not in self._read_retry
                    and self._active_until.get(username, 0) <= now
                    and username not in idle_checks):
                continue
            try:
                if username not in self.watermarks:
                    self._bootstrap(username, initial=False)
                    self._baseline_retry.discard(username)
                # 不以秒级 last_time 为读取条件：同秒多条消息和失败重试都必须处理。
                self._read_updates(username)
                self._session_fingerprints[username] = fingerprint
                self._last_checked[username] = self.clock()
                self._read_retry.discard(username)
            except Exception as exc:
                self._read_retry.add(username)
                self.errors += 1
                self._print(f"读取 {username} 新消息失败，下轮重试：{exc}")
        self.flush_due()
        if self.clock() - self._last_heartbeat >= 15:
            self._last_heartbeat = self.clock()
            with self._lock:
                pending, active = len(self._pending), len(self._active)
            self._print(f"监听中 | 私聊 {len(self.watermarks)} | 新消息 {self.received} | "
                        f"待合并 {pending} | 正在处理 {active} | 已发送 {self.sent} | 错误 {self.errors}")

    def queue_message(self, username, content, event_id=None, created_at=None, seq=0,media=None):
        """加入独立联系人缓冲；每条新消息重新计算该联系人的截止时间。"""
        content = clean_text(content)
        if not content or not self._private(username):
            return
        with self._lock:
            if self._closed:
                raise RuntimeError("监听器已关闭")
            if event_id:
                batches=list(self._batches.get(username,()))
                if username in self._inflight_batches:batches.append(self._inflight_batches[username])
                if any(event_id in batch.event_ids for batch in batches) or any(event_id==event[0] for event in self._pending_events.get(username,())):return
            messages, _ = self._pending.get(username, ([], 0))
            now=self.clock()
            if not messages:
                self._order_counter+=1
                self._pending_order[username]=(float(created_at) if created_at is not None else self.wall_clock(),int(seq),self._order_counter)
                self._pending_started[username]=now
            messages.append(content)
            deadline=now+self.merge_seconds
            if self.serial_replies:deadline=min(deadline,self._pending_started[username]+max(10,self.merge_seconds))
            self._pending[username] = (messages, deadline)
            eligible = bool(event_id and created_at is not None and created_at >= self.auto_send_since)
            self._pending_events[username].append((event_id, eligible, seq))
            if media is not None:self._pending_media[username].append(dict(media))
            self._save_queue_locked()

    def flush_due(self, force=False):
        with self._lock:
            if not self.dispatch_allowed():return
            now = self.clock()
            for username, (messages, deadline) in list(self._pending.items()):
                if not force and deadline > now:
                    continue
                self._pending.pop(username)
                events = self._pending_events.pop(username, [])
                ids = tuple(e[0] for e in events) if events and all(e[1] for e in events) else ()
                batch = ReplyBatch("\n".join(messages), ids, max((e[2] for e in events), default=0),tuple(self._pending_media.pop(username,[])),self._pending_order.pop(username))
                self._pending_started.pop(username,None)
                if self.serial_replies and self._batches[username]:
                    # 尚未开始生成的同人批次合并；保持原排队位置和全部新消息凭证。
                    previous=list(self._batches[username]);self._batches[username].clear()
                    batch=ReplyBatch('\n'.join([b.text for b in previous]+[batch.text]),
                        tuple(e for b in previous+[batch] for e in b.event_ids) if all(b.event_ids for b in previous+[batch]) else (),
                        max(b.incoming_seq for b in previous+[batch]),tuple(m for b in previous+[batch] for m in b.media),min(b.order for b in previous+[batch]))
                self._batches[username].append(batch)
                if not self.serial_replies and username not in self._active:
                    self._active.add(username)
                    self._executor.submit(self._drain_contact, username)
            self._save_queue_locked()
            if self.serial_replies and not self._serial_running and self._next_serial_locked() is not None:
                self._serial_running=True
                self._executor.submit(self._drain_serial)

    def _next_serial_locked(self):
        if self._queue_journal is not None and self._stop.is_set():return None
        if not self.dispatch_allowed():return None
        if self._model_resume_at>self.wall_clock():return None
        if self._model_resume_at:
            self._model_resume_at=0;self._model_pause_reason=None
            self._save_queue_locked()
        ready=[(batches[0].order,user) for user,batches in self._batches.items() if batches]
        if not ready:return None
        order,user=min(ready)
        # 较早联系人仍在合并时等待；同人排队后又来消息时，先合并再生成。
        if user in self._pending or any(pending<order for pending in self._pending_order.values()):return None
        return user

    def _drain_serial(self):
        try:
            while True:
                with self._lock:
                    user=self._next_serial_locked()
                    if user is None:return
                    self._active.add(user)
                try:self._drain_contact(user,once=True)
                finally:
                    with self._lock:
                        self._active.discard(user)
                        if not self._batches[user]:self._batches.pop(user,None)
                        self._idle.notify_all()
        except Exception as exc:
            # 调度本身异常也必须释放运行标志，下一轮可继续剩余联系人。
            self.errors+=1
            self.last_error={'at':time.time(),'contact':None,'message':'串行调度异常：'+str(exc)}
            self._print(self.last_error['message'])
        finally:
            with self._lock:
                self._serial_running=False
                self._idle.notify_all()

    def queue_status(self):
        with self._lock:
            value={'mode':'global_fifo' if self.serial_replies else 'per_contact_parallel',
                'maxConcurrentReplies':1 if self.serial_replies else self._executor._max_workers,
                'waitingBatches':sum(len(b) for b in self._batches.values()),'mergingContacts':len(self._pending),'active':len(self._active),'dispatchPaused':not self.dispatch_allowed()}
            if self._queue_journal is not None:
                value.update(persisted=True,quotaPaused=self._model_resume_at>self.wall_clock(),quotaResumeAt=self._model_resume_at or None,quotaReason=self._model_pause_reason)
            return value

    def _get_bridge(self):
        with self._bridge_lock:
            if self._bridge is not None and getattr(self._bridge, "needs_reconnect", False):
                self._print("Codex 连接失效，正在自动重建连接并恢复联系人会话……")
                old, self._bridge = self._bridge, None
                old.close()
            if self._bridge is None:
                factory = self.bridge_factory
                if factory is None:
                    from codex_bridge import CodexBridge
                    factory = CodexBridge
                self.bridge_cwd.mkdir(parents=True, exist_ok=True)
                self._bridge = factory(state_path=self.state_path,
                                       cwd=self.bridge_cwd, model=self.model)
            return self._bridge

    def _drain_contact(self, username,once=False):
        # 每联系人只允许一个工作线程，后续批次按先后顺序等待。
        while True:
            with self._lock:
                if self._queue_journal is not None and self._stop.is_set():return
                if not self._batches[username]:
                    self._batches.pop(username, None)
                    self._active.discard(username)
                    self._idle.notify_all()
                    return
                batch = self._batches[username].popleft()
                self._inflight_batches[username]=batch
                self._save_queue_locked()
                incoming = batch.text
            generating=False
            try:
                name = self.get_display_name(username)
                check_batch=getattr(self.auto_sender,'batch_skip_reason',None)
                skip=check_batch(username,batch.event_ids,batch.incoming_seq) if check_batch and batch.event_ids else None
                if skip:
                    self._print(f'{name}：{skip}')
                    if once:return
                    continue
                preflight=getattr(self.auto_sender,'preflight',None)
                preflight_batch=getattr(self.auto_sender,'preflight_batch',None)
                if batch.event_ids:
                    if preflight_batch is not None:preflight_batch(username,batch.event_ids,batch.incoming_seq)
                    elif preflight is not None:preflight(username)
                plan=self.counter_policy.plan(username,incoming) if self.counter_policy is not None and not batch.media else None
                local_clock=clock_reply(incoming) if not plan and not batch.media else None
                reply=plan['text'] if plan and plan['route']=='local' else (local_clock or (local_rule_reply(incoming) if self.local_rules and not batch.media else None))
                source=f"本地反击（等级{plan['level']}，0 Codex）" if plan and plan['route']=='local' else "本地规则（0 Codex）"
                if local_clock is not None:source='本机时钟（0 Codex）'
                if reply is None:
                    severe=bool(plan and plan['route']=='codex')
                    lulu=bool(self.auto_sender is not None and hasattr(self.auto_sender,'greetings'))
                    label='鹭鹭 / gpt-6.1-sol / medium；首次介绍本地' if lulu else 'Plus / low'
                    self._print(f"正在为 {name} 生成{'严重辱骂反击' if severe else '回复'}（{label}）……")
                    try:
                        metadata={'counter_context':plan['context']} if severe else {}
                        if batch.media:metadata['media_messages']=list(batch.media)
                        generating=True
                        reply = self._get_bridge().reply(username, incoming, display_name=name,**metadata)
                        generating=False
                    except Exception as error:
                        if not severe:raise
                        self._print(f"{name}：反击生成未完成，使用本地短句：{error}")
                        reply=None
                    source = "Codex 反击（等级3 / low）" if severe else ('本地首次介绍（0 Codex）' if getattr(reply,'day',None) else '鹭鹭 gpt-6.1-sol（medium）' if lulu else "Codex Plus（low）")
                    if severe and (not isinstance(reply,str) or not reply.strip()):
                        reply=plan['fallback'];source='本地反击保底（等级3）'
                delivery = "仅打印"
                if self._queue_journal is not None and self._stop.is_set():
                    with self._lock:self._batches[username].appendleft(batch)
                    self._print(f'{name}：停止开关已开启，保留原批次待人工恢复')
                    return
                if not isinstance(reply,str) or not reply.strip():
                    self._print(f"{name}：本次留给主人处理；不自动重试")
                    if once:return
                    continue
                self._print(f"\n----------- 拟回复 -----------\n回复对象：{name}\n"
                            f"合并消息：{incoming}\n拟回复：{reply}\n来源：{source}\n"
                            "------------------------------")
                self.replies += 1
                if self.auto_sender is not None and batch.event_ids and not self._stop.is_set():
                    delivery = self.auto_sender.deliver(username, reply, batch.event_ids, batch.incoming_seq)
                    if delivery == "已发送并确认":
                        self.sent += 1
                    self._print(f"{name}：{delivery}")
                record = ReplyRecord(username, name, incoming, reply, source, delivery)
                if self.on_reply is not None:
                    self.on_reply(record)
            except Exception as exc:
                self.errors += 1
                self.last_error = {'at':time.time(),'contact':username,'message':str(exc)}
                from reply_queue_journal import quota_error,quota_resume_at
                if generating and quota_error(exc):
                    with self._lock:
                        waiting=[batch]+list(self._batches[username])
                        self._batches[username].clear()
                        merged=ReplyBatch('\n'.join(b.text for b in waiting),
                            tuple(event for b in waiting for event in b.event_ids) if all(b.event_ids for b in waiting) else (),
                            max(b.incoming_seq for b in waiting),tuple(media for b in waiting for media in b.media),min(b.order for b in waiting))
                        self._batches[username].append(merged)
                        self._model_resume_at=quota_resume_at(exc,self.wall_clock())
                        self._model_pause_reason='Codex额度不足；原批次及后续来信保存在本地，恢复时间后自动接续'
                    self._print(f'{username}：额度不足，已保留待回复批次；恢复复核时间 {datetime.fromtimestamp(self._model_resume_at).isoformat()}')
                else:
                    self._print(f"为 {username} 生成拟回复失败：{exc}\n"
                                f"本次未回复消息：{incoming}")
            finally:
                with self._lock:
                    self._inflight_batches.pop(username,None)
                    self._save_queue_locked()
            if once:return

    def wait_idle(self, timeout=180):
        """等待已提交的拟回复完成；尚未到合并时间的缓冲不在等待范围。"""
        deadline = time.monotonic() + timeout
        with self._idle:
            while self._active or (self.serial_replies and (self._serial_running or any(self._batches.values()))):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._idle.wait(remaining)
        return True

    def _schedule(self):
        while not self._stop.wait(0.1):
            self.flush_due()

    def run(self, run_seconds=None):
        self.initialize()
        self._scheduler = threading.Thread(target=self._schedule, name="wechat-merge", daemon=True)
        self._scheduler.start()
        deadline = None if run_seconds is None else self.clock() + run_seconds
        try:
            while not self._stop.is_set():
                if deadline is not None and self.clock() >= deadline:
                    break
                try:
                    self.poll_once()
                except Exception as exc:
                    self.errors += 1
                    self._print(f"扫描会话失败，下轮重试：{exc}")
                delay = self.poll_interval
                if deadline is not None:
                    delay = min(delay, max(0, deadline - self.clock()))
                self._stop.wait(delay)
        finally:
            self.close()

    def close(self):
        if self._closed:
            return
        self._stop.set()
        if self._scheduler is not None and self._scheduler is not threading.current_thread():
            self._scheduler.join(timeout=2)
        # 停止时完成已经收集的消息，避免退出前最后一批无提示地丢失。
        self.flush_due(force=True)
        with self._lock:
            self._closed = True
        self._executor.shutdown(wait=True)
        if self._bridge is not None:
            self._bridge.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-seconds", type=float, help="限时监听，用于本地验证")
    parser.add_argument("--merge-seconds", type=float, default=MERGE_SECONDS)
    parser.add_argument("--poll-interval", type=float, default=POLL_INTERVAL)
    parser.add_argument("--state-dir", type=Path, help="联系人线程映射存放目录")
    parser.add_argument("--model", default=None, help="覆盖 Codex 默认模型，推理仍使用 low")
    parser.add_argument("--print-only", action="store_true", help="临时关闭授权联系人的自动发送")
    parser.add_argument("--auto-reply-config", type=Path,
                        default=Path(__file__).resolve().parent / "wechat_autoreply.json")
    args = parser.parse_args(argv)
    if args.run_seconds is not None and args.run_seconds <= 0:
        parser.error("--run-seconds 必须大于 0")
    from wechatauto import WeChatDB
    from wechat_sender import AutoReplySender, ListenerInstanceLock, load_auto_reply_config
    state_root = args.state_dir or Path(__file__).resolve().parent / ".wechat_codex"
    instance = ListenerInstanceLock(state_root / "listener.lock")
    listener = None
    try:
        instance.acquire()
        print("正在初始化微信数据库……", flush=True)
        db = WeChatDB()
        allowed = {} if args.print_only else load_auto_reply_config(args.auto_reply_config, db)
        account = hashlib.sha256(db.get_self_info()["username"].encode("utf-8")).hexdigest()[:20]
        auto_sender = AutoReplySender(db, allowed, state_root / account / "deliveries.sqlite3") if allowed else None
        listener = PrivateChatListener(
            db, state_root=state_root, model=args.model, auto_sender=auto_sender,
            merge_seconds=args.merge_seconds, poll_interval=args.poll_interval,
            output=lambda text: print(text, flush=True),
        )
        listener.run(run_seconds=args.run_seconds)
    except KeyboardInterrupt:
        print("\n正在停止……", flush=True)
    except RuntimeError as exc:
        print(f"监听未启动或已停止：{exc}", flush=True)
        return 2
    finally:
        if listener is not None:
            listener.close()
        instance.close()
    print(f"已停止；本次拟回复 {listener.replies} 条，已发送 {listener.sent} 条，错误 {listener.errors} 次", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
