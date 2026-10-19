"""Conversation records: what the user and the agent said to each other, kept in the repo so teammates' agents can read it.

One Markdown file per session under `convo.dir` in team-sync.json (off when unset). The agent's turn-end hook calls
update() with the agent's local transcript (Claude Code JSONL, Codex rollout JSONL). Tool calls, tool output, thinking
and injected context are left out; credential-shaped strings are redacted with the commit gate's patterns. When the
verbatim part passes `compact_chars`, the hook asks the running agent to summarise the older part and hand the summary to
`team-sync convo compact`; the newest `keep_recent_chars` stay verbatim.

Opt-outs: a user message containing one of NO_RECORD drops that session's record; TEAM_SYNC_NO_CONVO=1 in the
environment, or <PREFIX>_CONVO=off in the env file, turns recording off on that machine.
"""
import datetime as dt
import glob
import json
import os
import pathlib
import re
import time

from .gate import LINE_PAT
from .msg import t

AGENT_LABEL = {'claude': 'Claude Code', 'codex': 'Codex'}
NO_RECORD = ('不记录这段对话', '不记录本次对话', '暂停记录', '[no-record]')        # pause: from this message on
RESUME = ('恢复记录', '继续记录', '[record]')                                    # resume from this message on
RESUME_FILL = ('恢复记录并补上', '补上刚才的记录', '[record-all]')                # resume, and add what was held back
MARK = re.compile(r'<!-- team-sync-convo (\{.*?\}) -->')
MSG_MARK = re.compile(r'^<!-- msg (\S+) (?:user|agent|note) -->$', re.M)
SUM_START, SUM_END = '<!-- summaries:start -->', '<!-- summaries:end -->'
KEYVAL = re.compile(r'(?i)((?:password|passwd|secret|token|api[_-]?key|密码)\s*[:=：]\s*)(["\']?)[^\s"\'，。]{6,}')
REMINDER = re.compile(r'<system-reminder>.*?</system-reminder>', re.S)
EMAIL = re.compile(r'(?<![\w.+-])[\w.+-]+@((?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,})')
SKIP_PREFIX = ('<', '[Request interrupted', 'This session is being continued', '# AGENTS.md instructions',
               '[team-sync hook', '[团队同步 hook')
MERGE_GAP_SEC = 1800            # consecutive agent texts closer than this are one turn, shown as one block
ASK_AGAIN_SEC, MAX_ASKS = 600, 3


# ---------------------------------------------------------------- config
def _dir(cfg):
    return ((cfg.data.get('convo') or {}).get('dir') or '').strip().strip('/')


def recording(cfg):
    off = os.environ.get('TEAM_SYNC_NO_CONVO') or (cfg.get('CONVO') or '').lower() in ('off', '0', 'no', 'false')
    return None if not _dir(cfg) or off else (cfg.data.get('convo') or {})


def is_record(cfg, relpath):
    d = _dir(cfg)
    return bool(d) and relpath.replace('\\', '/').startswith(d + '/')


def redact(cfg, text):
    """Credential-shaped strings → [redacted]; e-mail addresses → [email] unless on one of convo.keep_email_domains."""
    extra = [x for x in cfg.data.get('gate_extra_patterns', []) if x]
    pat = re.compile(LINE_PAT.pattern + ('|' + '|'.join(extra) if extra else ''))
    text = KEYVAL.sub(lambda m: m.group(1) + m.group(2) + '[redacted]', pat.sub('[redacted]', text))
    keep = tuple(d.lower().lstrip('@.') for d in (cfg.data.get('convo') or {}).get('keep_email_domains') or [])
    own = lambda dom: any(dom == k or dom.endswith('.' + k) for k in keep)
    return EMAIL.sub(lambda m: m.group(0) if own(m.group(1).lower()) else '[email]', text)


# ---------------------------------------------------------------- transcript parsers: one JSON object in, [(iso, role, text)] out
def _text(content):
    if isinstance(content, str):
        return content
    out = []
    for b in content or []:
        if isinstance(b, dict) and b.get('type') in ('text', 'input_text', 'output_text'):
            out.append(b.get('text') or '')
        elif isinstance(b, dict) and b.get('type') in ('image', 'input_image'):
            out.append('[image]')
    return '\n'.join(out)


