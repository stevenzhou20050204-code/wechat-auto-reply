"""政治敏感话题静默处理；规则先于首次介绍，未知媒体不发送保底话术。"""
import json,re,unicodedata
from pathlib import Path

TERMS=('政治','政党','共产党','中共','国民党','民进党','习近平','毛泽东','邓小平','江泽民','胡锦涛','六四','八九学运','天安门事件','坦克人','法轮功','台独','藏独','港独','台湾独立','西藏独立','香港独立','政变','国家领导人','总统','总理','民主运动','人权运动','政治迫害','政治犯','言论审查','颠覆政权','抗议示威','大选','选举','台海','两岸统一','俄乌战争','巴以冲突','以巴冲突','politic','election','regime','tiananmen','tibet independence','taiwan independence')
SAFE_GREETING=re.compile(r'^(你好|您好|嗨|哈喽|在吗|在不在|在嘛|早|早上好|上午好|中午好|下午好|晚上好|晚安|谢谢|谢了|收到|好的|好|嗯|哈哈+|hello|hi|ok|现在几点(?:了)?|几点了|今天几号|今天星期几)[?？!！。~～\s]*$',re.I)

def enabled(path=None):
    path=Path(path) if path else Path(__file__).parent/'wechat_autoreply.json'
    if not path.exists():return False
    config=json.loads(path.read_text(encoding='utf-8-sig'))
    return config.get('content_policy',{}).get('sensitive_politics')=='silent'

def sensitive(text,recent=()):
    def match(value):
        value=unicodedata.normalize('NFKC',str(value)).casefold()
        compact=re.sub(r'[\s\u200b-\u200f\ufeff·._-]','',value)
        return any(re.sub(r'\s','',term.casefold()) in compact for term in TERMS)
    if match(text):return True
    if len(str(text))<80 and re.search(r'你怎么看|你觉得呢|继续(?:说|讲)|接着说|刚才那个|然后呢|支持哪边|站哪边',str(text)):
        return any(match(row.get('text','')) for row in list(recent)[-4:])
    return False

def safe_first_message(text):return bool(SAFE_GREETING.fullmatch(str(text).strip()))

def latest_incoming_blocked(db,user):
    if not enabled():return False
    rows=list(db.get_messages(user,limit=8))
    incoming=[row for row in rows if str(row.get('sender_id'))!='2']
    if not incoming:return False
    latest=max(incoming,key=lambda row:int(row.get('sort_seq') or 0))
    return sensitive(latest.get('content',''))
