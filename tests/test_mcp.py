import os
from pathlib import Path
import sys
import tempfile
import unittest
from mcp import ClientSession,StdioServerParameters
from mcp.client.stdio import stdio_client
from feishu_agent_bridge.config import Config
from feishu_agent_bridge.store import Store
from tests.helpers import message,settings

ROOT = Path(__file__).resolve().parents[1]


class McpTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_stdio_server_reads_inbox_claims_and_completes_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg=Config.from_dict(settings(tmp))
            path=Path(tmp)/'config.json'
            cfg.save(path)
            store=Store(cfg)
            self.addCleanup(store.close)
            store.ingest(message(mentions=[{'id':'ou_self'}]))
            tid=store.create_task('om_one','review','check authorized changes')
            params=StdioServerParameters(command=sys.executable,args=['-m','feishu_agent_bridge','--config',str(path),'mcp'],env=os.environ|{'PYTHONPATH':str(ROOT/'src')})
            async with stdio_client(params) as (reader,writer), ClientSession(reader,writer) as session:
                await session.initialize()
                names={tool.name for tool in (await session.list_tools()).tools}
                self.assertIn('pending_tasks',names)
                result=await session.call_tool('pending_tasks',{})
                self.assertFalse(result.isError)
                self.assertIn(tid,str(result))
                claimed=await session.call_tool('claim_task',{'task_id':tid,'owner':'main-thread'})
                self.assertFalse(claimed.isError)
                completed=await session.call_tool('complete_task',{'task_id':tid,'owner':'main-thread','result':'检查已通过'})
                self.assertFalse(completed.isError)
                again=await session.call_tool('complete_task',{'task_id':tid,'owner':'main-thread','result':'重复'})
                self.assertIn('false',str(again).lower())
            self.assertEqual(len(store.pending_replies()),1)
