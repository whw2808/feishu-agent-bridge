"""Central deterministic validation and rate limits for all group replies."""
import asyncio
import json
import re
from feishu_agent_bridge.adapters.lark import TransportError
from feishu_agent_bridge.store import now_ms


class Sender:
    def __init__(self,store,transport):
        self.store,self.transport = store,transport

    def validate(self,reply):
        source = self.store.get_message(reply['message_id'])
        if (not source or not self.store.config.group(reply['chat_id']) or source['chat_id']!=reply['chat_id']
            or source['own'] or source['update_ms']!=reply['version']):
            return 'invalid or stale source'
        if reply['kind'] != 'completed' and not source['addressed']:
            return 'ordinary messages cannot generate unsolicited replies'
        body = reply['body']
        if not isinstance(body,str) or not body.strip() or len(body)>6000 or re.search(r'<\s*at\b',body,re.I):
            return 'invalid reply body or injected mention'
        mentions = json.loads(reply['mentions'])
        if not isinstance(mentions,list) or any(m != source['sender_id'] for m in mentions):
            return 'mention outside original sender'
        count = self.store.db.execute("SELECT count(*) FROM replies WHERE root_id=? AND state='sent'",
                                      (reply['root_id'],)).fetchone()[0]
        if count >= self.store.config.max_thread_replies:
            return 'thread reply limit reached'
        duplicate = self.store.db.execute("SELECT 1 FROM replies WHERE root_id=? AND state='sent' AND body=?",
                                          (reply['root_id'],body)).fetchone()
        if duplicate:
            return 'repeated reply suppressed'
        return None

    async def dispatch(self):
        sent = 0
        for reply in self.store.pending_replies():
            error = self.validate(reply)
            if error:
                self.store.finish_reply(reply['id'],'blocked',error=error)
                continue
            recent = self.store.db.execute("SELECT count(*) FROM replies WHERE chat_id=? AND state='sent' AND sent_ms>?",
                                         (reply['chat_id'],now_ms()-60000)).fetchone()[0]
            if recent >= self.store.config.replies_per_minute:
                continue
            if not self.store.claim_reply(reply['id']):
                continue
            try:
                external = await self.transport.reply(reply['message_id'],reply['body'],json.loads(reply['mentions']),
                                  reply['id'],chat_id=reply['chat_id'])
                self.store.finish_reply(reply['id'],'sent',external)
                sent += 1
            except TransportError as exc:
                self.store.finish_reply(reply['id'],'uncertain' if exc.uncertain else 'failed',error=str(exc)[:800])
            except asyncio.CancelledError:
                self.store.finish_reply(reply['id'],'uncertain',error='sender cancelled before acknowledgment')
                raise
            except Exception as exc:
                self.store.finish_reply(reply['id'],'uncertain',error=str(exc)[:800])
        return sent

    async def loop(self,stop):
        while not stop.is_set():
            await self.dispatch()
            try:
                await asyncio.wait_for(stop.wait(),0.5)
            except asyncio.TimeoutError:
                pass
