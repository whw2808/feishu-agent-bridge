import asyncio
import tempfile
import unittest

from feishu_agent_bridge.collector import Collector
from feishu_agent_bridge.config import Config
from feishu_agent_bridge.store import Store
from feishu_agent_bridge.worker import Worker
from tests.helpers import message, settings
from tests.test_transport import FakeLark, raw


class Agent:
    def __init__(self, actions=None):
        self.actions = actions or []
        self.seen = []

    async def read(self, group, messages, summary):
        self.seen.extend(messages)
        return {'summary': '已阅读本批消息', 'actions': self.actions}


class WorkerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.config = Config.from_dict(settings(self.tmp.name, ack_seconds=.001))
        self.store = Store(self.config)
        self.addCleanup(self.store.close)
        self.group = self.config.groups[0]

    async def test_ordinary_messages_are_read_without_unprompted_reply(self):
        self.store.ingest(message())
        agent = Agent([{'message_id':'om_one','kind':'reply','text':'不要主动插话'}])
        self.assertEqual(await Worker(self.store,agent).tick(self.group),1)
        self.assertEqual(self.store.get_message('om_one')['read_state'],'read')
        self.assertEqual(self.store.pending_replies(),[])
        self.assertEqual(self.store.tasks(),[])

    async def test_true_mention_answer_is_durable_without_primary_queue(self):
        self.store.ingest(message(mentions=[{'id':'ou_self'}]))
        agent = Agent([{'message_id':'om_one','kind':'reply','text':'接口是 MemoryProvider。'}])
        self.assertEqual(await Worker(self.store,agent).tick(self.group),1)
        self.assertEqual(self.store.pending_replies()[0]['body'],'接口是 MemoryProvider。')
        self.assertEqual(self.store.tasks(),[])

    async def test_implementation_request_becomes_task_with_truthful_feedback(self):
        self.store.ingest(message(mentions=[{'id':'ou_self'}]))
        agent = Agent([{'message_id':'om_one','kind':'handoff','text':'请修改接口并跑测试。','title':'修改接口'}])
        self.assertEqual(await Worker(self.store,agent).tick(self.group),1)
        self.assertEqual(len(self.store.tasks()),1)
        self.assertIn('待办',self.store.pending_replies()[0]['body'])
        self.assertNotIn('已经完成',self.store.pending_replies()[0]['body'])

    async def test_blocked_inference_does_not_block_history_collection_or_ack(self):
        started,release = asyncio.Event(),asyncio.Event()
        class SlowAgent(Agent):
            async def read(self,*args):
                started.set()
                await release.wait()
                return {'summary':'read','actions':[]}
        self.store.ingest(message(mentions=[{'id':'ou_self'}]))
        worker = Worker(self.store,SlowAgent())
        task = asyncio.create_task(worker.tick(self.group))
        await asyncio.sleep(.01)
        if not started.is_set():
            await task
        self.assertTrue(started.is_set(),'the independent worker must be running')
        collector = Collector(self.store,FakeLark([{'items':[raw('om_parallel')],'has_more':False}]))
        self.assertTrue(await collector.scan_group(self.group))
        self.assertIsNotNone(self.store.get_message('om_parallel'))
        self.assertFalse(task.done())
        self.assertEqual(worker.acknowledge_due(),1)
        self.assertEqual(worker.acknowledge_due(),0)
        release.set()
        await task

    async def test_pure_bot_confirmation_cannot_start_ping_pong(self):
        self.store.ingest(message(sender_type='bot',sender_id='cli_peer',mentions=[{'id':'ou_self'}],
                                  content='收到，谢谢。此测试到此收口，无需再互相确认。'))
        agent = Agent([{'message_id':'om_one','kind':'reply','text':'我也收到！'}])
        self.assertEqual(await Worker(self.store,agent).tick(self.group),1)
        self.assertEqual(self.store.pending_replies(),[])

    async def test_short_bot_thanks_does_not_trigger_reply_or_slow_ack(self):
        self.store.ingest(message(sender_type='bot',sender_id='cli_peer',mentions=[{'id':'ou_self'}],
                                  content='@_user_1 收到，谢谢。'))
        self.store.reserve_messages(['om_one'])
        await asyncio.sleep(.005)
        worker = Worker(self.store,Agent([{'message_id':'om_one','kind':'reply','text':'我也收到！'}]))
        self.assertEqual(worker.acknowledge_due(),0)
        self.store.db.execute("UPDATE messages SET read_state='pending'")
        self.store.db.commit()
        await worker.tick(self.group)
        self.assertEqual(self.store.pending_replies(),[])

    async def test_waiting_job_gets_truthful_queue_feedback_without_starting_inference(self):
        self.store.ingest(message(mentions=[{'id':'ou_self'}]))
        await asyncio.sleep(.005)
        worker = Worker(self.store,Agent())
        self.assertEqual(worker.acknowledge_due(),1)
        self.assertIn('队列',self.store.pending_replies()[0]['body'])
        self.assertNotIn('正在阅读',self.store.pending_replies()[0]['body'])
        self.assertEqual(self.store.get_message('om_one')['read_state'],'pending')

    async def test_unknown_source_and_injected_mention_rejected(self):
        self.store.ingest(message(mentions=[{'id':'ou_self'}]))
        for action in ({'message_id':'om_private','kind':'reply','text':'secret'},
                       {'message_id':'om_one','kind':'reply','text':'<at user_id="ou_private">hey</at>'}):
            with self.subTest(action=action):
                self.store.db.execute("UPDATE messages SET read_state='pending'")
                self.store.db.commit()
                self.assertEqual(await Worker(self.store,Agent([action])).tick(self.group),1)
                self.assertEqual(self.store.pending_replies(),[])

    async def test_large_message_is_read_in_complete_chunks_without_lost_tail(self):
        config = Config.from_dict(settings(self.tmp.name,max_batch_chars=256))
        self.store.config = config
        text = '正文'*400+'最后重要任务'
        self.store.ingest(message(content=text))
        agent = Agent()
        worker = Worker(self.store,agent)
        for _ in range(8):
            await worker.tick(self.group)
        reconstructed = ''.join(m['content'] for m in agent.seen)
        self.assertEqual(reconstructed,text)
        self.assertEqual(self.store.get_message('om_one')['read_state'],'read')

    async def test_edit_during_inference_is_not_marked_read_by_stale_result(self):
        store = self.store
        class EditingAgent(Agent):
            async def read(self,*args):
                store.ingest(message(content='new question',update_time='4000'))
                return {'summary':'old','actions':[]}
        self.store.ingest(message())
        self.assertEqual(await Worker(self.store,EditingAgent()).tick(self.group),1)
        self.assertEqual(self.store.get_message('om_one')['read_state'],'pending')
        self.assertEqual(self.store.get_message('om_one')['read_offset'],0)

    async def test_failed_model_does_not_mark_messages_read(self):
        class FailingAgent(Agent):
            async def read(self,*args):
                raise RuntimeError('inference failed')
        self.store.ingest(message())
        self.assertEqual(await Worker(self.store,FailingAgent()).tick(self.group),0)
        self.assertEqual(self.store.get_message('om_one')['read_state'],'pending')
        self.assertIn('inference failed',self.store.get_message('om_one')['last_error'] or '')
