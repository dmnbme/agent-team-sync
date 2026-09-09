"""Secret gate for git pre-commit: refuse commits that add credential-shaped strings or credential files.
Runs under plain Python so it behaves the same on macOS, Linux and Windows (Git Bash)."""
import re
import subprocess

from .msg import t

FILE_PAT = re.compile(r'(^|/)(\.env[^/]*|config\.local\.json|[^/]*\.session(-journal)?|[^/]*_access_token[^/]*|\.site_password\.txt|[^/]*\.pem|id_rsa[^/]*)$')
ALLOW = re.compile(r'\.example$|\.sample$')
LINE_PAT = re.compile(
    r'sb_secret_[A-Za-z0-9_]{20,}|sbp_[A-Za-z0-9_]{20,}|sk_live_[A-Za-z0-9]{8,}|whsec_[A-Za-z0-9+/=]{8,}|re_[A-Za-z0-9]{20,}'
    r'|sk-[A-Za-z0-9_-]{32,}|ghp_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{20,}|AKIA[0-9A-Z]{16}|xox[abp]-[A-Za-z0-9-]{10,}'
    r'|eyJhbGciOi[A-Za-z0-9_-]{30,}|-----BEGIN [A-Z ]*PRIVATE KEY-----|_TEAM_KEY=[A-Za-z0-9]{16,}')


def run(cfg):
    g = lambda *a: subprocess.run(['git', *a], cwd=cfg.repo, capture_output=True, text=True, encoding='utf-8', errors='replace').stdout
    files = [f for f in g('diff', '--cached', '--name-only', '--diff-filter=ACMR').splitlines() if f.strip()]
    bad_files = [f for f in files if FILE_PAT.search(f) and not ALLOW.search(f)]
    extra = [x for x in cfg.data.get('gate_extra_patterns', []) if x]
    line_pat = re.compile(LINE_PAT.pattern + ('|' + '|'.join(extra) if extra else ''))
    hits = []
    for f in files:
        if ALLOW.search(f) or f.endswith('gate.py'):
            continue
        for line in g('diff', '--cached', '-U0', '--', f).splitlines():
            if line.startswith('+') and not line.startswith('+++') and line_pat.search(line):
                hits.append(f'{f}: {line[:140]}')
    if not bad_files and not hits:
        return 0
    if bad_files:
        print(t(cfg.lang, 'gate_files')); [print('   ' + f) for f in bad_files]
    if hits:
        print(t(cfg.lang, 'gate_lines')); [print('   ' + h) for h in hits[:20]]
    print(t(cfg.lang, 'gate_hint', env_file=cfg.env_file.name))
    return 1
