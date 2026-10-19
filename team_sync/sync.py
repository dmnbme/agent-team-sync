"""Session sync: the git layer, automated around agent sessions so nobody has to know git.

  team-sync start   pull; print recent commits, journal tail, active leases, fingerprint status, drive path
  team-sync stop    commit local changes (secret gate runs in pre-commit), pull --rebase, push
  team-sync stop --session-end   same, plus one journal line listing the files this person changed since the last such line
  team-sync note "what / where / next"
  team-sync hook antigravity pre_invocation|stop     stdin JSON → stdout JSON (Antigravity hook protocol)
  team-sync hook codex session_start|prompt|stop     stdin JSON → stdout JSON (Codex hook protocol)
  team-sync hook claude prompt|stop                  stdin JSON → stdout JSON (Claude Code: teammate messages; conversation record)

All git operations on one machine (hooks, background autosync, manual) are serialised with a file lock.
A pull conflict is never left half-merged: rebase is aborted, local work stays, a marker is written and shown next start.
"""
import contextlib
import datetime as dt
import io
import json
import os
import pathlib
import platform
import shutil
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
    if os.environ.get('TEAM_SYNC_NO_NOTIFY'):                  # selftest: its conflicts are staged, not news
        return
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


def ensure_attrs(cfg):
    """Everyone appends to the journal; with merge=union two people's new lines merge instead of conflicting."""
    line = f"{cfg.data['journal']} merge=union"
    ga = cfg.repo / '.gitattributes'
    have = ga.read_text(encoding='utf-8') if ga.exists() else ''
    if line not in [l.strip() for l in have.splitlines()]:
        ga.write_text(have.rstrip('\n') + ('\n' if have.strip() else '') + '# team-sync: concurrent journal appends keep both sides\n' + line + '\n',
                      encoding='utf-8')


def journal_path(cfg):
    return cfg.repo / cfg.data['journal']


def note(cfg, text, auto=False):
    j = journal_path(cfg); j.parent.mkdir(parents=True, exist_ok=True)
    if not j.exists():
        j.write_text(t(cfg.lang, 'journal_head'), encoding='utf-8')
    with open(j, 'a', encoding='utf-8') as f:
        f.write(f"- {dt.datetime.now():%Y-%m-%d %H:%M} · {cfg.who()} · {'auto · ' if auto else ''}{text.strip()}\n")


# Signs of a flaky HTTP/2 path (some networks and proxies drop long HTTP/2 streams); HTTP/1.1 is the usual cure.
HTTP2_TROUBLE = ('HTTP/2', 'RPC failed', 'curl 92', 'curl 56', 'curl 18', 'unexpected disconnect', 'Operation too slow', 'early EOF')


def _net(cfg, *args):
    """A git command that talks to the remote. On failure, retry once; when the error looks like a flaky HTTP/2 path, switch
    this repo to HTTP/1.1 first (only if nobody set http.version)."""
    r = git(cfg, *args)
    if r.returncode == 0:
        return r
    err = r.stderr + r.stdout
    if any(k in err for k in HTTP2_TROUBLE) and not git(cfg, 'config', 'http.version').stdout.strip():
        git(cfg, 'config', 'http.version', 'HTTP/1.1')
    time.sleep(2)
    return git(cfg, *args)


def pull(cfg):
    if git(cfg, 'remote', 'get-url', 'origin').returncode != 0:
        return t(cfg.lang, 'no_origin')
    stashes = lambda: len(git(cfg, 'stash', 'list').stdout.splitlines())
    before = stashes()
    marker = state(cfg, 'conflict.txt')
    # Fetch and rebase separately: a failed fetch is always the network, whatever git's wording, and is never a conflict.
    if _net(cfg, 'fetch', '--quiet', 'origin').returncode != 0:
        return t(cfg.lang, 'offline')
    if git(cfg, 'rev-parse', '--abbrev-ref', '@{u}').returncode == 0:
        r = git(cfg, 'rebase', '--autostash', '--quiet', '@{u}')
    else:                                                       # no upstream set: let pull pick the branch
        r = git(cfg, 'pull', '--rebase', '--autostash', '--quiet')
    if r.returncode == 0:
        if marker.exists():
            marker.unlink()
        return t(cfg.lang, 'pulled')
    out = (r.stderr or r.stdout).strip()
    gd = cfg.repo / '.git'
    if not (gd / 'rebase-merge').exists() and not (gd / 'rebase-apply').exists():
        return '⚠ ' + out[:300]                                 # failed before any rebase started: nothing to undo, not a conflict
    git(cfg, 'rebase', '--abort')
    if stashes() > before:                                       # only pop an autostash git left behind, never an older stash
        git(cfg, 'stash', 'pop')
    raw = out.splitlines()
    gist = [l for l in raw if 'CONFLICT' in l or 'could not apply' in l or l.startswith('error:')][:3] or raw[:2]
    msg = t(cfg.lang, 'conflict') + '\n   ' + '\n   '.join(l[:160] for l in gist)
    marker.write_text(f'{dt.datetime.now():%Y-%m-%d %H:%M}\n{msg}\n', encoding='utf-8')
    notify(cfg, t(cfg.lang, 'conflict_notify'))
    return msg


