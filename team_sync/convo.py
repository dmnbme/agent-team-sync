"""Conversation records: what the user and the agent said to each other, kept in the repo so teammates' agents can read it.

One Markdown file per session under `convo.dir` in team-sync.json (off when unset). The agent's turn-end hook calls
update() with the agent's local transcript (Claude Code JSONL, Codex rollout JSONL). Tool calls, tool output, thinking
and injected context are left out; credential-shaped strings are redacted with the commit gate's patterns. When the
verbatim part passes `compact_chars`, the hook asks the running agent to summarise the older part and hand the summary to
`team-sync convo compact`; the newest `keep_recent_chars` stay verbatim.

Opt-outs: a user message containing one of NO_RECORD drops that session's record; TEAM_SYNC_NO_CONVO=1 in the
environment, or <PREFIX>_CONVO=off in the env file, turns recording off on that machine.

Attachments: images the user pastes are saved once (named by content hash) under the shared drive's attachments folder
(`convo.attachments_dir`, by month) and the record shows where; without a shared drive they stay "[image]". Files the
user @-mentions are shown repo-relative, drive-relative, or, anywhere else, by file name only. The turn-end hook asks the
running agent for a one-line note on each new image and each such outside file (what it is and how it bears on the
work), which `team-sync convo note` writes next to the reference.
"""
import base64
import binascii
import datetime as dt
import glob
import hashlib
import itertools
import json
import os
import pathlib
import re
import time

from . import __version__
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
IMG_TOKEN = re.compile(r'⟦img:([^⟧]+)⟧')                         # in cached text: ⟦img:YYYY-MM/<hash>.<ext>⟧
IMG_EXT = {'image/png': 'png', 'image/jpeg': 'jpg', 'image/jpg': 'jpg', 'image/webp': 'webp', 'image/gif': 'gif'}
CODEX_IMG_WRAP = re.compile(r'^\s*(<image name=[^>]*>|</image>)\s*$')   # Codex wraps each pasted image in these two texts
CODEX_PREAMBLE = ('# Files mentioned by the user:', '# Context from my IDE setup:')   # Codex app / IDE extension
CODEX_REQUEST = re.compile(r'^## My request(?: for Codex)?:[ \t]*$', re.M)
CODEX_MENTION = re.compile(r'^## (.+?): (.+?)[ \t]*\n(Image attachment: true)?', re.M)
FILE_REF = re.compile(r'@"([^"\n]+)"|@((?:/|~/|[A-Za-z]:\\)[^\s"`\'，。、；：）)]+)')   # @"/a b/c.md" or @/a/c.md
NOTE_KEY = re.compile(r'^(img:[0-9a-f]{16}\.[a-z]{3,4}|file:[0-9a-f]{10})$')
MAX_NOTE_ASKS = 2
TITLE_MAX = 120
_IMGS = {}                      # <hash>.<ext> -> base64 data, filled while a transcript is parsed


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
def _image_token(b):
    """An image block (Claude Code base64 source, Codex data: URL) → ⟦img:<hash>.<ext>⟧, its data kept in _IMGS."""
    src = b.get('source') if isinstance(b.get('source'), dict) else {}
    data, mtype = src.get('data'), src.get('media_type')
    url = b.get('image_url')
    url = url.get('url') if isinstance(url, dict) else url
    if not data and isinstance(url, str) and url.startswith('data:'):
        head, _, data = url.partition(',')
        mtype = head[5:].split(';')[0]
    ext = IMG_EXT.get((mtype or '').lower())
    if not data or not ext:
        return None
    name = f"{hashlib.sha1(data.encode()).hexdigest()[:16]}.{ext}"
    _IMGS[name] = data
    return f'⟦img:{name}⟧'


def _text(content):
    if isinstance(content, str):
        return content
    out = []
    for b in content or []:
        if isinstance(b, dict) and b.get('type') in ('text', 'input_text', 'output_text'):
            if not CODEX_IMG_WRAP.match(b.get('text') or ''):
                out.append(b.get('text') or '')
        elif isinstance(b, dict) and b.get('type') in ('image', 'input_image'):
            out.append(_image_token(b) or '[image]')
    return '\n'.join(out)


def _user_text(content):
    s = REMINDER.sub('', _text(content)).strip()
    return '' if not s or s.startswith(SKIP_PREFIX) else s


def _claude(d):
    if d.get('isSidechain') or d.get('isMeta') or d.get('isCompactSummary') or d.get('isApiErrorMessage'):
        return []
    ts, typ = d.get('timestamp') or '', d.get('type')
    if typ in ('custom-title', 'ai-title'):                      # the name in the app's sidebar: renamed by the user / generated
        name = (d.get('customTitle') if typ == 'custom-title' else d.get('aiTitle')) or ''
        return [(ts, 'title_' + typ.split('-')[0], name.strip())] if name.strip() else []
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


