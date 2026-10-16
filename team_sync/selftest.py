"""Two-clone simulation in a temp dir: proves sync, conflict handling, leases (if configured), the Antigravity, Codex and
Claude Code hooks, and conversation records.
Never touches the real origin. `team-sync selftest [--keep]`"""
import json
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile

from .config import CONFIG_NAME, Config

FAILS = []


def check(name, ok, detail=''):
    print(('  ✓ ' if ok else '  ✗ ') + name + (f'  ({detail})' if detail else ''))
    if not ok:
        FAILS.append(name)


import os

CLEAN_ENV = {k: v for k, v in os.environ.items() if k not in ('TEAM_SYNC_REPO', 'CLAUDE_PROJECT_DIR', 'CODEBUDDY_PROJECT_DIR')}
CLEAN_ENV['TEAM_SYNC_NO_NOTIFY'] = '1'          # the staged conflict in step 3 must not pop up on the desktop


def sh(cwd, *args, stdin=None, env=None):
    r = subprocess.run(list(args), cwd=cwd, capture_output=True, text=True, input=stdin, encoding='utf-8', errors='replace',
                       env=dict(CLEAN_ENV, **(env or {})))
    return (r.stdout + r.stderr).strip()


def jsonl(path, rows):
    with open(path, 'a', encoding='utf-8') as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + '\n')


