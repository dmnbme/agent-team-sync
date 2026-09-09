"""Two-clone simulation in a temp dir: proves sync, conflict handling, leases (if configured) and the Antigravity hook.
Never touches the real origin. `team-sync selftest [--keep]`"""
import json
import pathlib
import shutil
import subprocess
import sys
import tempfile

from .config import CONFIG_NAME, Config

FAILS = []


def check(name, ok, detail=''):
    print(('  ✓ ' if ok else '  ✗ ') + name + (f'  ({detail})' if detail else ''))
    if not ok:
        FAILS.append(name)


import os

CLEAN_ENV = {k: v for k, v in os.environ.items() if k not in ('TEAM_SYNC_REPO', 'CLAUDE_PROJECT_DIR', 'CODEBUDDY_PROJECT_DIR')}


def sh(cwd, *args, stdin=None):
    r = subprocess.run(list(args), cwd=cwd, capture_output=True, text=True, input=stdin, encoding='utf-8', errors='replace', env=CLEAN_ENV)
    return (r.stdout + r.stderr).strip()


def run(cfg, keep=False):
    tmp = pathlib.Path(tempfile.mkdtemp(prefix='team-sync-selftest-'))
    print(f'temp dir {tmp}\n')
    sh(tmp, 'git', 'clone', '-q', '--bare', str(cfg.repo), 'origin.git')
    env_lines = [f'{k}={v}' for k, v in cfg.env().items() if k != cfg.key('WHO')]
    for who in ('alice', 'bob'):
        sh(tmp, 'git', 'clone', '-q', 'origin.git', who)
        sh(tmp / who, 'git', 'config', 'user.name', who); sh(tmp / who, 'git', 'config', 'user.email', f'{who}@selftest.local')
        if (cfg.repo / CONFIG_NAME).exists():                      # use the working-tree config even if not committed yet
            shutil.copy2(cfg.repo / CONFIG_NAME, tmp / who / CONFIG_NAME)
        (tmp / who / cfg.data['env_file']).write_text('\n'.join([f"{cfg.key('WHO')}={who}"] + env_lines) + '\n', encoding='utf-8')
    A, B = tmp / 'alice', tmp / 'bob'
    ts = lambda cwd, *a, stdin=None: sh(cwd, sys.executable, '-m', 'team_sync', '--repo', str(cwd), *a, stdin=stdin)   # explicit repo: never touch the real one
    jdir = pathlib.Path(cfg.data['journal']).parent
    fa, fb = A / jdir / 'selftest_note.md', B / jdir / 'selftest_note.md'

    print('1 alice edits, session ends')
    fa.parent.mkdir(parents=True, exist_ok=True); fa.write_text('alice v1\n', encoding='utf-8')
    ts(A, 'note', 'selftest: alice added selftest_note.md'); out = ts(A, 'stop')
    check('auto commit + push', 'Pushed' in out or '已推送' in out, out.replace('\n', ' | ')[:100])

    print('2 bob starts a session')
    out = ts(B, 'start')
    check('bob received the file', fb.exists())
    check('summary shows alice commit', 'sync(alice)' in out)
    check('summary shows alice journal line', 'selftest: alice added' in out)

    print('3 same-line conflict')
    fa.write_text('alice v2\n', encoding='utf-8'); ts(A, 'stop')
    fb.write_text('bob v2\n', encoding='utf-8'); out = ts(B, 'stop')
    check('loser gets a conflict notice', 'conflict' in out.lower() or '冲突' in out)
    check('local content kept', fb.read_text(encoding='utf-8').strip() == 'bob v2')
    check('conflict marker written', (B / '.git' / 'team-sync' / 'conflict.txt').exists())
    check('next start shows it', 'conflict' in ts(B, 'start').lower() or '冲突' in ts(B, 'start'))

    if cfg.get('TEAM_KEY'):
        print('4 leases')
        res = 'selftest:lease'
        a = json.loads(ts(A, 'lease', 'acquire', res, '--ttl', '60'))
        braw = ts(B, 'lease', 'acquire', res, '--ttl', '60'); b = json.loads(braw.splitlines()[-1]) if braw.startswith('{') else {}
        check('alice acquires', a.get('ok') is True); check('bob refused', b.get('ok') is False, f"holder={b.get('holder')}")
        ts(A, 'lease', 'release', res)
        c = json.loads(ts(B, 'lease', 'acquire', res, '--ttl', '60')); check('bob acquires after release', c.get('ok') is True)
        ts(B, 'lease', 'release', res)
    else:
        print(f"4 leases (skipped: {cfg.key('TEAM_KEY')} not configured)")

    print('5 Antigravity hook')
    out = ts(B, 'hook', 'antigravity', 'pre_invocation', stdin='{"conversationId":"selftest","invocationNum":0}')
    try:
        msg = json.loads(out)['injectSteps'][0]['userMessage']; check('valid JSON with injected summary', len(msg) > 100)
    except Exception as e:  # noqa: BLE001
        check('valid JSON with injected summary', False, str(e)[:80])
    check('no duplicate injection in the same conversation', ts(B, 'hook', 'antigravity', 'pre_invocation', stdin='{"conversationId":"selftest","invocationNum":0}').strip() == '{}')

    if keep:
        print(f'\nkept: {tmp}')
    else:
        shutil.rmtree(tmp, ignore_errors=True)
    print('\n' + ('all passed' if not FAILS else f'{len(FAILS)} failed: ' + ', '.join(FAILS)))
    return 1 if FAILS else 0
