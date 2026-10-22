"""本地回复队列快照及额度冷却；不修改原发送账本或消息指纹。"""
import json,math,os,re
from datetime import datetime,timedelta,timezone
from pathlib import Path

CHINA=timezone(timedelta(hours=8))

def quota_error(error):
    text=str(error).lower().replace('’',"'")
    return any(token in text for token in ("hit your usage limit",'usage_limit_reached','rate_limit_exceeded','insufficient_quota'))

def quota_resume_at(error,now):
    """尊重服务端明确恢复时间，另留五分钟；未知时间五分钟后有限复核。"""
    match=re.search(r'try again at\s+(\d{1,2})(?::(\d{2}))?\s*([ap]m)',str(error),re.I)
    if not match:return float(now)+300
    hour,minute=int(match[1]),int(match[2] or 0)
    if not 1<=hour<=12 or not 0<=minute<60:return float(now)+300
    hour=hour%12+(12 if match[3].lower()=='pm' else 0)
    local=datetime.fromtimestamp(now,CHINA)
    candidate=local.replace(hour=hour,minute=minute,second=0,microsecond=0).timestamp()
    if now-candidate>43200:candidate+=86400
    return max(float(now)+300,candidate+300)

class ReplyQueueJournal:
    """一个微信账号一份原子快照；账号或格式错误时保留原文件并停止。"""
    def __init__(self,path,account):
        self.path=Path(path);self.account=account;self._last_written=None
    def load(self):
        if not self.path.exists():return {'batches':[],'resume_at':0,'pause_reason':None}
        try:
            value=json.loads(self.path.read_text(encoding='utf-8-sig'))
            if value.get('version')!=1 or value.get('account')!=self.account:raise ValueError('账号或版本不匹配')
            rows=value['batches'];resume=value['resume_at']
            if not isinstance(rows,list) or len(rows)>10000 or not isinstance(resume,(int,float)) or not math.isfinite(resume) or resume<0:raise ValueError('排队数据无效')
            for row in rows:
                batch=row['batch']
                if not isinstance(row['contact_id'],str) or not row['contact_id']:raise ValueError('联系人无效')
                if not isinstance(batch['text'],str) or not isinstance(batch['event_ids'],list) or not all(isinstance(x,str) for x in batch['event_ids']):raise ValueError('新来信凭证无效')
                if not isinstance(batch['incoming_seq'],int) or not isinstance(batch['media'],list) or not isinstance(batch['order'],list) or len(batch['order'])!=3:raise ValueError('排序或媒体无效')
                if not all(isinstance(x,(int,float)) and math.isfinite(x) for x in batch['order']):raise ValueError('排序值无效')
            return value
        except (OSError,ValueError,KeyError,TypeError) as error:
            raise RuntimeError('回复队列快照不可读取，保留原文件，禁止重置：'+str(error)) from error
    def save(self,batches,resume_at=0,pause_reason=None):
        value={'version':1,'account':self.account,'batches':batches,'resume_at':resume_at,'pause_reason':pause_reason}
        body=json.dumps(value,ensure_ascii=False,allow_nan=False)
        if body==self._last_written:return
        self.path.parent.mkdir(parents=True,exist_ok=True)
        temporary=self.path.with_suffix('.tmp')
        with temporary.open('w',encoding='utf-8') as stream:
            stream.write(body);stream.flush();os.fsync(stream.fileno())
        temporary.replace(self.path);self._last_written=body
