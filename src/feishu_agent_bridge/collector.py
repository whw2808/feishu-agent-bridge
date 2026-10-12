"""Incremental, persistent history scans and real-time ingestion."""
import asyncio
import json
import logging
from feishu_agent_bridge.adapters.lark import normalize_history
from feishu_agent_bridge.store import now_ms

LOG = logging.getLogger(__name__)


class Collector:
    def __init__(self,store,transport):
        self.store,self.transport = store,transport

    def new_scan(self,group):
        previous = int(self.store.meta(f'history:{group.chat_id}:complete',group.start_ms))
        return {'start_ms':max(group.start_ms,previous-60000),'end_ms':now_ms(),'page_token':''}

    async def scan_group(self,group):
        key = f'history:{group.chat_id}:scan'
        saved = self.store.meta(key)
        scan = json.loads(saved) if saved else self.new_scan(group)
        self.store.set_meta(key,json.dumps(scan))
        try:
            if hasattr(self.transport,'members'):
                members = await self.transport.members(group.chat_id)
                self.store.register_bots(group.chat_id,members.get('bots',[]))
            data = await self.transport.history(group,scan)
            if not isinstance(data,dict) or not isinstance(data.get('items',[]),list):
                raise ValueError('invalid history page')
            for item in reversed(data.get('items',[])):
                normalized = normalize_history(item)
                if normalized.get('chat_id') is None:
                    normalized['chat_id'] = group.chat_id
                self.store.ingest(normalized)
                await self.resolve_parent(normalized)
            if data.get('has_more'):
                scan['page_token'] = data.get('page_token')
                if not scan['page_token']:
                    raise ValueError('has_more without page token')
                self.store.set_meta(key,json.dumps(scan))
            else:
                self.store.set_meta(f'history:{group.chat_id}:complete',scan['end_ms'])
                self.store.set_meta(key,'')
            self.store.set_meta(f'health:history:{group.chat_id}','ok')
            return True
        except Exception as exc:
            self.store.set_meta(key,json.dumps(self.new_scan(group)))
            self.store.set_meta(f'health:history:{group.chat_id}',str(exc)[:800])
            LOG.warning('history scan failed chat=%s error=%s',group.chat_id,exc)
            return False

    async def resolve_parent(self,event):
        parent = event.get('reply_to') or event.get('parent_id')
        chat = event.get('chat_id')
        if not parent or self.store.get_message(parent) or not self.store.config.group(chat):
            return
        if not hasattr(self.transport,'original'):
            return
        try:
            original = await self.transport.original(parent,chat)
            self.store.ingest(normalize_history(original))
        except Exception as exc:
            self.store.set_meta('health:parent:'+str(parent),str(exc)[:800])

    async def history_loop(self,stop):
        deadlines = {g.chat_id:0 for g in self.store.config.groups}
        while not stop.is_set():
            for group in self.store.config.groups:
                if asyncio.get_running_loop().time() >= deadlines[group.chat_id]:
                    success = await self.scan_group(group)
                    incomplete = bool(self.store.meta(f'history:{group.chat_id}:scan'))
                    delay = 0.2 if success and incomplete else self.store.config.poll_seconds if success else 5
                    deadlines[group.chat_id] = asyncio.get_running_loop().time()+delay
            try:
                await asyncio.wait_for(stop.wait(),0.2)
            except asyncio.TimeoutError:
                pass

    async def ingestion_loop(self,stop,received):
        while not stop.is_set():
            try:
                event = await asyncio.wait_for(received.get(),0.5)
            except asyncio.TimeoutError:
                continue
            if isinstance(event,dict) and event.get('control'):
                self.store.set_meta('health:events',event.get('error') or event['control'])
            else:
                if (isinstance(event,dict) and event.get('sender_type')=='bot' and
                    self.store.config.group(event.get('chat_id')) and hasattr(self.transport,'members')):
                    try:
                        members = await self.transport.members(event['chat_id'])
                        self.store.register_bots(event['chat_id'],members.get('bots',[]))
                    except Exception as exc:
                        self.store.set_meta('health:bot_identity',str(exc)[:800])
                self.store.ingest(event)
                if isinstance(event,dict):
                    await self.resolve_parent(event)
            received.task_done()
