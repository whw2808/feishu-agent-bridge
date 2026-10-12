"""Rules that model output cannot override."""
import re


def unverified_work_claim(text):
    """Conservative Chinese/English operation claims require trusted primary provenance."""
    patterns = (r'(?:已|已经|成功|完成|全部|所有).{0,30}(?:修改|更新|修复|合并|提交|推送|实现|认领|接管|绿了)',
                r'测试.{0,20}(?:通过|全绿|绿了)',
                r'\b(?:implemented|fixed|merged|committed|pushed|modified)\b',
                r'\b(?:tests?|suite).{0,30}\b(?:passed|passing|green|successful)\b')
    return any(re.search(pattern,text,re.I) for pattern in patterns)


def closure(text):
    if re.search(r'无需.{0,12}(?:回复|确认)|测试.{0,12}(?:收口|结束)|no (?:reply|further confirmation) (?:needed|required)',text,re.I):
        return True
    plain = re.sub(r'@_user_\d+|<at\b[^>]*>.*?</at>', '', text, flags=re.I)
    plain = re.sub(r'[\s，。,.!！;；:：]+', '', plain).lower()
    return bool(re.fullmatch(r'(?:已收到|收到|谢谢|感谢|thanks|thankyou|acknowledged|received|gotit|okay|ok)+',plain))


def valid_action(action,source,chunk):
    if not isinstance(action,dict) or action.get('kind') not in ('reply','handoff'):
        return False
    if not source['addressed'] or source['own'] or not chunk['complete'] or closure(source['content']):
        return False
    text = action.get('text')
    if not isinstance(text,str) or not text.strip() or len(text)>6000 or re.search(r'<\s*at\b',text,re.I):
        return False
    if action['kind']=='reply' and unverified_work_claim(text):
        return False
    if set(action)-{'message_id','kind','text','title'}:
        return False
    if action['kind']=='handoff' and (not isinstance(action.get('title'),str) or not action['title'].strip() or len(action['title'])>200):
        return False
    return True
