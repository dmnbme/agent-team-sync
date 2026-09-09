# agent-team-sync

Keep a small team and their AI coding agents in sync.

Three people, three different agents (Claude Code, Antigravity, WorkBuddy), one repository. Two of them do not know git.
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

```bash
pip install --user "agent-team-sync @ git+https://github.com/dmnbme/agent-team-sync@v0.1.1"
python3 -m team_sync version
```

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
pip install --user "agent-team-sync @ git+https://github.com/dmnbme/agent-team-sync@v0.1.1"
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
| Claude Code | `.claude/settings.json` | SessionStart → `start`, SessionEnd → `stop` |
| Antigravity | `.agents/hooks.json` | PreInvocation → summary injected once per conversation via `injectSteps`; Stop → `stop` when idle |
| CodeBuddy / WorkBuddy | `.codebuddy/settings.json` | SessionStart → `start`, SessionEnd → `stop` |
| anything else | none needed | the background autosync still runs; the rules file says to run `team-sync start` |

Instruction files: `AGENTS.md` is the single source; `CLAUDE.md` and `CODEBUDDY.md` contain one line, `@AGENTS.md`.

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
