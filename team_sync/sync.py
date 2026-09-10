"""Session sync: the git layer, automated around agent sessions so nobody has to know git.

  team-sync start   pull; print recent commits, journal tail, active leases, fingerprint status, drive path
  team-sync stop    commit local changes (secret gate runs in pre-commit), pull --rebase, push; auto journal line
  team-sync note "what / where / next"
  team-sync hook antigravity pre_invocation|stop     stdin JSON → stdout JSON (Antigravity hook protocol)

All git operations on one machine (hooks, background autosync, manual) are serialised with a file lock.
A pull conflict is never left half-merged: rebase is aborted, local work stays, a marker is written and shown next start.
"""
import contextlib
import datetime as dt
import io
import json
import os
import platform
import subprocess
import sys
import time

try:
    import fcntl
except ImportError:  # Windows
    fcntl = None
    import msvcrt

from . import __version__
from .msg import t


def git(cfg, *args):
    return subprocess.run(['git', *args], cwd=cfg.repo, capture_output=True, text=True, encoding='utf-8', errors='replace')


def is_repo(cfg):
    return (cfg.repo / '.git').exists()


def state(cfg, name):
    cfg.state_dir.mkdir(parents=True, exist_ok=True)
    return cfg.state_dir / name


@contextlib.contextmanager
def serialized(cfg, wait=90):
    if not is_repo(cfg):
        yield False; return
    fh = open(state(cfg, 'lock'), 'a+b')
    if os.path.getsize(fh.name) == 0:
        fh.write(b'0'); fh.flush()
    deadline = time.time() + wait
    while True:
        try:
            if fcntl:
                fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
            else:
                fh.seek(0); msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
            break
        except OSError:
            if time.time() > deadline:
                fh.close(); yield False; return
            time.sleep(2)
    try:
        yield True
    finally:
        try:
            if fcntl:
                fcntl.flock(fh, fcntl.LOCK_UN)
            else:
                fh.seek(0); msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
        fh.close()


def notify(cfg, text):
    safe = text.replace('"', "'").replace("'", '')[:200]
    try:
        if platform.system() == 'Darwin':
            subprocess.run(['osascript', '-e', f'display notification "{safe}" with title "team-sync"'], capture_output=True)
        elif platform.system() == 'Windows':
            ps = ("Add-Type -AssemblyName System.Windows.Forms; Add-Type -AssemblyName System.Drawing; "
                  "$n = New-Object System.Windows.Forms.NotifyIcon; $n.Icon = [System.Drawing.SystemIcons]::Warning; $n.Visible = $true; "
                  f"$n.ShowBalloonTip(10000, 'team-sync', '{safe}', [System.Windows.Forms.ToolTipIcon]::Warning); Start-Sleep 8; $n.Dispose()")
            subprocess.Popen(['powershell', '-NoProfile', '-WindowStyle', 'Hidden', '-Command', ps], creationflags=0x08000000)
        elif platform.system() == 'Linux':
            subprocess.run(['notify-send', 'team-sync', safe], capture_output=True)
    except Exception:
        pass


def ensure_hook(cfg):
    hook = cfg.repo / '.git' / 'hooks' / 'pre-commit'
    shim = cfg.repo / 'bin' / 'team_sync.py'
    py = sys.executable.replace('\\', '/')                       # the interpreter that has team_sync; git hooks run with an arbitrary PATH
    entry = f'"$(git rev-parse --show-toplevel)/bin/team_sync.py"' if shim.exists() else '-m team_sync'
    want = f'#!/bin/sh\nPY="{py}"\n[ -x "$PY" ] && exec "$PY" {entry} gate\nexec python3 {entry} gate\n'
    if not hook.exists() or hook.read_text(encoding='utf-8', errors='replace') != want:
        hook.parent.mkdir(parents=True, exist_ok=True)
        hook.write_text(want, encoding='utf-8')
        try:
            hook.chmod(0o755)
        except OSError:
            pass


def journal_path(cfg):
    return cfg.repo / cfg.data['journal']


def note(cfg, text, auto=False):
    j = journal_path(cfg); j.parent.mkdir(parents=True, exist_ok=True)
    if not j.exists():
        j.write_text(t(cfg.lang, 'journal_head'), encoding='utf-8')
    with open(j, 'a', encoding='utf-8') as f:
        f.write(f"- {dt.datetime.now():%Y-%m-%d %H:%M} · {cfg.who()} · {'auto · ' if auto else ''}{text.strip()}\n")


