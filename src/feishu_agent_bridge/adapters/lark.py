"""Async lark-cli adapter; shell-free argv and owned-process shutdown."""
import asyncio
import json
import os
import re
from feishu_agent_bridge.config import resolve_lark_binary


class TransportError(Exception):
    def __init__(self, message, uncertain=False):
        super().__init__(message)
        self.uncertain = uncertain


def normalize_history(item):
    sender = item.get('sender') or {}
    content = item.get('body',{}).get('content','')
    try:
        parsed = json.loads(content)
        if isinstance(parsed,dict) and isinstance(parsed.get('text'),str):
            content = parsed['text']
        else:
            content = json.dumps(parsed,ensure_ascii=False)
    except (ValueError,TypeError):
        pass
    return {'chat_id': item.get('chat_id'), 'message_id': item.get('message_id'),
            'create_time': item.get('create_time'), 'update_time': item.get('update_time'),
            'sender_id': sender.get('id'), 'sender_type': 'bot' if sender.get('sender_type')=='app' else sender.get('sender_type'),
            'message_type': item.get('msg_type','unknown'), 'content': content,
            'mentions': item.get('mentions') or [], 'root_id': item.get('root_id'),
            'reply_to': item.get('parent_id'), 'deleted': item.get('deleted',False)}


async def terminate(proc):
    if proc.returncode is None:
        try:
            proc.terminate()
        except ProcessLookupError:
            return
        try:
            await asyncio.wait_for(proc.wait(),10)
        except asyncio.TimeoutError as exc:
            # Never kill an event consumer and leak its server subscription.
            raise TransportError('owned CLI process did not exit within 10 seconds') from exc