def ensure_identity(cfg):
    """With no user.email configured, git signs commits with a guess (the OS full name, user@host.local) that GitHub links
    to nobody: no username, no avatar. If gh is logged in, give this repo the GitHub login and its noreply address.
    Tried at most once a day. Returns a line for the summary, or ''."""
    if git(cfg, 'config', 'user.email').stdout.strip():
        return ''
    f = state(cfg, 'identity_tried')
    today = dt.date.today().isoformat()
    try:
        if f.exists() and f.read_text(encoding='utf-8').strip() == today:
            return ''
        f.write_text(today, encoding='utf-8')
    except OSError:
        return ''
    gh = shutil.which('gh') or next((str(p) for p in (pathlib.Path.home() / '.team-sync' / 'bin' / n for n in ('gh', 'gh.exe')) if p.exists()), None)
    if not gh:
        return ''
    try:
        r = subprocess.run([gh, 'api', 'user', '--jq', '.login + " " + (.id|tostring)'], capture_output=True, text=True, timeout=15)
    except Exception:  # noqa: BLE001
        return ''
    parts = r.stdout.split()
    if r.returncode != 0 or len(parts) != 2:
        return ''
    login, uid = parts
    git(cfg, 'config', 'user.name', login)
    git(cfg, 'config', 'user.email', f'{uid}+{login}@users.noreply.github.com')
    return t(cfg.lang, 'identity_set', login=login)


def start(cfg):
    if not is_repo(cfg):
        print(t(cfg.lang, 'not_repo')); return
    with serialized(cfg) as got:
        if not got:
            print(t(cfg.lang, 'busy'))
        _start_body(cfg, pull_ok=got)


