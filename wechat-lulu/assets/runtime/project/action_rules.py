"""未接入的付款动作只回文字，不承诺或声称已经执行；无模型消耗。"""
import re

_PAYMENT_REQUEST=re.compile(
    r'(?:转(?:账|钱|过来|给我)|给我.{0,8}(?:[一二三四五六七八九十两\d]+\s*(?:块|元)|钱)|'
    r'(?:发|给|来).{0,5}红包|(?:借|给|要).{0,6}[一二三四五六七八九十两\d]+\s*(?:块钱|元钱))'
)
_DISCUSSION=re.compile(r'(?:怎么|如何|手续费|安全吗|是什么意思|记录|多久到账)')
_TO_OWNER=re.compile(r'(?:给你|转给你|转你|发你|送你).{0,12}(?:钱|元|块|红包)|(?:我|我来|我帮你).{0,5}(?:发红包|转账)')
_FROM_OWNER=re.compile(r'转给我|转我|给我.{0,8}(?:钱|元|块)|(?:发|给).{0,5}红包.{0,3}给我')

def payment_request_reply(incoming,recent=None):
    text=str(incoming).strip()
    # 对方提供礼物或入款由模型理解上下文；不能误判为要求主人付款。
    if _TO_OWNER.search(text) and not _FROM_OWNER.search(text):return None
    if not _DISCUSSION.search(text) and _PAYMENT_REQUEST.search(text):
        if any(str(row.get('text','')).strip()=='那给你两块' and row.get('role')=='self' for row in recent or []):
            return '刚才说早了，转账还得我自己弄'
        return '转账还得我自己弄，先等等'
    return None
