"""Leases: mutual exclusion between agents, kept in Postgres (Supabase PostgREST RPC).

A lease, not a lock: agents crash, laptops sleep, people hit Ctrl+C. A lease expires on its own; long jobs renew it from a
background thread. Locked per resource, not per person: two people can fetch two different channels at once.

CLI:  team-sync lease acquire <resource> [--ttl 900] [--note ...] | renew | release [--force] | list
Code: from team_sync.leases import lease
      with lease('collect:telegram:wawa', ttl=1800, note='fetch'):
          ...   # SystemExit with who-holds-it if unavailable
"""
import json
import sys
import threading
import time
import urllib.error
import urllib.request

from .config import Config
from .msg import t


class LeaseError(Exception):
    pass


def _rpc(cfg, fn, **args):
    url, key, team = cfg.get('SUPABASE_URL'), cfg.get('SUPABASE_ANON_KEY'), cfg.get('TEAM_KEY')
    missing = [cfg.key(n) for n, v in (('SUPABASE_URL', url), ('SUPABASE_ANON_KEY', key), ('TEAM_KEY', team)) if not v]
    if missing:
        raise LeaseError(t(cfg.lang, 'lease_missing', keys=', '.join(missing), env_file=cfg.env_file.name))
    req = urllib.request.Request(
        f"{url.rstrip('/')}/rest/v1/rpc/{fn}", method='POST',
        data=json.dumps({'p_key': team, **args}).encode(),
        headers={'apikey': key, 'Authorization': f'Bearer {key}', 'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read() or 'null')
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors='replace')[:300]
        if 'bad team key' in body:
            raise LeaseError(t(cfg.lang, 'lease_badkey', key=cfg.key('TEAM_KEY')))
        raise LeaseError(f'{fn}: HTTP {e.code} {body}')
    except urllib.error.URLError as e:
        raise LeaseError(f'{fn}: cannot reach database ({e.reason})')


def acquire(resource, ttl=900, note=None, cfg=None):
    cfg = cfg or Config()
    return _rpc(cfg, 'lease_acquire', p_resource=resource, p_holder=cfg.holder(), p_ttl_sec=int(ttl), p_note=note)


def renew(resource, ttl=900, cfg=None):
    cfg = cfg or Config()
    return _rpc(cfg, 'lease_renew', p_resource=resource, p_holder=cfg.holder(), p_ttl_sec=int(ttl))


def list_active(cfg=None):
    cfg = cfg or Config()
    return _rpc(cfg, 'lease_list') or []


def release(resource, force=False, cfg=None):
    cfg = cfg or Config()
    r = _rpc(cfg, 'lease_release', p_resource=resource, p_holder=cfg.holder(), p_force=bool(force))
    if not r.get('ok') and not force:
        # CLI acquire/release are different processes: allow releasing a lease held by the same person on the same machine
        mine = [x for x in list_active(cfg) if x['resource'] == resource and x['holder'].startswith(cfg.holder_prefix() + ':')]
        if mine:
            r = _rpc(cfg, 'lease_release', p_resource=resource, p_holder=cfg.holder(), p_force=True)
    return r


class lease:
    """Context manager: acquire on enter, renew every ttl/3 s in the background, release on exit."""

    def __init__(self, resource, ttl=900, note=None, wait=0, cfg=None):
        self.resource, self.ttl, self.note, self.wait = resource, int(ttl), note, wait
        self.cfg = cfg or Config()
        self._stop = threading.Event()

    def __enter__(self):
        deadline = time.time() + self.wait
        while True:
            r = acquire(self.resource, self.ttl, self.note, self.cfg)
            if r.get('ok'):
                break
            if time.time() >= deadline:
                raise SystemExit(t(self.cfg.lang, 'lease_busy', resource=self.resource, holder=r.get('holder'),
                                   expires=r.get('expires_at'), note=r.get('note') or '-'))
            time.sleep(15)
        threading.Thread(target=self._keepalive, daemon=True).start()
        return self

    def _keepalive(self):
        while not self._stop.wait(max(30, self.ttl // 3)):
            try:
                r = renew(self.resource, self.ttl, self.cfg)
                if not r.get('ok'):
                    print(f"⚠ lease {self.resource}: renew refused ({r.get('reason')}), now held by {r.get('holder')}", file=sys.stderr)
            except Exception as e:  # network blips must not kill the job
                print(f'⚠ lease renew failed (continuing): {e}', file=sys.stderr)

    def __exit__(self, *exc):
        self._stop.set()
        try:
            release(self.resource, cfg=self.cfg)
        except Exception as e:
            print(f'⚠ lease release failed: {e} (it will expire)', file=sys.stderr)
        return False


def cli(ns, cfg):
    try:
        if ns.op == 'list':
            rows = list_active(cfg)
            if not rows:
                print(t(cfg.lang, 'lease_none')); return 0
            for x in rows:
                print(f"{x['resource']:32} {x['holder']:28} expires {x['expires_at'][:19]}  {x.get('note') or ''}")
            return 0
        fn = {'acquire': lambda: acquire(ns.resource, ns.ttl, ns.note, cfg),
              'renew': lambda: renew(ns.resource, ns.ttl, cfg),
              'release': lambda: release(ns.resource, ns.force, cfg)}[ns.op]
        res = fn(); print(json.dumps(res, ensure_ascii=False))
        return 0 if res.get('ok') else 1
    except LeaseError as e:
        print(f'✗ {e}'); return 2
