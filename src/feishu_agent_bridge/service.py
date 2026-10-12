"""Only this instance's user service is managed; no shared CLI bus control."""
import fcntl
import os
from pathlib import Path
import plistlib
import shlex
import subprocess
import sys
from feishu_agent_bridge.config import private_write
from feishu_agent_bridge.store import stable_id


def label(config):
    return 'io.feishu-agent-bridge.'+stable_id(config.profile,config.bot_app_id,str(config.data_dir))[:12]


def render_launch_agent(config,config_path,python):
    return {'Label':label(config),'ProgramArguments':[python,'-m','feishu_agent_bridge','--config',str(config_path),'run'],
            'RunAtLoad':True,'KeepAlive':True,'ThrottleInterval':10,'ProcessType':'Background','ExitTimeOut':40,
            'EnvironmentVariables':{'PATH':os.environ.get('PATH','/usr/bin:/bin'),'PYTHONUNBUFFERED':'1'},
            'StandardOutPath':str(config.data_dir/'service.log'),'StandardErrorPath':str(config.data_dir/'service.log')}


def render_systemd(config_path,python):
    # systemd uses its own quoting; percent is a specifier even inside quotes.
    def quote(value):
        return '"'+str(value).replace('\\','\\\\').replace('"','\\"').replace('%','%%')+'"'
    return ('[Unit]\nDescription=Feishu Agent Bridge\nAfter=network-online.target\n\n[Service]\nType=simple\n'+
            'ExecStart='+quote(python)+' -m feishu_agent_bridge --config '+quote(config_path)+' run\n'+
            'Restart=on-failure\nRestartSec=10\nTimeoutStopSec=40\nUMask=0077\n\n[Install]\nWantedBy=default.target\n')


def is_running(config):
    path = config.data_dir/'service.lock'
    if not path.exists():
        return False
    with path.open() as handle:
        try:
            fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
            return False
        except BlockingIOError:
            try:
                pid = int(handle.read())
                os.kill(pid,0)
                return True
            except (ValueError,ProcessLookupError,PermissionError):
                return False


def command(args,*,allow_failure=False):
    result = subprocess.run(args,capture_output=True,text=True,timeout=45)
    if result.returncode and not allow_failure:
        raise RuntimeError('service manager failed: '+(result.stderr or result.stdout)[-1000:])
    return result


def start(config,config_path):
    config_path = Path(config_path).resolve()
    config.data_dir.mkdir(parents=True,exist_ok=True,mode=0o700)
    if sys.platform=='darwin':
        path = Path.home()/'Library'/'LaunchAgents'/(label(config)+'.plist')
        private_write(path,plistlib.dumps(render_launch_agent(config,config_path,sys.executable)).decode())
        target = f'gui/{os.getuid()}'
        if command(['/bin/launchctl','print',target+'/'+label(config)],allow_failure=True).returncode:
            command(['/bin/launchctl','bootstrap',target,str(path)])
        return {'label':label(config),'service_file':str(path)}
    if sys.platform.startswith('linux'):
        path = Path.home()/'.config'/'systemd'/'user'/(label(config)+'.service')
        private_write(path,render_systemd(config_path,sys.executable))
        command(['systemctl','--user','daemon-reload'])
        command(['systemctl','--user','enable','--now',path.name])
        return {'label':label(config),'service_file':str(path)}
    raise RuntimeError('user service currently supports macOS/Linux; use run in a supervised terminal')


def stop(config):
    if sys.platform=='darwin':
        command(['/bin/launchctl','bootout',f'gui/{os.getuid()}/'+label(config)])
    elif sys.platform.startswith('linux'):
        command(['systemctl','--user','disable','--now',label(config)+'.service'])
    else:
        raise RuntimeError('stop the foreground run process using SIGTERM')
    return {'stopped':True,'label':label(config)}


def primary_wrapper(config_path):
    """A short shell-free command description for operators, never executed here."""
    return shlex.join([sys.executable,'-m','feishu_agent_bridge','--config',str(config_path),'run'])