def _codex_mentions(s):
    """Codex app and IDE extension put attachments in a preamble, not as @-mentions:
        # Files mentioned by the user:\n\n## a.pdf: /abs/a.pdf\n\n## b.png: /abs/b.png\nImage attachment: true\n\n…\n## My request:\n<text>
    → '@"/abs/a.pdf"\n\n<text>'. Image lines are dropped when the image itself came with the message; the IDE context
    (active file, open tabs) is not something the user mentioned and is dropped too."""
    if not s.startswith(CODEX_PREAMBLE):
        return s
    asks = list(CODEX_REQUEST.finditer(s))
    if not asks:
        return s
    head, body = s[:asks[-1].start()], s[asks[-1].end():].strip()
    files = head.split('# Context from my IDE setup:', 1)[0]
    found = list(CODEX_MENTION.finditer(files))
    imgs = sum(1 for m in found if m.group(3))
    keep_imgs = len(IMG_TOKEN.findall(body)) < imgs           # pictures not in the message: keep their paths
    refs = [f'@"{m.group(2)}"' for m in found if keep_imgs or not m.group(3)]
    return '\n'.join(refs) + ('\n\n' if refs and body else '') + body


def _codex(d):
    p = d.get('payload') or {}
    if d.get('type') == 'event_msg' and p.get('type') == 'thread_rolled_back':   # the user stepped back N turns
        return [(d.get('timestamp') or '', 'rollback', str(int(p.get('num_turns') or 0)))]
    if d.get('type') != 'response_item' or p.get('type') != 'message' or p.get('role') not in ('user', 'assistant'):
        return []
    ts = d.get('timestamp') or ''
    if p['role'] == 'user':                                       # AGENTS.md, environment context, hook context: skipped
        s = _codex_mentions(_user_text(p.get('content')))
        return [(ts, 'user', s)] if s else []
    s = _text(p.get('content')).strip()
    return [(ts, 'agent', s)] if s else []


PARSERS = {'claude': _claude, 'codex': _codex}


def _codex_rollout(sid):
    home = os.environ.get('CODEX_HOME') or os.path.join(os.path.expanduser('~'), '.codex')
    hits = glob.glob(os.path.join(home, 'sessions', '*', '*', '*', f'rollout-*{sid}*.jsonl'))
    return max(hits, key=os.path.getmtime) if hits else None


def _codex_title(sid):
    """The thread's name in the Codex app sidebar (generated, or as the user renamed it), from its session index."""
    home = os.environ.get('CODEX_HOME') or os.path.join(os.path.expanduser('~'), '.codex')
    name = ''
    try:
        with open(os.path.join(home, 'session_index.jsonl'), encoding='utf-8') as f:
            for line in f:
                if sid in line:
                    try:
                        d = json.loads(line)
                    except ValueError:
                        continue
                    if d.get('id') == sid and (d.get('thread_name') or '').strip():
                        name = d['thread_name'].strip()
    except OSError:
        pass
    return name


def _app_titles(agent, sid, tpath):
    """The names the app gave the session, read from the whole transcript (Claude Code) or the session index (Codex).
    For sessions recorded before 0.1.34, whose title entries were read before the tool looked for them."""
    if agent == 'codex':
        name = _codex_title(sid)
        return {'app': name} if name else {}
    out = {}
    try:
        with open(tpath, encoding='utf-8', errors='replace') as f:
            for line in f:
                if '-title"' in line:
                    try:
                        d = json.loads(line)
                    except ValueError:
                        continue
                    for item in _claude(d) if isinstance(d, dict) else []:
                        if item[1].startswith('title_'):
                            out[item[1][6:]] = item[2]
    except (OSError, TypeError):
        pass
    return out


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


def _read(path, offset, parse, tip=None):
    """Complete lines from byte `offset`; a half-written last line waits for the next call. Each item gets its message
    id and, for a Claude Code prompt, (parentUuid, the entry just before it): they differ after a rewind. `tip` is the
    last entry seen by the previous call."""
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
        if not isinstance(d, dict):
            continue
        mid = d.get('uuid') or (d.get('payload') or {}).get('id') or ''   # message id: survives forks and continuations
        link = (d.get('parentUuid'), tip) if d.get('type') == 'user' and 'parentUuid' in d else None
        out.extend(item + (mid, link if item[1] == 'user' else None) for item in parse(d))
        if d.get('uuid') and not d.get('isSidechain'):
            tip = d['uuid']
    return out, offset + end, tip


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


# The record's header (0.1.34+): YAML front matter. Simple values are written plain, anything else as JSON (a subset of
# YAML), so the file stays readable and any YAML reader can load it; this module parses its own output without PyYAML.
# The meaning of each field is in the README the tool keeps in the records folder (convo_readme).
FRONT = (('title', 'title'), ('topics', 'topics'), ('who', 'who'), ('agent', 'agent'), ('started', 'start'),
         ('updated', 'last'), ('timezone', None), ('continued_from', 'parent'), ('prev_month', 'prev'),
         ('next_month', 'next'), ('session', 'session'), ('earlier_sessions', 'earlier_sessions'),
         ('summarized_through', 'summarized'), ('hidden_through', 'through'), ('attachment_notes', 'notes'))
FIRST_USER = re.compile(r'<!-- msg \S+ user -->\n\*\*[^\n]*\*\*\n\n(.*?)(?=\n<!-- msg |\n</?details>|\Z)', re.S)
PLAIN = re.compile(r'[\w][\w .:+/-]*')


def _yv(v):
    return v if isinstance(v, str) and PLAIN.fullmatch(v) and v.strip() == v else json.dumps(v, ensure_ascii=False)


