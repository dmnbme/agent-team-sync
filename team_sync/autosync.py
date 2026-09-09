"""Background sync independent of any agent: a scheduler runs `team-sync stop` every N minutes.
macOS: launchd user agent · Windows: Task Scheduler (pythonw, no console) · Linux: systemd user timer."""
import os
import platform
import subprocess
import sys

from .msg import t

LABEL = 'com.agent-team-sync.autosync'


def _log(cfg):
    return cfg.state_dir / 'autosync.log'


def _python():
    exe = sys.executable
    if platform.system() == 'Windows':
        w = os.path.join(os.path.dirname(exe), 'pythonw.exe')
        return w if os.path.exists(w) else exe
    return exe


def on(cfg):
    interval = int(cfg.data['sync'].get('autosync_interval_sec', 300)); cfg.state_dir.mkdir(parents=True, exist_ok=True)
    py, repo, log = _python(), str(cfg.repo), str(_log(cfg))
    shim = cfg.repo / 'bin' / 'team_sync.py'
    args = [str(shim), 'stop'] if shim.exists() else ['-m', 'team_sync', 'stop']
    sysname = platform.system()
    if sysname == 'Darwin':
        plist = os.path.expanduser(f'~/Library/LaunchAgents/{LABEL}.{cfg.repo.name}.plist')
        os.makedirs(os.path.dirname(plist), exist_ok=True)
        with open(plist, 'w', encoding='utf-8') as f:
            f.write(f'''<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>{LABEL}.{cfg.repo.name}</string>
  <key>ProgramArguments</key><array><string>{py}</string>{''.join(f'<string>{a}</string>' for a in args)}</array>
  <key>WorkingDirectory</key><string>{repo}</string>
  <key>StartInterval</key><integer>{interval}</integer>
  <key>RunAtLoad</key><true/>
  <key>StandardOutPath</key><string>{log}</string>
  <key>StandardErrorPath</key><string>{log}</string>
  <key>EnvironmentVariables</key><dict>
    <key>PATH</key><string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
    <key>HOME</key><string>{os.path.expanduser('~')}</string>
    <key>TEAM_SYNC_REPO</key><string>{repo}</string>
  </dict>
</dict></plist>''')
        uid = os.getuid()
        subprocess.run(['launchctl', 'bootout', f'gui/{uid}/{LABEL}.{cfg.repo.name}'], capture_output=True)
        r = subprocess.run(['launchctl', 'bootstrap', f'gui/{uid}', plist], capture_output=True, text=True)
        if r.returncode != 0:
            print(r.stderr.strip()); return 1
    elif sysname == 'Windows':
        task = f'team-sync autosync {cfg.repo.name}'
        ps = (f"$a = New-ScheduledTaskAction -Execute '{py}' -Argument '{' '.join(chr(34) + a + chr(34) if ' ' in a else a for a in args)}' -WorkingDirectory '{repo}'; "
              f"$tr = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Seconds {interval}) -RepetitionDuration ([TimeSpan]::MaxValue); "
              "$s = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Minutes 10) -MultipleInstances IgnoreNew -StartWhenAvailable; "
              f"Register-ScheduledTask -TaskName '{task}' -Action $a -Trigger $tr -Settings $s -Force | Out-Null")
        r = subprocess.run(['powershell', '-NoProfile', '-Command', ps], capture_output=True, text=True)
        if r.returncode != 0:
            print(r.stderr.strip()); return 1
    elif sysname == 'Linux':
        d = os.path.expanduser('~/.config/systemd/user'); os.makedirs(d, exist_ok=True)
        name = f'team-sync-{cfg.repo.name}'
        open(f'{d}/{name}.service', 'w').write(f'[Unit]\nDescription=team-sync autosync {repo}\n[Service]\nType=oneshot\nWorkingDirectory={repo}\nEnvironment=TEAM_SYNC_REPO={repo}\nExecStart={py} {' '.join(args)}\nStandardOutput=append:{log}\nStandardError=append:{log}\n')
        open(f'{d}/{name}.timer', 'w').write(f'[Unit]\nDescription=team-sync autosync timer\n[Timer]\nOnBootSec=1min\nOnUnitActiveSec={interval}s\n[Install]\nWantedBy=timers.target\n')
        subprocess.run(['systemctl', '--user', 'daemon-reload'], capture_output=True)
        r = subprocess.run(['systemctl', '--user', 'enable', '--now', f'{name}.timer'], capture_output=True, text=True)
        if r.returncode != 0:
            print(r.stderr.strip()); return 1
    else:
        print(t(cfg.lang, 'autosync_unsupported', os=sysname)); return 1
    print(t(cfg.lang, 'autosync_on', min=interval // 60, repo=repo, log=log)); return 0


def off(cfg):
    sysname = platform.system()
    if sysname == 'Darwin':
        subprocess.run(['launchctl', 'bootout', f'gui/{os.getuid()}/{LABEL}.{cfg.repo.name}'], capture_output=True)
        p = os.path.expanduser(f'~/Library/LaunchAgents/{LABEL}.{cfg.repo.name}.plist')
        if os.path.exists(p):
            os.remove(p)
    elif sysname == 'Windows':
        subprocess.run(['powershell', '-NoProfile', '-Command', f"Unregister-ScheduledTask -TaskName 'team-sync autosync {cfg.repo.name}' -Confirm:$false -ErrorAction SilentlyContinue"], capture_output=True)
    elif sysname == 'Linux':
        subprocess.run(['systemctl', '--user', 'disable', '--now', f'team-sync-{cfg.repo.name}.timer'], capture_output=True)
    print(t(cfg.lang, 'autosync_off')); return 0


def status(cfg):
    sysname = platform.system(); interval = int(cfg.data['sync'].get('autosync_interval_sec', 300)); running = False
    if sysname == 'Darwin':
        running = subprocess.run(['launchctl', 'print', f'gui/{os.getuid()}/{LABEL}.{cfg.repo.name}'], capture_output=True).returncode == 0
    elif sysname == 'Windows':
        running = subprocess.run(['powershell', '-NoProfile', '-Command', f"if (Get-ScheduledTask -TaskName 'team-sync autosync {cfg.repo.name}' -ErrorAction SilentlyContinue) {{ exit 0 }} else {{ exit 1 }}"], capture_output=True).returncode == 0
    elif sysname == 'Linux':
        running = subprocess.run(['systemctl', '--user', 'is-active', f'team-sync-{cfg.repo.name}.timer'], capture_output=True).returncode == 0
    print(t(cfg.lang, 'autosync_running', min=interval // 60, repo=cfg.repo, log=_log(cfg)) if running else t(cfg.lang, 'autosync_stopped')); return 0


def log(cfg, n=40):
    p = _log(cfg)
    if not p.exists():
        print('(no log yet)'); return 0
    lines = p.read_text(encoding='utf-8', errors='replace').splitlines()
    print('\n'.join(lines[-n:])); return 0
