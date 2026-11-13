"""Times the team reads: always with the time zone, because teammates sit in different zones (0.1.37+).

Written times (commit messages, journal lines, message headers) carry the writer's local time and zone, e.g.
'2026-11-14 10:17 UTC+11'. Stored times (message metadata, state files) are ISO 8601 with the offset. Shown times are
converted to the reader's local time and labelled with the reader's zone. Values written by older versions have no
offset; they are read as the reader's local time, which is all that can be known about them.
"""
import datetime as dt


def now():
    return dt.datetime.now().astimezone()


def tz_label(d=None):
    d = (d or now()).astimezone()
    mins = int(d.utcoffset().total_seconds() // 60)
    h, m = divmod(abs(mins), 60)
    return f"UTC{'+' if mins >= 0 else '-'}{h}" + (f':{m:02d}' if m else '')


def stamp(d=None, fmt='%Y-%m-%d %H:%M'):
    """'2026-11-14 10:17 UTC+11': a time with its zone, in the zone it is given in (local when none)."""
    d = d or now()
    d = d if d.tzinfo else d.astimezone()
    return f'{d.strftime(fmt)} {tz_label(d)}'


def iso(d=None):
    """ISO 8601 with the offset, to the second, for metadata and state files."""
    return (d or now()).astimezone().isoformat(timespec='seconds')


def parse(s):
    """An ISO time → an aware datetime (no offset: local time, as older versions wrote). None when unreadable."""
    if not isinstance(s, str) or not s.strip():
        return None
    try:
        d = dt.datetime.fromisoformat(s.strip().replace('Z', '+00:00'))
    except ValueError:
        return None
    return d if d.tzinfo else d.astimezone()


def show(s, fmt='%m-%d %H:%M'):
    """A stored ISO time in the reader's local time, with the zone: '11-14 10:17 UTC+11'."""
    d = parse(s)
    return stamp(d.astimezone(), fmt) if d else (s or '')[5:16].replace('T', ' ')