def _user_text(content):
    s = REMINDER.sub('', _text(content)).strip()
    return '' if not s or s.startswith(SKIP_PREFIX) else s


def _claude(d):
    if d.get('isSidechain') or d.get('isMeta') or d.get('isCompactSummary') or d.get('isApiErrorMessage'):
        return []
    ts, typ = d.get('timestamp') or '', d.get('type')
    if typ == 'attachment':                                       # typed while the agent was still working
        a = d.get('attachment') or {}
        if a.get('type') == 'queued_command' and a.get('commandMode', 'prompt') == 'prompt' \
                and (a.get('origin') or {}).get('kind', 'human') == 'human':
            s = _user_text(a.get('prompt'))
            return [(ts, 'user', s)] if s else []
        return []
    m = d.get('message') if isinstance(d.get('message'), dict) else {}
    c = m.get('content')
    if typ == 'user':
        if isinstance(c, list) and any(isinstance(b, dict) and b.get('type') == 'tool_result' for b in c):
            r = d.get('toolUseResult')
            if isinstance(r, dict) and isinstance(r.get('answers'), dict) and r['answers']:   # the user's picks in a multiple-choice question
                notes = r.get('annotations') if isinstance(r.get('annotations'), dict) else {}
                s = '\n'.join(f'[answer] {q} → {a}' + (f" ({notes[q]['notes']})" if isinstance(notes.get(q), dict) and notes[q].get('notes') else '')
                              for q, a in r['answers'].items())
                return [(ts, 'user', s)]
            return []
        if 'origin' in d and (d.get('origin') or {}).get('kind') != 'human':   # task notifications, peer sessions
            return []
        s = _user_text(c)
        return [(ts, 'user', s)] if s else []
    if typ == 'assistant' and m.get('model') != '<synthetic>' and isinstance(c, list):
        parts = []
        for b in c:
            if not isinstance(b, dict):
                continue
            if b.get('type') == 'text' and (b.get('text') or '').strip():
                parts.append(b['text'].strip())
            elif b.get('type') == 'tool_use' and b.get('name') == 'AskUserQuestion':
                for q in (b.get('input') or {}).get('questions') or []:
                    opts = [f"- {o.get('label', '')}: {o.get('description', '')}" for o in q.get('options') or [] if isinstance(o, dict)]
                    parts.append('\n'.join([f"[question] {q.get('question', '')}"] + opts))
        s = '\n\n'.join(parts)
        return [(ts, 'agent', s)] if s else []
    return []


def _codex(d):
    p = d.get('payload') or {}
    if d.get('type') != 'response_item' or p.get('type') != 'message' or p.get('role') not in ('user', 'assistant'):
        return []
    ts = d.get('timestamp') or ''
    if p['role'] == 'user':                                       # AGENTS.md, environment context, hook context: skipped
        s = _user_text(p.get('content'))
        return [(ts, 'user', s)] if s else []
    s = _text(p.get('content')).strip()
    return [(ts, 'agent', s)] if s else []


PARSERS = {'claude': _claude, 'codex': _codex}


def _codex_rollout(sid):
    home = os.environ.get('CODEX_HOME') or os.path.join(os.path.expanduser('~'), '.codex')
    hits = glob.glob(os.path.join(home, 'sessions', '*', '*', '*', f'rollout-*{sid}*.jsonl'))
    return max(hits, key=os.path.getmtime) if hits else None


def codex_is_exec(data):
    """True for a scripted `codex exec` run (session_meta source=exec): batch jobs, not conversations."""
    sid = re.sub(r'[^A-Za-z0-9_-]', '', str(data.get('session_id') or ''))
    tpath = data.get('transcript_path') or (_codex_rollout(sid) if sid else None)
    if not tpath or not os.path.isfile(tpath):
        return False
    try:
        with open(tpath, encoding='utf-8') as f:
            for _, line in zip(range(20), f):
                d = json.loads(line)
                if d.get('type') == 'session_meta':
                    p = d.get('payload') or {}
                    return p.get('source') == 'exec' or p.get('originator') == 'codex_exec'
    except (OSError, ValueError):
        pass
    return False