def _start_body(cfg, pull_ok=True):
    ensure_hook(cfg)
    ensure_attrs(cfg)
    lines = [t(cfg.lang, 'header', holder=cfg.holder(), now=f'{dt.datetime.now():%Y-%m-%d %H:%M}')]
    ident = ensure_identity(cfg)
    if ident:
        lines.append(ident)
    marker = state(cfg, 'conflict.txt')
    if marker.exists():
        lines.append(t(cfg.lang, 'conflict_pending') + '\n   ' + marker.read_text(encoding='utf-8').strip().replace('\n', '\n   '))
    wanted = cfg.data.get('version')
    if wanted and wanted != __version__:
        lines.append(t(cfg.lang, 'version_mismatch', installed=__version__, wanted=wanted))
    lines.append(pull(cfg) if pull_ok else t(cfg.lang, 'not_pulled'))
    from .inbox import deliver, receipts
    inbox = deliver(cfg)                                        # first thing after the pull, so it is not lost in the summary
    if inbox:
        lines.insert(1, inbox)
    since_f = state(cfg, 'auto_since')                          # the next session-end journal line starts from here
    if not since_f.exists():
        head = git(cfg, 'rev-parse', 'HEAD').stdout.strip()
        if head:
            since_f.write_text(head, encoding='utf-8')
    stamp = state(cfg, 'last_start')
    since = stamp.read_text(encoding='utf-8').strip() if stamp.exists() else (dt.date.today() - dt.timedelta(days=7)).isoformat()
    n = int(cfg.data['sync'].get('log_commits', 12))
    cdir = ((cfg.data.get('convo') or {}).get('dir') or '').strip('/')
    idir = ((cfg.data.get('inbox') or {}).get('dir') or '').strip('/')
    only = ['--', '.'] + [f':(exclude){x}' for x in (cdir, idir) if x] if (cdir or idir) else []   # records and messages alone are noise here
    log = git(cfg, 'log', f'--since={since}', '--format=%h %ad %an: %s', '--date=short', f'-{n}', *only).stdout.strip()
    try:
        rc = receipts(cfg, since)
        if rc:
            lines.append(rc)
    except Exception:  # noqa: BLE001
        pass
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
    try:
        from .convo import recent
        rc = recent(cfg)
        if rc:
            lines.append(rc)
    except Exception as e:  # noqa: BLE001
        lines.append(f'conversation records: listing failed ({e})')
    if (cfg.data.get('drive') or {}).get('root_name'):
        d = cfg.drive()
        lines.append(t(cfg.lang, 'drive', path=d) if d else t(cfg.lang, 'drive_missing', key=cfg.key('DRIVE_ROOT')))
        if d:
            try:                                                # top-level folders, so the agent knows what lives on the drive
                tops = sorted(p.name for p in d.iterdir() if p.is_dir() and not p.name.startswith('.'))
                if tops:
                    lines.append(t(cfg.lang, 'drive_folders', list=' · '.join(tops[:30]) + (' …' if len(tops) > 30 else '')))
            except OSError:
                pass
    lines.append(t(cfg.lang, 'rules', rules=cfg.data['rules_file']))
    stamp.write_text(dt.datetime.now().isoformat(timespec='seconds'), encoding='utf-8')
    print('\n'.join(lines))


def stop(cfg, session_end=False):
    if not is_repo(cfg):
        return
    with serialized(cfg) as got:
        if not got:
            print(t(cfg.lang, 'busy')); return
        _stop_body(cfg, session_end)


def _stage(cfg):
    git(cfg, 'add', '-A')
    return [l for l in git(cfg, '-c', 'core.quotePath=false', 'diff', '--cached', '--name-only').stdout.splitlines() if l.strip()]


def _session_files(cfg, staged):
    """What this person changed since the last session-end line: their sync commits since then (autosync and turn-end
    pushes commit along the way), then what is staged now. Conversation records and the journal itself left out."""
    from .convo import is_record
    from .inbox import is_message
    since_f = state(cfg, 'auto_since')
    since = since_f.read_text(encoding='utf-8').strip() if since_f.exists() else ''
    names = []
    if since and git(cfg, 'cat-file', '-e', since + '^{commit}').returncode == 0:
        log = git(cfg, '-c', 'core.quotePath=false', 'log', '--reverse', '--format=', '--name-only',
                  f'--grep=^sync({cfg.who()}):', f'{since}..HEAD').stdout
        names = [l.strip() for l in log.splitlines() if l.strip()]
    out = []
    for f in names + staged:
        if f not in out and f != cfg.data['journal'] and not is_record(cfg, f) and not is_message(cfg, f):
            out.append(f)
    return out


def _stop_body(cfg, session_end=False):
    ensure_hook(cfg)
    ensure_attrs(cfg)
    staged = _stage(cfg)
    if session_end:                                             # one journal line per session, not one per autosync run
        files = _session_files(cfg, staged)
        if files:
            preview = ', '.join(files[:6]) + (f' … ({len(files)})' if len(files) > 6 else '')
            note(cfg, t(cfg.lang, 'auto_changed', n=len(files), files=preview), auto=True)
            staged = _stage(cfg)
    try:
        _commit_push(cfg, staged)
    finally:
        if session_end:
            try:
                state(cfg, 'auto_since').write_text(git(cfg, 'rev-parse', 'HEAD').stdout.strip(), encoding='utf-8')
            except OSError:
                pass


def _commit_push(cfg, staged):
    if staged:
        r = git(cfg, 'commit', '-q', '-m', f'sync({cfg.who()}): {dt.datetime.now():%Y-%m-%d %H:%M} · {len(staged)} files')
        if r.returncode != 0:
            print(t(cfg.lang, 'commit_blocked') + '\n' + (r.stdout + r.stderr).strip()[:800])
            git(cfg, 'reset', '-q'); return
        print(t(cfg.lang, 'committed', n=len(staged)))
    if git(cfg, 'remote', 'get-url', 'origin').returncode == 0:
        msg = pull(cfg)
        if msg.startswith('⚠'):
            print(msg); return
        p = _net(cfg, 'push', '--quiet')
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


