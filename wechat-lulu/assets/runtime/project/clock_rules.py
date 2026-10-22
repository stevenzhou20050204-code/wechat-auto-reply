"""直接问当前时间时本地回答，不依赖模型猜测或历史时间。"""
import datetime,re

CHINA=datetime.timezone(datetime.timedelta(hours=8))

def clock_context(now=None):
    current=now or datetime.datetime.now(CHINA)
    current=current.astimezone(CHINA)
    return {'local':current.isoformat(timespec='seconds'),'timezone':'Asia/Shanghai','hour':current.hour,'minute':current.minute}

def is_clock_question(text):
    value=re.sub(r'[\s？?！!。,.，~～]','',str(text))
    return bool(re.fullmatch(r'(?:请|麻烦)?(?:告诉我|说一下|看一下|帮我看下)?(?:现在|这会儿|此刻)?(?:是)?几点(?:了|钟|啊|呀)?',value) or value.lower() in ('whattimeisit','whattimeisitnow'))

def clock_reply(incoming,recent=(),now=None):
    direct=is_clock_question(incoming)
    followup=bool(re.fullmatch(r'(?:你)?(?:不能|能不能|可以)?看一下(?:吗)?[？?]?|你说一下(?:呀)?[，,]?我手机坏了[。]?',str(incoming).strip()))
    if not direct and not (followup and any(is_clock_question(row.get('text','')) for row in recent[-8:])):return None
    current=clock_context(now)
    return f"现在{current['hour']}点{current['minute']:02d}分"
