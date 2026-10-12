"""Model work uses an independent thread and never blocks collection."""
import asyncio
import logging
from feishu_agent_bridge.policy import closure,valid_action,unverified_work_claim
from feishu_agent_bridge.store import now_ms

LOG = logging.getLogger(__name__)


class Worker:
    def __init__(self,store,agent):
        self.store,self.agent = store,agent
        self.semaphore = asyncio.Semaphore(1)

    async def tick(self,group,*,addressed_only=False):
        async with self.semaphore:
            rows = self.store.pending_messages(group.chat_id,20,addressed_only=addressed_only,
                                                prioritize_mentions=addressed_only)
            if not rows:
                return 0
            recent = self.store.db.execute('SELECT count(*) FROM model_runs WHERE started_ms>?',(now_ms()-3600000,)).fetchone()[0]
            if recent >= self.store.config.max_jobs_per_hour:
                self.store.set_meta('health:model','hourly model-job budget reached; messages retained')
                return 0
            chunks,remaining = [],self.store.config.max_batch_chars
            share = max(256,self.store.config.max_batch_chars//len(rows))
            for row in rows:
                if remaining <= 0:
                    break
                start = row['read_offset']
                part = row['content'][start:start+min(remaining,share)]
                chunks.append({'message_id':row['message_id'],'version':row['update_ms'],
                               'content':part,'offset':start,'complete':start+len(part)>=len(row['content']),
                               'sender_type':row['sender_type'],'addressed':bool(row['addressed']),
                               'root_id':row['root_id'],'parent_id':row['parent_id'],'message_type':row['kind']})
                remaining -= len(part)
            ids = [m['message_id'] for m in chunks]
            sources = {r['message_id']:r for r in rows if r['message_id'] in ids}
            versions = {mid:sources[mid]['update_ms'] for mid in ids}
            offsets = {c['message_id']:c['offset']+len(c['content']) for c in chunks}
            self.store.reserve_messages(ids)
            with self.store.db:
                self.store.db.execute('DELETE FROM model_runs WHERE started_ms<?',(now_ms()-3600000,))
                self.store.db.execute('INSERT INTO model_runs VALUES (?)',(now_ms(),))
            try:
                result = await asyncio.wait_for(self.agent.read(group,chunks,self.store.meta('summary:'+group.chat_id,'')),
                                                self.store.config.model_timeout_seconds)
                if (not isinstance(result,dict) or not isinstance(result.get('summary'),str)
                    or len(result['summary'])>8000 or not isinstance(result.get('actions'),list)
                    or len(result['actions'])>len(chunks)):
                    raise ValueError('invalid structured background decision')
                used = set()
                for action in result['actions']:
                    mid = action.get('message_id') if isinstance(action,dict) else None
                    chunk = next((c for c in chunks if c['message_id']==mid),None)
                    source = sources.get(mid)
                    current = self.store.get_message(mid) if source else None
                    if (source and current['update_ms']==versions[mid] and source['addressed'] and chunk['complete']
                        and not closure(source['content']) and isinstance(action.get('text'),str)
                        and action.get('kind')=='reply' and unverified_work_claim(action['text'])):
                        self.store.create_task(mid,'核验项目操作状态','请核验原消息所问的项目操作与验证结果。后台回复没有完成操作的可信证据，不能作为完成声明。')
                        self.store.enqueue_reply(mid,'handoff','这条请求需要开发会话核验实际状态，已记录为待办；目前没有可确认的项目操作结果。',[])
                        continue
                    if (not source or mid in used or current['update_ms']!=versions[mid]
                        or not valid_action(action,source,chunk)):
                        self.store.set_meta('health:decision','invalid, unaddressed, duplicate or stale action suppressed')
                        continue
                    used.add(mid)
                    if action['kind']=='reply':
                        self.store.enqueue_reply(mid,'final',action['text'],[source['sender_id']],version=versions[mid])
                    else:
                        task = self.store.create_task(mid,action['title'],action['text'])
                        self.store.enqueue_reply(mid,'handoff',f'已记录到开发会话待办（{task[:8]}），尚未开始修改项目。处理结果会在此话题反馈。',[],version=versions[mid])
                self.store.set_meta('summary:'+group.chat_id,result['summary'])
                self.store.mark_read(ids,versions=versions,offsets=offsets)
                self.store.set_meta('health:model','ok')
                return len(ids)
            except asyncio.CancelledError:
                self.store.reading_failed(ids,'background processing cancelled; message retained')
                raise
            except Exception as exc:
                self.store.reading_failed(ids,exc)
                self.store.set_meta('health:model',str(exc)[:800])
                for mid,source in sources.items():
                    if source['addressed'] and not closure(source['content']):
                        self.store.enqueue_reply(mid,'error','后台处理暂时失败，消息已保留等待恢复；目前没有完成项目操作。',[])
                LOG.warning('background model failed: %s',exc)
                return 0

    def acknowledge_due(self):
        count = 0
        rows = self.store.db.execute("SELECT * FROM messages WHERE addressed=1 AND own=0 AND read_state IN ('reading','pending') AND ack_due_ms<=?",(now_ms(),)).fetchall()
        for row in rows:
            if closure(row['content']):
                continue
            mid = row['message_id']
            before = self.store.db.execute("SELECT 1 FROM replies WHERE message_id=? AND version=? AND kind='ack'",(mid,row['update_ms'])).fetchone()
            if not before:
                text = ('消息已收到，后台正在阅读并核对上下文；当前开发对话继续运行。' if row['read_state']=='reading' else
                        '消息已收到并进入后台队列，尚未开始阅读；当前开发对话继续运行。')
                self.store.enqueue_reply(mid,'ack',text,[])
                count += 1
        return count

    async def loop(self,group,stop):
        next_normal = 0
        while not stop.is_set():
            now = asyncio.get_running_loop().time()
            normal = now >= next_normal
            await self.tick(group,addressed_only=not normal)
            if normal:
                next_normal = asyncio.get_running_loop().time()+self.store.config.read_seconds
            try:
                await asyncio.wait_for(stop.wait(),.5)
            except asyncio.TimeoutError:
                pass