def _read(path, offset, parse):
    """Complete lines from byte `offset`; a half-written last line waits for the next call."""
    with open(path, 'rb') as f:
        f.seek(offset)
        chunk = f.read()
    end = chunk.rfind(b'\n') + 1
    out = []
    for line in chunk[:end].splitlines():
        try:
            d = json.loads(line)
        except ValueError:
            continue
        if isinstance(d, dict):
            mid = d.get('uuid') or (d.get('payload') or {}).get('id') or ''   # message id: survives forks and continuations
            out.extend(item + (mid,) for item in parse(d))
    return out, offset + end


# ---------------------------------------------------------------- helpers
def _secs(iso):
    try:
        return dt.datetime.fromisoformat(iso.replace('Z', '+00:00')).timestamp()
    except (AttributeError, ValueError):
        return None


def _local(iso, fmt='%m-%d %H:%M'):
    try:
        return dt.datetime.fromisoformat(iso.replace('Z', '+00:00')).astimezone().strftime(fmt)
    except (AttributeError, ValueError):
        return (iso or '')[:16].replace('T', ' ')


def _tz(iso):
    z = _local(iso, '%z')
    if not re.fullmatch(r'[+-]\d{4}', z):
        return ''
    return f'UTC{z[0]}{int(z[1:3])}' + (f':{z[3:]}' if z[3:] != '00' else '')


def _append(msgs, new):
    """msgs rows are [first_iso, role, text, last_iso]; one agent turn becomes one row."""
    for iso, role, text, *_ in new:
        last = msgs[-1] if msgs else None
        a, b = (_secs(last[3]) if last else None), _secs(iso)
        close = a is not None and b is not None and abs(b - a) < MERGE_GAP_SEC
        if last and close and role == last[1] == 'agent':
            last[2] += '\n\n' + text
            last[3] = iso
        elif last and close and role == last[1] == 'user' and text == last[2]:
            continue
        else:
            msgs.append([iso, role, text, iso])


def _name(cfg, agent, sid, first_iso):
    day = _local(first_iso, '%Y-%m-%d')[:10]
    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', day):
        day = dt.date.today().isoformat()
    who = re.sub(r'[^\w-]', '', cfg.who()) or 'unknown'
    return f"{day[:7]}/{day}_{who}_{agent}_{re.sub(r'[^A-Za-z0-9]', '', sid)[:8]}.md"


def _head_of(text):
    m = MARK.search(text[:8000])
    try:
        return json.loads(m.group(1)) if m else {}
    except ValueError:
        return {}


def _summaries_of(text):
    i, j = text.find(SUM_START), text.find(SUM_END)
    return text[i + len(SUM_START):j].strip('\n') if 0 <= i < j else ''


def _dump(head):
    return json.dumps(head, ensure_ascii=False).replace('>', '\\u003e')       # keeps "-->" out of the comment


def _write(cfg, path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    try:                                                          # atomic where the state dir is writable (not in a Codex sandbox)
        tmp = cfg.state_dir / 'convo' / (path.name + '.tmp')
        tmp.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(text, encoding='utf-8')
        os.replace(tmp, path)
    except OSError:
        path.write_text(text, encoding='utf-8')


def _cache(cfg, agent, sid):
    return cfg.state_dir / 'convo' / f"{agent}_{re.sub(r'[^A-Za-z0-9_-]', '', sid)[:80]}.json"


def _load(p):
    try:
        return json.loads(p.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}


def _save(p, st):
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(st, ensure_ascii=False), encoding='utf-8')
    except OSError:
        pass


