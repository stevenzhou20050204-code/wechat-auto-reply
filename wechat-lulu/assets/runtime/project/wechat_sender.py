"""只向用户明确授权并绑定的微信账号发送新消息的回复。"""

import json
from contextlib import contextmanager
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import threading
import time
import uuid


AUTHORIZED_NAMES = {"咖喱", "联系人甲", "联系人丙"}

class KnownNotSent(RuntimeError):
    """发送子进程明确证明尚未预约点击；保留旧指纹，不冒充未知结果。"""
    def __init__(self,message,phase=None,request_id=None):
        super().__init__(message);self.phase=phase;self.request_id=request_id


class ListenerInstanceLock:
    """防止多个监听进程同时回复；进程退出后操作系统自动释放锁。"""

    def __init__(self, path):
        self.path = Path(path)
        self.handle = None

    def acquire(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open("a+b")
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            handle.close()
            raise RuntimeError("已有微信监听正在运行，不能重复启动自动回复。") from exc
        self.handle = handle
        return self

    def close(self):
        if self.handle is not None:
            self.handle.close()
            self.handle = None


def load_auto_reply_config(path, db):
    """配置绑定实际微信账号 ID，禁止用包含匹配扩大发送范围。"""
    path = Path(path)
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if data.get("version") not in (1,2):
        raise RuntimeError("自动回复配置版本不正确。")
    if data.get("enabled") is not True:
        return {}
    if data.get("account_username") != db.get_self_info().get("username"):
        raise RuntimeError("当前微信账号与自动回复授权配置不一致，已停止自动发送。")
    if data.get('version')==2:
        if data.get('scope')!='all_private_contacts' or data.get('assistant_name')!='鹭鹭':raise RuntimeError('鹭鹭监听配置不正确')
        from lulu_policy import ContactDirectory
        return ContactDirectory(db,exclusions=data.get('contact_exclusions'),config_path=path)
    contacts = data.get("contacts")
    if not isinstance(contacts, dict) or set(contacts) != AUTHORIZED_NAMES:
        raise RuntimeError("自动回复名单必须与用户授权的三个联系人一致。")
    allowed = {}
    for label, username in contacts.items():
        if (not isinstance(username, str) or not username or "@chatroom" in username
                or username == data["account_username"] or username.startswith("gh_")):
            raise RuntimeError("自动回复名单包含无效联系人。")
        exact = [c for c in db.search_contact(username) if c.get("username") == username]
        if len(exact) != 1:
            raise RuntimeError(f"无法唯一确认授权联系人：{label}")
        allowed[username] = label
    if len(allowed) != len(contacts):
        raise RuntimeError("授权名称必须分别对应独立微信账号。")
    return allowed


class VerifiedWeChatSender:
    """使用项目 UIA 发送接口，严格确认聊天名称后回读目标账号数据库。"""

    def __init__(self, db,contact_guard=None,progress_hook=None):
        self.db = db;self.contact_guard=contact_guard;self.progress_hook=progress_hook

    def __call__(self, username, text):
        import uiautomation as auto
        # 回复工作线程需要自己的 COM 环境，不跨线程复用控件对象。
        with auto.UIAutomationInitializerInThread():
            return self._send(username, text)

    def _send(self, username, text, **metadata):
        from passive_sender import PassiveSender
        sender=PassiveSender(self.db,contact_guard=self.contact_guard,progress_hook=self.progress_hook)
        try:return sender(username,text,**metadata)
        finally:self.last_progress=dict(sender.progress)

    def _legacy_uia_send_disabled(self, username, text):
        hits = [c for c in self.db.search_contact(username) if c.get("username") == username]
        if len(hits) != 1 or "@chatroom" in username:
            raise RuntimeError("发送目标不是唯一的私聊联系人。")
        contact = hits[0]
        display = contact.get("remark") or contact.get("nick_name")
        if not display:
            raise RuntimeError("授权联系人没有可核对的显示名称。")
        duplicates = [c for c in self.db.search_contact(display)
                      if (c.get("remark") or c.get("nick_name")) == display]
        if len(duplicates) != 1 or duplicates[0]["username"] != username:
            raise RuntimeError("微信存在同名显示对象，停止发送以避免误发。")
        before = self.db.get_messages(username, limit=20)
        if not before:
            raise RuntimeError("无法读取目标会话发送前水位。")
        maximum = max(int(m.get("sort_seq") or 0) for m in before)
        seen = {(int(m.get("sort_seq") or 0), int(m.get("local_id") or 0)) for m in before}
        from wechatauto import WeChatGUI
        gui = WeChatGUI()
        gui._cached_db = self.db
        uia = gui._get_uia()
        if uia is None:
            raise RuntimeError("微信 UIA 不可用；保留草稿，等待界面恢复后处理下一条新消息。")
        if uia.current_chat() != display:
            if not uia.open_chat(username, section="联系人"):
                raise RuntimeError(f"无法打开授权联系人：{display}")
        if uia.current_chat() != display:
            raise RuntimeError("当前聊天与授权目标不一致，已停止发送。")
        if not uia.send_text(text):
            raise RuntimeError("发送结果未确认；不会自动重试这一批消息。")
        deadline = time.monotonic() + 12
        while time.monotonic() < deadline:
            for message in self.db.get_new_messages(username, max(0, maximum - 1), limit=200):
                identity = (int(message.get("sort_seq") or 0), int(message.get("local_id") or 0))
                if identity in seen:
                    continue
                if str(message.get("sender_id")) == "2" and message.get("content") == text:
                    return True
            time.sleep(0.5)
        raise RuntimeError("已操作发送，但目标数据库尚未确认；不会重复发送。")


class BoundedWeChatSender:
    """把可能卡住的界面调用放进独立进程，超时结束且不重发。"""

    def __init__(self, db, timeout=45, runner=None):
        self.account_id = db.get_self_info()["username"]
        self.timeout = timeout
        self.runner = runner or subprocess.run

    def __call__(self, username, text):
        return self.send_event(username,text)

    def send_event(self, username, text, event_ids=(), incoming_seq=None, adopt_draft=False):
        request_id = uuid.uuid4().hex
        payload = {"request_id": request_id, "account": self.account_id,
                   "username": username, "text": text, "event_ids":list(event_ids),
                   "expected_seq":incoming_seq,"adopt_draft":bool(adopt_draft)}
        if getattr(text,'image_path',None):payload['image_path']=text.image_path
        # 只有子进程明确证实仍停在已核对草稿、从未预约点击时才接续一次。
        for attempt in range(2):
            try:response=self._run_once(payload)
            except KnownNotSent:
                # 请求级落盘证明尚未预约点击时，只接续一次；未知结果绝不进入此分支。
                if attempt==0:
                    time.sleep(.3);continue
                raise
            if response.get('ok') is True:return True
            if attempt==0 and response.get('not_sent_proven') is True:
                time.sleep(.3);continue
            error=response.get('error') or '微信发送未确认。'
            if response.get('not_sent_proven') is True:raise KnownNotSent(error,response.get('phase'),request_id)
            raise RuntimeError(error)

    def _run_once(self,payload):
        try:
            result = self.runner(
                [sys.executable, "-u", str(Path(__file__).resolve()), "--send-once"],
                input=json.dumps(payload, ensure_ascii=False), text=True,
                encoding="utf-8", errors="replace", stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, timeout=self.timeout,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except subprocess.TimeoutExpired as exc:
            from send_progress import proven_preclick
            phase=proven_preclick(payload)
            if phase:raise KnownNotSent('微信界面操作超时；请求进度证明尚未预约点击，旧事件保留为未发送。',phase,payload['request_id']) from exc
            raise RuntimeError("微信界面操作超时；已结束发送进程，该批消息不会重发。") from exc
        prefix = "WECHAT_SEND_RESULT "
        for line in reversed(result.stdout.splitlines()):
            if not line.startswith(prefix):
                continue
            response = json.loads(line[len(prefix):])
            if response.get("request_id") != payload['request_id']:
                break
            if result.returncode!=0 and response.get('ok') is True:break
            return response
        raise RuntimeError("微信发送进程未返回确认；该批消息不会重发。")


class AutoReplySender:
    """名单、账号、新消息凭证与发送记录共同限制自动发送。"""

    def __init__(self, db, allowed_contacts, ledger_path, send=None, stop_path=None):
        self.db = db
        self.account_id = db.get_self_info()["username"]
        self.allowed = allowed_contacts if getattr(allowed_contacts,'dynamic',False) else dict(allowed_contacts)
        self.stop_path=Path(stop_path) if stop_path is not None else Path(__file__).parent/'.wechat_codex'/'STOP'
        self.path = Path(ledger_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.send_action = send or BoundedWeChatSender(db)
        self.lock = threading.Lock()
        with self._database() as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS deliveries (event_id TEXT PRIMARY KEY, contact_id TEXT NOT NULL, status TEXT NOT NULL, created REAL NOT NULL)")

    @contextmanager
    def _database(self):
        conn = sqlite3.connect(self.path)
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def deliver(self, username, text, event_ids, incoming_seq):
        if getattr(self.allowed,'dynamic',False):self.allowed.refresh()
        if username not in self.allowed or not event_ids or "@chatroom" in username:
            return "仅打印"
        if not isinstance(text, str) or not text.strip() or len(text) > 1000:
            return "未发送：草稿正文无效或过长"
        with self.lock:
            if self.stop_path.exists():
                raise RuntimeError('手动停止开关已开启，未创建发送预约')
            if self.db.get_self_info().get("username") != self.account_id:
                raise RuntimeError("微信账号发生变化，停止自动发送。")
            # 主人已经手动回复时，不再追加上一批生成的草稿。
            recent = self.db.get_messages(username, limit=20)
            if any(str(m.get("sender_id")) == "2" and int(m.get("sort_seq") or 0) > incoming_seq for m in recent):
                return "未发送：主人已回复"
            if any(int(m.get("sort_seq") or 0)>incoming_seq for m in recent):
                return "未发送：有更新消息，等待下一批"
            with self._database() as conn:
                conn.execute("BEGIN IMMEDIATE")
                for event_id in event_ids:
                    if conn.execute("SELECT 1 FROM deliveries WHERE event_id=?", (event_id,)).fetchone():
                        return "未发送：该消息已有发送记录"
                conn.executemany("INSERT INTO deliveries VALUES (?, ?, 'attempting', ?)",
                                 [(event_id, username, time.time()) for event_id in event_ids])
            # 先落盘再执行；失败或进程中断后的相同消息不会再次发送。
            try:
                send_event=getattr(self.send_action,'send_event',None)
                success=send_event(username,text,event_ids=event_ids,incoming_seq=incoming_seq) if send_event else self.send_action(username,text)
                if success is not True:
                    raise RuntimeError("目标会话没有确认发送成功。")
            except Exception as error:
                with self._database() as conn:
                    state='not_sent' if isinstance(error,KnownNotSent) else 'unconfirmed'
                    conn.executemany("UPDATE deliveries SET status=? WHERE event_id=?", [(state,i) for i in event_ids])
                raise
            with self._database() as conn:
                conn.executemany("UPDATE deliveries SET status='sent' WHERE event_id=?", [(i,) for i in event_ids])
            return "已发送并确认"

    def batch_skip_reason(self,username,event_ids,incoming_seq):
        """持久队列恢复后先检查旧预约和主人回复，避免重新调用模型或重复发送。"""
        if getattr(self.allowed,'dynamic',False):self.allowed.refresh()
        if username not in self.allowed:return '未处理：联系人已排除'
        if self.db.get_self_info().get('username')!=self.account_id:raise RuntimeError('微信账号发生变化')
        with self._database() as conn:
            if any(conn.execute('SELECT 1 FROM deliveries WHERE event_id=?',(event,)).fetchone() for event in event_ids):
                return '未处理：原事件已有账本记录，保留记录且不重试'
        recent=self.db.get_messages(username,limit=20)
        if any(str(m.get('sender_id'))=='2' and int(m.get('sort_seq') or 0)>incoming_seq for m in recent):return '未处理：主人已回复'
        if any(int(m.get('sort_seq') or 0)>incoming_seq for m in recent):return '未处理：有更新来信，等待对应新批次'
        return None

    def preflight(self,username):
        return self.preflight_batch(username,(),None)

    def preflight_batch(self,username,event_ids,incoming_seq):
        """生成前按真实新批次处理旧预约，同一事件始终保持禁止重试。"""
        if getattr(self.allowed,'dynamic',False):self.allowed.refresh()
        if username not in self.allowed:raise RuntimeError('联系人不在自动回复名单')
        if self.db.get_self_info().get('username')!=self.account_id:raise RuntimeError('微信账号发生变化')
        if event_ids and incoming_seq is not None:
            reason=self.batch_skip_reason(username,event_ids,incoming_seq)
            if reason:raise RuntimeError(reason)
        from passive_sender import PassiveSender
        with self.lock:
            record=PassiveSender(self.db).supersede_pending(username,event_ids,incoming_seq)
            if record.get('phase') in ('send_reserved','clicked'):raise RuntimeError('上次发送尚未确认；保留记录，禁止重发')
            return record


def send_once_worker():
    """发送子进程再次验证名单和账号，结果仅通过固定格式返回。"""
    payload = {};sender=None;request_progress=None;trace=None
    try:
        # 管道内容固定为 UTF-8，不依赖启动它的 Windows 终端代码页。
        if hasattr(sys.stdin,'reconfigure'):sys.stdin.reconfigure(encoding='utf-8',errors='strict')
        payload = json.loads(sys.stdin.read())
        from send_progress import RequestProgress
        request_progress=RequestProgress(payload);request_progress.mark('started')
        import faulthandler
        trace=request_progress.path.with_suffix('.trace.log').open('w',encoding='utf-8')
        faulthandler.dump_traceback_later(20,repeat=True,file=trace)
        from ui_permission import ensure_ui_allowed
        ensure_ui_allowed()
        from wechatauto import WeChatDB
        from stable_db import make_db
        db = make_db()
        request_progress.mark('db_ready')
        allowed = load_auto_reply_config(Path(__file__).resolve().parent / "wechat_autoreply.json", db)
        request_progress.mark('contacts_ready')
        def check_contact(user):
            if getattr(allowed,'dynamic',False):allowed.refresh()
            if user not in allowed:raise RuntimeError('联系人已被排除，停止发送')
            from topic_policy import latest_incoming_blocked
            if latest_incoming_blocked(db,user):raise RuntimeError('政治敏感话题按用户设置静默')
        if (payload.get("account") != db.get_self_info().get("username")
                or payload.get("username") not in allowed):
            raise RuntimeError("发送子进程账号或授权联系人不匹配。")
        text = payload.get("text")
        if not isinstance(text, str) or not text.strip() or len(text) > 1000:
            raise RuntimeError("发送正文无效。")
        sender=VerifiedWeChatSender(db,contact_guard=check_contact,progress_hook=request_progress.mark)
        metadata={key:payload[key] for key in ('expected_seq','event_ids','adopt_draft') if key in payload}
        config=json.loads((Path(__file__).parent/'wechat_autoreply.json').read_text(encoding='utf-8-sig'))
        metadata['replace_unsent_drafts']=config.get('version')==2 and config.get('replace_unsent_drafts') is True
        request_progress.mark('sender_ready')
        if payload.get('image_path'):
            from passive_sender import PassiveSender
            image_path=Path(payload['image_path']).resolve();allowed_root=Path(__file__).parent/'.wechat_codex'/'lulu'
            if not image_path.is_relative_to(allowed_root.resolve()):raise RuntimeError('只允许发送鹭鹭生成目录内的图片')
            sender=PassiveSender(db,contact_guard=check_contact,progress_hook=request_progress.mark)
            success=sender.send_image(payload['username'],image_path,expected_seq=payload.get('expected_seq'),event_ids=payload.get('event_ids',()),replace_unsent_drafts=metadata['replace_unsent_drafts'])
        else:
            success = sender._send(payload["username"], text,**metadata)
        if success is not True:
            raise RuntimeError("发送没有获得目标数据库确认。")
        response, status = {"ok": True}, 0
    except Exception as exc:
        response, status = {"ok": False, "error": str(exc)}, 2
        progress=getattr(sender,'last_progress',getattr(sender,'progress',{}))
        response['phase']=progress.get('phase') or (request_progress.record.get('phase') if request_progress else None)
        from send_progress import proven_preclick
        response['not_sent_proven']=bool(request_progress and proven_preclick(payload))
        response['resume_safe']=(progress.get('phase')=='draft_verified' and '发送按钮' in str(exc))
    response["request_id"] = payload.get("request_id")
    print("WECHAT_SEND_RESULT " + json.dumps(response, ensure_ascii=False), flush=True)
    if trace is not None:
        faulthandler.cancel_dump_traceback_later();trace.close()
    return status


if __name__ == "__main__":
    if "--send-once" in sys.argv:
        sys.stdout.reconfigure(encoding="utf-8")
        raise SystemExit(send_once_worker())
    raise SystemExit("请使用 wxlisten 启动监听。")
