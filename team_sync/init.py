"""`team-sync init`: drop the config, hook files, env example and rules template into a repo (never overwrites)."""
import json
import pathlib
import shutil

from .config import CONFIG_NAME, DEFAULTS

RES = pathlib.Path(__file__).parent / 'resources'


def _write(path, content, created):
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding='utf-8'); created.append(str(path))


def run(cfg, ns):
    created = []
    conf = dict(DEFAULTS); conf.update({'team': ns.team or cfg.repo.name, 'lang': ns.lang, 'env_prefix': (ns.prefix or 'TEAM').upper(), 'version': __import__('team_sync').__version__})
    _write(cfg.repo / CONFIG_NAME, json.dumps(conf, ensure_ascii=False, indent=2) + '\n', created)
    p = conf['env_prefix']
    _write(cfg.repo / '.env.team.example', f'# copy to .env.team (git-ignored); the maintainer sends one per member\n{p}_WHO=alice\n{p}_SUPABASE_URL=https://xxxx.supabase.co\n{p}_SUPABASE_ANON_KEY=\n{p}_TEAM_KEY=\n# {p}_DRIVE_ROOT=   # only if auto-detection fails\n', created)
    _write(cfg.repo / 'bin' / 'team_sync.py', (RES / 'shim.py').read_text(encoding='utf-8'), created)
    _write(cfg.repo / '.claude' / 'settings.json', (RES / 'hooks' / 'claude.settings.json').read_text(encoding='utf-8'), created)
    _write(cfg.repo / '.agents' / 'hooks.json', (RES / 'hooks' / 'antigravity.hooks.json').read_text(encoding='utf-8'), created)
    _write(cfg.repo / '.codebuddy' / 'settings.json', (RES / 'hooks' / 'codebuddy.settings.json').read_text(encoding='utf-8'), created)
    _write(cfg.repo / 'AGENTS.md', (RES / 'AGENTS.template.md').read_text(encoding='utf-8'), created)
    for pointer in ('CLAUDE.md', 'CODEBUDDY.md'):
        _write(cfg.repo / pointer, '@AGENTS.md\n', created)
    gi = cfg.repo / '.gitignore'
    lines = ['.env.team', '.env', '.env.*', '!.env.team.example', '*.session', 'config.local.json']
    if gi.exists():
        have = gi.read_text(encoding='utf-8')
        add = [l for l in lines if l not in have.splitlines()]
        if add:
            gi.write_text(have.rstrip('\n') + '\n# team-sync\n' + '\n'.join(add) + '\n', encoding='utf-8'); created.append(str(gi) + ' (appended)')
    else:
        _write(gi, '# team-sync\n' + '\n'.join(lines) + '\n', created)
    print('created:' if created else 'nothing to do (all files exist)')
    for c in created:
        print('  ' + c)
    print('next: fill team-sync.json (drive, fingerprint, journal), `team-sync db install`, generate a passphrase, `team-sync autosync on`.')
    return 0