def pull(cfg):
    if git(cfg, 'remote', 'get-url', 'origin').returncode != 0:
        return t(cfg.lang, 'no_origin')
    r = git(cfg, 'pull', '--rebase', '--autostash', '--quiet')
    marker = state(cfg, 'conflict.txt')
    if r.returncode == 0:
        if marker.exists():
            marker.unlink()
        return t(cfg.lang, 'pulled')
    git(cfg, 'rebase', '--abort'); git(cfg, 'stash', 'pop')
    raw = (r.stderr or r.stdout).strip().splitlines()
    gist = [l for l in raw if 'CONFLICT' in l or 'could not apply' in l or l.startswith('error:')][:3] or raw[:2]
    msg = t(cfg.lang, 'conflict') + '\n   ' + '\n   '.join(l[:160] for l in gist)
    marker.write_text(f'{dt.datetime.now():%Y-%m-%d %H:%M}\n{msg}\n', encoding='utf-8')
    notify(cfg, t(cfg.lang, 'conflict_notify'))
    return msg


def start(cfg):
    if not is_repo(cfg):
        print(t(cfg.lang, 'not_repo')); return
    with serialized(cfg) as got:
        if not got:
            print(t(cfg.lang, 'busy'))
        _start_body(cfg, pull_ok=got)


def _start_body(cfg, pull_ok=True):
    ensure_hook(cfg)
    lines = [t(cfg.lang, 'header', holder=cfg.holder(), now=f'{dt.datetime.now():%Y-%m-%d %H:%M}')]
    marker = state(cfg, 'conflict.txt')
    if marker.exists():
        lines.append(t(cfg.lang, 'conflict_pending') + '\n   ' + marker.read_text(encoding='utf-8').strip().replace('\n', '\n   '))
    wanted = cfg.data.get('version')
    if wanted and wanted != __version__:
        lines.append(t(cfg.lang, 'version_mismatch', installed=__version__, wanted=wanted))
    lines.append(pull(cfg) if pull_ok else t(cfg.lang, 'not_pulled'))
    stamp = state(cfg, 'last_start')
    since = stamp.read_text(encoding='utf-8').strip() if stamp.exists() else (dt.date.today() - dt.timedelta(days=7)).isoformat()
    n = int(cfg.data['sync'].get('log_commits', 12))
    log = git(cfg, 'log', f'--since={since}', '--format=%h %ad %an: %s', '--date=short', f'-{n}').stdout.strip()
    lines.append(t(cfg.lang, 'recent_commits', since=since[:10]) + '\n' + ('   ' + log.replace('\n', '\n   ') if log else '   ' + t(cfg.lang, 'none')))
    j = journal_path(cfg)
    if j.exists():
        k = int(cfg.data['sync'].get('journal_tail', 8))
        tail = [l for l in j.read_text(encoding='utf-8').splitlines() if l.startswith('- ')][-k:]
        lines.append(t(cfg.lang, 'journal_tail') + '\n' + ('   ' + '\n   '.join(tail) if tail else '   ' + t(cfg.lang, 'none')))
    if cfg.get('TEAM_KEY'):
        try:
            from .leases import list_active
            rows = list_active(cfg)
            lines.append(t(cfg.lang, 'leases') + (', '.join(f"{x['resource']}←{x['holder']}" for x in rows) if rows else t(cfg.lang, 'none')))
        except Exception as e:
            lines.append(t(cfg.lang, 'leases_error', err=e))
    else:
        missing = [cfg.key(n) for n in ('SUPABASE_URL', 'SUPABASE_ANON_KEY', 'TEAM_KEY') if not cfg.get(n)]
        if not cfg.env_file.exists():
            lines.append(t(cfg.lang, 'env_absent', env_file=cfg.env_file.name))
        elif len(missing) > 1:
            lines.append(t(cfg.lang, 'env_incomplete', missing=', '.join(missing), env_file=cfg.env_file.name))
        else:
            lines.append(t(cfg.lang, 'leases_unconfigured', key=cfg.key('TEAM_KEY'), env_file=cfg.env_file.name))
    try:
        from .fingerprint import summary_line
        fp = summary_line(cfg, quick=True)
        if fp:
            lines.append(fp)
    except Exception as e:
        lines.append(f'fingerprint: check failed ({e})')
    if (cfg.data.get('drive') or {}).get('root_name'):
        d = cfg.drive()
        lines.append(t(cfg.lang, 'drive', path=d) if d else t(cfg.lang, 'drive_missing', key=cfg.key('DRIVE_ROOT')))
    lines.append(t(cfg.lang, 'rules', rules=cfg.data['rules_file']))
    stamp.write_text(dt.datetime.now().isoformat(timespec='seconds'), encoding='utf-8')
    print('\n'.join(lines))