class Lark:
    def __init__(self,config):
        self.config = config
        self.binary = resolve_lark_binary(config.lark_cli)
        self.env = os.environ | {'LARKSUITE_CLI_NO_UPDATE_NOTIFIER':'1','LARKSUITE_CLI_NO_SKILLS_NOTIFIER':'1'}

    async def call(self,args,*,write=False):
        try:
            proc = await asyncio.create_subprocess_exec(self.binary,*args,'--profile',self.config.profile,
                   '--format','json',stdin=asyncio.subprocess.DEVNULL,stdout=asyncio.subprocess.PIPE,
                   stderr=asyncio.subprocess.PIPE,env=self.env)
        except OSError as exc:
            raise TransportError(f'CLI could not start: {exc}') from exc
        try:
            stdout,stderr = await asyncio.wait_for(proc.communicate(),20)
        except (asyncio.TimeoutError,asyncio.CancelledError) as exc:
            try:
                await terminate(proc)
            except TransportError:
                pass  # Cleanup cannot turn ambiguous write acceptance into a known rejection.
            if isinstance(exc,asyncio.CancelledError):
                raise
            raise TransportError('CLI operation timed out; acceptance unknown' if write else 'CLI read timed out',uncertain=write) from exc
        try:
            result = json.loads(stdout)
        except (ValueError,UnicodeDecodeError) as exc:
            raise TransportError('CLI returned invalid JSON',uncertain=write) from exc
        accepted = isinstance(result,dict) and result.get('ok') and isinstance(result.get('data'),dict) and result['data'].get('message_id')
        if proc.returncode or not isinstance(result,dict) or not result.get('ok'):
            if write and accepted:
                return result['data']
            error = result.get('error',{}) if isinstance(result,dict) else {}
            rejected = (error.get('type') in ('auth','validation') or error.get('subtype') in
                        ('missing_scope','missing_scopes','invalid_params','permission_denied','missing_token'))
            raise TransportError('CLI API failure: '+str(error.get('type','unknown'))+'/'+str(error.get('subtype',error.get('code','unknown'))),uncertain=write and not rejected)
        return result.get('data',{})

    async def history(self,group,scan):
        params = {'container_id_type':'chat','container_id':group.chat_id,
                  'start_time':str(scan['start_ms']//1000),'end_time':str(scan['end_ms']//1000+1),
                  'page_size':50,'sort_type':'ByCreateTimeDesc'}
        if scan['page_token']:
            params['page_token'] = scan['page_token']
        return await self.call(['api','GET','/open-apis/im/v1/messages','--params',json.dumps(params),'--as','user'])

    async def original(self,mid,chat_id):
        data = await self.call(['api','GET','/open-apis/im/v1/messages/'+mid,'--as','user'])
        items = data.get('items',[])
        if (len(items)!=1 or items[0].get('message_id')!=mid or items[0].get('chat_id') != chat_id
            or not self.config.group(chat_id) or items[0].get('deleted')):
            raise TransportError('original message is unavailable or outside configured group')
        return items[0]

    async def members(self,chat_id):
        return await self.call(['im','+chat-members-list','--chat-id',chat_id,'--member-types','bot','--as','user'])

    async def reply(self,mid,body,mentions,key,*,chat_id):
        original = await self.original(mid,chat_id)
        sender = original.get('sender',{})
        allowed = {sender.get('id')}
        bot_open_id = None
        if sender.get('sender_type')=='app' or str(sender.get('id','')).startswith('cli_'):
            members = await self.members(chat_id)
            matches = [bot for bot in members.get('bots',[]) if bot.get('app_id')==sender.get('id')]
            if len(matches)!=1:
                raise TransportError('cannot uniquely resolve original bot in configured group')
            bot_open_id = matches[0].get('member_id','')
            if not re.fullmatch(r'ou_[A-Za-z0-9_-]+',bot_open_id):
                raise TransportError('invalid original bot member ID')
            allowed.add(bot_open_id)
        if any(m not in allowed for m in mentions):
            raise TransportError('reply mentions must identify original sender')
        prefix = ''
        for identity in mentions:
            if identity.startswith('cli_'):
                identity = bot_open_id or ''
            if re.fullmatch(r'ou_[A-Za-z0-9_-]+',identity):
                prefix += f'<at user_id="{identity}">sender</at> '
            else:
                raise TransportError('original sender has no valid application-scoped open ID')
        data = await self.call(['im','+messages-reply','--message-id',mid,'--text',prefix+body,
                               '--as','bot','--idempotency-key',key],write=True)
        external_id = data.get('message_id') or data.get('message',{}).get('message_id')
        if not external_id:
            raise TransportError('reply accepted without message ID',uncertain=True)
        return external_id

    async def events(self,stop,received):
        while not stop.is_set():
            proc = None
            try:
                proc = await asyncio.create_subprocess_exec(self.binary,'event','consume','im.message.receive_v1',
                     '--profile',self.config.profile,'--as','bot','--timeout','24h',
                     stdin=asyncio.subprocess.DEVNULL,stdout=asyncio.subprocess.PIPE,
                     stderr=asyncio.subprocess.PIPE,env=self.env,limit=1024*1024)
                async def stderr_reader():
                    while line := await proc.stderr.readline():
                        text = line.decode(errors='replace')
                        if '[event] ready' in text or 'feishu-websocket: connected' in text:
                            await received.put({'control':'connected'})
                        elif 'disconnected' in text:
                            await received.put({'control':'disconnected'})
                reader = asyncio.create_task(stderr_reader())
                try:
                    while line := await proc.stdout.readline():
                        try:
                            event = json.loads(line)
                        except ValueError:
                            continue
                        await received.put(event)
                    await proc.wait()
                finally:
                    reader.cancel()
                    await asyncio.gather(reader,return_exceptions=True)
                    await terminate(proc)
                await received.put({'control':'disconnected','error':f'event consumer exited {proc.returncode}'})
            except asyncio.CancelledError:
                if proc is not None:
                    await terminate(proc)
                raise
            except Exception as exc:
                await received.put({'control':'disconnected','error':str(exc)[:800]})
            try:
                await asyncio.wait_for(stop.wait(),5)
            except asyncio.TimeoutError:
                pass