def _render(cfg, head, summaries, msgs, limit):
    L, through = cfg.lang, head.get('through') or ''
    agent = AGENT_LABEL.get(head['agent'], head['agent'])
    out = [t(L, 'convo_title', who=head['who'], agent=agent, date=_local(head['start'], '%Y-%m-%d')),
           '<!-- team-sync-convo ' + _dump(head) + ' -->', '',
           t(L, 'convo_intro', limit=limit, tz=_tz(head.get('last') or '') or '?')]
    if head.get('parent'):
        out.append(t(L, 'convo_parent', file=head['parent']))
    if head.get('prev'):
        out.append(t(L, 'convo_prev_month', file=head['prev']))
    if head.get('next'):
        out.append(t(L, 'convo_next_month', file=head['next']))
    if head.get('title'):
        out.append(t(L, 'convo_opening', title=head['title']))
    out += ['', t(L, 'convo_summaries'), SUM_START, summaries.strip() or t(L, 'convo_none'), SUM_END, '',
            t(L, 'convo_raw_since', since=_local(through)) if through else t(L, 'convo_raw'), '']
    for row in msgs:
        iso, role, text = row[0], row[1], row[2]
        if through and iso <= through:
            continue
        if role == 'note':                                        # pause / resume
            out += [f'<!-- msg {iso} note -->', f'> {_local(iso)} · {text}', '']
            continue
        out += [f'<!-- msg {iso} {role} -->', f"**{_local(iso)} · {head['who'] if role == 'user' else agent}**", '', text, '']
    return '\n'.join(out).rstrip('\n') + '\n'


# ---------------------------------------------------------------- forks and continuations
# A forked or continued session starts with a copy of the earlier transcript (same message ids). Each message belongs to the
# first session that recorded it; later sessions skip it and link to that record instead of repeating it.
def _index_path(cfg):
    return cfg.state_dir / 'convo' / 'index.json'


def _caches(cfg):
    return [p for p in (cfg.state_dir / 'convo').glob('*.json') if p.name != 'index.json']


def _first_seen(path):
    """(first timestamp, creation time) of a transcript, to order sessions that share history."""
    first = ''
    try:
        with open(path, encoding='utf-8') as f:
            for line in f:
                try:
                    first = json.loads(line).get('timestamp') or ''
                except ValueError:
                    continue
                if first:
                    break
        st = os.stat(path)
        return first, getattr(st, 'st_birthtime', st.st_mtime)
    except OSError:
        return first, 0.0


def _index(cfg):
    """message id -> cache key of the session that owns it; built once from every cached session, oldest first."""
    ip = _index_path(cfg)
    idx = _load(ip)
    if idx.get('v') == 1:
        return idx
    idx = {'v': 1, 'ids': {}}
    sessions = []
    for cp in _caches(cfg):
        st = _load(cp)
        if st.get('path') and os.path.isfile(st['path']):
            sessions.append((_first_seen(st['path']), cp, st))
    for _, cp, st in sorted(sessions, key=lambda x: x[0]):
        agent = cp.stem.split('_', 1)[0]
        if agent not in PARSERS:
            continue
        msgs, _ = _read(st['path'], 0, PARSERS[agent])
        for m in msgs:
            if m[-1]:
                idx['ids'].setdefault(m[-1], cp.stem)
    _save(ip, idx)
    for _, cp, st in sessions:                                    # re-render every record so the copies drop out
        agent, sid = cp.stem.split('_', 1)
        _save(cp, {'path': st['path'], 'offset': 0, 'msgs': [], 'file': st.get('file'), 'off': st.get('off'), 'prev_file': st.get('prev_file')})
        update(cfg, agent, {'session_id': sid, 'transcript_path': st['path'], 'no_ask': True}, idx)
    return idx


ASKING = re.compile(r'[?？]|吗|怎么|怎样|如何|如果|假如|会不会|是不是|能不能|可不可以|what|how|does|will|\bif\b', re.I)


def _is_cmd(text, phrases):
    """True only when the message is the instruction itself (it opens the message, or ends a very short one), not a
    question about it or a passing mention: asking how the switch works must not flip it."""
    s = re.sub(r'\s+', '', text)
    for k in phrases:
        k2 = k.replace(' ', '')
        if (k2 not in s.lower()) if k2.startswith('[') else (k2 not in s):
            continue
        if ASKING.search(text):
            return False
        if s.lower().startswith(k2) or (s.endswith(k2) and len(s) - len(k2) <= 6):     # "不记录这段对话,…" / "这段请不记录这段对话"
            return True
    return False


