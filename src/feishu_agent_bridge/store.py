"""Durable message inbox, scan cursors, response outbox and developer tasks."""
import hashlib
import json
import os
import re
import sqlite3
import time


def now_ms():
    return int(time.time()*1000)


def stable_id(*parts):
    return hashlib.sha256(json.dumps(parts, ensure_ascii=False).encode()).hexdigest()[:32]


class Store:
    def __init__(self, config, *, recover=False):
        self.config = config
        config.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        config.data_dir.chmod(0o700)
        path = config.data_dir/'state.sqlite3'
        if not path.exists():
            os.close(os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600))
        path.chmod(0o600)
        self.db = sqlite3.connect(path, timeout=3)
        self.db.row_factory = sqlite3.Row
        self.db.executescript('''
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS messages(
              message_id TEXT PRIMARY KEY,chat_id TEXT NOT NULL,sender_id TEXT NOT NULL,
              sender_type TEXT NOT NULL,created_ms INTEGER NOT NULL,update_ms INTEGER NOT NULL,
              content TEXT NOT NULL,kind TEXT NOT NULL,addressed INTEGER NOT NULL,own INTEGER NOT NULL,
              root_id TEXT,parent_id TEXT,payload TEXT NOT NULL,read_state TEXT NOT NULL,
              read_offset INTEGER NOT NULL DEFAULT 0,last_error TEXT,attempts INTEGER NOT NULL DEFAULT 0,
              next_attempt_ms INTEGER NOT NULL DEFAULT 0,received_ms INTEGER NOT NULL,ack_due_ms INTEGER);
            CREATE TABLE IF NOT EXISTS versions(message_id TEXT NOT NULL,update_ms INTEGER NOT NULL,
              payload TEXT NOT NULL,PRIMARY KEY(message_id,update_ms));
            CREATE TABLE IF NOT EXISTS replies(id TEXT PRIMARY KEY,message_id TEXT NOT NULL,
              version INTEGER NOT NULL,chat_id TEXT NOT NULL,root_id TEXT NOT NULL,kind TEXT NOT NULL,
              body TEXT NOT NULL,mentions TEXT NOT NULL,state TEXT NOT NULL,created_ms INTEGER NOT NULL,
              sent_ms INTEGER,external_id TEXT,last_error TEXT);
            CREATE TABLE IF NOT EXISTS tasks(id TEXT PRIMARY KEY,message_id TEXT NOT NULL,
              chat_id TEXT NOT NULL,title TEXT NOT NULL,detail TEXT NOT NULL,state TEXT NOT NULL,
              owner TEXT,result TEXT,created_ms INTEGER NOT NULL,
              delivery_state TEXT NOT NULL DEFAULT 'pending',queue_id TEXT,last_error TEXT);
            CREATE INDEX IF NOT EXISTS messages_pending ON messages(chat_id,read_state,addressed,created_ms);
            CREATE TABLE IF NOT EXISTS model_runs(started_ms INTEGER NOT NULL);
        ''')
        binding = json.dumps({k: getattr(config,k) for k in ('profile','bot_open_id','bot_app_id')}, sort_keys=True)
        if self.meta('binding') not in (None,binding):
            self.db.close()
            raise ValueError('database is bound to another application/profile identity')
        self.set_meta('binding',binding)
        if recover:
            with self.db:
                self.db.execute("UPDATE messages SET read_state='pending' WHERE read_state='reading'")
                self.db.execute("UPDATE replies SET state='uncertain',last_error='stopped before acknowledgment' WHERE state='sending'")
                self.db.execute("UPDATE tasks SET delivery_state='uncertain' WHERE delivery_state='sending'")

    def close(self):
        self.db.close()

    def meta(self,key,default=None):
        row = self.db.execute('SELECT value FROM metadata WHERE key=?',(key,)).fetchone()
        return row[0] if row else default

    def set_meta(self,key,value):
        with self.db:
            self.db.execute('INSERT INTO metadata VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',(key,str(value)))

    def ingest(self,event):
        if not isinstance(event,dict):
            return False
        group = self.config.group(event.get('chat_id'))
        mid,sender = event.get('message_id'),event.get('sender_id')
        mentions = event.get('mentions',[])
        if (not group or not isinstance(mid,str) or not re.fullmatch(r'om_[A-Za-z0-9_-]+',mid)
            or not isinstance(sender,str) or not sender or event.get('sender_type') not in ('user','bot')
            or not isinstance(mentions,list) or not isinstance(event.get('content'),str)):
            return False
        try:
            created = int(event['create_time'])
            updated = max(created,int(event.get('update_time') or created))
        except (KeyError,ValueError,TypeError):
            return False
        if created < group.start_ms:
            return False
        old = self.get_message(mid)
        if old and (old['chat_id'] != group.chat_id or old['sender_id'] != sender or old['update_ms'] >= updated):
            return False
        own = sender in (self.config.bot_open_id,self.config.bot_app_id)
        parent = event.get('reply_to') or event.get('parent_id')
        parent_row = self.get_message(parent) if parent else None
        addressed = any(isinstance(m,dict) and (m.get('id') == self.config.bot_open_id or
                        (isinstance(m.get('id'),dict) and m['id'].get('open_id') == self.config.bot_open_id))
                        for m in mentions) or bool(parent_row and parent_row['own'])
        kind = event.get('message_type','text')
        supported = kind in ('text','post','image','file','audio','video','media','system')
        payload = json.dumps(event,ensure_ascii=False)
        state = 'own' if own else 'pending' if supported else 'unsupported'
        with self.db:
            self.db.execute('INSERT OR IGNORE INTO versions VALUES (?,?,?)',(mid,updated,payload))
            self.db.execute('''INSERT INTO messages
              (message_id,chat_id,sender_id,sender_type,created_ms,update_ms,content,kind,addressed,
               own,root_id,parent_id,payload,read_state,received_ms)
              VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(message_id) DO UPDATE SET
              update_ms=excluded.update_ms,content=excluded.content,payload=excluded.payload,
              kind=excluded.kind,addressed=excluded.addressed,read_state=excluded.read_state,
              read_offset=0,last_error=NULL,attempts=0,next_attempt_ms=0,ack_due_ms=NULL''',
              (mid,group.chat_id,sender,event['sender_type'],created,updated,event['content'],kind,
               int(addressed),int(own),event.get('root_id'),parent,payload,state,now_ms()))
        return True

    def get_message(self,mid):
        row = self.db.execute('SELECT * FROM messages WHERE message_id=?',(mid,)).fetchone()
        return dict(row) if row else None

    def pending_messages(self,chat_id,limit=50,*,addressed_only=False):
        if not self.config.group(chat_id):
            return []
        rows = self.db.execute("SELECT * FROM messages WHERE chat_id=? AND read_state='pending' AND next_attempt_ms<=? "
             + ('AND addressed=1 ' if addressed_only else '')+'ORDER BY addressed DESC,created_ms,message_id LIMIT ?',
             (chat_id,now_ms(),limit)).fetchall()
        return [dict(r) for r in rows]

    def reserve_messages(self,ids):
        with self.db:
            for mid in ids:
                self.db.execute("UPDATE messages SET read_state='reading',ack_due_ms=? WHERE message_id=? AND read_state='pending'",
                                (now_ms()+int(self.config.ack_seconds*1000),mid))

    def mark_read(self,ids,*,versions=None,offsets=None):
        with self.db:
            for mid in ids:
                current = self.get_message(mid)
                if not current or (versions is not None and current['update_ms'] != versions[mid]):
                    continue
                offset = offsets[mid] if offsets is not None else len(current['content'])
                state = 'read' if offset >= len(current['content']) else 'pending'
                self.db.execute('UPDATE messages SET read_state=?,read_offset=?,ack_due_ms=NULL,last_error=NULL WHERE message_id=?',(state,offset,mid))

    def reading_failed(self,ids,error):
        with self.db:
            for mid in ids:
                row = self.get_message(mid)
                if row and row['read_state'] == 'reading':
                    self.db.execute("UPDATE messages SET read_state='pending',last_error=?,attempts=attempts+1,next_attempt_ms=?,ack_due_ms=NULL WHERE message_id=?",
                      (str(error)[:800],now_ms()+min(300000,5000*2**min(row['attempts'],6)),mid))

    def enqueue_reply(self,mid,kind,body,mentions,*,version=None):
        source = self.get_message(mid)
        if not source or not self.config.group(source['chat_id']):
            raise ValueError('reply source outside configured scope')
        version = source['update_ms'] if version is None else version
        rid = stable_id(mid,version,kind)
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO replies(id,message_id,version,chat_id,root_id,kind,body,mentions,state,created_ms) VALUES (?,?,?,?,?,?,?,?,'pending',?)",
              (rid,mid,version,source['chat_id'],source['root_id'] or mid,kind,body,json.dumps(mentions),now_ms()))
        return rid

    def reply(self,rid):
        row = self.db.execute('SELECT * FROM replies WHERE id=?',(rid,)).fetchone()
        return dict(row) if row else None

    def pending_replies(self):
        return [dict(r) for r in self.db.execute("SELECT * FROM replies WHERE state='pending' ORDER BY created_ms,id")]

    def claim_reply(self,rid):
        with self.db:
            return bool(self.db.execute("UPDATE replies SET state='sending' WHERE id=? AND state='pending'",(rid,)).rowcount)

    def finish_reply(self,rid,state,external_id=None,error=None):
        with self.db:
            self.db.execute('UPDATE replies SET state=?,external_id=?,last_error=?,sent_ms=? WHERE id=?',
                            (state,external_id,error,now_ms() if state == 'sent' else None,rid))

    def create_task(self,mid,title,detail):
        source = self.get_message(mid)
        if not source or not self.config.group(source['chat_id']):
            raise ValueError('task source outside configured scope')
        tid = stable_id(mid,source['update_ms'],'task')
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO tasks(id,message_id,chat_id,title,detail,state,created_ms) VALUES (?,?,?,?,?,'pending',?)",
                            (tid,mid,source['chat_id'],title,detail,now_ms()))
        return tid

    def tasks(self,*,include_done=False):
        return [dict(r) for r in self.db.execute('SELECT * FROM tasks '+('' if include_done else "WHERE state!='done' ")+'ORDER BY created_ms')
                if self.config.group(r['chat_id'])]

    def claim_task(self,tid,owner):
        if not isinstance(owner,str) or not owner.strip():
            raise ValueError('task owner required')
        with self.db:
            return bool(self.db.execute("UPDATE tasks SET state='claimed',owner=? WHERE id=? AND state='pending'",(owner,tid)).rowcount)

    def complete_task(self,tid,owner,result):
        if not isinstance(result,str) or not result.strip() or len(result)>6000:
            raise ValueError('completion must be 1..6000 characters')
        with self.db:
            row = self.db.execute('SELECT * FROM tasks WHERE id=?',(tid,)).fetchone()
            if not row or row['state'] != 'claimed' or row['owner'] != owner:
                return False
            if not self.config.group(row['chat_id']):
                raise ValueError('task outside current configured scope')
            self.db.execute("UPDATE tasks SET state='done',result=? WHERE id=?",(result,tid))
            self.enqueue_reply(row['message_id'],'completed',result,[])
        return True

    def status(self):
        return {'messages': {r[0]:r[1] for r in self.db.execute('SELECT read_state,count(*) FROM messages GROUP BY read_state')},
                'replies': {r[0]:r[1] for r in self.db.execute('SELECT state,count(*) FROM replies GROUP BY state')},
                'pending_tasks': len(self.tasks()),
                'health': {r[0]:r[1] for r in self.db.execute("SELECT key,value FROM metadata WHERE key LIKE 'health:%'")}}