def stop(cfg):
    if not is_repo(cfg):
        return
    with serialized(cfg) as got:
        if not got:
            print(t(cfg.lang, 'busy')); return
        _stop_body(cfg)


def _stop_body(cfg):
    ensure_hook(cfg)
    git(cfg, 'add', '-A')
    staged = [l for l in git(cfg, 'diff', '--cached', '--name-only').stdout.splitlines() if l.strip()]
    if staged:
        preview = ', '.join(staged[:6]) + (f' … ({len(staged)})' if len(staged) > 6 else '')
        note(cfg, t(cfg.lang, 'auto_changed', n=len(staged), files=preview), auto=True)
        git(cfg, 'add', '-A')
        r = git(cfg, 'commit', '-q', '-m', f'sync({cfg.who()}): {dt.datetime.now():%Y-%m-%d %H:%M} · {len(staged)} files')
        if r.returncode != 0:
            print(t(cfg.lang, 'commit_blocked') + '\n' + (r.stdout + r.stderr).strip()[:800])
            git(cfg, 'reset', '-q'); return
        print(t(cfg.lang, 'committed', n=len(staged)))
    if git(cfg, 'remote', 'get-url', 'origin').returncode == 0:
        msg = pull(cfg)
        if msg.startswith('⚠'):
            print(msg); return
        p = git(cfg, 'push', '--quiet')
        print(t(cfg.lang, 'pushed') if p.returncode == 0 else t(cfg.lang, 'push_failed', err=(p.stderr or p.stdout).strip()[:300]))
    if cfg.get('TEAM_KEY'):
        try:
            from .leases import list_active
            mine = [x['resource'] for x in list_active(cfg) if x['holder'] == cfg.holder()]
            if mine:
                print(t(cfg.lang, 'held_leases', list=', '.join(mine)))
        except Exception:
            pass


def status(cfg):
    if not is_repo(cfg):
        print(t(cfg.lang, 'not_repo')); return
    print(git(cfg, 'status', '--short', '--branch').stdout.rstrip())
    print(git(cfg, 'log', '--oneline', '-5').stdout.rstrip())


def _capture(fn):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        try:
            fn()
        except Exception as e:  # noqa: BLE001
            print(f'(sync error, work not affected: {e})')
    return buf.getvalue().strip()


def hook(cfg, kind, event):
    """Antigravity: stdin JSON in, stdout JSON out. Never raise, never print anything but JSON."""
    try:
        raw = sys.stdin.read() if not sys.stdin.isatty() else ''
        data = json.loads(raw) if raw.strip() else {}
    except Exception:
        data = {}
    out = {}
    try:
        if kind == 'antigravity' and event == 'pre_invocation':
            conv = str(data.get('conversationId') or 'na')
            seen_f = state(cfg, 'hook_seen.json')
            try:
                seen = json.loads(seen_f.read_text(encoding='utf-8')) if seen_f.exists() else {}
            except Exception:
                seen = {}
            now = time.time(); ttl = float(cfg.data['sync'].get('inject_ttl_hours', 6)) * 3600
            if int(data.get('invocationNum', 0) or 0) == 0 and now - float(seen.get(conv, 0)) > ttl:
                seen = {k: v for k, v in seen.items() if now - float(v) < 48 * 3600}; seen[conv] = now
                try:
                    seen_f.write_text(json.dumps(seen), encoding='utf-8')
                except Exception:
                    pass
                text = _capture(lambda: start(cfg))
                if text:
                    out = {'injectSteps': [{'userMessage': t(cfg.lang, 'hook_inject_prefix') + text}]}
        elif kind == 'antigravity' and event == 'stop':
            if data.get('fullyIdle', True):
                _capture(lambda: stop(cfg))
    except Exception:
        out = {}
    sys.stdout.write(json.dumps(out, ensure_ascii=False))