def _convo(cfg, agent, data):
    """Update this session's conversation record; returns a compaction request for the agent, or None. Never raises."""
    try:
        from .convo import update
        return update(cfg, agent, data)
    except Exception as e:  # noqa: BLE001
        try:
            with open(state(cfg, 'convo.log'), 'a', encoding='utf-8') as f:
                f.write(f'{dt.datetime.now():%Y-%m-%d %H:%M} {agent}: {e!r}\n')
        except Exception:
            pass
        return None


def _stop_in_background(cfg):
    """Run `stop` detached so the hook returns at once (the hook itself has to stay synchronous to answer)."""
    if os.environ.get('TEAM_SYNC_FOREGROUND'):                   # selftest
        _capture(lambda: stop(cfg)); return
    shim = cfg.repo / 'bin' / 'team_sync.py'
    cmd = [sys.executable, str(shim)] if shim.exists() else [sys.executable, '-m', 'team_sync']
    kw = {'cwd': str(cfg.repo), 'stdin': subprocess.DEVNULL, 'close_fds': True}
    if platform.system() == 'Windows':
        kw['creationflags'] = 0x00000008 | 0x00000200 | 0x08000000   # detached, new process group, no window
    else:
        kw['start_new_session'] = True
    with open(state(cfg, 'hook_stop.log'), 'a', encoding='utf-8') as log:
        subprocess.Popen(cmd + ['--repo', str(cfg.repo), 'stop'], stdout=log, stderr=log, **kw)


def hook(cfg, kind, event):
    """Agent hook adapters (Antigravity, Codex, Claude Code): stdin JSON in, stdout JSON out. Never raise, never print anything but JSON."""
    try:
        raw = sys.stdin.read() if not sys.stdin.isatty() else ''
        data = json.loads(raw) if raw.strip() else {}
    except Exception:
        data = {}
    out = {}
    try:
        if kind == 'codex':
            from .convo import codex_is_exec
            if codex_is_exec(data):                              # a script calling `codex exec`: no sync, no record
                sys.stdout.write('{}'); return
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
            else:
                from .inbox import deliver
                text = deliver(cfg)
                if text:
                    out = {'injectSteps': [{'userMessage': text}]}
        elif kind == 'antigravity' and event == 'stop':
            if data.get('fullyIdle', True):
                _capture(lambda: stop(cfg))
        elif kind == 'codex' and event == 'session_start':
            # SessionStart: hookSpecificOutput.additionalContext becomes developer context for the thread
            text = _capture(lambda: start(cfg))
            if text:
                out = {'hookSpecificOutput': {'hookEventName': 'SessionStart', 'additionalContext': t(cfg.lang, 'hook_inject_prefix') + text}}
        elif kind in ('codex', 'claude') and event == 'prompt':
            # UserPromptSubmit, before every turn: unread teammate messages only. A local read, no network, so it adds no
            # wait; the turn-end sync and the background autosync are what bring new messages in.
            from .inbox import deliver
            text = deliver(cfg)
            if text:
                out = {'hookSpecificOutput': {'hookEventName': 'UserPromptSubmit', 'additionalContext': text}}
        elif kind == 'codex' and event == 'stop':
            # Stop fires when a turn ends. Write the conversation record, then push in a detached process so the user is
            # never held up. A compaction request continues the turn; the push then happens when that turn ends.
            # (SessionEnd is capped at 3 s by Codex, too short for pull + push; the background autosync covers the rest.)
            ask = _convo(cfg, 'codex', data)
            if ask:
                out = {'decision': 'block', 'reason': ask}
            else:
                _stop_in_background(cfg)
        elif kind == 'claude' and event == 'stop':
            # Claude Code pushes at SessionEnd and through autosync; at each turn end only the conversation record is written.
            ask = _convo(cfg, 'claude', data)
            if ask:
                out = {'decision': 'block', 'reason': ask}
    except Exception:
        out = {}
    sys.stdout.write(json.dumps(out, ensure_ascii=False))
