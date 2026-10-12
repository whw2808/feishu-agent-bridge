import asyncio
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from feishu_agent_bridge.adapters.lark import Lark,TransportError
from feishu_agent_bridge.config import Config
from feishu_agent_bridge.delivery import Sender
from feishu_agent_bridge.store import Store
from feishu_agent_bridge.worker import Worker
from tests.helpers import message,settings
from tests.test_transport import FakeLark,raw
from tests.test_worker import Agent


class ReviewTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.config=Config.from_dict(settings(self.tmp.name))
        self.store=Store(self.config)
        self.addCleanup(self.store.close)
        self.group=self.config.groups[0]

    async def test_unverified_model_work_claims_cannot_be_sent(self):
        for i,text in enumerate(('已完成项目修改，所有测试通过并已合并。',
                                  'I fixed the code, all tests passed and the PR is merged.',
                                  '文件已更新，测试全部绿了。')):
            mid='om_claim'+str(i)
            self.store.ingest(message(mid,mentions=[{'id':'ou_self'}]))
            await Worker(self.store,Agent([{'message_id':mid,'kind':'reply','text':text}])).tick(self.group)
            transport=FakeLark([])
            await Sender(self.store,transport).dispatch()
            self.assertFalse(any(reply[1]==text for reply in transport.sent),text)
        self.assertTrue(self.store.tasks(), 'verification request must remain actionable')

    async def test_direct_outbox_cannot_bypass_model_completion_guard(self):
        self.store.ingest(message(mentions=[{'id':'ou_self'}]))
        rid=self.store.enqueue_reply('om_one','final','我已修复代码并提交合并。',[])
        await Sender(self.store,FakeLark([])).dispatch()
        self.assertEqual(self.store.reply(rid)['state'],'blocked')

    async def test_task_edit_cancellation_invalidates_claim_completion_and_delivery(self):
        self.store.ingest(message(mentions=[{'id':'ou_self'}]))
        tid=self.store.create_task('om_one','old task','change files')
        self.store.ingest(message(content='取消原请求，不要修改项目',update_time='3000'))
        self.assertEqual(self.store.tasks(),[])
        self.assertFalse(self.store.claim_task(tid,'main'))
        self.assertFalse(self.store.complete_task(tid,'main','已完成修改'))

    async def test_completion_keys_use_original_task_version(self):
        self.store.ingest(message(mentions=[{'id':'ou_self'}]))
        first=self.store.create_task('om_one','one','v1')
        self.assertTrue(self.store.claim_task(first,'main'))
        self.assertTrue(self.store.complete_task(first,'main','verified one'))
        self.store.ingest(message(update_time='3000',mentions=[{'id':'ou_self'}]))
        second=self.store.create_task('om_one','two','v2')
        self.assertTrue(self.store.claim_task(second,'main'))
        self.assertTrue(self.store.complete_task(second,'main','verified two'))
        replies=self.store.pending_replies()
        self.assertEqual(sorted(r['version'] for r in replies),[2000,3000])
        self.assertEqual({r.get('version') for r in self.store.tasks(include_done=True)}, {2000,3000})

    async def test_event_shutdown_with_full_queue_cancels_stderr_status_reader(self):
        class Proc:
            returncode=None
            def __init__(self):
                self.stdout=asyncio.StreamReader()
                self.stderr=asyncio.StreamReader()
                self.stderr.feed_data(b'[event] ready event_key=im.message.receive_v1\n')
            def terminate(self):
                self.returncode=0
                self.stdout.feed_eof()
                self.stderr.feed_eof()
            async def wait(self):
                return self.returncode
        queue=asyncio.Queue(maxsize=1)
        await queue.put({'full':True})
        proc=Proc()
        with patch('asyncio.create_subprocess_exec',return_value=proc):
            task=asyncio.create_task(Lark(self.config).events(asyncio.Event(),queue))
            await asyncio.sleep(.02)
            task.cancel()
            completed,pending=await asyncio.wait([task],timeout=.1)
            if pending:
                task.cancel()
                await asyncio.gather(task,return_exceptions=True)
            self.assertTrue(completed,'shutdown cannot depend on a stopped queue consumer')
            self.assertEqual(proc.returncode,0)

    async def test_child_before_parent_is_addressed_after_own_parent_arrives(self):
        self.store.ingest(message('om_child',reply_to='om_parent'))
        self.store.ingest(message('om_parent',sender_id='cli_self',sender_type='bot',create_time='1100'))
        self.assertTrue(self.store.get_message('om_child')['addressed'])

    async def test_pre_activation_own_parent_can_resolve_current_reply(self):
        self.store.ingest(message('om_child',reply_to='om_parent'))
        self.store.ingest(message('om_parent',sender_id='cli_self',sender_type='bot',create_time='900'))
        self.assertTrue(self.store.get_message('om_child')['addressed'])

    async def test_bot_open_id_event_and_app_id_history_have_one_canonical_sender(self):
        self.store.ingest(message(sender_id='ou_peer',sender_type='bot'))
        self.assertTrue(hasattr(self.store,'register_bots'),'trusted same-profile identity mapping is needed')
        self.store.register_bots('oc_team',[{'app_id':'cli_peer','member_id':'ou_peer'}])
        self.assertTrue(self.store.ingest(message(sender_id='cli_peer',sender_type='bot',update_time='3000',content='edited')))
        self.assertEqual(self.store.get_message('om_one')['sender_id'],'cli_peer')
        self.assertEqual(self.store.get_message('om_one')['content'],'edited')

    async def test_legitimate_event_bot_open_id_is_allowed_but_unrelated_id_rejected(self):
        class BotLark(Lark):
            async def call(self,args,*,write=False):
                if 'GET' in args:
                    return {'items':[raw(sender={'id':'cli_peer','sender_type':'app'})]}
                if '+chat-members-list' in args:
                    return {'bots':[{'app_id':'cli_peer','member_id':'ou_peer'}]}
                return {'message_id':'om_sent'}
        transport=BotLark(self.config)
        try:
            sent=await transport.reply('om_history','answer',['ou_peer'],'key',chat_id='oc_team')
        except TransportError:
            sent=None
        self.assertEqual(sent,'om_sent')
        with self.assertRaises(TransportError):
            await transport.reply('om_history','answer',['ou_unrelated'],'key',chat_id='oc_team')

    async def test_post_write_network_error_is_uncertain_but_acceptance_id_is_preserved(self):
        cli=Path(self.tmp.name)/'write-cli'
        config=Config.from_dict(settings(self.tmp.name,lark_cli=str(cli)))
        for result in ({'ok':False,'error':{'type':'network','subtype':'response_timeout'}},
                       {'ok':True,'data':{'message_id':'om_accepted'}}):
            cli.write_text('#!'+sys.executable+'\nimport json\nprint('+repr(json.dumps(result))+')\nraise SystemExit(1)\n')
            cli.chmod(0o700)
            if result['ok']:
                try:
                    value=await Lark(config).call(['write'],write=True)
                except TransportError:
                    value={}
                self.assertEqual(value.get('message_id'),'om_accepted')
            else:
                with self.assertRaises(TransportError) as error:
                    await Lark(config).call(['write'],write=True)
                self.assertTrue(error.exception.uncertain)

    async def test_long_addressed_message_cannot_starve_short_ordinary_message(self):
        self.store.config=Config.from_dict(settings(self.tmp.name,max_batch_chars=256))
        self.store.ingest(message('om_long',content='x'*16000,mentions=[{'id':'ou_self'}]))
        self.store.ingest(message('om_short',content='ordinary',create_time='3000'))
        worker=Worker(self.store,Agent())
        for _ in range(3):
            await worker.tick(self.group)
        self.assertEqual(self.store.get_message('om_short')['read_state'],'read')
