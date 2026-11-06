"""Turn-end reminders (Claude Code, Codex): when this session changed files in the team repository, ask the agent once to
decide whether the change deserves a journal note, and whether a knowledge doc that covers those files needs updating.

The agent decides; the hook only notices. A request is made at most once per batch of new changes, never inside the turn
that answers a request (`stop_hook_active`), not more often than `again_min` minutes, and at most `max_asks` times per
session. Config (team-sync.json):

    "remind": {
      "journal": true,                 # ask about a journal note (default true)
      "docs": [                        # knowledge docs and the paths whose changes may make them stale
        {"paths": ["data/", "docs/site-changes.md"], "doc": "docs/knowledge/pipeline.md"}
      ],
      "ignore": ["tmp/"],              # paths never counted as changes
      "again_min": 60, "max_asks": 4
    }

Changes are this machine's sync commits since the session's first prompt, plus what is not committed yet; another session
on the same machine can show up too, so the request tells the agent to ignore what it did not do. Off unless "remind" is
in the config.
"""
from __future__ import annotations

import fnmatch
import json
import re
import time

KEEP_DAYS = 7


def _sid(data):
    return re.sub(r'[^A-Za-z0-9_-]', '', str(data.get('session_id') or ''))


def _conf(cfg):
    r = cfg.data.get('remind')
    return r if isinstance(r, dict) else {}


def _load(cfg):
    from .sync import state
    f = state(cfg, 'remind.json')
    try:
        st = json.loads(f.read_text(encoding='utf-8')) if f.exists() else {}
    except Exception:
        st = {}
    now = time.time()
    return f, {k: v for k, v in st.items() if isinstance(v, dict) and now - float(v.get('t0') or 0) < KEEP_DAYS * 86400}


def _save(f, st):
    try:
        f.write_text(json.dumps(st, ensure_ascii=False), encoding='utf-8')
    except OSError:
        pass


def _head(cfg):
    from .sync import git
    return git(cfg, 'rev-parse', 'HEAD').stdout.strip()


def mark(cfg, data):
    """UserPromptSubmit: remember where the repository stood when this session began."""
    sid = _sid(data)
    if not sid or not _conf(cfg):
        return
    f, st = _load(cfg)
    if sid not in st:
        st[sid] = {'base': _head(cfg), 't0': time.time(), 'jlen': len(_jlines(cfg)), 'asks': 0, 'last_ask': 0, 'seen': [], 'docs_asked': []}
        _save(f, st)


def _ignored(cfg, path, extra):
    from .convo import is_record
    from .inbox import is_message
    if path == cfg.data.get('journal') or is_record(cfg, path) or is_message(cfg, path):
        return True
    return any(path.startswith(p) or fnmatch.fnmatch(path, p) for p in extra)


def _changed(cfg, base, extra):
    """Files this person changed since `base`: their sync commits and other commits by them, then the working tree."""
    from .sync import git
    names = []
    if base and git(cfg, 'cat-file', '-e', base + '^{commit}').returncode == 0:
        email = git(cfg, 'config', 'user.email').stdout.strip()
        for args in ([f'--grep=^sync({cfg.who()}):'], [f'--author={email}'] if email else []):
            if args:
                out = git(cfg, '-c', 'core.quotePath=false', 'log', '--format=', '--name-only', *args, f'{base}..HEAD').stdout
                names += [l.strip() for l in out.splitlines() if l.strip()]
    st = git(cfg, '-c', 'core.quotePath=false', 'status', '--porcelain', '--untracked-files=all').stdout
    for line in st.splitlines():
        p = line[3:].strip()
        if ' -> ' in p:
            p = p.split(' -> ', 1)[1]
        names.append(p.strip('"'))
    out = []
    for p in names:
        if p and p not in out and not _ignored(cfg, p, extra):
            out.append(p)
    return out


def _jlines(cfg):
    from .sync import journal_path
    j = journal_path(cfg)
    try:
        return j.read_text(encoding='utf-8').splitlines() if j.exists() else []
    except OSError:
        return []


def _noted_after(cfg, n):
    """A journal line by this person, not an automatic one, among the lines after the first `n`."""
    who = cfg.who()
    pat = re.compile(r'^- \d{4}-\d{2}-\d{2} \d{2}:\d{2} · ([^·]+?) · (.*)$')
    for line in _jlines(cfg)[n:]:
        m = pat.match(line)
        if m and m.group(1).strip() == who and not m.group(2).startswith('auto · '):
            return True
    return False


def ask(cfg, data):
    """Stop: a request for the agent, or None."""
    from .msg import t
    rc = _conf(cfg)
    if not rc or data.get('stop_hook_active') or data.get('no_ask'):
        return None
    sid = _sid(data)
    if not sid:
        return None
    f, st = _load(cfg)
    s = st.get(sid)
    if s is None:                                                   # no prompt seen (hook added mid-session): start here
        st[sid] = {'base': _head(cfg), 't0': time.time(), 'jlen': len(_jlines(cfg)), 'asks': 0, 'last_ask': 0, 'seen': [], 'docs_asked': []}
        _save(f, st)
        return None
    now = time.time()
    if int(s.get('asks') or 0) >= int(rc.get('max_asks', 4)) or now - float(s.get('last_ask') or 0) < float(rc.get('again_min', 60)) * 60:
        return None
    extra = [p for p in rc.get('ignore') or [] if isinstance(p, str)]
    files = _changed(cfg, s.get('base'), extra)
    new = [p for p in files if p not in (s.get('seen') or [])]
    if not new:
        return None
    L, items = cfg.lang, []
    if rc.get('journal', True) and not _noted_after(cfg, int(s.get('jlen') or 0)):
        items.append(t(L, 'remind_journal'))
    asked = list(s.get('docs_asked') or [])
    for rule in rc.get('docs') or []:
        doc, paths = rule.get('doc'), [p for p in rule.get('paths') or [] if isinstance(p, str)]
        if not doc or doc in files or doc in asked:
            continue
        hits = [p for p in new if any(p.startswith(q) or fnmatch.fnmatch(p, q) for q in paths)]
        if hits:
            asked.append(doc)
            items.append(t(L, 'remind_doc', doc=doc, files=', '.join(hits[:4]) + (' …' if len(hits) > 4 else '')))
    s['seen'] = files
    if not items:
        _save(f, st)
        return None
    s.update(asks=int(s.get('asks') or 0) + 1, last_ask=now, docs_asked=asked, jlen=len(_jlines(cfg)))
    _save(f, st)
    shown = ', '.join(new[:6]) + (f' … ({len(new)})' if len(new) > 6 else '')
    return t(L, 'remind_ask', files=shown, items='\n'.join(f'{i}. {x}' for i, x in enumerate(items, 1)))
