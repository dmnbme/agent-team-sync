"""Repo discovery, team-sync.json, the per-machine env file, identity, and shared-drive resolution."""
import glob
import json
import os
import pathlib
import socket
import sys

for _s in (sys.stdout, sys.stderr):          # Windows consoles/pipes default to a legacy code page
    try:
        _s.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

CONFIG_NAME = 'team-sync.json'
DEFAULTS = {
    'team': 'team',
    'lang': 'en',
    'env_prefix': 'TEAM',
    'env_file': '.env.team',
    'fallback_env': {'file': '', 'map': {}},
    'rules_file': 'AGENTS.md',
    'journal': 'docs/journal.md',
    'fingerprint': {'root': '', 'manifest': 'data_manifest.json'},
    'drive': {'root_name': '', 'marker': '', 'patterns': []},
    'sync': {'autosync_interval_sec': 300, 'log_commits': 12, 'journal_tail': 8, 'inject_ttl_hours': 6},
    'source': 'git+https://github.com/dmnbme/agent-team-sync',
    'version': '',
}
ENV_KEYS = ('WHO', 'SUPABASE_URL', 'SUPABASE_ANON_KEY', 'TEAM_KEY', 'DRIVE_ROOT')
_DRIVE_PATTERNS = (
    '~/Library/CloudStorage/GoogleDrive-*/共享云端硬盘', '~/Library/CloudStorage/GoogleDrive-*/Shared drives',
    '~/Google Drive/共享云端硬盘', '~/Google Drive/Shared drives',
    '~/Library/CloudStorage/OneDrive-*', '~/Library/CloudStorage/Dropbox', '~/Dropbox',
) + tuple(f'{d}:/共享云端硬盘' for d in 'DEFGHIJKLMNOPQRSTUVWXYZ') + tuple(f'{d}:/Shared drives' for d in 'DEFGHIJKLMNOPQRSTUVWXYZ')


def _merge(base, over):
    out = dict(base)
    for k, v in (over or {}).items():
        out[k] = _merge(base[k], v) if isinstance(v, dict) and isinstance(base.get(k), dict) else v
    return out


def find_repo(start=None):
    """The repo root: the nearest ancestor holding team-sync.json, else .git. Agents set a project-dir variable; honour it."""
    for var in ('TEAM_SYNC_REPO', 'CLAUDE_PROJECT_DIR', 'CODEBUDDY_PROJECT_DIR'):
        v = os.environ.get(var)
        if v and pathlib.Path(v).is_dir():
            return pathlib.Path(v).resolve()
    p = pathlib.Path(start or os.getcwd()).resolve()
    for cand in (p, *p.parents):
        if (cand / CONFIG_NAME).exists():
            return cand
    for cand in (p, *p.parents):
        if (cand / '.git').exists():
            return cand
    return p


def _parse_env_file(path):
    out = {}
    if not path.exists():
        return out
    for line in path.read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        k, v = line.split('=', 1)
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out


class Config:
    def __init__(self, repo=None):
        self.repo = pathlib.Path(repo) if repo else find_repo()
        raw = {}
        f = self.repo / CONFIG_NAME
        if f.exists():
            raw = json.loads(f.read_text(encoding='utf-8'))
        self.data = _merge(DEFAULTS, raw)
        self.exists = f.exists()
        self.lang = self.data.get('lang') or 'en'
        self.prefix = self.data['env_prefix']
        self.env_file = self.repo / self.data['env_file']
        self.state_dir = self.repo / '.git' / 'team-sync'
        self._env = None

    # ── env ──
    def key(self, name):
        return f'{self.prefix}_{name}'

    def env(self):
        if self._env is None:
            e = _parse_env_file(self.env_file)
            fb = self.data.get('fallback_env') or {}
            if fb.get('file'):
                fe = _parse_env_file(self.repo / fb['file'])
                for mine, theirs in (fb.get('map') or {}).items():
                    e.setdefault(mine, fe.get(theirs, ''))
            for n in ENV_KEYS:
                k = self.key(n)
                if os.environ.get(k):
                    e[k] = os.environ[k]
            self._env = {k: v for k, v in e.items() if v}
        return self._env

    def get(self, name):
        return self.env().get(self.key(name))

    # ── identity ──
    def who(self):
        return self.get('WHO') or os.environ.get('USER') or os.environ.get('USERNAME') or 'unknown'

    def holder_prefix(self):
        return f"{self.who()}@{socket.gethostname().split('.')[0]}"

    def holder(self):
        """person@machine:process. Two agents on one machine are two holders; set TEAM_SYNC_SESSION_ID to share one."""
        return f"{self.holder_prefix()}:{os.environ.get('TEAM_SYNC_SESSION_ID') or os.getpid()}"

    # ── shared drive ──
    def drive(self):
        d = self.data.get('drive') or {}
        root_name, marker = d.get('root_name') or '', d.get('marker') or ''
        if not root_name:
            return None
        def ok(p):
            return p.is_dir() and (not marker or (p / marker).exists())
        hint = self.get('DRIVE_ROOT')
        if hint:
            p = pathlib.Path(os.path.expanduser(hint))
            if ok(p / root_name):
                return p / root_name
            if ok(p):
                return p
        for pat in list(d.get('patterns') or []) + list(_DRIVE_PATTERNS):
            for m in glob.glob(os.path.expanduser(pat)):
                if ok(pathlib.Path(m) / root_name):
                    return pathlib.Path(m) / root_name
        return None

    def require_drive(self):
        p = self.drive()
        if p is None:
            raise SystemExit(f"shared drive not found; set {self.key('DRIVE_ROOT')} in {self.env_file.name}")
        return p