def _front(head):
    tz = _tz(head.get('last') or head.get('start') or '')
    fmt = lambda iso: _local(iso, '%Y-%m-%d %H:%M')
    vals = {'title': head.get('title') or '', 'agent': AGENT_LABEL.get(head.get('agent'), head.get('agent')),
            'started': fmt(head.get('start') or ''), 'updated': fmt(head.get('last') or ''), 'timezone': tz}
    out = ['---']
    for key, inner in FRONT:
        v = vals[key] if key in vals else head.get(inner)
        if v in (None, '', [], {}) and key not in ('title', 'who', 'agent', 'session'):
            continue
        if key == 'attachment_notes':
            out.append('attachment_notes:')
            out += [f'  {json.dumps(k, ensure_ascii=False)}: {json.dumps(n, ensure_ascii=False)}' for k, n in sorted(v.items())]
        elif key == 'title':
            out.append(f'title: {json.dumps(v, ensure_ascii=False)}')
        else:
            out.append(f'{key}: {_yv(v)}')
    return '\n'.join(out + ['---'])


def _iso(local, tz):
    """'2026-10-31 09:54' + 'UTC+8' → '2026-10-31T01:54:00.000Z' (what the old header kept)."""
    m = re.fullmatch(r'UTC([+-])(\d{1,2})(?::(\d{2}))?', tz or '')
    try:
        t0 = dt.datetime.strptime(local, '%Y-%m-%d %H:%M')
    except (TypeError, ValueError):
        return ''
    off = dt.timedelta(hours=int(m.group(2)), minutes=int(m.group(3) or 0)) * (1 if m.group(1) == '+' else -1) if m else dt.timedelta()
    return (t0 - off).strftime('%Y-%m-%dT%H:%M:00.000Z')


def _head_of(text):
    if text.startswith('---\n'):
        end = text.find('\n---\n', 3)
        if end < 0:
            return {}
        raw, cur = {}, None
        for line in text[4:end].split('\n'):
            if line.startswith('  ') and cur:                     # an entry under attachment_notes
                k, _, v = line.strip().partition(': ')
                try:
                    raw[cur][json.loads(k)] = json.loads(v)
                except ValueError:
                    pass
                continue
            k, _, v = line.partition(':')
            k, v = k.strip(), v.strip()
            if not k:
                continue
            if k == 'attachment_notes' and not v:
                raw[k], cur = {}, k
                continue
            cur = None
            try:
                raw[k] = json.loads(v) if v[:1] in '"[{' else v
            except ValueError:
                raw[k] = v
        head = {inner: raw[key] for key, inner in FRONT if inner and key in raw and key not in ('started', 'updated')}
        for key, inner in (('started', 'start'), ('updated', 'last')):
            head[inner] = _iso(raw.get(key), raw.get('timezone'))
        back = {v: k for k, v in AGENT_LABEL.items()}
        head['agent'] = back.get(head.get('agent'), head.get('agent'))
        return head
    m = MARK.search(text[:8000])                                  # records written before 0.1.34
    try:
        return json.loads(m.group(1)) if m else {}
    except ValueError:
        return {}


def _rehead(cfg, text, head):
    """The same record with a new header (anything before the summary heading is header, old or new format)."""
    i = text.find(SUM_START)
    k = text.rfind(t(cfg.lang, 'convo_summaries'), 0, i) if i >= 0 else -1
    return _front(head) + '\n\n' + text[k:] if k >= 0 else None


def _summaries_of(text):
    i, j = text.find(SUM_START), text.find(SUM_END)
    return text[i + len(SUM_START):j].strip('\n') if 0 <= i < j else ''


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


# ---------------------------------------------------------------- attachments
def _att_dir(cfg):
    return ((cfg.data.get('convo') or {}).get('attachments_dir') or t(cfg.lang, 'convo_att_dir')).strip('/')


def _drive_name(cfg):
    return ((cfg.data.get('drive') or {}).get('root_name') or '').strip('/')


def _save_image(cfg, rel, drive):
    """Write one pasted image under <drive>/<attachments>/<rel>; without the drive mounted, into a local queue that
    _flush_pending moves there later. Same content, same name: saved once however often it is pasted."""
    data = _IMGS.get(rel.rsplit('/', 1)[-1])
    if data is None:
        return
    p = (drive / _att_dir(cfg) if drive else cfg.state_dir / 'convo' / 'pending') / rel
    if p.exists():
        return
    try:
        raw = base64.b64decode(data)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(raw)
    except (OSError, ValueError, binascii.Error):
        pass


def _flush_pending(cfg, drive):
    q = cfg.state_dir / 'convo' / 'pending'
    if not drive or not q.is_dir():
        return
    for p in sorted(q.rglob('*')):
        if not p.is_file():
            continue
        dest = drive / _att_dir(cfg) / p.relative_to(q)
        try:
            if not dest.exists():
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(p.read_bytes())
            p.unlink()
        except OSError:
            pass


def _keep_images(cfg, text, iso):
    """Give each new image token its month folder (saving happens once the message is known not to be paused).
    Without a shared drive configured: '[image]'."""
    if not _drive_name(cfg):
        return IMG_TOKEN.sub('[image]', text)
    return IMG_TOKEN.sub(lambda m: m.group(0) if '/' in m.group(1) else f"⟦img:{_local(iso, '%Y-%m')}/{m.group(1)}⟧", text)


