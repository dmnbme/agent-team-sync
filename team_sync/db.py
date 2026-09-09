"""Database setup: apply schema.sql, register the team passphrase, print env files for members.
Backends: a Postgres DSN via psql, or the Supabase management API (project ref + access token)."""
import json
import os
import pathlib
import subprocess
import sys
import urllib.request

from . import __version__

SCHEMA = pathlib.Path(__file__).parent / 'resources' / 'schema.sql'
UA = 'Mozilla/5.0 (agent-team-sync/' + __version__ + ')'


def _run_sql(sql, dsn=None, project_ref=None, token=None):
    if dsn:
        r = subprocess.run(['psql', dsn, '-v', 'ON_ERROR_STOP=1', '-q', '-f', '-'], input=sql, capture_output=True, text=True)
        if r.returncode != 0:
            raise SystemExit(r.stderr.strip()[:800])
        return r.stdout
    if project_ref and token:
        req = urllib.request.Request(f'https://api.supabase.com/v1/projects/{project_ref}/database/query', method='POST',
                                     data=json.dumps({'query': sql}).encode(),
                                     headers={'Authorization': f'Bearer {token}', 'User-Agent': UA, 'Content-Type': 'application/json'})
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                return resp.read().decode()
        except urllib.error.HTTPError as e:
            raise SystemExit(f'HTTP {e.code}: {e.read().decode()[:500]}')
    raise SystemExit('give --dsn postgres://... or --project-ref <ref> with SUPABASE_ACCESS_TOKEN (or --token-file)')


def _token(ns):
    if getattr(ns, 'token_file', None):
        line = pathlib.Path(os.path.expanduser(ns.token_file)).read_text(encoding='utf-8').strip()
        return line.split('=', 1)[1] if '=' in line else line
    return os.environ.get('SUPABASE_ACCESS_TOKEN')


def cli(ns, cfg):
    if ns.op == 'sql':
        sys.stdout.write(SCHEMA.read_text(encoding='utf-8')); return 0
    if ns.op == 'install':
        _run_sql(SCHEMA.read_text(encoding='utf-8'), ns.dsn, ns.project_ref, _token(ns))
        print('✓ tables and functions ready: team_config / agent_leases / collect_cursors + lease_* / cursor_*'); return 0
    if ns.op == 'register-key':
        key = cfg.get('TEAM_KEY')
        if not key or len(key) < 16:
            raise SystemExit(f"{cfg.key('TEAM_KEY')} (16+ chars) missing in {cfg.env_file.name}. Generate one: openssl rand -hex 24")
        safe = key.replace("'", "''")
        _run_sql(f"insert into public.team_config(key, value) values ('team_key', '{safe}') on conflict (key) do update set value = excluded.value, updated_at = now();",
                 ns.dsn, ns.project_ref, _token(ns))
        print('✓ team passphrase registered (value not echoed)'); return 0
    if ns.op == 'env-for':
        need = {cfg.key(n): cfg.get(n) for n in ('SUPABASE_URL', 'SUPABASE_ANON_KEY', 'TEAM_KEY')}
        missing = [k for k, v in need.items() if not v]
        if missing:
            raise SystemExit('missing ' + ', '.join(missing))
        out = pathlib.Path(ns.out or (pathlib.Path.home() / 'Desktop' / 'env.team.txt'))
        body = [f'# team-sync config for {ns.who}. Put it in the Downloads folder, then run the setup script; it lands in the repo as {cfg.env_file.name}. Do not forward.',
                f"{cfg.key('WHO')}={ns.who}"] + [f'{k}={v}' for k, v in need.items()]
        out.write_text('\n'.join(body) + '\n', encoding='utf-8')
        try:
            out.chmod(0o600)
        except OSError:
            pass
        print(f'✓ wrote {out} for {ns.who}; delete it after sending'); return 0
    return 1
