import json
from pathlib import Path
import sys
import tempfile
import unittest

from feishu_agent_bridge.adapters.lark import Lark, TransportError, normalize_history
from feishu_agent_bridge.collector import Collector
from feishu_agent_bridge.config import Config
from feishu_agent_bridge.delivery import Sender
from feishu_agent_bridge.store import Store
from tests.helpers import message, settings


def raw(mid='om_history', **changes):
    return {'message_id': mid, 'chat_id': 'oc_team', 'create_time': '2200', 'msg_type': 'text',
            'body': {'content': json.dumps({'text': 'ordinary history'})},
            'sender': {'id': 'cli_other', 'sender_type': 'app'}, 'mentions': [], **changes}


class FakeLark:
    def __init__(self, pages):
        self.pages, self.calls, self.sent = list(pages), [], []

    async def history(self, group, scan):
        self.calls.append(dict(scan))
        item = self.pages.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    async def reply(self, mid, body, mentions, key, *, chat_id):
        self.sent.append((mid, body, mentions, key))
        return 'om_response'


class TransportTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.config = Config.from_dict(settings(self.tmp.name))
        self.store = Store(self.config)
        self.addCleanup(self.store.close)

    def test_history_normalization_preserves_bot_identity_and_rich_content(self):
        normalized = normalize_history(raw())
        self.assertEqual(normalized.get('content'), 'ordinary history')
        self.assertEqual(normalized['sender_type'], 'bot')
        post = raw(msg_type='post', body={'content': json.dumps({'zh_cn': {'title': '通知',
                          'content': [[{'tag': 'text', 'text': '接口变更'}]]}})})
        self.assertIn('接口变更', normalize_history(post)['content'])

    async def test_history_reads_unmentioned_messages_and_deduplicates_live_event(self):
        self.store.ingest(message('om_existing'))
        transport = FakeLark([{'items': [raw('om_history')], 'has_more': False}])
        collector = Collector(self.store, transport)
        self.assertTrue(await collector.scan_group(self.config.groups[0]))
        self.assertEqual(len(self.store.pending_messages('oc_team')), 2)
        self.assertTrue(self.store.meta('history:oc_team:complete'))

    async def test_mid_page_failure_keeps_complete_cursor_and_rebuilds_page_gate(self):
        transport = FakeLark([{'items': [raw('om_newest')], 'has_more': True, 'page_token': 'older'},
                              TransportError('page expired')])
        collector = Collector(self.store, transport)
        self.assertTrue(await collector.scan_group(self.config.groups[0]))
        self.assertTrue(self.store.meta('history:oc_team:scan'))
        self.assertFalse(await collector.scan_group(self.config.groups[0]))
        self.assertIsNone(self.store.meta('history:oc_team:complete'))
        scan = json.loads(self.store.meta('history:oc_team:scan'))
        self.assertEqual(scan['page_token'], '')
        transport.pages = [{'items': [raw('om_newest'), raw('om_oldest')], 'has_more': False}]
        self.assertTrue(await collector.scan_group(self.config.groups[0]))
        self.assertEqual(len(self.store.pending_messages('oc_team')), 2)

    async def test_cli_argv_keeps_untrusted_text_literal(self):
        executable = Path(self.tmp.name)/'fake-lark'
        executable.write_text('#!'+sys.executable+'\nimport json,sys\nprint(json.dumps({"ok":True,"data":{"args":sys.argv[1:]}}))\n')
        executable.chmod(0o700)
        config = Config.from_dict(settings(self.tmp.name, lark_cli=str(executable)))
        text = 'literal $(touch /bad) `do-not-run`\nhello'
        result = await Lark(config).call(['probe', '--text', text])
        self.assertEqual(result.get('args', [])[:3], ['probe', '--text', text])
        self.assertIn('test-profile', result['args'])

    async def test_durable_reply_dispatches_once_and_retains_source(self):
        self.store.ingest(message(mentions=[{'id': 'ou_self'}]))
        rid = self.store.enqueue_reply('om_one', 'final', '已核对接口。', ['ou_other'])
        transport = FakeLark([])
        sender = Sender(self.store, transport)
        self.assertEqual(await sender.dispatch(), 1)
        self.assertEqual(self.store.reply(rid)['state'], 'sent')
        self.assertEqual(transport.sent[0][0], 'om_one')
        self.assertEqual(await sender.dispatch(), 0)

    async def test_sender_rejects_injected_mentions_and_unknown_target(self):
        self.store.ingest(message(mentions=[{'id': 'ou_self'}]))
        rid = self.store.enqueue_reply('om_one', 'final', '<at user_id="ou_private">x</at>', ['ou_stranger'])
        sender = Sender(self.store, FakeLark([]))
        await sender.dispatch()
        self.assertEqual(self.store.reply(rid)['state'], 'blocked')

    async def test_ambiguous_delivery_is_retained_and_not_retried(self):
        class Uncertain(FakeLark):
            async def reply(self, *args):
                raise TransportError('network timed out after write', uncertain=True)
        self.store.ingest(message(mentions=[{'id': 'ou_self'}]))
        rid = self.store.enqueue_reply('om_one', 'final', 'answer', [])
        sender = Sender(self.store, Uncertain([]))
        await sender.dispatch()
        self.assertEqual(self.store.reply(rid)['state'], 'uncertain')
        self.assertEqual(await sender.dispatch(), 0)

    async def test_bot_app_id_is_resolved_to_real_group_member_for_true_mention(self):
        class BotLark(Lark):
            def __init__(self, config):
                super().__init__(config)
                self.text = ''

            async def call(self, args, *, write=False):
                if 'GET' in args:
                    return {'items': [raw(sender={'id': 'cli_other', 'sender_type': 'app'})]}
                if '+chat-members-list' in args:
                    return {'bots': [{'app_id': 'cli_other', 'member_id': 'ou_other_bot', 'name': 'peer'}]}
                self.text = args[args.index('--text')+1]
                return {'message_id': 'om_sent'}
        transport = BotLark(self.config)
        await transport.reply('om_history', '回答', ['cli_other'], 'stable-key', chat_id='oc_team')
        self.assertIn('<at user_id="ou_other_bot">', transport.text)
        self.assertNotIn('user_id="cli_', transport.text)