def _file_key(path):
    return 'file:' + hashlib.sha1(path.encode('utf-8')).hexdigest()[:10]


def _placer(cfg):
    """→ function(raw @-path) → ('repo' | 'drive' | 'local', shown path or file name, normalised absolute path)."""
    repo = str(cfg.repo).replace('\\', '/').rstrip('/') + '/'
    root = _drive_name(cfg)
    drive = cfg.drive() if root else None
    dprefix = os.path.realpath(str(drive)).replace('\\', '/').rstrip('/') + '/' if drive else None
    cloud = re.compile(r'(CloudStorage|Google ?Drive|GoogleDrive|共享云端硬盘|Shared drives|My Drive|OneDrive|Dropbox)')

    def place(raw):
        p = os.path.expanduser(raw.strip()).replace('\\', '/')
        real = os.path.realpath(p).replace('\\', '/')
        if dprefix and real != p and real.startswith(dprefix):      # through a shortcut to the drive, e.g. <repo>/CSCA Drive/…
            p = real
        if p.startswith(repo):
            return 'repo', p[len(repo):], p
        if dprefix and p.startswith(dprefix):
            return 'drive', f'{root}/{p[len(dprefix):]}', p
        if root and cloud.search(p) and f'/{root}/' in p:          # another machine's mount point
            return 'drive', f"{root}/{p.split(f'/{root}/', 1)[1]}", p
        return 'local', p.rstrip('/').rsplit('/', 1)[-1] or p, p
    return place


def _show(cfg, notes, text, role, place):
    """Cached text → what the record shows: image tokens as drive paths, @-mentioned files placed, notes appended."""
    L, root = cfg.lang, _drive_name(cfg)

    def img(m):
        rel = m.group(1)
        note = notes.get('img:' + rel.rsplit('/', 1)[-1])
        return t(L, 'convo_image', path=f'Drive:{root}/{_att_dir(cfg)}/{rel}') + (t(L, 'convo_note_suffix', note=note) if note else '')
    text = IMG_TOKEN.sub(img, text)
    if role != 'user':
        return text

    def ref(m):
        kind, shown, abspath = place(m.group(1) or m.group(2))
        if kind == 'repo':
            return f'@{shown}'
        if kind == 'drive':
            return f'@Drive:{shown}'
        note = notes.get(_file_key(abspath))
        return t(L, 'convo_local_file', fname=shown) + (t(L, 'convo_note_suffix', note=note) if note else '')
    return FILE_REF.sub(ref, text)


def _note_wanted(cfg, text, iso, place):
    """(key, what, label) for every image and every outside @-mentioned file in one user message."""
    out = []
    for m in IMG_TOKEN.finditer(text):
        out.append(('img:' + m.group(1).rsplit('/', 1)[-1], 'image', _local(iso)))
    for m in FILE_REF.finditer(text):
        kind, shown, abspath = place(m.group(1) or m.group(2))
        if kind == 'local':
            out.append((_file_key(abspath), 'file', shown))
    return out


def _render(cfg, head, summaries, msgs, limit, withdrawn=()):
    L, through = cfg.lang, head.get('through') or ''
    agent = AGENT_LABEL.get(head['agent'], head['agent'])
    out = [_front(head), '', t(L, 'convo_summaries'), SUM_START, summaries.strip() or t(L, 'convo_none'), SUM_END, '',
            t(L, 'convo_raw_since', since=_local(through)) if through else t(L, 'convo_raw'), '']
    gone = lambda iso: any(a <= iso <= b for a, b in withdrawn or ())
    notes, place = head.get('notes') or {}, _placer(cfg)
    folded = False
    for k, row in enumerate(msgs):
        iso, role, text = row[0], row[1], row[2]
        if through and iso <= through:
            continue
        if gone(iso) != folded:                                   # taken back by a rewind: folded, verbatim kept
            if folded:
                out += ['</details>', '']
            else:
                n = sum(1 for _ in itertools.takewhile(lambda m: gone(m[0]), msgs[k:]))
                out += ['<details>', f"<summary>{t(L, 'convo_withdrawn', n=n)}</summary>", '']
            folded = not folded
        if role == 'note':                                        # pause / resume
            out += [f'<!-- msg {iso} note -->', f'> {_local(iso)} · {text}', '']
            continue
        out += [f'<!-- msg {iso} {role} -->', f"**{_local(iso)} · {head['who'] if role == 'user' else agent}**", '',
                _show(cfg, notes, text, role, place), '']
    if folded:
        out += ['</details>', '']
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
        msgs, _, _ = _read(st['path'], 0, PARSERS[agent])
        for m in msgs:
            if m[3]:
                idx['ids'].setdefault(m[3], cp.stem)
    _save(ip, idx)
    for _, cp, st in sessions:                                    # re-render every record so the copies drop out
        agent, sid = cp.stem.split('_', 1)
        keep = {k: st[k] for k in ('continues', 'continued_by', 'forked_after', 'sessions', 'chain_parent') if st.get(k)}
        _save(cp, {'path': st['path'], 'offset': 0, 'msgs': [], 'file': st.get('file'), 'off': st.get('off'), 'prev_file': st.get('prev_file'), **keep})
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
    for iso, role, text, *rest in new:
        mid, link = (list(rest) + [None, None])[:2]
        if role == 'rollback':                                    # Codex: the last N turns were taken back
            _withdraw_turns(st, int(text))
            continue
        if role == 'user' and link and link[0] != link[1]:        # Claude Code: this prompt hangs off an earlier point
            _rewound(st, link[0])
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
            if link and mid:
                st.setdefault('prompts', []).append([mid, iso])


