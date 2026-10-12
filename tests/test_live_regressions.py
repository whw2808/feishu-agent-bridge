import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

from feishu_agent_bridge.collector import Collector
from feishu_agent_bridge.config import Config
from feishu_agent_bridge.store import Store
from tests.helpers import message, settings
from tests.test_transport import FakeLark, raw

ROOT = Path(__file__).resolve().parents[1]


class ParentTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.config = Config.from_dict(settings(self.tmp.name))
        self.store = Store(self.config)
        self.addCleanup(self.store.close)

    async def test_old_nonown_parent_releases_current_ordinary_child_without_backfill(self):
        class ParentLark(FakeLark):
            async def original(self, mid, chat):
                return raw(mid, create_time='900')
        child = message('om_child', reply_to='om_old')
        self.store.ingest(child)
        await Collector(self.store, ParentLark([])).resolve_parent(child)
        self.assertEqual(self.store.get_message('om_child')['read_state'], 'pending')
        self.assertFalse(self.store.get_message('om_child')['addressed'])
        self.assertIsNone(self.store.get_message('om_old'), 'old content must stay outside activation window')
        self.store.ingest(message('om_second', reply_to='om_old'))
        self.assertEqual(self.store.get_message('om_second')['read_state'], 'pending')

    async def test_failed_parent_lookup_is_retried_after_complete_history_cursor(self):
        class ParentLark(FakeLark):
            attempts = 0
            async def original(self, mid, chat):
                self.attempts += 1
                if self.attempts == 1:
                    raise ValueError('temporary read failure')
                return raw(mid, create_time='900')
        child = message('om_child', reply_to='om_old')
        self.store.ingest(child)
        transport = ParentLark([{'items': [], 'has_more': False}])
        collector = Collector(self.store, transport)
        await collector.resolve_parent(child)
        self.assertEqual(self.store.get_message('om_child')['read_state'], 'awaiting_parent')
        self.assertTrue(await collector.scan_group(self.config.groups[0]))
        self.assertEqual(self.store.get_message('om_child')['read_state'], 'pending')

    async def test_unverified_parent_group_or_id_cannot_promote_child(self):
        for changes in ({'chat_id': 'oc_other'}, {'message_id': 'om_unrelated'}):
            class ParentLark(FakeLark):
                async def original(self, mid, chat):
                    return raw(mid, sender={'id': 'cli_self', 'sender_type': 'app'}, **changes)
            child = message('om_child', reply_to='om_old')
            self.store.ingest(child)
            await Collector(self.store, ParentLark([])).resolve_parent(child)
            self.assertEqual(self.store.get_message('om_child')['read_state'], 'awaiting_parent')


class PackagingTests(unittest.TestCase):
    def test_marketplace_uses_clean_stage_instead_of_developer_worktree(self):
        marketplace = json.loads((ROOT/'.agents/plugins/marketplace.json').read_text())
        self.assertEqual(marketplace['plugins'][0]['source']['path'], './dist/plugin')

    def test_staging_and_zip_exclude_private_and_developer_files(self):
        spec = importlib.util.spec_from_file_location('package_plugin', ROOT/'scripts/package_plugin.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.assertTrue(hasattr(module, 'build'), 'packager must stage a clean plugin for native install')
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for name in ('plugin.json', 'mcp.json', 'LICENSE', 'README.md', 'README.zh-CN.md',
                         'skills/setup/SKILL.md', 'docs/operations.md', '.artifacts/history.json',
                         '.venv/auth.json', '.git/config', 'skills/setup/private.sqlite3'):
                target = root/name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text('{}')
            stage, archive = module.build(root)
            self.assertTrue(archive.is_file())
            self.assertEqual({str(p.relative_to(stage)) for p in stage.rglob('*') if p.is_file()},
                             {'plugin.json', 'mcp.json', 'LICENSE', 'README.md', 'README.zh-CN.md',
                              'skills/setup/SKILL.md', 'docs/operations.md'})

