"""Messages between teammates' agents, kept in the repo so they travel with the normal sync.

  team-sync msg send <who>[,<who>...] "text"     (or text on stdin)
  team-sync msg list

One file per message: <inbox.dir>/<recipient>/<YYYY-MM-DD_HHMMSS>_<sender>.md, so two people writing at once never
touch the same file. The recipient's agent gets unread messages at session start and before each turn (the
UserPromptSubmit hook; only a local read, autosync and turn-end syncs bring new ones in). Delivering marks the file
read; the receipt syncs back and shows in the sender's next session summary.

A message is relayed to the person, never executed by their agent: anyone who can push to the repo could otherwise
steer a teammate's agent, and the person would not know why it acted.
"""
import datetime as dt
import json
import re

from .msg import t

HEAD = re.compile(r'^<!-- team-sync-msg (\{.*?\}) -->\n', re.S)
NAME = re.compile(r'^[A-Za-z0-9_.-]{1,32}$')


def _dir(cfg):
    d = ((cfg.data.get('inbox') or {}).get('dir') or '').strip('/')
    return cfg.repo / d if d else None


def is_message(cfg, relpath):
    d = ((cfg.data.get('inbox') or {}).get('dir') or '').strip('/')
    return bool(d) and relpath.replace('\\', '/').startswith(d + '/')


def _read(path):
    try:
        s = path.read_text(encoding='utf-8')
        m = HEAD.match(s)
        return (json.loads(m.group(1)), s[m.end():]) if m else (None, s)
    except (OSError, ValueError):
        return None, ''


def _write(path, meta, body):
    path.write_text(f'<!-- team-sync-msg {json.dumps(meta, ensure_ascii=False)} -->\n{body}', encoding='utf-8')


def _text(body):
    """The message text without the visible header line."""
    lines = body.split('\n')
    return '\n'.join(lines[2:] if lines and lines[0].startswith('**') else lines).strip()


def send(cfg, to, text):
    d = _dir(cfg)
    if d is None:
        raise SystemExit(t(cfg.lang, 'inbox_off'))
    text = text.strip()
    if not text:
        raise SystemExit(t(cfg.lang, 'inbox_empty'))
    names = [n.strip().lower() for n in to.split(',') if n.strip()]
    bad = [n for n in names if not NAME.match(n)]
    if bad or not names:
        raise SystemExit(t(cfg.lang, 'inbox_bad_name', names=', '.join(bad) or to))
    me, now = cfg.who(), dt.datetime.now()
    known = {p.name for p in d.iterdir() if p.is_dir()} if d.exists() else set()
    out = []
    for n in names:
        (d / n).mkdir(parents=True, exist_ok=True)
        p = d / n / f'{now:%Y-%m-%d_%H%M%S}_{me}.md'
        k = 2
        while p.exists():
            p = d / n / f'{now:%Y-%m-%d_%H%M%S}_{me}_{k}.md'; k += 1
        meta = {'from': me, 'to': n, 'at': now.isoformat(timespec='seconds'), 'read': None}
        _write(p, meta, f'**{me} → {n} · {now:%m-%d %H:%M}**\n\n{text}\n')
        out.append((n, p))
    for n, p in out:
        print(t(cfg.lang, 'inbox_sent', to=n, path=p.relative_to(cfg.repo).as_posix()))
    new = [n for n in names if n not in known and n != me]
    if new:
        print(t(cfg.lang, 'inbox_new_name', names=', '.join(new)))
    from .sync import _stop_in_background                       # push now, not at the next turn end or autosync run
    try:
        _stop_in_background(cfg)
    except Exception:  # noqa: BLE001
        pass
    return 0


def deliver(cfg):
    """Unread messages for this person as text for the agent's context, marked read. '' when none. Never raises."""
    try:
        d = _dir(cfg)
        box = d / cfg.who().lower() if d else None
        if not box or not box.is_dir():
            return ''
        items = []
        for p in sorted(box.glob('*.md')):
            meta, body = _read(p)
            if meta and not meta.get('read'):
                items.append((p, meta, body))
        if not items:
            return ''
        now = dt.datetime.now().isoformat(timespec='seconds')
        lines = [t(cfg.lang, 'inbox_head')]
        for p, meta, body in items:
            at = (meta.get('at') or '')[5:16].replace('T', ' ')
            msg = _text(body).replace('\n', '\n      ')
            lines.append(t(cfg.lang, 'inbox_item', frm=meta.get('from', '?'), at=at, text=msg))
            meta['read'] = now; meta['read_by'] = cfg.holder_prefix()
            try:
                _write(p, meta, body)
            except OSError:
                pass
        return '\n'.join(lines)
    except Exception:  # noqa: BLE001
        return ''


def receipts(cfg, since):
    """Messages this person sent that were read after `since` (ISO), as one summary line. '' when none."""
    d = _dir(cfg)
    if d is None or not d.exists():
        return ''
    me, got, waiting = cfg.who(), [], []
    for p in sorted(d.glob('*/*.md')):
        meta, _ = _read(p)
        if not meta or meta.get('from') != me:
            continue
        if meta.get('read') and meta['read'] >= since:
            got.append(f"{meta.get('to')} {meta['read'][5:16].replace('T', ' ')}")
        elif not meta.get('read'):
            waiting.append(meta.get('to', '?'))
    out = []
    if got:
        out.append(t(cfg.lang, 'inbox_receipts', list=', '.join(got)))
    if waiting:
        out.append(t(cfg.lang, 'inbox_waiting', list=', '.join(sorted(set(waiting)))))
    return '\n'.join(out)


def list_cli(cfg):
    d = _dir(cfg)
    if d is None:
        print(t(cfg.lang, 'inbox_off')); return 0
    me, rows = cfg.who(), []
    for p in sorted(d.glob('*/*.md')) if d.exists() else []:
        meta, body = _read(p)
        if meta and me in (meta.get('from'), meta.get('to')):
            state = t(cfg.lang, 'inbox_state_read', at=meta['read'][5:16].replace('T', ' ')) if meta.get('read') else t(cfg.lang, 'inbox_state_unread')
            first = _text(body).split('\n')[0][:60]
            rows.append(f"{(meta.get('at') or '')[5:16].replace('T', ' ')}  {meta.get('from')} → {meta.get('to')}  [{state}]  {first}")
    print('\n'.join(rows[-20:]) if rows else t(cfg.lang, 'none'))
    return 0
