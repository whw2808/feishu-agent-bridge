"""Service task supervision and singleton ownership."""
import asyncio
import fcntl
import logging
import os
import signal
from feishu_agent_bridge.adapters.codex import CodexAgent,PrimaryQueue
from feishu_agent_bridge.adapters.lark import Lark,TransportError
from feishu_agent_bridge.collector import Collector
from feishu_agent_bridge.delivery import Sender
from feishu_agent_bridge.store import Store,now_ms

LOG = logging.getLogger(__name__)


async def wait(stop,seconds):
    try:
        await asyncio.wait_for(stop.wait(),seconds)
    except asyncio.TimeoutError:
        pass


async def feedback_loop(store,worker,stop):
    while not stop.is_set():
        worker.acknowledge_due()
        store.set_meta('health:heartbeat',now_ms())
        await wait(stop,.5)


async def handoff_loop(store,stop):
    queue = PrimaryQueue(store.config)
    while not stop.is_set():
        if store.config.auto_handoff:
            for task in store.tasks():
                if task['delivery_state'] != 'pending' or task['state']!='pending':
                    continue
                with store.db:
                    store.db.execute("UPDATE tasks SET delivery_state='sending' WHERE id=? AND delivery_state='pending'",(task['id'],))
                try:
                    qid = await queue.deliver(task)
                    with store.db:
                        store.db.execute("UPDATE tasks SET delivery_state='queued',queue_id=? WHERE id=?",(qid,task['id']))
                except TransportError as exc:
                    with store.db:
                        store.db.execute('UPDATE tasks SET delivery_state=?,last_error=? WHERE id=?',
                                          ('uncertain' if exc.uncertain else 'failed',str(exc)[:800],task['id']))
        await wait(stop,1)


async def run(config,*,agent=None,transport=None):
    config.data_dir.mkdir(parents=True,exist_ok=True,mode=0o700)
    with (config.data_dir/'service.lock').open('a+') as lock:
        try:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError('this profile instance is already running') from exc
        lock.seek(0)
        lock.truncate()
        lock.write(str(os.getpid()))
        lock.flush()
        store = Store(config,recover=True)
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM,signal.SIGINT):
            loop.add_signal_handler(sig,stop.set)
        transport = transport or Lark(config)
        agent = agent or CodexAgent(config,store)
        collector = Collector(store,transport)
        from feishu_agent_bridge.worker import Worker
        worker = Worker(store,agent)
        received = asyncio.Queue(maxsize=1024)
        store.set_meta('service_pid',os.getpid())
        tasks = [asyncio.create_task(transport.events(stop,received)),
                 asyncio.create_task(collector.ingestion_loop(stop,received)),
                 asyncio.create_task(collector.history_loop(stop)),
                 asyncio.create_task(Sender(store,transport).loop(stop)),
                 asyncio.create_task(feedback_loop(store,worker,stop)),
                 asyncio.create_task(handoff_loop(store,stop))]
        tasks += [asyncio.create_task(worker.loop(group,stop)) for group in config.groups]
        def failed(task):
            if not task.cancelled() and task.exception() is not None:
                store.set_meta('health:runtime',str(task.exception())[:800])
                LOG.error('runtime task failed: %s',task.exception())
                stop.set()
        for task in tasks:
            task.add_done_callback(failed)
        try:
            await stop.wait()
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks,return_exceptions=True)
            store.set_meta('health:events','stopped')
            store.set_meta('stopped_ms',now_ms())
            store.close()
            for sig in (signal.SIGTERM,signal.SIGINT):
                loop.remove_signal_handler(sig)