def run(cfg, keep=False):
    tmp = pathlib.Path(tempfile.mkdtemp(prefix='team-sync-selftest-'))
    print(f'temp dir {tmp}\n')
    sh(tmp, 'git', 'clone', '-q', '--bare', str(cfg.repo), 'origin.git')
    env_lines = [f'{k}={v}' for k, v in cfg.env().items() if k != cfg.key('WHO')]
    for who in ('alice', 'bob'):
        sh(tmp, 'git', 'clone', '-q', 'origin.git', who)
        sh(tmp / who, 'git', 'config', 'user.name', who); sh(tmp / who, 'git', 'config', 'user.email', f'{who}@selftest.local')
        if (cfg.repo / CONFIG_NAME).exists():                      # use the working-tree config even if not committed yet
            shutil.copy2(cfg.repo / CONFIG_NAME, tmp / who / CONFIG_NAME)
        (tmp / who / cfg.data['env_file']).write_text('\n'.join([f"{cfg.key('WHO')}={who}"] + env_lines) + '\n', encoding='utf-8')
    A, B = tmp / 'alice', tmp / 'bob'
    ts = lambda cwd, *a, stdin=None, env=None: sh(cwd, sys.executable, '-m', 'team_sync', '--repo', str(cwd), *a, stdin=stdin, env=env)   # explicit repo: never touch the real one
    jdir = pathlib.Path(cfg.data['journal']).parent
    fa, fb = A / jdir / 'selftest_note.md', B / jdir / 'selftest_note.md'

    print('1 alice edits, session ends')
    fa.parent.mkdir(parents=True, exist_ok=True); fa.write_text('alice v1\n', encoding='utf-8')
    ts(A, 'note', 'selftest: alice added selftest_note.md'); out = ts(A, 'stop')
    check('auto commit + push', 'Pushed' in out or '已推送' in out, out.replace('\n', ' | ')[:100])

    print('2 bob starts a session')
    out = ts(B, 'start')
    check('bob received the file', fb.exists())
    check('summary shows alice commit', 'sync(alice)' in out)
    check('summary shows alice journal line', 'selftest: alice added' in out)

    print('3 same-line conflict')
    fa.write_text('alice v2\n', encoding='utf-8'); ts(A, 'stop')
    fb.write_text('bob v2\n', encoding='utf-8'); out = ts(B, 'stop')
    check('loser gets a conflict notice', 'conflict' in out.lower() or '冲突' in out)
    check('local content kept', fb.read_text(encoding='utf-8').strip() == 'bob v2')
    check('conflict marker written', (B / '.git' / 'team-sync' / 'conflict.txt').exists())
    check('next start shows it', 'conflict' in ts(B, 'start').lower() or '冲突' in ts(B, 'start'))

    if cfg.get('TEAM_KEY'):
        print('4 leases')
        res = 'selftest:lease'
        a = json.loads(ts(A, 'lease', 'acquire', res, '--ttl', '60'))
        braw = ts(B, 'lease', 'acquire', res, '--ttl', '60'); b = json.loads(braw.splitlines()[-1]) if braw.startswith('{') else {}
        check('alice acquires', a.get('ok') is True); check('bob refused', b.get('ok') is False, f"holder={b.get('holder')}")
        ts(A, 'lease', 'release', res)
        c = json.loads(ts(B, 'lease', 'acquire', res, '--ttl', '60')); check('bob acquires after release', c.get('ok') is True)
        ts(B, 'lease', 'release', res)
    else:
        print(f"4 leases (skipped: {cfg.key('TEAM_KEY')} not configured)")

    print('5 Antigravity hook')
    out = ts(B, 'hook', 'antigravity', 'pre_invocation', stdin='{"conversationId":"selftest","invocationNum":0}')
    try:
        msg = json.loads(out)['injectSteps'][0]['userMessage']; check('valid JSON with injected summary', len(msg) > 100)
    except Exception as e:  # noqa: BLE001
        check('valid JSON with injected summary', False, str(e)[:80])
    check('no duplicate injection in the same conversation', ts(B, 'hook', 'antigravity', 'pre_invocation', stdin='{"conversationId":"selftest","invocationNum":0}').strip() == '{}')

    print('6 Codex hook')
    out = ts(B, 'hook', 'codex', 'session_start', stdin='{"hook_event_name":"SessionStart","source":"startup"}')
    try:
        hso = json.loads(out)['hookSpecificOutput']
        check('SessionStart returns additionalContext', hso.get('hookEventName') == 'SessionStart' and len(hso.get('additionalContext', '')) > 100)
    except Exception as e:  # noqa: BLE001
        check('SessionStart returns additionalContext', False, str(e)[:80])
    cdir = (jdir / 'convo_selftest').as_posix()                    # alice records conversations, with tiny limits
    conf = json.loads((A / CONFIG_NAME).read_text(encoding='utf-8'))
    conf['convo'] = {'dir': cdir, 'compact_chars': 600, 'keep_recent_chars': 200}
    (A / CONFIG_NAME).write_text(json.dumps(conf, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    ro = tmp / 'rollout-2026-01-02T00-00-00-selftestcodex1.jsonl'
    um = lambda when, text: {'timestamp': when, 'type': 'response_item', 'payload': {'type': 'message', 'role': 'user', 'content': [{'type': 'input_text', 'text': text}]}}
    jsonl(ro, [{'timestamp': '2026-01-02T00:00:00.000Z', 'type': 'session_meta', 'payload': {'id': 'selftestcodex1'}},
               um('2026-01-02T00:00:01.000Z', '# AGENTS.md instructions for /x\nINJECTED-SHOULD-NOT-APPEAR'),
               um('2026-01-02T00:00:01.500Z', '<environment_context>ENV-SHOULD-NOT-APPEAR</environment_context>'),
               um('2026-01-02T00:00:02.000Z', '帮我把招生情报整理成表格'),
               {'timestamp': '2026-01-02T00:00:03.000Z', 'type': 'response_item', 'payload': {'type': 'function_call', 'name': 'shell', 'arguments': 'TOOL-SHOULD-NOT-APPEAR'}},
               {'timestamp': '2026-01-02T00:00:04.000Z', 'type': 'response_item', 'payload': {'type': 'message', 'role': 'assistant', 'content': [{'type': 'output_text', 'text': '表格已经放好了。'}]}}])
    (A / jdir / 'selftest_codex.md').write_text('from a codex turn\n', encoding='utf-8')   # alice: bob still has the step-3 conflict
    out = ts(A, 'hook', 'codex', 'stop', stdin=json.dumps({'hook_event_name': 'Stop', 'session_id': 'selftestcodex1', 'transcript_path': str(ro)}),
             env={'TEAM_SYNC_FOREGROUND': '1'})
    check('Stop prints only JSON', out.strip() == '{}', out[:80])
    pushed = sh(tmp, 'git', '--git-dir', 'origin.git', 'log', '-1', '--name-only', '--format=')
    check('Stop commits + pushes the turn, record included', 'selftest_codex.md' in pushed and '_codex_selftest.md' in pushed, pushed.replace('\n', ' ')[:120])
    rec = next(iter((A / cdir).glob('*/*_codex_*.md')), None)
    body = rec.read_text(encoding='utf-8') if rec else ''
    check('Codex record: user + agent text only', '帮我把招生情报整理成表格' in body and '表格已经放好了' in body
          and not any(x in body for x in ('INJECTED-SHOULD', 'ENV-SHOULD', 'TOOL-SHOULD')))
    check('journal auto line leaves records out', cdir not in (A / cfg.data['journal']).read_text(encoding='utf-8'))
    ex = tmp / 'rollout-2026-01-02T01-00-00-selftestexec1.jsonl'
    jsonl(ex, [{'timestamp': '2026-01-02T01:00:00.000Z', 'type': 'session_meta', 'payload': {'id': 'selftestexec1', 'source': 'exec', 'originator': 'codex_exec'}},
               um('2026-01-02T01:00:01.000Z', 'SCRIPTED-EXEC-SHOULD-NOT-BE-RECORDED')])
    (A / jdir / 'selftest_exec.md').write_text('changed during a scripted run\n', encoding='utf-8')
    out = ts(A, 'hook', 'codex', 'stop', stdin=json.dumps({'session_id': 'selftestexec1', 'transcript_path': str(ex)}), env={'TEAM_SYNC_FOREGROUND': '1'})
    check('scripted `codex exec` run: no record, no sync', out.strip() == '{}' and not list((A / cdir).glob('*/*_codex_selftes*xec*.md'))
          and 'selftest_exec.md' not in sh(tmp, 'git', '--git-dir', 'origin.git', 'log', '-1', '--name-only', '--format='))

    print('7 Claude Code conversation record')
    tr = tmp / 'claude-selftest.jsonl'
    cu = lambda when, text, **kw: dict({'type': 'user', 'origin': {'kind': 'human'}, 'timestamp': when, 'message': {'role': 'user', 'content': text}}, **kw)
    ca = lambda when, *blocks: {'type': 'assistant', 'timestamp': when, 'message': {'role': 'assistant', 'model': 'x', 'content': list(blocks)}}
    tx = lambda text: {'type': 'text', 'text': text}
    jsonl(tr, [cu('2026-01-03T00:00:00.000Z', '把首页标题改短一点'),
               ca('2026-01-03T00:00:05.000Z', tx('先看一下首页。'), {'type': 'tool_use', 'id': 't1', 'name': 'Read', 'input': {}}),
               {'type': 'user', 'timestamp': '2026-01-03T00:00:06.000Z', 'toolUseResult': {}, 'message': {'role': 'user', 'content': [{'type': 'tool_result', 'tool_use_id': 't1', 'content': 'TOOL-OUTPUT-SHOULD-NOT-APPEAR'}]}},
               {'type': 'user', 'isMeta': True, 'timestamp': '2026-01-03T00:00:06.500Z', 'message': {'role': 'user', 'content': 'META-SHOULD-NOT-APPEAR'}},
               cu('2026-01-03T00:00:06.700Z', '<task-notification>NOTIFY-SHOULD-NOT-APPEAR</task-notification>', origin={'kind': 'task-notification'}),
               {'type': 'attachment', 'timestamp': '2026-01-03T00:00:07.000Z', 'attachment': {'type': 'queued_command', 'commandMode': 'prompt', 'origin': {'kind': 'human'}, 'prompt': '顺便把副标题也改了'}},
               ca('2026-01-03T00:00:09.000Z', {'type': 'tool_use', 'id': 't2', 'name': 'AskUserQuestion', 'input': {'questions': [{'question': '副标题用哪版?', 'options': [{'label': '短版', 'description': '一行'}, {'label': '长版', 'description': '两行'}]}]}}),
               {'type': 'user', 'timestamp': '2026-01-03T00:00:20.000Z', 'toolUseResult': {'answers': {'副标题用哪版?': '短版'}}, 'message': {'role': 'user', 'content': [{'type': 'tool_result', 'tool_use_id': 't2', 'content': 'answered'}]}},
               ca('2026-01-03T00:00:30.000Z', tx('改好了。顺手发现一个密钥 sk-' + 'a' * 40 + ' 和 password=hunter2xyz,已经提醒用户 some.one+x@gmail.com。'))])
    stdin = lambda **kw: json.dumps(dict({'session_id': 'selftestclaude1', 'transcript_path': str(tr), 'hook_event_name': 'Stop'}, **kw))
    out = ts(A, 'hook', 'claude', 'stop', stdin=stdin())
    check('short record: Stop returns empty JSON', out.strip() == '{}', out[:80])
    rec = next(iter((A / cdir).glob('*/*_claude_*.md')), None)
    body = rec.read_text(encoding='utf-8') if rec else ''
    check('both sides, mid-turn message, question and answer', all(x in body for x in (
        '把首页标题改短一点', '先看一下首页', '顺便把副标题也改了', '[question] 副标题用哪版?', '[answer] 副标题用哪版? → 短版', '改好了')))
    check('no tool output, meta or notifications', not any(x in body for x in ('TOOL-OUTPUT', 'META-SHOULD', 'NOTIFY-SHOULD')))
    check('secrets and e-mail addresses redacted', 'sk-aaaa' not in body and 'hunter2xyz' not in body and '[redacted]' in body
          and 'some.one' not in body and '[email]' in body)
    jsonl(tr, [row for k in range(12) for row in (cu(f'2026-01-03T01:{k:02d}:00.000Z', f'第 {k} 个问题:' + '内容' * 20),
                                                   ca(f'2026-01-03T01:{k:02d}:30.000Z', tx(f'第 {k} 个回答:' + '回答' * 20)))])
    out = ts(A, 'hook', 'claude', 'stop', stdin=stdin(stop_hook_active=True))
    check('no compaction request inside a hook continuation', out.strip() == '{}', out[:80])
    out = ts(A, 'hook', 'claude', 'stop', stdin=stdin())
    try:
        d = json.loads(out); reason = d.get('reason', '')
    except ValueError:
        d, reason = {}, ''
    check('over the limit: asks the agent to compact', d.get('decision') == 'block' and 'convo compact' in reason, out[:80])
    fm, tm = re.search(r'--file "([^"]+)"', reason), re.search(r'--through (\S+)', reason)
    if fm and tm:
        out = ts(A, 'convo', 'compact', '--file', fm.group(1), '--through', tm.group(1), stdin='用户要求把首页标题和副标题改短,选了短版,已改好;之后连续问了十几个测试问题。')
        body = rec.read_text(encoding='utf-8'); raw = body.split('<!-- summaries:end -->', 1)[-1]
        check('compact writes the summary, drops what it covers', '选了短版' in body and '把首页标题改短一点' not in raw and '第 11 个回答' in raw, out[:80])
        out = ts(A, 'hook', 'claude', 'stop', stdin=stdin())
        check('back under the limit afterwards', out.strip() == '{}', out[:80])
    else:
        check('compact writes the summary, drops what it covers', False, 'no --file / --through in the request')
    tr2 = tmp / 'claude-selftest-2.jsonl'
    jsonl(tr2, [cu('2026-01-04T00:00:00.000Z', '不记录这段对话。帮我看看这份私人文件'), ca('2026-01-04T00:00:05.000Z', tx('好的。'))])
    ts(A, 'hook', 'claude', 'stop', stdin=json.dumps({'session_id': 'norecord1', 'transcript_path': str(tr2)}))
    check('"不记录这段对话" leaves no record', not list((A / cdir).glob('*/*_norecord.md')))
    check('session start lists recent records', cdir + '/' in ts(A, 'start'))

    print('7b forked session: copied history is not recorded twice')
    pa, pb = tmp / 'claude-parent.jsonl', tmp / 'claude-fork.jsonl'
    base = [dict(cu('2026-01-05T00:00:00.000Z', '父会话的第一个问题'), uuid='u1', sessionId='parentsess'),
            dict(ca('2026-01-05T00:00:10.000Z', tx('父会话的回答')), uuid='u2', sessionId='parentsess')]
    jsonl(pa, base)
    ts(A, 'hook', 'claude', 'stop', stdin=json.dumps({'session_id': 'parentsess', 'transcript_path': str(pa)}))
    jsonl(pb, base + [dict(cu('2026-01-05T00:05:00.000Z', 'fork 之后的新问题'), uuid='u3', sessionId='forksess1'),
                      dict(ca('2026-01-05T00:05:10.000Z', tx('fork 之后的回答')), uuid='u4', sessionId='forksess1')])
    ts(A, 'hook', 'claude', 'stop', stdin=json.dumps({'session_id': 'forksess1', 'transcript_path': str(pb)}))
    fk = next(iter((A / cdir).glob('*/*_claude_forksess*.md')), None)
    fb = fk.read_text(encoding='utf-8') if fk else ''
    check('fork record: only its own messages, linked to the parent', 'fork 之后的新问题' in fb and '父会话的第一个问题' not in fb.split('<!-- summaries:end -->')[-1]
          and '_claude_parentse' in fb)

    print('8 journal: concurrent lines merge; auto line only at session end')
    C = tmp / 'carol'
    sh(tmp, 'git', 'clone', '-q', 'origin.git', 'carol')
    sh(C, 'git', 'config', 'user.name', 'carol'); sh(C, 'git', 'config', 'user.email', 'carol@selftest.local')
    (C / cfg.data['env_file']).write_text('\n'.join([f"{cfg.key('WHO')}=carol"] + env_lines) + '\n', encoding='utf-8')
    ts(C, 'start')
    ts(A, 'note', 'selftest: alice writes at the same time'); ts(A, 'stop')
    ts(C, 'note', 'selftest: carol writes at the same time'); out = ts(C, 'stop')        # carol has not pulled alice's line
    pushed = sh(tmp, 'git', '--git-dir', 'origin.git', 'show', 'HEAD:' + cfg.data['journal'])
    check('two people appending to the journal: merged, no conflict', ('Pushed' in out or '已推送' in out)
          and 'alice writes at the same time' in pushed and 'carol writes at the same time' in pushed, out.replace('\n', ' | ')[:100])
    ts(A, 'start')
    (A / jdir / 'selftest_s1.md').write_text('turn 1\n', encoding='utf-8'); ts(A, 'stop')          # like autosync / a turn end
    check('plain stop (autosync, turn end) writes no auto line', 'selftest_s1.md' not in (A / cfg.data['journal']).read_text(encoding='utf-8'))
    (A / jdir / 'selftest_s2.md').write_text('turn 2\n', encoding='utf-8'); ts(A, 'stop', '--session-end')
    autos = [l for l in (A / cfg.data['journal']).read_text(encoding='utf-8').splitlines() if ' · auto · ' in l]
    last = autos[-1] if autos else ''
    check('session end: one line for the whole session, records left out', 'selftest_s1.md' in last and 'selftest_s2.md' in last
          and cdir not in last, last[-90:])

    if keep:
        print(f'\nkept: {tmp}')
    else:
        shutil.rmtree(tmp, ignore_errors=True)
    print('\n' + ('all passed' if not FAILS else f'{len(FAILS)} failed: ' + ', '.join(FAILS)))
    return 1 if FAILS else 0
