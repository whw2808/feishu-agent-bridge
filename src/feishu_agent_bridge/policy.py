"""Rules that model output cannot override."""
import re


def closure(text):
    return bool(re.search(r'无需.{0,12}(?:回复|确认)|测试.{0,12}(?:收口|结束)|no (?:reply|further confirmation) (?:needed|required)',text,re.I))


def valid_action(action,source,chunk):
    if not isinstance(action,dict) or action.get('kind') not in ('reply','handoff'):
        return False
    if not source['addressed'] or source['own'] or not chunk['complete'] or closure(source['content']):
        return False
    text = action.get('text')
    if not isinstance(text,str) or not text.strip() or len(text)>6000 or re.search(r'<\s*at\b',text,re.I):
        return False
    if set(action)-{'message_id','kind','text','title'}:
        return False
    if action['kind']=='handoff' and (not isinstance(action.get('title'),str) or not action['title'].strip() or len(action['title'])>200):
        return False
    return True
