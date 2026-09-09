import argparse
import subprocess
import sys

from . import __version__
from .config import Config


def main(argv=None):
    ap = argparse.ArgumentParser(prog='team-sync', description='Keep a small team and their AI coding agents in sync: git + shared drive + Postgres leases.')
    ap.add_argument('--repo', help='repo root (default: found from cwd / CLAUDE_PROJECT_DIR / CODEBUDDY_PROJECT_DIR)')
    sub = ap.add_subparsers(dest='cmd')
    sub.add_parser('start', help='pull + print the team summary (session start hook)')
    sub.add_parser('stop', help='commit + pull --rebase + push (session end hook / autosync)')
    sub.add_parser('status', help='git status and last commits')
    n = sub.add_parser('note', help='append a line to the team journal'); n.add_argument('text', nargs='+')
    h = sub.add_parser('hook', help='agent hook adapters (stdin JSON → stdout JSON)'); h.add_argument('kind', choices=['antigravity']); h.add_argument('event')
    sub.add_parser('gate', help='secret gate (used by the pre-commit hook)')
    l = sub.add_parser('lease', help='leases'); ls = l.add_subparsers(dest='op', required=True)
    for op in ('acquire', 'renew', 'release'):
        x = ls.add_parser(op); x.add_argument('resource'); x.add_argument('--ttl', type=int, default=900)
        if op == 'acquire':
            x.add_argument('--note')
        if op == 'release':
            x.add_argument('--force', action='store_true')
    ls.add_parser('list')
    c = sub.add_parser('cursor', help='shared cursors'); cs = c.add_subparsers(dest='op', required=True)
    cs.add_parser('show'); cs.add_parser('pull'); cp = cs.add_parser('push'); cp.add_argument('--force', action='store_true')
    f = sub.add_parser('fingerprint', help='data fingerprint manifest'); f.add_argument('--check', action='store_true'); f.add_argument('--quick', action='store_true')
    a = sub.add_parser('autosync', help='background sync scheduler'); a.add_argument('op', choices=['on', 'off', 'status', 'log'])
    d = sub.add_parser('db', help='database setup'); ds = d.add_subparsers(dest='op', required=True)
    for op in ('sql', 'install', 'register-key', 'env-for'):
        x = ds.add_parser(op); x.add_argument('--dsn'); x.add_argument('--project-ref'); x.add_argument('--token-file')
        if op == 'env-for':
            x.add_argument('who'); x.add_argument('--out')
    i = sub.add_parser('init', help='write team-sync.json, hook files, env example and AGENTS.md into this repo')
    i.add_argument('--team'); i.add_argument('--prefix', default='TEAM'); i.add_argument('--lang', default='en', choices=['en', 'zh'])
    st = sub.add_parser('selftest', help='two-clone simulation in a temp dir'); st.add_argument('--keep', action='store_true')
    sub.add_parser('paths', help='print repo, drive, identity, env status')
    sub.add_parser('upgrade', help='install the version pinned in team-sync.json')
    sub.add_parser('version')
    ns = ap.parse_args(argv)
    if ns.cmd is None:
        ap.print_help(); return 0
    if ns.cmd == 'version':
        print(__version__); return 0
    cfg = Config(ns.repo)
    import os
    for var in ('TEAM_SYNC_REPO', 'CLAUDE_PROJECT_DIR', 'CODEBUDDY_PROJECT_DIR'):   # resolved now; children (selftest clones, scripts) must not inherit them
        os.environ.pop(var, None)
    if sys.stdout is None or sys.stderr is None:          # pythonw (Windows scheduler): no console → log file
        cfg.state_dir.mkdir(parents=True, exist_ok=True)
        log = open(cfg.state_dir / 'autosync.log', 'a', encoding='utf-8'); sys.stdout = sys.stderr = log
    try:
        if ns.cmd == 'start':
            from .sync import start; start(cfg); return 0
        if ns.cmd == 'stop':
            from .sync import stop; stop(cfg); return 0
        if ns.cmd == 'status':
            from .sync import status; status(cfg); return 0
        if ns.cmd == 'note':
            from .sync import note; note(cfg, ' '.join(ns.text)); from .msg import t; print(t(cfg.lang, 'noted', journal=cfg.data['journal'])); return 0
        if ns.cmd == 'hook':
            from .sync import hook; hook(cfg, ns.kind, ns.event); return 0
        if ns.cmd == 'gate':
            from .gate import run; return run(cfg)
        if ns.cmd == 'lease':
            from .leases import cli; return cli(ns, cfg)
        if ns.cmd == 'cursor':
            from .cursors import cli; return cli(ns, cfg)
        if ns.cmd == 'fingerprint':
            from .fingerprint import cli; return cli(ns, cfg)
        if ns.cmd == 'autosync':
            from . import autosync; return getattr(autosync, ns.op)(cfg)
        if ns.cmd == 'db':
            from .db import cli; return cli(ns, cfg)
        if ns.cmd == 'init':
            from .init import run; return run(cfg, ns)
        if ns.cmd == 'selftest':
            from .selftest import run; return run(cfg, ns.keep)
        if ns.cmd == 'paths':
            e = cfg.env()
            print('repo     ', cfg.repo); print('config   ', 'team-sync.json ✓' if cfg.exists else 'team-sync.json ✗ (run: team-sync init)')
            print('drive    ', cfg.drive() or ('(not configured)' if not (cfg.data.get('drive') or {}).get('root_name') else '(not found)'))
            print('identity ', cfg.holder())
            print('database ', ' | '.join(f"{cfg.key(k)} {'✓' if e.get(cfg.key(k)) else '✗'}" for k in ('SUPABASE_URL', 'SUPABASE_ANON_KEY', 'TEAM_KEY')))
            print('version  ', __version__, f"(config wants {cfg.data['version']})" if cfg.data.get('version') else ''); return 0
        if ns.cmd == 'upgrade':
            import os, pathlib
            pkg = pathlib.Path(os.environ.get('TEAM_SYNC_PKG') or pathlib.Path.home() / '.team-sync' / 'pkg')
            want = cfg.data.get('version')
            if (pkg / '.git').exists() and want:
                r = subprocess.run(['git', '-C', str(pkg), 'fetch', '-q', '--tags', 'origin'])
                r = subprocess.run(['git', '-C', str(pkg), 'checkout', '-q', f'v{want}']) if r.returncode == 0 else r
                from .msg import t; print(t(cfg.lang, 'upgraded', version=want) if r.returncode == 0 else f'✗ git exit {r.returncode}'); return r.returncode
            want = cfg.data.get('version'); src = cfg.data.get('source') or 'agent-team-sync'
            spec = f'{src}@v{want}' if want and src.startswith('git+') else (f'agent-team-sync=={want}' if want else src)
            r = subprocess.run([sys.executable, '-m', 'pip', 'install', '--user', '--upgrade', '--quiet', spec] if not src.startswith('git+') else [sys.executable, '-m', 'pip', 'install', '--user', '--upgrade', '--quiet', f'agent-team-sync @ {spec}'])
            from .msg import t; print(t(cfg.lang, 'upgraded', version=want or 'latest') if r.returncode == 0 else f'✗ pip exit {r.returncode}'); return r.returncode
    except SystemExit as e:
        if e.code not in (0, None):
            print(e.code if isinstance(e.code, str) else '', file=sys.stderr)
            return 1 if isinstance(e.code, str) else e.code
        return 0
    except Exception as e:  # hooks must never break a session
        print(f'team-sync {ns.cmd} error: {e}', file=sys.stderr); return 0
    return 0


if __name__ == '__main__':
    sys.exit(main())
