"""请求级发送进度：点击预约先落盘，超时只能凭同一请求的证据判定未发。"""
import hashlib,json,os,re,time
from pathlib import Path

PRECLICK={'started','db_ready','contacts_ready','sender_ready','prepare_start','prepared','replace_reserved','paste_reserved','pasted','draft_verified','draft_discarded'}
ROOT=Path(__file__).parent/'.wechat_codex'

class RequestProgress:
    def __init__(self,payload,root=ROOT):
        request=payload.get('request_id','')
        if not isinstance(request,str) or re.fullmatch('[0-9a-f]{32}',request) is None:raise ValueError('发送请求ID格式错误')
        self.root=Path(root);self.path=self.root/'send-requests'/(request+'.json')
        self.record={'request_id':request,'contact_hash':hashlib.sha256(payload['username'].encode()).hexdigest(),'event_ids':payload.get('event_ids',[]),'text_hash':hashlib.sha256(payload['text'].encode()).hexdigest(),'pid':os.getpid()}
    def mark(self,phase,progress=None):
        self.record.update(phase=phase,at=time.time())
        if progress and progress.get('attempt_id'):self.record['attempt_id']=progress['attempt_id']
        self.path.parent.mkdir(parents=True,exist_ok=True);temp=self.path.with_suffix('.tmp')
        with temp.open('w',encoding='utf-8') as stream:
            json.dump(self.record,stream,ensure_ascii=False);stream.flush();os.fsync(stream.fileno())
        temp.replace(self.path)

def proven_preclick(payload,root=ROOT):
    """仅在子进程已结束后调用；未知、错配、损坏或任何点击预约都保持未知。"""
    try:
        request=payload['request_id']
        if re.fullmatch('[0-9a-f]{32}',request) is None:return None
        record=json.loads((Path(root)/'send-requests'/(request+'.json')).read_text(encoding='utf-8'))
        contact=hashlib.sha256(payload['username'].encode()).hexdigest()
        if record.get('request_id')!=request or record.get('contact_hash')!=contact or record.get('event_ids')!=payload.get('event_ids',[]):return None
        if record.get('text_hash')!=hashlib.sha256(payload['text'].encode()).hexdigest() or record.get('phase') not in PRECLICK:return None
        journal=Path(root)/'send-journals'/(contact+'.json')
        if journal.exists():
            prior=json.loads(journal.read_text(encoding='utf-8'))
            if set(prior.get('event_ids',[]))&set(payload.get('event_ids',[])) and prior.get('phase') in ('send_reserved','clicked','sent_confirmed'):return None
        return record['phase']
    except (OSError,ValueError,KeyError,TypeError):return None