def command(text):
    """'fill', 'on', 'off' or None for one user message."""
    for name, phrases in (('fill', RESUME_FILL), ('on', RESUME), ('off', NO_RECORD)):
        if _is_cmd(text, phrases):
            return name
    return None


def opts_out(text):
    return command(text) == 'off'


def _apply(cfg, st, new):
    """Add this turn's messages to the record, honouring pause / resume. While paused, messages are held in the local
    cache only (never written to the repo), so "恢复记录并补上" can still add them after a false trigger."""
    held = st.setdefault('held', [])
    for iso, role, text in new:
        cmd = command(text) if role == 'user' else None
        if cmd == 'off' and not st.get('off'):
            st['off'] = True
            _append(st['msgs'], [(iso, 'note', t(cfg.lang, 'convo_paused_note'))])
        elif cmd in ('on', 'fill') and st.get('off'):
            st['off'] = False
            if cmd == 'fill' and held:
                paused = t(cfg.lang, 'convo_paused_note')
                for row in reversed(st['msgs']):                 # the pause line no longer says "not recorded"
                    if row[1] == 'note' and row[2] == paused:
                        row[2] = t(cfg.lang, 'convo_paused_filled_note'); break
                _append(st['msgs'], held)
                _append(st['msgs'], [(iso, 'note', t(cfg.lang, 'convo_filled_note', n=len(held)))])
            else:
                _append(st['msgs'], [(iso, 'note', t(cfg.lang, 'convo_resumed_note'))])
            st['held'] = held = []
        if st.get('off'):
            held.append([iso, role, text])
        else:
            _append(st['msgs'], [(iso, role, text)])


def turn_notice(cfg, agent, data):
    """Before a turn: (message for the user, context for the agent) when this session is paused or this prompt flips it;
    (None, None) otherwise. Same rule as the turn-end hook, applied to the prompt about to be sent."""
    if recording(cfg) is None or agent not in PARSERS:
        return None, None
    sid = re.sub(r'[^A-Za-z0-9_-]', '', str(data.get('session_id') or ''))
    off = bool(_load(_cache(cfg, agent, sid)).get('off')) if sid else False
    cmd = command(str(data.get('prompt') or ''))
    if cmd == 'off' or (off and cmd not in ('on', 'fill')):
        # Agent UIs hide or fold hook notices (Codex shows none, Claude Code folds it), so the agent also opens its reply with it
        return t(cfg.lang, 'convo_turn_paused'), t(cfg.lang, 'convo_turn_paused_ctx') + '\n' + t(cfg.lang, 'convo_first_line', line=t(cfg.lang, 'convo_turn_paused'))
    if off and cmd == 'fill':
        return t(cfg.lang, 'convo_turn_filled'), t(cfg.lang, 'convo_first_line', line=t(cfg.lang, 'convo_turn_filled'))
    if off and cmd == 'on':
        return t(cfg.lang, 'convo_turn_resumed'), t(cfg.lang, 'convo_first_line', line=t(cfg.lang, 'convo_turn_resumed'))
    return None, None


# ---------------------------------------------------------------- turn end (hook)
def _head(cfg, agent, sid, st, msgs, prev):
    first_user = next((m[2] for m in msgs if m[1] == 'user'), '')
    head = {'session': sid, 'agent': agent, 'who': cfg.who(), 'title': re.sub(r'\s+', ' ', first_user)[:60] or prev.get('title', ''),
            'start': msgs[0][0], 'last': msgs[-1][3], 'through': prev.get('through') or ''}
    if st.get('parent'):
        pf = _load(cfg.state_dir / 'convo' / f"{st['parent']}.json").get('file')
        if pf:
            head['parent'] = f"{_dir(cfg)}/{pf}"
    if st.get('prev_file'):
        head['prev'] = f"{_dir(cfg)}/{st['prev_file']}"
    return head


