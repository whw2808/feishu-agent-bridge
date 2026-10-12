import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from feishu_agent_bridge.config import Config
from feishu_agent_bridge.service import render_launch_agent, render_systemd
from feishu_agent_bridge.store import Store
from tests.helpers import message, settings

ROOT = Path(__file__).resolve().parents[1]


class CliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.config = Config.from_dict(settings(self.tmp.name))
        self.path = Path(self.tmp.name)/'config.json'
        self.config.save(self.path)

    def command(self, *args):
        return subprocess.run([sys.executable, '-m', 'feishu_agent_bridge', '--config', str(self.path), *args],
                              cwd=ROOT, env=os.environ|{'PYTHONPATH':str(ROOT/'src')},
                              capture_output=True, text=True, timeout=10)

    def test_status_cli_returns_state_without_inference_or_resetting_live_jobs(self):
        store = Store(self.config)
        self.addCleanup(store.close)
        store.ingest(message())
        store.reserve_messages(['om_one'])
        result = self.command('status')
        self.assertTrue(result.stdout.strip(), 'CLI must return JSON status')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['messages']['reading'], 1)
        self.assertEqual(store.get_message('om_one')['read_state'], 'reading')

    def test_missing_config_is_explained_without_traceback(self):
        self.path.unlink()
        result = self.command('status')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('setup', result.stderr)
        self.assertNotIn('Traceback', result.stderr)

    def test_launch_service_uses_configurable_paths_and_only_owned_label(self):
        with patch.dict(os.environ, {'PATH': '/usr/bin:/bin'}):
            plist = render_launch_agent(self.config, self.path, sys.executable)
        self.assertIn('ProgramArguments', plist)
        self.assertIn(str(self.path), plist['ProgramArguments'])
        self.assertTrue(plist['Label'].startswith('io.feishu-agent-bridge.'))
        self.assertNotIn('/Users/whw', json.dumps(plist).replace(sys.executable, 'PYTHON'))
        unit = render_systemd(self.path, sys.executable)
        self.assertIn('ExecStart=', unit)
        self.assertIn(str(self.path), unit)
        self.assertNotIn('sudo', unit)

    def test_portable_plugin_and_marketplace_have_no_personal_configuration(self):
        self.assertTrue((ROOT/'plugin.json').is_file())
        plugin = json.loads((ROOT/'plugin.json').read_text())
        self.assertEqual(plugin['name'], 'feishu-agent-bridge')
        self.assertIn('agent-plugins.org', plugin['$schema'])
        mcp = json.loads((ROOT/'mcp.json').read_text())
        self.assertEqual(mcp['mcpServers']['feishu']['type'], 'stdio')
        for name in ('plugin.json','mcp.json','.agents/plugins/marketplace.json'):
            self.assertNotIn('/Users/whw', (ROOT/name).read_text())

    def test_inbox_claim_and_completion_roundtrip(self):
        store = Store(self.config)
        self.addCleanup(store.close)
        store.ingest(message(mentions=[{'id':'ou_self'}]))
        tid = store.create_task('om_one','change','coordinate change')
        result = self.command('claim', tid, '--owner','main-thread')
        self.assertTrue(result.stdout.strip())
        self.assertTrue(json.loads(result.stdout)['claimed'])
        result = self.command('complete', tid, '--owner','main-thread','--result','已验证完成')
        self.assertTrue(json.loads(result.stdout)['completed'])
        again = self.command('complete', tid, '--owner','main-thread','--result','重复完成')
        self.assertFalse(json.loads(again.stdout)['completed'])
        self.assertEqual(len(store.pending_replies()),1)