# ---------------------------------------------------------------- rewind
# Claude Code keeps rewound messages in the transcript and hangs the new prompt off the point it went back to; Codex
# writes a thread_rolled_back event with the number of turns. Either way the taken-back part stays in the record,
# folded under a "withdrawn" line, the way summaries keep the verbatim text.
def _withdraw(st, iso):
    """Mark every row from the one starting at `iso` to the end as withdrawn."""
    msgs = st['msgs']
    i = next((k for k in range(len(msgs)) if msgs[k][0] >= iso), None)
    if i is None:
        return
    a, b = msgs[i][0], msgs[-1][0]
    st['withdrawn'] = [r for r in st.get('withdrawn') or [] if not (a <= r[0] and r[1] <= b)] + [[a, b]]
    st['prompts'] = [p for p in st.get('prompts') or [] if p[1] < a]


def _rewound(st, parent):
    prompts = st.get('prompts') or []
    if not prompts:
        return
    pos = {}
    try:
        with open(st['path'], 'rb') as f:
            for i, line in enumerate(f):
                try:
                    u = json.loads(line).get('uuid')
                except (ValueError, AttributeError):
                    continue
                if u:
                    pos.setdefault(u, i)
    except OSError:
        return
    at = pos.get(parent, -1) if parent else -1
    gone = next((p for p in prompts if pos.get(p[0], -2) > at), None)
    if gone:
        _withdraw(st, gone[1])


def _gone(st, iso):
    return any(a <= iso <= b for a, b in st.get('withdrawn') or [])


