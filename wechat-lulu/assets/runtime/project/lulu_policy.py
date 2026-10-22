"""鹭鹭的私聊范围、每日招呼及给主人留言的持久化规则。"""
import json,sqlite3,time,threading,hashlib,re
from contextlib import contextmanager
from pathlib import Path
from datetime import datetime
from clock_rules import CHINA

GREETING='你好，我是小助手鹭鹭，主人现在不在，有重要消息我会转告他哒'
SERVICE_IDS={'filehelper','weixin','newsapp','fmessage','medianote','floatbottle','qmessage','qqmail','tmessage','notification_messages','weixinreminder','officialaccounts','weixinfile'}

def private_contact(user,self_id):
    return bool(user and user!=self_id and ('@' not in user or user.endswith('@openim')) and not user.startswith(('gh_','weixin_')) and user not in SERVICE_IDS)

def contact_exclusions(value=None):
    """排除条件只接受明确列表，损坏配置不能退回到全员允许。"""
    if value is None:value={}
    if not isinstance(value,dict):raise RuntimeError('联系人排除配置必须为对象')
    result={}
    for key in ('labels','nicknames','contact_ids'):
        entries=value.get(key,[])
        if not isinstance(entries,list) or any(not isinstance(item,str) or not item.strip() for item in entries):raise RuntimeError('联系人排除条件必须为非空字符串列表：'+key)
        result[key]={item.strip() for item in entries}
    return result

def contact_label_ids(buffer):
    """读取已由通讯录界面核对的标签字段30，按完整ID比较，不能子串匹配。"""
    data=bytes(buffer or b'');position=0;labels=[]
    def varint():
        nonlocal position
        value=0
        for shift in range(0,70,7):
            if position>=len(data):raise RuntimeError('联系人附加字段被截断')
            byte=data[position];position+=1;value|=(byte&127)<<shift
            if not byte&128:return value
        raise RuntimeError('联系人附加字段整数格式错误')
    while position<len(data):
        key=varint();number,wire=key>>3,key&7
        if number==0:raise RuntimeError('联系人附加字段编号无效')
        if number==30 and wire!=2:raise RuntimeError('联系人标签字段编码发生变化')
        if wire==0:varint();continue
        if wire==1:length=8
        elif wire==5:length=4
        elif wire==2:length=varint()
        else:raise RuntimeError('联系人附加字段编码发生变化')
        end=position+length
        if end>len(data):raise RuntimeError('联系人附加字段长度无效')
        if number==30:
            if wire!=2 or labels:raise RuntimeError('联系人标签字段不唯一')
            try:value=data[position:end].decode('ascii')
            except UnicodeDecodeError as error:raise RuntimeError('联系人标签ID编码无效') from error
            if value and not re.fullmatch(r'[0-9]+(?:,[0-9]+)*',value):raise RuntimeError('联系人标签ID列表格式无效')
            labels.append(value)
        position=end
    return {int(value) for value in labels[0].split(',')} if labels and labels[0] else set()

def contact_roster(db,exclusions=None):
    """取通讯录好友标志，排除数据库中仅因同群出现的陌生人及服务号。"""
    from stable_db import cache_gate
    with cache_gate():
        conn=db._contact_conn()
        if conn is None:raise RuntimeError('无法读取当前账号的通讯录')
        try:
            fields={row[1] for row in conn.execute('PRAGMA table_info(contact)')}
            required={'username','remark','nick_name','local_type','flag','delete_flag','verify_flag'}
            if not required<=fields:raise RuntimeError('通讯录分类字段发生变化，停止扩展回复名单')
            policy=contact_exclusions(exclusions)
            blocked_labels=set()
            if policy['labels']:
                if 'extra_buffer' not in fields:raise RuntimeError('通讯录缺少标签成员字段，停止扩展回复名单')
                try:
                    labels=conn.execute('SELECT label_id_,label_name_ FROM contact_label').fetchall()
                except sqlite3.DatabaseError as error:raise RuntimeError('无法读取联系人标签，停止扩展回复名单') from error
                blocked_labels={int(row[0]) for row in labels if row[1] in policy['labels']}
            column=',extra_buffer' if policy['labels'] else ''
            rows=conn.execute('SELECT username,remark,nick_name'+column+' FROM contact WHERE local_type IN (1,5,6) AND (flag & 1)=1 AND delete_flag=0 AND verify_flag=0').fetchall()
            result={}
            for row in rows:
                if row[0] in policy['contact_ids'] or row[2] in policy['nicknames']:continue
                if policy['labels'] and contact_label_ids(row[3])&blocked_labels:continue
                result[row[0]]=row[1] or row[2] or row[0]
            return result
        finally:conn.close()

