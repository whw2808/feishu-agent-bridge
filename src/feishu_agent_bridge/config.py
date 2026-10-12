"""Validated portable configuration. Credentials stay in lark-cli."""
from dataclasses import asdict, dataclass, fields
import json
import math
import os
from pathlib import Path
import re
import shutil
import time


def private_write(path: Path, data: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temp = path.with_name(path.name + f'.{os.getpid()}.tmp')
    with os.fdopen(os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), 'w') as handle:
        handle.write(data)
    os.replace(temp, path)
    path.chmod(0o600)


@dataclass(frozen=True)
class Group:
    chat_id: str
    project_dir: str
    start_ms: int
    brief: str = 'Read project documentation and coordinate; do not change project files.'


@dataclass(frozen=True)
class Config:
    profile: str
    bot_open_id: str
    bot_app_id: str
    groups: tuple[Group, ...]
    data_dir: Path
    lark_cli: str = 'lark-cli'
    codex_bin: str = 'codex'
    model: str | None = None
    poll_seconds: float = 30
    read_seconds: float = 60
    ack_seconds: float = 10
    model_timeout_seconds: float = 120
    max_jobs_per_hour: int = 60
    max_batch_chars: int = 16000
    replies_per_minute: int = 6
    max_thread_replies: int = 4
    primary_session: str | None = None
    auto_handoff: bool = False

    @classmethod
    def from_dict(cls, data):
        if not isinstance(data, dict):
            raise ValueError('configuration must be an object')
        unknown = set(data) - {f.name for f in fields(cls)}
        if unknown:
            raise ValueError('unknown configuration keys: ' + ', '.join(sorted(unknown)))
        for key, prefix in (('profile', ''), ('bot_open_id', 'ou_'), ('bot_app_id', 'cli_')):
            value = data.get(key)
            if not isinstance(value, str) or not value.strip() or not value.startswith(prefix):
                raise ValueError(f'invalid {key}')
        groups = data.get('groups')
        if not isinstance(groups, list) or not groups:
            raise ValueError('at least one explicitly configured group is required')
        parsed = []
        for entry in groups:
            if not isinstance(entry, dict) or not re.fullmatch(r'oc_[A-Za-z0-9_-]+', entry.get('chat_id', '')):
                raise ValueError('invalid configured chat_id')
            root = Path(entry.get('project_dir', '')).expanduser().resolve()
            if not entry.get('project_dir') or not root.is_dir():
                raise ValueError('project_dir must be an existing directory')
            start = entry.get('start_ms', int(time.time()*1000))
            if type(start) is not int or start < 0:
                raise ValueError('start_ms must be a nonnegative integer')
            parsed.append(Group(entry['chat_id'], str(root), start, str(entry.get('brief', Group.brief))))
        if len({g.chat_id for g in parsed}) != len(parsed):
            raise ValueError('duplicate configured group')
        if not isinstance(data.get('data_dir'), str) or not data['data_dir']:
            raise ValueError('data_dir is required')
        config = cls(**(data | {'groups': tuple(parsed), 'data_dir': Path(data['data_dir']).expanduser().resolve()}))
        for key in ('poll_seconds', 'read_seconds', 'ack_seconds', 'model_timeout_seconds', 'max_jobs_per_hour',
                    'max_batch_chars', 'replies_per_minute', 'max_thread_replies'):
            value = getattr(config, key)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
                raise ValueError(f'{key} must be finite and positive')
        for key in ('max_jobs_per_hour', 'max_batch_chars', 'replies_per_minute', 'max_thread_replies'):
            if type(getattr(config, key)) is not int:
                raise ValueError(f'{key} must be an integer')
        if config.max_batch_chars < 256:
            raise ValueError('max_batch_chars must be at least 256')
        if type(config.auto_handoff) is not bool:
            raise ValueError('auto_handoff must be boolean')
        if config.primary_session is not None and not re.fullmatch(r'[A-Za-z0-9_-]{8,128}', config.primary_session):
            raise ValueError('invalid primary_session')
        if config.auto_handoff and not config.primary_session:
            raise ValueError('auto_handoff requires primary_session')
        for key in ('lark_cli', 'codex_bin'):
            if not isinstance(getattr(config, key), str) or not getattr(config, key):
                raise ValueError(f'{key} is required')
        return config

    @classmethod
    def load(cls, path):
        return cls.from_dict(json.loads(Path(path).expanduser().read_text()))

    def save(self, path):
        data = asdict(self)
        data['data_dir'] = str(self.data_dir)
        private_write(Path(path), json.dumps(data, ensure_ascii=False, indent=2)+'\n')

    def group(self, chat_id):
        return next((g for g in self.groups if g.chat_id == chat_id), None)


def default_config_path():
    return Path(os.environ.get('XDG_CONFIG_HOME', Path.home()/'.config'))/'feishu-agent-bridge'/'config.json'


def resolve_lark_binary(value):
    """The Node launcher doesn't forward SIGTERM; use its native child."""
    resolved = Path(shutil.which(value) or value).expanduser().resolve()
    if resolved.name == 'run.js' and resolved.parent.name == 'scripts':
        native = resolved.parent.parent/'bin'/'lark-cli'
        if native.is_file() and os.access(native, os.X_OK):
            return str(native)
    return str(resolved)
