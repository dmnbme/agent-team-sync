"""Shared cursors: "how far did we get" counters (e.g. last message id per channel) shared through Postgres.

Truth lives in the database; a local JSON cache is the offline fallback. Values only move forward unless --force.
CLI:  team-sync cursor show | pull | push [--force]        (local cache path: state_dir/cursors.json by default)
Code: from team_sync.cursors import merged, push
"""
import json
import sys

from .config import Config
from .leases import LeaseError, _rpc


def cache_path(cfg):
    p = (cfg.data.get('cursors') or {}).get('cache')
    return cfg.repo / p if p else cfg.state_dir / 'cursors.json'


def get_all(cfg=None):
    cfg = cfg or Config()
    return {r['name']: int(r['value']) for r in (_rpc(cfg, 'cursor_get_all') or [])}


def set_one(name, value, force=False, cfg=None):
    cfg = cfg or Config()
    return _rpc(cfg, 'cursor_set', p_name=name, p_value=int(value), p_by=cfg.holder_prefix(), p_force=bool(force))


def load_local(cfg):
    p = cache_path(cfg)
    return json.loads(p.read_text(encoding='utf-8')) if p.exists() else {}


def save_local(cfg, m):
    p = cache_path(cfg); p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(m, ensure_ascii=False, indent=2), encoding='utf-8')


def merged(cfg=None):
    """local ∪ remote, max per key. Returns (dict, remote_ok)."""
    cfg = cfg or Config()
    local = load_local(cfg)
    try:
        remote = get_all(cfg)
    except LeaseError as e:
        print(f'⚠ cursors: using local cache (database unavailable: {e})', file=sys.stderr)
        return local, False
    out = dict(local)
    for k, v in remote.items():
        out[k] = max(int(v), int(out.get(k, 0)))
    return out, True


def push(m, force=False, cfg=None):
    cfg = cfg or Config()
    return sum(1 for k, v in m.items() if set_one(k, v, force, cfg).get('changed'))


def cli(ns, cfg):
    try:
        if ns.op == 'show':
            for k, v in sorted(get_all(cfg).items()):
                print(f'{v:>12}  {k}')
        elif ns.op == 'pull':
            m, ok = merged(cfg); save_local(cfg, m)
            print(f"{'✓' if ok else '⚠ local only'} {len(m)} cursors → {cache_path(cfg)}")
        else:
            print(f'✓ pushed, {push(load_local(cfg), ns.force, cfg)} changed')
        return 0
    except LeaseError as e:
        print(f'✗ {e}'); return 2