def shanghai_day(now=None):
    return datetime.fromtimestamp(time.time() if now is None else now,CHINA).date().isoformat()

class ContactDirectory(dict):
    """名单只来自当前账号的联系人表；改名和新联系人定期刷新。"""
    dynamic=True
    def __init__(self,db,clock=time.monotonic,exclusions=None,config_path=None):
        super().__init__();self.db=db;self.self_id=db.get_self_info()['username'];self.clock=clock;self.refreshed=-1e9;self.lock=threading.RLock();self.exclusions=exclusions;self.config_path=Path(config_path) if config_path else None;self.config_revision=None;self.refresh()
    def _revision(self):
        if self.config_path is None:return None
        stat=self.config_path.stat();return stat.st_mtime_ns,stat.st_size
    def refresh(self):
        with self.lock:
            revision=self._revision();enabled=True;exclusions=self.exclusions
            if self.config_path is not None:
                config=json.loads(self.config_path.read_text(encoding='utf-8-sig'))
                if (config.get('account_username')!=self.self_id or config.get('version')!=2 or config.get('scope')!='all_private_contacts' or config.get('assistant_name')!='鹭鹭'):raise RuntimeError('动态联系人配置与绑定账号不一致')
                if self._revision()!=revision:raise RuntimeError('联系人配置正在变更，暂停本次回复')
                enabled=config.get('enabled') is True;exclusions=config.get('contact_exclusions',{})
            rows=contact_roster(self.db,exclusions) if enabled else {}
            if self.db.get_self_info()['username']!=self.self_id:raise RuntimeError('微信账号发生变化')
            values={user:name or user for user,name in rows.items() if private_contact(user,self.self_id)}
            self.clear();self.update(values);self.refreshed=self.clock();self.config_revision=revision;self.exclusions=exclusions
    def policy_status(self):
        with self.lock:
            policy=contact_exclusions(self.exclusions)
            return {'version':1,'labels':sorted(policy['labels']),'nicknames':sorted(policy['nicknames']),'boundContactCount':len(policy['contact_ids'])}
    def __contains__(self,user):
        if not private_contact(user,self.self_id):return False
        with self.lock:
            if self._revision()!=self.config_revision or self.clock()-self.refreshed>=30 or (not dict.__contains__(self,user) and self.clock()-self.refreshed>=5):self.refresh()
            return dict.__contains__(self,user)

class LuluReply(str):
    def __new__(cls,text,*,day=None,image_path=None,important=False,summary=''):
        value=super().__new__(cls,text);value.day=day;value.image_path=image_path;value.important=important;value.summary=summary
        return value

