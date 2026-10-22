"""新来信取代失败旧批次；保留旧记录，不把未知发送冒充成功或未发送。"""
import hashlib,json
from pathlib import Path

def policy_enabled(db,root=None):
    root=Path(root) if root is not None else Path(__file__).resolve().parent
    path=root/'wechat_autoreply.json'
    if not path.exists():return False
    config=json.loads(path.read_text(encoding='utf-8-sig'))
    return (config.get('version')==2 and config.get('enabled') is True
            and config.get('account_username')==db.get_self_info().get('username')
            and config.get('pending_reply_policy')=='latest_new_message')

def distinct_newer_batch(prior,user,event_ids,incoming_seq):
    """必须是同一联系人、严格更新的来信和互不相交的原始事件凭证。"""
    old=prior.get('event_ids')
    return (prior.get('contact_hash')==hashlib.sha256(user.encode()).hexdigest()
            and isinstance(incoming_seq,int) and not isinstance(incoming_seq,bool)
            and isinstance(prior.get('incoming_seq'),int)
            and incoming_seq>prior['incoming_seq']
            and isinstance(old,list) and bool(old) and all(isinstance(x,str) and x for x in old)
            and isinstance(event_ids,(list,tuple)) and bool(event_ids) and all(isinstance(x,str) and x for x in event_ids)
            and not set(old).intersection(event_ids))