def _withdraw_turns(st, n):
    users = [m for m in st['msgs'] if m[1] == 'user' and not _gone(st, m[0])]
    if n > 0 and users:
        _withdraw(st, users[-min(n, len(users))][0])


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
def _cut(text, n=TITLE_MAX):
    """One line of at most n characters; a longer one ends at the last punctuation in its second half, with '…'."""
    text = re.sub(r'\s+', ' ', text or '').strip()
    if len(text) <= n:
        return text
    stops = [k for k, ch in enumerate(text[:n]) if ch in '。！？；，、.!?;,' and k >= n // 2]
    return (text[:stops[-1]] if stops else text[:n - 1]).rstrip(' ，、,;；') + '…'


def _head(cfg, agent, sid, st, msgs, prev):
    first_user = next((m[2] for m in msgs if m[1] == 'user'), '')
    first_user = IMG_TOKEN.sub(t(cfg.lang, 'convo_image_short'), first_user)
    first_user = FILE_REF.sub(lambda m: (m.group(1) or m.group(2)).replace('\\', '/').rstrip('/').rsplit('/', 1)[-1], first_user)
    titles = st.get('titles') or {}                               # renamed by the user > named by the app > opening message
    title = titles.get('custom') or titles.get('ai') or titles.get('app') or _cut(first_user) or prev.get('title', '')
    head = {'session': sid, 'agent': agent, 'who': cfg.who(), 'title': title,
            'start': msgs[0][0], 'last': msgs[-1][3], 'through': prev.get('through') or ''}
    if prev.get('topics'):                                        # kept up to date by the agent when it compacts (0.1.34+)
        head['topics'] = prev['topics']
    if prev.get('summarized'):                                    # summarized up to here; the verbatim text stays (0.1.25+)
        head['summarized'] = prev['summarized']
    if prev.get('notes'):                                         # one-line notes on images and outside files (0.1.30+)
        head['notes'] = prev['notes']
    if st.get('parent'):
        pst, hops = _load(cfg.state_dir / 'convo' / f"{st['parent']}.json"), 0
        while not pst.get('file') and pst.get('continued_by') and hops < 50:     # its file went on under a later session id
            pst, hops = _load(cfg.state_dir / 'convo' / f"{pst['continued_by']}.json"), hops + 1
        pf = pst.get('file')
        if pf:
            head['parent'] = f"{_dir(cfg)}/{pf}"
    if st.get('prev_file'):
        head['prev'] = f"{_dir(cfg)}/{st['prev_file']}"
    if st.get('sessions'):
        head['earlier_sessions'] = st['sessions']
    return head


def _continue(cfg, st, me, owner, last_copied):
    """A Claude Code session that was resumed gets a new id and a copy of the whole transcript. When the copy runs up to
    the last message the earlier session recorded, this is the same conversation going on: take over its file (and its
    month links, pause state and compaction state) instead of opening a new one. A copy that stops earlier is a branch
    and keeps its own file. If the earlier session is used again after a takeover, it was a branch after all: from then
    on it writes a new file linked to this one, so the two never write the same file."""
    oc = cfg.state_dir / 'convo' / f'{owner}.json'
    ost = _load(oc)
    if not st.get('continues'):                                   # first sight: decide
        if not ost.get('file') or ost.get('last_mid') != last_copied or ost.get('continued_by'):
            return
        msgs = ost.get('msgs') or []
        st.update({'continues': owner, 'file': ost['file'], 'chain_parent': ost.get('chain_parent') or ost.get('parent'),
                   'sessions': (ost.get('sessions') or []) + [owner.split('_', 1)[-1]]})
        for k in ('prev_file', 'off', 'held', 'asked', 'asks'):
            if ost.get(k) is not None:
                st[k] = ost[k]
        ost.update({'continued_by': me, 'forked_after': msgs[-1][3] if msgs else '', 'pre': msgs,
                    'file': None, 'msgs': [], 'prev_file': None, 'asks': 0})
        ost.pop('asked', None)
        _save(oc, ost)
    if not st.get('msgs'):                                        # first takeover, or after an index rebuild
        st['msgs'] = [list(m) for m in (ost.get('pre') or [])]
        for k in ('prompts', 'withdrawn'):
            st[k] = [list(x) for x in ost.get(k) or []]
    st['parent'] = st.get('chain_parent')


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
    new, st['offset'], st['tip'] = _read(tpath, int(st.get('offset') or 0), PARSERS[agent], st.get('tip'))
    if 'titles' not in st:                                        # a session recorded before 0.1.34: look once through what was read
        st['titles'] = _app_titles(agent, sid, tpath)
    for item in new:                                              # the app's own names for the session (0.1.34+)
        if item[1].startswith('title_'):
            st.setdefault('titles', {})[item[1][6:]] = item[2]
    new = [item for item in new if not item[1].startswith('title_')]
    if agent == 'codex':
        name = _codex_title(sid)
        if name:
            st.setdefault('titles', {})['app'] = name
    drive = cfg.drive() if _drive_name(cfg) else None
    _flush_pending(cfg, drive)
    me, own, copied = cf.stem, [], None
    for iso, role, text, mid, link in new:
        owner = idx['ids'].get(mid) if mid else None
        if owner and owner != me:                                 # copied from an earlier session
            st['parent'] = owner                                 # the latest session it copied from
            copied = (owner, mid)
            continue
        if mid:
            idx['ids'][mid] = me
            st['last_mid'] = mid
        own.append((iso, role, _keep_images(cfg, redact(cfg, text), iso), mid, link))
    if new and any(m[3] for m in new):
        _save(_index_path(cfg), idx)
    if st.get('forked_after'):                                    # this session's file was taken over; see _continue
        pre = [m for m in own if m[0] <= st['forked_after']]
        own = [m for m in own if m[0] > st['forked_after']]
        if pre:                                                   # only on an index rebuild: what the successor inherits
            _append(st.setdefault('pre', []), [m for m in pre if m[1] != 'rollback'])
    if st.get('continues') or (copied and not st.get('file')):
        _continue(cfg, st, me, st.get('continues') or copied[0], None if st.get('continues') else copied[1])
    if st.get('continued_by'):                                    # a branch of the session that took over its file
        st['parent'] = st['continued_by']
    new = own
    _apply(cfg, st, new)
    held = {h[2] for h in st.get('held') or []}                    # paused: kept on this machine only, images too
    for iso, role, text, *_ in new:
        if role == 'user' and text not in held:
            for m in IMG_TOKEN.finditer(text):
                _save_image(cfg, m.group(1), drive)
    _IMGS.clear()
    if not data.get('no_ask'):                                    # a live turn: these the agent has just seen, so it can describe them
        place, wait = _placer(cfg), st.setdefault('note_wait', {})
        for iso, role, text, *_ in new:
            if role == 'user' and text not in held:
                for key, what, label in _note_wanted(cfg, text, iso, place):
                    wait.setdefault(key, [what, label, iso, 0])
    msgs = st['msgs']
    if not msgs:
        if st.get('file') and (cfg.repo / _dir(cfg) / st['file']).exists():     # everything in it was a copy
            (cfg.repo / _dir(cfg) / st['file']).unlink()
        _save(cf, st)
        return None
    if not st.get('file'):
        st['file'] = name = _name(cfg, agent, sid, msgs[0][0])
        n = 2
        while st.get('continued_by') and (cfg.repo / _dir(cfg) / st['file']).exists():   # its old file lives on elsewhere
            st['file'] = name[:-3] + f'_{n}.md'; n += 1
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
            _write(cfg, opath, _render(cfg, ohead, _summaries_of(otext), msgs[:cut], limit, st.get('withdrawn')))
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
    text = _render(cfg, head, _summaries_of(old), shown, limit, st.get('withdrawn'))
    if text != old:
        _write(cfg, path, text)
    ask = _ask(cfg, st, head, msgs, limit, keep, data, path) or _note_ask(cfg, st, head, data, path)
    _save(cf, st)
    return ask


def _note_ask(cfg, st, head, data, path):
    """Ask the agent for a one-line note on each new image / outside file (twice at most per item)."""
    wait, notes = st.get('note_wait') or {}, head.get('notes') or {}
    for k in [k for k, v in wait.items() if k in notes or v[3] >= MAX_NOTE_ASKS]:
        del wait[k]
    if not wait or data.get('no_ask') or data.get('stop_hook_active'):
        return None
    L, items = cfg.lang, []
    for k, v in wait.items():
        v[3] += 1
        items.append(f'- {k}: ' + (t(L, 'convo_note_item_image', when=v[1]) if v[0] == 'image' else t(L, 'convo_note_item_file', fname=v[1])))
    return t(L, 'convo_note_ask', file=path.relative_to(cfg.repo).as_posix(), n=len(wait), items='\n'.join(items),
             template='\n'.join(f'{k} …' for k in wait))


def _ask(cfg, st, head, msgs, limit, keep, data, path):
    through = max(head.get('through') or '', head.get('summarized') or '')
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
    head = _head_of(s)
    i, j = s.find(SUM_START), s.find(SUM_END)
    if not head or not (0 <= i < j):
        print(t(L, 'convo_no_file', file=file))
        return 1
    summary = redact(cfg, (summary or '').replace(SUM_START, '').replace(SUM_END, '').strip())
    tm = re.match(r'\s*(?:topics|话题|主题)\s*[:：]\s*(.+)\n', summary + '\n', re.I)   # first line: topics of the whole session
    if tm:
        topics = [x.strip(' 「」"\'') for x in re.split(r'[,，、;；/|]', tm.group(1)) if x.strip(' 「」"\'')]
        if topics:
            head['topics'] = [_cut(x, 24) for x in topics][:8]
        summary = summary[tm.end():].strip()
    if len(summary) < 20:
        print(t(L, 'convo_short'))
        return 1
    old = max(head.get('through') or '', head.get('summarized') or '')
    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}T[0-9:.]+(Z|[+-]\d{2}:\d{2})?', through or '') or through <= old:
        print(t(L, 'convo_bad_through', through=through, old=old or '-'))
        return 1
    body = s[j + len(SUM_END):]
    starts = [x.start() for x in MSG_MARK.finditer(body)]
    blocks = [body[a:b] for a, b in zip(starts, starts[1:] + [len(body)])]
    covered = [x for x in blocks if old < MSG_MARK.match(x).group(1) <= through]
    first = MSG_MARK.match(covered[0]).group(1) if covered else through
    cur = s[i + len(SUM_START):j].strip()
    block = t(L, 'convo_range', a=_local(first), b=_local(through)) + '\n\n' + summary
    head['summarized'] = through                                  # the summary is added on top; the verbatim text is kept
    out = _rehead(cfg, s[:i + len(SUM_START)] + '\n' + (block if cur in ('', t(L, 'convo_none')) else cur + '\n\n' + block)
                  + '\n' + SUM_END + body, head)
    if out is None:
        print(t(L, 'convo_no_file', file=file))
        return 1
    _write(cfg, path, out.rstrip('\n') + '\n')
    cf = _cache(cfg, head.get('agent', ''), head.get('session', ''))
    st = _load(cf)
    if st:
        st['asks'] = 0
        _save(cf, st)                                             # silently skipped inside a read-only sandbox
    print(t(L, 'convo_compacted', upto=_local(through), file=path.relative_to(cfg.repo).as_posix()))
    return 0