class DailyGreetings:
    """招呼预约与原发送账本共用文件，重启后不重复招呼。"""
    def __init__(self,path):
        self.path=Path(path);self.path.parent.mkdir(parents=True,exist_ok=True)
        with self._database() as conn:
            conn.execute('CREATE TABLE IF NOT EXISTS lulu_greetings (contact TEXT, day TEXT, status TEXT, event_ids TEXT, updated REAL, PRIMARY KEY(contact,day))')
            conn.execute('CREATE TABLE IF NOT EXISTS lulu_greeting_attempts (contact TEXT, day TEXT, event_ids TEXT, status TEXT, updated REAL, PRIMARY KEY(contact,day,event_ids))')
            conn.execute('INSERT OR IGNORE INTO lulu_greeting_attempts SELECT contact,day,event_ids,status,updated FROM lulu_greetings')
    @contextmanager
    def _database(self):
        conn=sqlite3.connect(self.path)
        try:
            with conn:yield conn
        finally:conn.close()
    def status(self,user,day):
        with self._database() as conn:
            row=conn.execute('SELECT status,event_ids FROM lulu_greetings WHERE contact=? AND day=?',(user,day)).fetchone()
            if not row:return 'new'
            if row[0]=='reserved':
                events=json.loads(row[1]);states=[conn.execute('SELECT status FROM deliveries WHERE event_id=? AND contact_id=?',(event,user)).fetchone() for event in events]
                if events and all(state and state[0]=='sent' for state in states):
                    conn.execute("UPDATE lulu_greetings SET status='sent',updated=? WHERE contact=? AND day=?",(time.time(),user,day))
                    conn.execute("UPDATE lulu_greeting_attempts SET status='sent',updated=? WHERE contact=? AND day=? AND event_ids=?",(time.time(),user,day,row[1]));return 'sent'
                if events and all(state and state[0]=='not_sent' for state in states):
                    conn.execute("UPDATE lulu_greetings SET status='blocked',updated=? WHERE contact=? AND day=?",(time.time(),user,day))
                    conn.execute("UPDATE lulu_greeting_attempts SET status='blocked',updated=? WHERE contact=? AND day=? AND event_ids=?",(time.time(),user,day,row[1]));return 'blocked'
            return row[0]
    def reserve(self,user,day,event_ids):
        with self._database() as conn:
            conn.execute('BEGIN IMMEDIATE')
            row=conn.execute('SELECT status FROM lulu_greetings WHERE contact=? AND day=?',(user,day)).fetchone()
            if row and row[0] in ('sent','reserved'):raise RuntimeError('当日招呼已有预约或已发送，禁止重复')
            conn.execute('INSERT OR REPLACE INTO lulu_greetings VALUES (?,?,?,?,?)',(user,day,'reserved',json.dumps(list(event_ids)),time.time()))
            conn.execute('INSERT OR IGNORE INTO lulu_greeting_attempts VALUES (?,?,?,?,?)',(user,day,json.dumps(list(event_ids)),'reserved',time.time()))
    def supersede_reserved(self,user,day,event_ids,incoming_seq,incoming_time):
        """新来信释放失败介绍预约，旧尝试与未知发送账本完整保留。"""
        if self.status(user,day)!='reserved':return False
        with self._database() as conn:
            conn.execute('BEGIN IMMEDIATE')
            row=conn.execute('SELECT status,event_ids,updated FROM lulu_greetings WHERE contact=? AND day=?',(user,day)).fetchone()
            if not row or row[0]!='reserved':return False
            old=json.loads(row[1])
            if not old or not event_ids or set(old).intersection(event_ids) or incoming_time<=row[2]:return False
            # 保存原预约快照，不能把未知旧介绍标成已发送或未发送。
            conn.execute('CREATE TABLE IF NOT EXISTS lulu_greeting_supersessions (contact TEXT, day TEXT, old_event_ids TEXT, old_updated REAL, new_event_ids TEXT, incoming_seq INTEGER, resolved_at REAL)')
            conn.execute('INSERT INTO lulu_greeting_supersessions VALUES (?,?,?,?,?,?,?)',
                (user,day,row[1],row[2],json.dumps(list(event_ids)),incoming_seq,time.time()))
            conn.execute("UPDATE lulu_greetings SET status='superseded',updated=? WHERE contact=? AND day=?",(time.time(),user,day))
            conn.execute("UPDATE lulu_greeting_attempts SET status='superseded',updated=? WHERE contact=? AND day=? AND event_ids=?",(time.time(),user,day,row[1]))
            return True
    def finish(self,user,day,status):
        with self._database() as conn:
            row=conn.execute('SELECT event_ids FROM lulu_greetings WHERE contact=? AND day=?',(user,day)).fetchone()
            conn.execute('UPDATE lulu_greetings SET status=?,updated=? WHERE contact=? AND day=?',(status,time.time(),user,day))
            if row:conn.execute('UPDATE lulu_greeting_attempts SET status=?,updated=? WHERE contact=? AND day=? AND event_ids=?',(status,time.time(),user,day,row[0]))
    def health_counts(self):
        with self._database() as conn:
            rows=conn.execute("SELECT updated FROM lulu_greetings WHERE status='reserved'").fetchall()
        return {'pending':len(rows),'stale':sum(time.time()-row[0]>300 for row in rows)}

class ImportantInbox:
    def __init__(self,root):self.root=Path(root);self.root.mkdir(parents=True,exist_ok=True);self.lock=threading.Lock()
    def add(self,user,name,incoming,summary):
        identity=hashlib.sha256((user+'|'+shanghai_day()+'|'+incoming).encode()).hexdigest()
        with self.lock:
            path=self.root/'important-messages.jsonl'
            prior=path.read_text(encoding='utf-8') if path.exists() else ''
            if any(json.loads(line).get('id')==identity for line in prior.splitlines() if line.strip()):return
            row={'id':identity,'at':datetime.now(CHINA).isoformat(),'contact_id':user,'name':name,'summary':summary[:300],'incoming':incoming[:1200],'read':False}
            with path.open('a',encoding='utf-8') as file:file.write(json.dumps(row,ensure_ascii=False)+'\n')
            with (self.root/'重要消息.md').open('a',encoding='utf-8') as file:file.write('\n'+row['at']+'　'+str(name).replace('\n',' ')+'\n\n'+row['summary'].replace('\n',' ')+'\n')
