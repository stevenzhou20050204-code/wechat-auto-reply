"""明确针对本人的辱骂分级；转述与含糊句子仍交给正常上下文判断。"""
import hashlib,json,re,time,unicodedata
from collections import defaultdict,deque
from pathlib import Path

PREFIX=r'(?:你|妳|您)(?:这(?:个)?|是(?:个)?|就(?:是)?(?:个)?|真的?|真是(?:个)?|个|他妈的?|特么的?|tmd?|大|死|臭|太|很|可真){0,5}'
MODERATE=r'(?:傻[逼b比]|煞笔|沙比|傻狗|狗东西|畜生|废物|垃圾|蠢货|白痴|脑残|智障|神经病|有病|脑子有病|脑子进水|狗叫|弱智|菜[逼狗]|没用)'
MILD=r'(?:人机|嘴硬|菜鸡|笨蛋|怂包|装逼)'
REPORTED=re.compile(r'他说|她说|他骂|她骂|有人说|有人骂|别人说|别人骂|没说你|没有说你|没骂你|不是骂你|不是说你|评论里|引用|转述|翻译|什么意思|啥意思|怎么回复|怎么回|如何回复|如何回|举例|歌词|小说|电影|[“”‘’"《》]')

def severity(text):
    if not isinstance(text,str) or not text.strip() or len(text)>2000:return 0
    value=unicodedata.normalize('NFKC',text).lower()
    value=re.sub(r'[\s\u200b-\u200d\ufeff]','',value)
    if REPORTED.search(value):return 0
    if re.search(r'(?:操|草|艹|肏|日)(?:你|您)(?:的)?(?:妈|娘)',value):return 3
    if re.search(PREFIX+r'(?:死妈|没妈|全家去死)',value):return 3
    bare=value.strip('，。！？!?~～…、')
    if re.fullmatch(r'(?:cnmd?|nmsl|nmb|你妈的|你妈了个逼|去你妈的|滚你妈的)',bare):return 3
    if re.search(PREFIX+MODERATE,value):return 2
    if re.fullmatch(MODERATE+r'(?:滚|滚蛋|滚开)?',bare) or bare in ('sb','s.b.'):return 2
    if re.search(PREFIX+MILD+r'(?=[啊呀吧吗呢哦哈的就，。！？!?]|$)',value):return 1
    if re.fullmatch(MILD,bare):return 1
    return 0

class CounterPolicy:
    def __init__(self,engine_root,allowed,clock=time.monotonic):
        self.path=Path(engine_root)/'counter-policy.json';self.allowed=dict(allowed);self.clock=clock
        self.recent=defaultdict(lambda:deque(maxlen=8));self.last={};self.turns=defaultdict(int)
    def plan(self,username,incoming):
        # 名单和配置属于本机授权数据，聊天文字只能影响语气，不能扩大权限。
        if username not in self.allowed or self.allowed[username] not in ('咖喱','联系人甲','联系人丙'):return None
        config=json.loads(self.path.read_text(encoding='utf-8'))
        if config.get('version')!=1:raise RuntimeError('反击配置版本不正确')
        if config.get('enabled') is not True:return None
        if self.allowed[username] not in config.get('scope',[]):return None
        level=severity(incoming)
        if not level:return None
        now=self.clock();events=self.recent[username];events.append((now,level))
        escalation=config['escalation']
        if level==2 and sum(grade>=2 and now-at<=escalation['within_seconds'] for at,grade in events)>=escalation['moderate_messages']:level=3
        setting=config['levels'][str(level)]
        if level==3:
            return {'level':3,'route':'codex','fallback':setting['fallback'],'context':{'level':3,'instruction':setting['instruction']}}
        options=setting['replies'];self.turns[username]+=1
        start=int(hashlib.sha256(incoming.encode()).hexdigest()[:8],16)+self.turns[username]
        text=options[start%len(options)]
        if text==self.last.get(username):text=options[(start+1)%len(options)]
        if not isinstance(text,str) or not text.strip() or len(text)>60:raise RuntimeError('本地反击正文无效')
        self.last[username]=text
        return {'level':level,'route':'local','text':text}