def update(cfg, agent, data, idx=None):
    """Turn-end hook body. Returns None, or the text of a request asking the agent to compact the record."""
    c = recording(cfg)
    if c is None or agent not in PARSERS:
        return None
    sid = re.sub(r'[^A-Za-z0-9_-]', '', str(data.get('session_id') or ''))
    tpath = data.get('transcript_path') or (_codex_rollout(sid) if agent == 'codex' and sid else None)
    if not sid or not tpath or not os.path.isfile(tpath):
        return None
    cf = _cache(cfg, agent, sid)
    idx = idx if idx is not None else _index(cfg)                 # first run on a machine: builds it and re-renders old records
    st = _load(cf)
    if st.get('path') != tpath or os.path.getsize(tpath) < int(st.get('offset') or 0):
        st = {'path': tpath, 'offset': 0, 'msgs': [], 'file': st.get('file'), 'off': st.get('off')}
    new, st['offset'] = _read(tpath, int(st.get('offset') or 0), PARSERS[agent])
    me, own = cf.stem, []
    for iso, role, text, mid in new:
        owner = idx['ids'].get(mid) if mid else None
        if owner and owner != me:                                 # copied from an earlier session
            st['parent'] = owner                                 # the latest session it copied from
            continue
        if mid:
            idx['ids'][mid] = me
        own.append((iso, role, redact(cfg, text)))
    if new and any(m[-1] for m in new):
        _save(_index_path(cfg), idx)
    new = own
    _apply(cfg, st, new)
    msgs = st['msgs']
    if not msgs:
        if st.get('file') and (cfg.repo / _dir(cfg) / st['file']).exists():     # everything in it was a copy
            (cfg.repo / _dir(cfg) / st['file']).unlink()
        _save(cf, st)
        return None
    if not st.get('file'):
        st['file'] = _name(cfg, agent, sid, msgs[0][0])
    limit, keep = int(c.get('compact_chars') or 30000), int(c.get('keep_recent_chars') or 8000)
    # One file per session per month: when a session runs into a new month, this month's file is closed (with a link
    # forward) and the session goes on in a file under the new month's folder (with a link back).
    if st.get('prev_file') and msgs and _local(msgs[0][0], '%Y-%m-%d')[:7] < st['file'][:7]:   # re-read from the start (index rebuild)
        st['msgs'] = msgs = [m for m in msgs if _local(m[0], '%Y-%m-%d')[:7] >= st['file'][:7]] or msgs[-1:]
    cut = next((i for i, m in enumerate(msgs) if _local(m[0], '%Y-%m-%d')[:7] > st['file'][:7]), None)
    if cut is not None:
        nxt = _name(cfg, agent, sid, msgs[cut][0])
        if cut > 0:
            opath = cfg.repo / _dir(cfg) / st['file']
            otext = opath.read_text(encoding='utf-8') if opath.exists() else ''
            ohead = _head(cfg, agent, sid, st, msgs[:cut], _head_of(otext))
            ohead['next'] = f"{_dir(cfg)}/{nxt}"
            _write(cfg, opath, _render(cfg, ohead, _summaries_of(otext), msgs[:cut], limit))
        st['prev_file'], st['file'] = st['file'], nxt
        st['msgs'] = msgs = msgs[cut:]
        st.pop('asked', None); st['asks'] = 0
    path = cfg.repo / _dir(cfg) / st['file']
    old = path.read_text(encoding='utf-8') if path.exists() else ''
    head = _head(cfg, agent, sid, st, msgs, _head_of(old))
    shown = msgs
    tail = data.get('last_assistant_message')                     # Codex passes the final message; use it if not on disk yet
    if isinstance(tail, str) and tail.strip() and not st.get('off'):
        tail = redact(cfg, tail.strip())
        if not any(m[1] == 'agent' and tail[-200:] in m[2] for m in msgs[-3:]):
            now = dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.000Z')
            shown = msgs + [[now, 'agent', tail, now]]
    text = _render(cfg, head, _summaries_of(old), shown, limit)
    if text != old:
        _write(cfg, path, text)
    ask = _ask(cfg, st, head, msgs, limit, keep, data, path)
    _save(cf, st)
    return ask


