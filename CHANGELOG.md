# Changelog

## 0.1.10 (2026-10-17)

- The journal is marked `merge=union` in `.gitattributes`, added automatically by `start` / `stop`. Two people appending to the journal at the same time used to conflict, which stopped autosync on one machine until someone resolved it by hand; their lines now merge.
- The automatic journal line is written once, at session end: `team-sync stop --session-end`, used by the Claude Code and CodeBuddy SessionEnd hooks. It lists every file the person changed since the previous such line, including files that autosync or turn-end pushes committed in between. Autosync and turn-end pushes no longer write journal lines. Codex and Antigravity have no usable session-end event, so they write none; their work shows in the commit list, and Codex's in the conversation records.
- selftest: concurrent journal appends from two clones; no auto line from a plain `stop`; one line at session end.

## 0.1.9 (2026-10-17)

- Conversation records mask e-mail addresses as `[email]`, except for domains listed in `convo.keep_email_domains` (for example the team's own). Records quote what agents say about customers, and the repo is shared.

## 0.1.8 (2026-10-17)

- Conversation records, off unless `convo.dir` is set in `team-sync.json`. At every turn end the Claude Code and Codex hooks write the session's dialogue to one Markdown file per session under that directory: no tool calls, tool output, thinking or injected context; the user's answers to the agent's multiple-choice questions are kept; credential-shaped strings are redacted with the gate's patterns. Past `convo.compact_chars` the hook asks the running agent to fold the older part into a summary through the new `team-sync convo compact`. `team-sync convo list` and the session-start summary show recent records. Opt out per session with `[no-record]` / `不记录这段对话`, per machine with `TEAM_SYNC_NO_CONVO=1` or `<PREFIX>_CONVO=off`.
- Claude Code: new Stop hook, `team-sync hook claude stop`, in the `.claude/settings.json` template.
- Codex: the Stop hook is synchronous now, so it can answer with a compaction request; commit + push run in a detached process, so the turn still ends at once. The hook definition changed, so Codex asks to trust it again.
- The automatic journal line and the summary's recent-commits list leave conversation records out.
- selftest covers both agents' records: filtering, redaction, the compaction request, `convo compact`, opt-out.

## 0.1.7 (2026-10-17)

- Codex support. `team-sync init` now writes `.codex/hooks.json`: SessionStart returns the team summary as `additionalContext`, and Stop runs `stop` in the background after each turn (`async: true`). New adapter `team-sync hook codex session_start|stop`. SessionEnd is not used because Codex caps it at 3 s, too short for pull + push; the background autosync covers edits made after the last turn. Codex asks once to trust the project and once per hook (`/hooks`), and again whenever the hook file changes.
- The automatic journal line lists non-ASCII paths as they are (`题库/pipeline/x.py`), not as git's octal escapes.
- selftest covers the Codex hook (summary JSON, commit + push from Stop).

## 0.1.6 (2026-09-28)

- Fix: a pull that failed because the remote was unreachable (no network, DNS, timeout) was reported as a merge conflict, with a desktop notification on every autosync run and a conflict marker shown at the next start. It is now reported as "sync skipped", with no marker and no notification.
- Fix: after a failed pull, `stash pop` ran unconditionally and could apply an unrelated older stash. It now pops only a stash that the failed pull itself left behind.

## 0.1.5 (2026-09-10)

- A member whose `.env.team` is absent or half-filled now gets a message saying so, in `start` and in `paths`. It used to report only the missing team key, which sent people asking for one line when the whole file was missing.
- `bin/team_sync.py` is written executable by `init`.

## 0.1.4 (2026-09-09)

- Fix: child processes inherited the repo-locating variables (`TEAM_SYNC_REPO` and agent project-dir variables), so `selftest` clones could act on the real repo. The CLI now drops them after resolving the repo, and selftest passes `--repo` explicitly with a scrubbed environment.

## 0.1.3 (2026-09-09)

- Launcher `bin/team_sync.py` (written by `init`): hooks, the pre-commit gate and autosync no longer depend on which `python3` is first on PATH. Falls back to a checkout at `~/.team-sync/pkg`, so members need git only, not pip.
- Pre-commit hook records the interpreter that ran `team-sync start`.
- `upgrade` switches a `~/.team-sync/pkg` checkout to the pinned tag.

## 0.1.2 (2026-09-09)

- selftest: clones get the working-tree `team-sync.json` even when it is not committed yet.

## 0.1.1 (2026-09-09)

- Fix: message lookup crashed on strings that take a `key` argument (session summary failed to print).

## 0.1.0 (2026-09-09)

First release, extracted from a three-person team's internal tooling.

- `start` / `stop`: git pull, commit, push around agent sessions; team journal; conflict abort with local work kept, marker and desktop notification.
- Hook adapters for Claude Code, Antigravity (PreInvocation/Stop with `injectSteps`), CodeBuddy/WorkBuddy.
- Leases (Postgres security-definer functions, TTL, keepalive) and shared cursors.
- Fingerprint manifest for a large data directory kept out of git.
- Secret gate as a git pre-commit hook.
- Background autosync: launchd, Windows Task Scheduler, systemd user timer.
- `init`, `db install|register-key|env-for`, `selftest`, `upgrade`.