# ---------------------------------------------------------------- notes on attachments (the agent runs this when asked)
def note(cfg, file, text):
    """Lines '<key> <note>' (keys from the hook's request) → notes next to those images / files in the record."""
    L = cfg.lang
    path = pathlib.Path(file) if os.path.isabs(file) else cfg.repo / file
    try:
        s = path.read_text(encoding='utf-8')
    except OSError:
        print(t(L, 'convo_no_file', file=file))
        return 1
    head, added = _head_of(s), {}
    if not head:
        print(t(L, 'convo_no_file', file=file))
        return 1
    for line in (text or '').splitlines():
        parts = line.strip().lstrip('-').strip().split(None, 1)
        if len(parts) < 2:
            continue
        key = parts[0].rstrip(':：')
        body = redact(cfg, parts[1].lstrip(':：').strip())
        if NOTE_KEY.match(key) and len(body) >= 4 and body.strip('.…。 ') != '':
            added[key] = re.sub(r'\s+', ' ', body)[:300]
    if not added:
        print(t(L, 'convo_note_none'))
        return 1
    head.setdefault('notes', {}).update(added)
    _write(cfg, path, _rehead(cfg, s, head) or s)
    cf = _cache(cfg, head.get('agent', ''), head.get('session', ''))
    st = _load(cf)
    if st.get('path') and os.path.isfile(st['path']) and head.get('agent') in PARSERS:
        try:                                                      # re-render so the notes show next to the references
            update(cfg, head['agent'], {'session_id': head['session'], 'transcript_path': st['path'], 'no_ask': True})
        except Exception:                                         # noqa: BLE001 — the notes are in the file's header either way
            pass
        st = _load(cf)
        for k in added:
            (st.get('note_wait') or {}).pop(k, None)
        _save(cf, st)                                             # silently skipped inside a read-only sandbox
    print(t(L, 'convo_noted', n=len(added), file=path.relative_to(cfg.repo).as_posix()))
    return 0


