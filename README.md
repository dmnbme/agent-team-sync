# agent-team-sync

Keep a small team and their AI coding agents in sync.

Three people, four different agents (Claude Code, Codex, Antigravity, WorkBuddy), one repository. Two of them do not know git.
Files live on a shared drive, code lives in git, and nobody knows what the others changed until something collides.
`team-sync` fixes that with three layers, each doing one job:

| layer | holds | mechanism |
|---|---|---|
| **git** | code, docs, skills, specs, data fingerprints | hooks pull at session start and commit + push at session end; a background timer does the same every 5 minutes |
| **shared drive** | large files (Google Drive, OneDrive, Dropbox) | path resolution only; the drive client does the syncing |
| **Postgres** (Supabase or any) | leases for mutual exclusion, shared cursors, the team passphrase | RLS-closed tables behind security-definer functions |

Enforcement never relies on the model remembering a rule: leases are taken inside scripts, the secret gate runs inside git, and
the background sync runs inside the OS scheduler. Hooks only add convenience: the session-start summary and the end-of-session push.

## What a session looks like

```
## Team sync · alice@laptop:4242 · 2026-09-09 10:02
Pulled latest
Recent commits (since 2026-09-08):
   3f1c2a9 2026-09-09 bob: sync(bob): 2026-09-09 09:40 · 2 files
Latest journal entries:
   - 2026-09-09 09:40 · bob · rewrote the pricing page copy / docs/pricing.md / needs review
Active leases: collect:telegram:main←bob@desk:9911
data/: matches manifest (3221 files, by alice on 2026-09-07)
Drive: /Users/alice/Library/CloudStorage/GoogleDrive-…/Shared drives/Team
Rules: see AGENTS.md. Take a lease through the scripts before touching shared resources.
```

That block is injected into the agent's context by a hook. When the session ends, local changes are committed with an
automatic journal line ("changed 3 files: …"), rebased on the remote and pushed. A conflict is never left half-merged:
the rebase is aborted, local work stays, a marker is written, a desktop notification fires, and the next session start prints it.

## Install

Two ways. The checkout needs git only and works with whatever `python3` a hook happens to get:

```bash
git clone --branch v0.1.8 https://github.com/dmnbme/agent-team-sync ~/.team-sync/pkg   # members
pip install --user "agent-team-sync @ git+https://github.com/dmnbme/agent-team-sync@v0.1.8"  # or: maintainers, gives the team-sync command
```

Inside a repo set up with `init`, everything runs through the committed launcher `bin/team_sync.py`, which finds the package either way.

No dependencies beyond the standard library. Python 3.9+ (the one that ships with macOS is enough). Windows: install
Python, and make sure a `python3` command exists (a copy of `python.exe` named `python3.exe` next to it works).

## Set up a repo (maintainer, once)

```bash
cd your-team-repo
team-sync init --team "Acme" --prefix ACME --lang en   # writes team-sync.json, hook files, .env.team.example, AGENTS.md
team-sync db install --project-ref <supabase-ref>      # SUPABASE_ACCESS_TOKEN in env, or --dsn postgres://…
echo "ACME_TEAM_KEY=$(openssl rand -hex 24)" >> .env.team
team-sync db register-key --project-ref <supabase-ref>
team-sync db env-for bob                                # writes ~/Desktop/env.team.txt to send to Bob
team-sync autosync on
git add -A && git commit -m "team-sync" && git push
```

`team-sync.json`:

```json
{
  "team": "Acme",
  "lang": "en",
  "env_prefix": "ACME",
  "env_file": ".env.team",
  "rules_file": "AGENTS.md",
  "journal": "docs/journal.md",
  "fingerprint": { "root": "data", "manifest": "data_manifest.json" },
  "drive": { "root_name": "Team", "marker": "Papers" },
  "sync": { "autosync_interval_sec": 300, "log_commits": 12, "journal_tail": 8 },
  "source": "git+https://github.com/dmnbme/agent-team-sync",
  "version": "0.1.0"
}
```

`version` is the pin: `start` warns when the installed version differs, `team-sync upgrade` installs the pinned one.

## Set up a machine (each member, once)