def _ask(cfg, st, head, msgs, limit, keep, data, path):
    through = head['through']
    raw = [m for m in msgs if not through or m[0] > through]
    size = sum(len(m[2]) for m in raw)
    if size <= limit:
        st['asks'] = 0
        return None
    if data.get('no_ask') or data.get('stop_hook_active') or time.time() - float(st.get('asked') or 0) < ASK_AGAIN_SEC or int(st.get('asks') or 0) >= MAX_ASKS:
        return None
    acc, cut = 0, None
    for i in range(len(raw) - 1, -1, -1):                         # keep the newest `keep` characters verbatim
        acc += len(raw[i][2])
        if acc >= keep:
            cut = i
            break
    if not cut:
        return None
    older = raw[:cut]
    st['asked'], st['asks'] = time.time(), int(st.get('asks') or 0) + 1
    return t(cfg.lang, 'convo_compact_ask', file=path.relative_to(cfg.repo).as_posix(), n=size, limit=limit,
             chars=sum(len(m[2]) for m in older), since=_local(older[0][0]), through=older[-1][0])


# ---------------------------------------------------------------- compact (the agent runs this when asked)
def compact(cfg, file, through, summary):
    L = cfg.lang
    path = pathlib.Path(file) if os.path.isabs(file) else cfg.repo / file
    try:
        s = path.read_text(encoding='utf-8')
    except OSError:
        print(t(L, 'convo_no_file', file=file))
        return 1
    m = MARK.search(s)
    i, j = s.find(SUM_START), s.find(SUM_END)
    if not m or not (m.end() <= i < j):
        print(t(L, 'convo_no_file', file=file))
        return 1
    head = json.loads(m.group(1))
    summary = redact(cfg, (summary or '').replace(SUM_START, '').replace(SUM_END, '').strip())
    if len(summary) < 20:
        print(t(L, 'convo_short'))
        return 1
    old = head.get('through') or ''
    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}T[0-9:.]+(Z|[+-]\d{2}:\d{2})?', through or '') or through <= old:
        print(t(L, 'convo_bad_through', through=through, old=old or '-'))
        return 1
    body = s[j + len(SUM_END):]
    starts = [x.start() for x in MSG_MARK.finditer(body)]
    blocks = [body[a:b] for a, b in zip(starts, starts[1:] + [len(body)])]
    covered = [x for x in blocks if old < MSG_MARK.match(x).group(1) <= through]
    kept = [x for x in blocks if MSG_MARK.match(x).group(1) > through]
    first = MSG_MARK.match(covered[0]).group(1) if covered else through
    cur = s[i + len(SUM_START):j].strip()
    block = t(L, 'convo_range', a=_local(first), b=_local(through)) + '\n\n' + summary
    head['through'] = through
    out = (s[:m.start(1)] + _dump(head) + s[m.end(1):i + len(SUM_START)] + '\n'
           + (block if cur in ('', t(L, 'convo_none')) else cur + '\n\n' + block) + '\n' + SUM_END + '\n\n'
           + t(L, 'convo_raw_since', since=_local(through)) + '\n\n' + ''.join(kept))
    _write(cfg, path, out.rstrip('\n') + '\n')
    cf = _cache(cfg, head.get('agent', ''), head.get('session', ''))
    st = _load(cf)
    if st:
        st['asks'] = 0
        _save(cf, st)                                             # silently skipped inside a read-only sandbox
    print(t(L, 'convo_compacted', upto=_local(through), file=path.relative_to(cfg.repo).as_posix()))
    return 0


# ---------------------------------------------------------------- session start
def recent(cfg, n=5):
    d = _dir(cfg)
    root = cfg.repo / d if d else None
    if not root or not root.is_dir():
        return ''
    rows = []
    for mdir in sorted((p for p in root.iterdir() if p.is_dir()), reverse=True)[:2]:
        for p in mdir.glob('*.md'):
            try:
                h = _head_of(p.read_text(encoding='utf-8')[:8000])
            except OSError:
                continue
            if h:
                rows.append((h.get('last') or '', h, p))
    rows.sort(key=lambda r: r[0], reverse=True)
    lines = [f"   {_local(last)} · {h.get('who')} · {AGENT_LABEL.get(h.get('agent'), h.get('agent'))} · "
             f"{(h.get('title') or '')[:30]} → {p.relative_to(cfg.repo).as_posix()}" for last, h, p in rows[:n]]
    return (t(cfg.lang, 'convo_recent') + '\n' + '\n'.join(lines)) if lines else ''
