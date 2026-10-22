"""按主人指定，针对本人的尺寸调侃和紧接着的追问固定回答“大”。"""
import re,unicodedata

_SUBJECT=re.compile(r'你(?:的|那(?:个)?)?(?:屌|吊|鸡鸡|鸡巴|叽叽|丁丁|小兄弟|那玩意(?:儿)?)')
_QUESTION=re.compile(r'大不大|大还是不大|大还是小|大吗|大么|多大|小不小|小吗|有多大|多长|长不长')
_OTHER_CONTEXT=re.compile(r'他说|她说|别人|有人|朋友|转述|引用|翻译|什么意思|怎么回复|怎么回|医生|检查|疾病|肿块|小时候|小孩|孩子')
_FOLLOWUP=re.compile(r'(?:你(?:就|直接)?说|直接说|就说|所以|那(?:你)?|到底|你到底)?(?:大不大|大还是不大|大还是小|不大吗|多大|多长)(?:啊|呀|嘛|吗|呢|吧|了)*')

def _normalize(text):
    value=unicodedata.normalize('NFKC',str(text))
    return re.sub(r'[\s，。！？!?、,.~～…“”"\u200b-\u200d\ufeff]','',value)

def self_size_reply(incoming,recent=()):
    if not isinstance(incoming,str) or len(incoming)>2000:return None
    lines=[line.strip() for line in incoming.splitlines() if line.strip()]
    if not lines:return None
    value=_normalize(lines[-1])
    if _OTHER_CONTEXT.search(value):return None
    if _SUBJECT.search(value) and _QUESTION.search(value):return '大'
    if not _FOLLOWUP.fullmatch(value):return None
    received=[_normalize(row.get('text','')) for row in (recent or [])[-8:] if row.get('role')=='fan']
    # 数据库最近消息通常包含本轮来信；自己的“不大”旧回复不能成为新规则。
    if received and received[-1] in (value,_normalize(incoming)):received.pop()
    received.extend(_normalize(line) for line in lines[:-1])
    for previous in reversed(received[-3:]):
        if _OTHER_CONTEXT.search(previous):return None
        if _SUBJECT.search(previous) and _QUESTION.search(previous):return '大'
        if not _FOLLOWUP.fullmatch(previous):return None
    return None