```bash
pip install --user "agent-team-sync @ git+https://github.com/dmnbme/agent-team-sync@v0.1.8"
git clone <team repo> ~/team && cd ~/team
cp ~/Downloads/env.team.txt .env.team        # the file the maintainer sent
team-sync start                              # installs the pre-commit gate, prints the summary
team-sync autosync on
team-sync paths                              # four lines: repo, drive, identity, database
```

Then open the folder in the agent. The hook files committed by `init` make every supported agent run `team-sync` automatically.

## Agents

| agent | file | events |
|---|---|---|
| Claude Code | `.claude/settings.json` | SessionStart → `start`; Stop → conversation record (see below); SessionEnd → `stop` |
| Antigravity | `.agents/hooks.json` | PreInvocation → summary injected once per conversation via `injectSteps`; Stop → `stop` when idle |
| CodeBuddy / WorkBuddy | `.codebuddy/settings.json` | SessionStart → `start`, SessionEnd → `stop` |
| Codex | `.codex/hooks.json` | SessionStart → summary returned as `additionalContext`; Stop → conversation record, then `stop` in a detached process after each turn. Codex asks once to trust the project and once to trust each hook (`/hooks`), again whenever the hook file changes; SessionEnd is not used because Codex caps it at 3 s, too short for pull + push |
| anything else | none needed | the background autosync still runs; the rules file says to run `team-sync start` |

Instruction files: `AGENTS.md` is the single source; `CLAUDE.md` and `CODEBUDDY.md` contain one line, `@AGENTS.md`.

## Conversation records

Optional. With `"convo": {"dir": "docs/conversations"}` in `team-sync.json`, the turn-end hook of Claude Code and Codex
writes what the user and the agent said to `<dir>/YYYY-MM/<date>_<who>_<agent>_<session>.md`, one file per session,
committed with everything else. Tool calls, tool output, thinking and injected context are left out; credential-shaped
strings are redacted with the gate's patterns. The session-start summary lists the five most recent records, so an agent
can open the one it needs.

When the verbatim part passes `compact_chars` (default 30000), the hook answers the turn end with a request: the running
agent summarises the older part and runs `team-sync convo compact --file F --through T` with the summary on stdin. The
newest `keep_recent_chars` (default 8000) stay verbatim. No API key and no extra model call: the agent that is already
running writes the summary.

Opting out: a user message containing `[no-record]` (or `不记录这段对话`) removes that session's record; whatever was
already pushed stays in git history. `TEAM_SYNC_NO_CONVO=1`, or `<PREFIX>_CONVO=off` in the env file, turns recording off on
one machine. Antigravity and CodeBuddy are not covered yet. Everyone with access to the repo can read the records, so tell
the people whose conversations are recorded.

## Leases

```python
from team_sync.leases import lease

with lease('collect:telegram:main', ttl=1800, note='fetch'):
    ...   # SystemExit with who-holds-it if unavailable; renewed every ttl/3 s in the background
```

```
team-sync lease acquire collect:telegram:main --ttl 900
team-sync lease list
team-sync lease release collect:telegram:main [--force]
```

A lease, not a lock: it expires, so a crashed agent cannot deadlock the team. Per resource, not per person.
The holder id is `person@machine:pid`, so two agents on one laptop are two holders.

## Other commands

```
team-sync note "what / where / next"      append to the team journal
team-sync convo list | compact            conversation records
team-sync cursor show|pull|push           shared "how far did we get" counters
team-sync fingerprint [--check|--quick]   manifest for the data directory kept out of git
team-sync gate                            secret gate (installed as pre-commit automatically)
team-sync selftest                        two-clone simulation in a temp dir, ~20 s
team-sync paths | status | version
```

## Design notes

- **Why three layers.** Sizes differ by four orders of magnitude (GBs of files, MBs of code, bytes of state) and so do the
  consistency needs. A drive has no locks and resolves conflicts by making copies; git detects conflicts only after the work
  is done; a database is the only place that can refuse *before* an expensive job starts.
- **Why leases live in scripts, not hooks.** Hooks differ per agent, need a trust prompt, and can be bypassed by an agent that
  writes its own command. A script that is the only path to the resource cannot be forgotten.
- **Why the passphrase.** Members never receive a service key. The anon key is public anyway; the passphrase is checked by
  every function and the tables are RLS-closed, so the anon key alone reads nothing.

## License

MIT
