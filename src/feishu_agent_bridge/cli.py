"""Portable CLI for setup, supervision, status and development inbox."""
import argparse
import asyncio
from dataclasses import asdict
import json
import logging
import os
from pathlib import Path
import shutil
import sys
import time
from feishu_agent_bridge.config import Config,default_config_path,resolve_lark_binary
from feishu_agent_bridge.service import is_running,start,stop
from feishu_agent_bridge.store import Store


def parser():
    p = argparse.ArgumentParser(prog='feishu-agent',description='Independent background Feishu collaboration')
    p.add_argument('--config',type=Path,default=default_config_path())
    sub = p.add_subparsers(dest='command',required=True)
    setup = sub.add_parser('setup',help='Create private configuration; existing CLI credentials are reused')
    for arg in ('profile','chat-id','bot-open-id','bot-app-id','project-dir'):
        setup.add_argument('--'+arg,required=True)
    setup.add_argument('--data-dir',type=Path)
    setup.add_argument('--since-ms',type=int)
    setup.add_argument('--model')
    setup.add_argument('--primary-session')
    setup.add_argument('--auto-handoff',action='store_true')
    for name in ('run','start','stop','status','doctor','inbox','summaries','mcp'):
        sub.add_parser(name)
    claim = sub.add_parser('claim')
    claim.add_argument('task_id')
    claim.add_argument('--owner',required=True)
    complete = sub.add_parser('complete')
    complete.add_argument('task_id')
    complete.add_argument('--owner',required=True)
    complete.add_argument('--result',required=True)
    bind = sub.add_parser('bind')
    bind.add_argument('session_id')
    bind.add_argument('--auto-handoff',action='store_true')
    retry = sub.add_parser('retry',help='Retry a known failed response; uncertain responses require reconciliation')
    retry.add_argument('reply_id')
    return p


async def doctor(config):
    from feishu_agent_bridge.adapters.lark import Lark
    from feishu_agent_bridge.collector import Collector
    checks = {'lark_binary':os.path.isfile(resolve_lark_binary(config.lark_cli)),
              'codex_binary':bool(shutil.which(config.codex_bin) or Path(config.codex_bin).is_file())}
    try:
        import openai_codex  # noqa: F401
        checks['agent_extra']=True
    except ImportError:
        checks['agent_extra']=False
    try:
        import mcp  # noqa: F401
        checks['mcp_extra']=True
    except ImportError:
        checks['mcp_extra']=False
    store = Store(config)
    try:
        lark = Lark(config)
        for group in config.groups:
            try:
                await lark.history(group,Collector(store,lark).new_scan(group))
                checks['history:'+group.chat_id]=True
            except Exception as exc:
                checks['history:'+group.chat_id]=str(exc)
    finally:
        store.close()
    return {'ok':all(value is True for value in checks.values()),'checks':checks,
            'note':'Event subscription and bot send permissions are verified by actual live acceptance, not inferred from history access.'}


def execute(args):
    path = args.config.expanduser().resolve()
    if args.command=='setup':
        if path.exists():
            raise ValueError('configuration already exists; edit it or choose another --config')
        state_root = Path(os.environ.get('XDG_STATE_HOME',Path.home()/'.local'/'state'))
        config = Config.from_dict({'profile':args.profile,'bot_open_id':args.bot_open_id,'bot_app_id':args.bot_app_id,
          'groups':[{'chat_id':args.chat_id,'project_dir':args.project_dir,
                     'start_ms':args.since_ms if args.since_ms is not None else int(time.time()*1000)}],
          'data_dir':str(args.data_dir or state_root/'feishu-agent-bridge'/args.profile),
          'lark_cli':resolve_lark_binary(shutil.which('lark-cli') or 'lark-cli'),
          'codex_bin':shutil.which('codex') or 'codex','model':args.model,
          'primary_session':args.primary_session,'auto_handoff':args.auto_handoff})
        config.save(path)
        return {'created':str(path),'next':'feishu-agent doctor, then start'}
    if not path.is_file():
        raise ValueError('configuration missing; run feishu-agent setup or pass --config')
    config = Config.load(path)
    if args.command=='run':
        from feishu_agent_bridge.runtime import run
        asyncio.run(run(config))
        return None
    if args.command=='mcp':
        from feishu_agent_bridge.mcp_server import serve
        serve(path)
        return None
    if args.command=='start':
        return start(config,path)
    if args.command=='stop':
        return stop(config)
    if args.command=='doctor':
        return asyncio.run(doctor(config))
    if args.command=='bind':
        data = asdict(config)
        data['data_dir']=str(config.data_dir)
        data['groups']=list(data['groups'])
        Config.from_dict(data|{'primary_session':args.session_id,'auto_handoff':args.auto_handoff}).save(path)
        return {'bound':args.session_id,'restart_required':True}
    store = Store(config)
    try:
        if args.command=='status':
            return store.status()|{'running':is_running(config),'data_dir':str(config.data_dir)}
        if args.command=='inbox':
            return {'tasks':store.tasks()}
        if args.command=='summaries':
            return {g.chat_id:store.meta('summary:'+g.chat_id,'') for g in config.groups}
        if args.command=='claim':
            return {'claimed':store.claim_task(args.task_id,args.owner)}
        if args.command=='complete':
            return {'completed':store.complete_task(args.task_id,args.owner,args.result)}
        if args.command=='retry':
            row = store.reply(args.reply_id)
            if not row or row['state']!='failed':
                raise ValueError('only known failed replies can be retried; reconcile uncertain acceptance first')
            with store.db:
                store.db.execute("UPDATE replies SET state='pending',last_error=NULL WHERE id=? AND state='failed'",(args.reply_id,))
            return {'retry_queued':args.reply_id}
    finally:
        store.close()


def main():
    logging.basicConfig(level=logging.INFO,format='%(asctime)s %(levelname)s %(name)s %(message)s',stream=sys.stderr)
    try:
        args = parser().parse_args()
        result = execute(args)
        if result is not None:
            print(json.dumps(result,ensure_ascii=False,indent=2))
        return 0 if args.command!='doctor' or result['ok'] else 1
    except (ValueError,RuntimeError,OSError,json.JSONDecodeError) as exc:
        print('feishu-agent: '+str(exc),file=sys.stderr)
        return 1