def backfill(cfg):
    """Records written before 0.1.30 show pasted images as "[image]". Put each one back in place (matched by message
    time against the local transcript), save the image to the shared drive, and place @-mentioned paths the new way.
    Only those user messages change: headers, summaries and everything else stay as written. Safe to run again."""
    L = cfg.lang
    if not _drive_name(cfg) or not _dir(cfg):
        print(t(L, 'convo_backfill_nodrive'))
        return 1
    drive, place, root = cfg.drive(), _placer(cfg), cfg.repo / _dir(cfg)
    files = {}
    for f in root.glob('*/*.md'):
        try:
            sess = _head_of(f.read_text(encoding='utf-8')[:8000]).get('session')
        except OSError:
            continue
        if sess:
            files.setdefault(sess, []).append(f)
    nfiles = nimg = 0
    for cp in _caches(cfg):
        st, (agent, sid) = _load(cp), cp.stem.split('_', 1)
        if agent not in PARSERS or not os.path.isfile(st.get('path') or '') or not files.get(sid):
            continue
        tokens = {}                                               # message time -> its images, in order
        for iso, role, text, *_ in _read(st['path'], 0, PARSERS[agent])[0]:
            if role == 'user':
                found = [f"{_local(iso, '%Y-%m')}/{m.group(1)}" for m in IMG_TOKEN.finditer(text)]
                if found:
                    tokens.setdefault(iso, []).extend(found)
        for f in files[sid]:
            s = f.read_text(encoding='utf-8')
            notes = _head_of(s).get('notes') or {}
            starts = [m.start() for m in MSG_MARK.finditer(s)]
            if not starts:
                continue
            out = [s[:starts[0]].replace('[image]', t(L, 'convo_image_short'))]   # the opening line and the title
            for a, b in zip(starts, starts[1:] + [len(s)]):
                block = s[a:b]
                m = re.match(r'<!-- msg (\S+) user -->', block)
                if m:
                    todo = list(tokens.get(m.group(1), []))

                    def put(_, todo=todo):
                        if not todo:
                            return '[image]'
                        rel = todo.pop(0)
                        _save_image(cfg, rel, drive)
                        return f'⟦img:{rel}⟧'
                    block = re.sub(r'\[image\]', put, block)
                    if agent == 'codex':                          # old Codex records kept the attachment preamble and <image> wrappers
                        mb = re.match(r'(<!-- msg \S+ user -->\n\*\*[^\n]*\*\*\n\n)(.*?)(\s*)$', block, re.S)
                        if mb:
                            body = '\n'.join(x for x in mb.group(2).split('\n') if not CODEX_IMG_WRAP.match(x))
                            block = mb.group(1) + _codex_mentions(body) + mb.group(3)
                    block = _show(cfg, notes, block, 'user', place)
                out.append(block)
            new = ''.join(out)
            if new != s:
                mark = t(L, 'convo_image', path='')[:4]               # "[图片:" / "[ima"
                nimg += new.count(mark) - s.count(mark)
                _write(cfg, f, new)
                nfiles += 1
        _IMGS.clear()
    print(t(L, 'convo_backfilled', files=nfiles, images=max(nimg, 0)))
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
             f"{_cut(h.get('title') or '', 40)} → {p.relative_to(cfg.repo).as_posix()}" for last, h, p in rows[:n]]
    return (t(cfg.lang, 'convo_recent') + '\n' + '\n'.join(lines)) if lines else ''


# ---------------------------------------------------------------- header format and README (0.1.34+)
def reformat(cfg):
    """Records this machine wrote with the old header (a title line, a JSON comment, an intro paragraph) get the YAML
    front matter. Only this person's own records: each record is written by its owner's machine, and an older version
    there could not read the new header of a record it is still writing. Returns how many were changed."""
    d = _dir(cfg)
    root = cfg.repo / d if d else None
    if not root or not root.is_dir():
        return 0
    n = 0
    for f in root.glob('*/*.md'):
        try:
            s = f.read_text(encoding='utf-8')
        except OSError:
            continue
        if s.startswith('---\n'):
            continue
        head = _head_of(s)
        if not head or head.get('who') != cfg.who():
            continue
        st = _load(_cache(cfg, head.get('agent', ''), head.get('session', '')))
        titles = st.get('titles') or (_app_titles(head.get('agent'), head.get('session', ''), st.get('path')) if st.get('path') or head.get('agent') == 'codex' else {})
        first = FIRST_USER.search(s)
        head['title'] = (titles.get('custom') or titles.get('ai') or titles.get('app')
                         or (_cut(first.group(1)) if first else '') or head.get('title', ''))
        new = _rehead(cfg, s, head)
        if new and new != s:
            _write(cfg, f, new)
            n += 1
    return n


def readme(cfg):
    """The records folder's README explains the format (what is kept, what each front-matter field means). Written by
    the tool so it always matches the format; a newer version's README is never replaced by an older one."""
    d = _dir(cfg)
    c = recording(cfg)
    if not d or c is None:
        return False
    path = cfg.repo / d / 'README.md'
    mark = f'<!-- team-sync convo-readme {__version__} -->'
    try:
        old = path.read_text(encoding='utf-8')
    except OSError:
        old = ''
    m = re.search(r'<!-- team-sync convo-readme ([\d.]+) -->', old)
    ver = lambda v: tuple(int(x) for x in re.findall(r'\d+', v))
    if m and ver(m.group(1)) >= ver(__version__):
        return False
    text = t(cfg.lang, 'convo_readme', limit=int(c.get('compact_chars') or 30000), dir=d, att=_att_dir(cfg),
             drive=_drive_name(cfg) or '-') + '\n\n' + mark + '\n'
    _write(cfg, path, text)
    return True
