# Agent Team Sync

[中文](README.md)

Lets the members of a small team, each working with their own AI coding agent, share one repository and one body of team context.

It is built for this situation: a few people work on one project, each with the agent they prefer (Claude Code, Codex, Antigravity, WorkBuddy), and some of them do not use git. Without a shared layer, each agent only knows its own machine. It cannot see what a teammate changed or why, two people running the same script overwrite each other, and secrets can end up in a commit.

## What it does

Once installed, members open their agent and work as usual. Hooks, git and the operating system's scheduler handle the rest:

- **A team summary at session start.** The tool pulls the latest changes, then puts recent commits, the last lines of the team journal, who is running which shared job, the shared drive's path and top-level folders, and the most recent conversation records into the agent's context.
- **Automatic commit and push.** At session end, local changes are committed, rebased onto the remote and pushed. A background sync does the same every 5 minutes, independent of any agent.
- **Conversation records.** After every turn, what the user and the agent said is written into the repository, one file per session. Tool calls and tool output are left out. When the verbatim text passes 30,000 characters, the agent that is already running summarises the older part. This needs no API key and no extra model call.
- **Messages to a teammate's agent.** `team-sync msg send <who> "..."`. The message reaches the recipient's agent when they open a new session or send their next message, and the agent relays it to them word for word. The sender sees a delivery receipt in their next session summary.
- **Leases.** Scripts that touch a shared resource, such as a scraper or a deploy, take a lease on it in the database before they start. If someone else holds it, the script stops and says who. Leases expire, so an agent that crashes halfway does not block the team.
- **Secret gate.** Before each commit, added lines are checked; credential-shaped strings and files such as `.env` are refused. Conversation records have secrets and e-mail addresses replaced as well.
- **Conflict handling.** If a pull conflicts, the merge is abandoned, local changes are kept, a desktop notification appears, and the next session start reports it. No half-merged files are left behind. Lines that several people append to the team journal at the same time merge automatically.
- **Commits under the GitHub identity.** When a member's machine has no git e-mail configured, the repository's commit identity is set to their GitHub username and GitHub noreply address, so commits show the username and avatar instead of the computer's full name.
- **Setup for non-technical members.** The setup script can be run by the member's own agent. When a step needs the person (signing in to GitHub, installing Google Drive), it stops and tells the agent what to ask the user to click.

## Three places for three kinds of data

| place | holds | how it syncs |
|---|---|---|
| **git repository** | code, docs, skills, specs, team journal, conversation records | hooks and the background sync pull, commit and push |
| **shared drive** (Google Drive, OneDrive, Dropbox) | large files, documents for people | the drive's own client; this tool only finds its path on each machine |
| **database** (Supabase; optional) | leases, shared progress counters, the team passphrase | six database functions called over HTTP, each checking the passphrase first; row-level security on. Without a database everything else works, minus leases |

The split follows the data. Sizes differ by four orders of magnitude (files in GB, code in MB, state in bytes), and so do the consistency needs. A shared drive has no locks and resolves conflicts by making copies; git detects a conflict only after the work is done; only a database can refuse an expensive job before it starts.

Enforcement does not depend on the model remembering a rule. Leases are taken inside scripts, the secret gate runs inside git, and the background sync runs in the operating system's scheduler. Hooks only provide the opening summary and the closing push.

## What the agent sees at session start

```
## Team sync · alice@laptop:4242 · 2026-09-09 10:02
Pulled latest
Recent commits (since 2026-09-08):
   3f1c2a9 2026-09-09 bob: sync(bob): 2026-09-09 09:40 · 2 files
Latest journal entries:
   - 2026-09-09 09:40 · bob · rewrote the pricing page copy / docs/pricing.md / needs review
Active leases: collect:telegram:main←bob@desk:9911
data/: matches manifest (3221 files, by alice on 2026-09-07)
Recent conversation records (open one to see what was discussed):
   09-09 09:38 · bob · Codex · show prices in the visitor's currency → docs/conversations/2026-09/2026-09-09_bob_codex_01a0f3c2.md
Drive: /Users/alice/Library/CloudStorage/GoogleDrive-…/Shared drives/Team
   folders: Contracts · Assets · Reports
Rules: see AGENTS.md. Take a lease through the scripts before touching shared resources.
```

## Supported agents

| agent | hook file | what runs |
|---|---|---|
| Claude Code | `.claude/settings.json` | session start: summary; before each turn: new messages; after each turn: conversation record, then commit and push in the background; session end: one journal line |
| Codex | `.codex/hooks.json` | session start: summary; before each turn: new messages; after each turn: conversation record, then commit and push in the background. Scripted `codex exec` runs are skipped |
| Antigravity | `.agents/hooks.json` | summary once per conversation; new messages on later calls; commit and push when idle |
| WorkBuddy / CodeBuddy | `.codebuddy/settings.json` | session start: summary; before each turn: new messages; session end: commit and push |
| any other agent | none | the background sync still runs; the opening summary depends on the model running `start` as the rules say, which it may skip (see the next section) |

The team rules live in one file, `AGENTS.md`; `CLAUDE.md` and `CODEBUDDY.md` contain a single line, `@AGENTS.md`.

The first time Codex opens the project, the user trusts the folder and then clicks Trust on each hook under Settings → Hooks; a changed hook file needs Trust again. Conversation records cover Claude Code and Codex; Antigravity and WorkBuddy are not supported yet.

## What is guaranteed and what depends on the model following instructions

Enforced by the system, whether or not the model complies:

- **Background sync**: the operating system's scheduler pulls, commits and pushes every 5 minutes, without going through the agent.
- **Secret gate**: it runs as git's pre-commit hook, so a `git commit` issued by the agent is checked too.
- **Leases**: they are taken inside the scripts, so running the script always checks first.
- **Agents with hooks**: the opening team summary, message delivery, conversation records and the end-of-session commit and push run from the agent's hooks.

Written in the rules, so the model has to follow them:

- **Running `start` first, in agents without hooks.** The model may skip it or run it late. Until it runs, the agent does not know what teammates changed recently and may keep editing old files; the background sync pulls new content within 5 minutes, and edits made in that window can conflict with someone else's. Agents without hooks have no conversation records either.
- **For every agent**: adding a journal line after a piece of work, reading the relevant knowledge file before starting, upgrading when the summary reports a version mismatch, summarising an overlong conversation record, relaying a message to the user instead of acting on it. These are requests; the model may not follow them.

A rule that must hold every time belongs in a script, a git hook or an agent hook, and not only in the rules file.

## Compared with hosted collaboration platforms

Hosted platforms for collaboration between several people and several agents also exist; they provide their own client, projects and task boards. The two kinds of tool differ as follows:

| | agent-team-sync | hosted collaboration platforms |
|---|---|---|
| agents used | the Claude Code, Codex, Antigravity or WorkBuddy that members already use, mixed freely | the platform's own agents |
| where data lives | your own git repository, shared drive and database | the platform's shared storage |
| new interface or account | none; members open their usual agent | install the platform's client or use its web app, then invite members |
| stopping two runs of the same job | database leases checked before a script starts | not described in public docs |
| protecting secrets | pre-commit gate; conversation records redacted | not described in public docs |
| task board, handoffs between roles, mobile app | not provided | provided |
| cost | open source (MIT); the database fits Supabase's free tier | no public pricing found |

Good fit: teams of 3 to 10 people who already use coding agents and want to keep their data. If you need a task board or approval flows across departments, a hosted platform fits better.

## Install

Requirements: git and Python 3.9 or later (the one that ships with macOS is enough). No third-party packages. The shared drive can be any service that syncs to a local folder: Google Drive, OneDrive and Dropbox are found automatically; for others (iCloud, Box, a NAS, Syncthing) add a path pattern under `drive.patterns` in `team-sync.json`, or set `<PREFIX>_DRIVE_ROOT=path` in each machine's `.env.team`.

```bash
git clone --branch v0.1.23 https://github.com/dmnbme/agent-team-sync ~/.team-sync/pkg   # members: git only
pip install --user "agent-team-sync @ git+https://github.com/dmnbme/agent-team-sync@v0.1.23"  # maintainers: gives the team-sync command
```

In a repository set up with `init`, everything runs through the committed launcher `bin/team_sync.py`, which finds the package either way. On Windows, install Python and make sure a `python3` command exists.

## Maintainer: set up the repository (once)

```bash
cd your-team-repo
team-sync init --team "Acme" --prefix ACME --lang en   # writes team-sync.json, hook files for four agents, .env.team.example, AGENTS.md
team-sync db install --project-ref <supabase-ref>      # SUPABASE_ACCESS_TOKEN in the environment, or --dsn postgres://…
echo "ACME_TEAM_KEY=$(openssl rand -hex 24)" >> .env.team
team-sync db register-key --project-ref <supabase-ref>
team-sync db env-for bob                                # writes ~/Desktop/env.team.txt to send to Bob
team-sync autosync on
git add -A && git commit -m "team-sync" && git push
```

Main fields of `team-sync.json`:

```json
{
  "team": "Acme",
  "lang": "en",
  "env_prefix": "ACME",
  "journal": "docs/journal.md",
  "fingerprint": { "root": "data", "manifest": "data_manifest.json" },
  "drive": { "root_name": "Team", "marker": "Papers" },
  "convo": { "dir": "docs/conversations", "compact_chars": 30000, "keep_recent_chars": 8000, "keep_email_domains": ["acme.com"] },
  "inbox": { "dir": "docs/inbox" },
  "version": "0.1.23"
}
```

`version` pins the team's version: when a member's installed version differs, the opening summary says so, and the rules tell the agent to run `team-sync upgrade`. Without `convo`, conversations are not recorded; without `inbox`, messages are off.

## Member: set up a machine (once)

Send the member a setup guide written for agents (such as `tools/SETUP_RUNBOOK.md`), the setup script and their `env.team.txt`, and have them tell their agent "follow this guide to set me up". The manual steps:

```bash
git clone <team repo> ~/team && cd ~/team
cp ~/Downloads/env.team.txt .env.team        # the file the maintainer sent
python3 bin/team_sync.py start               # installs the pre-commit gate and prints the summary
python3 bin/team_sync.py autosync on
python3 bin/team_sync.py paths               # four lines: repository, drive, identity, database
```

Then open the folder in the agent.

## Leases

```python
from team_sync.leases import lease

with lease('collect:telegram:main', ttl=1800, note='fetch'):
    ...   # exits with the holder's name if the resource is taken; renewed every ttl/3 seconds in the background
```

```
team-sync lease acquire collect:telegram:main --ttl 900
team-sync lease list
team-sync lease release collect:telegram:main [--force]
```

Leases are per resource, not per person. The holder id is `person@machine:process`, so two agents on one laptop are two holders.

## Conversation records

Add `"convo": {"dir": "docs/conversations"}` to `team-sync.json` to turn them on. One file per session per month: `<dir>/YYYY-MM/<date>_<who>_<agent>_<session>.md`, where the date is the session's first message in that month and the session part is the first 8 characters of the agent's session ID. When a session runs into a new month, it goes on in a file under the new month's folder; the two files link to each other, and the old one is no longer changed. Each month's folder holds what was actually said that month, and no file grows without limit.

- Left out: tool calls, tool output, thinking, injected context. Kept: what both sides said, and the user's answers to multiple-choice questions.
- Redaction: credential-shaped strings become `[redacted]`; e-mail addresses become `[email]`, except for domains listed in `keep_email_domains`.
- Compaction: when the verbatim part passes `compact_chars`, the hook asks the running agent at the end of that turn to write a summary and run `team-sync convo compact`. The newest `keep_recent_chars` stay verbatim.
- Pause and resume: only these fixed phrases count (string matching, no model in the hook; `[...]` is case-insensitive):

  | Effect | Phrases |
  |---|---|
  | Pause from this message on; the earlier part stays | `[no-record]`, `不记录这段对话`, `不记录本次对话`, `暂停记录` |
  | Resume from this message on; the paused part is not added | `[record]`, `恢复记录`, `继续记录` |
  | Resume and add the paused part (for a false trigger) | `[record-all]`, `恢复记录并补上`, `补上刚才的记录` |

  Conditions: the phrase opens the message, or ends it with at most 6 characters before it (spaces removed); and nothing in the message reads as a question (`?`, `？`, what, how, does, will, if, or the Chinese 吗, 怎么, 怎样, 如何, 如果, 假如, 会不会, 是不是, 能不能, 可不可以). Other wordings do not trigger. The paused part is kept only in the local cache, never in the repository; the record marks where recording paused and resumed. While paused, every turn tells the user: the hook sends a UI notice and asks the agent to open its reply with the same line (the Codex desktop app does not show hook notices, and Claude Code folds them). `TEAM_SYNC_NO_CONVO=1`, or `<PREFIX>_CONVO=off` in the env file, turns recording off on one machine.
- Forked or continued sessions: the earlier conversation copied into the new session is not recorded again; the file starts with a link to the record it continues. This is decided by message id, with an index on each machine.
- Everyone with access to the repository can read the records. Tell the people whose conversations are recorded before turning this on.

## Messages

Turn them on with `"inbox": {"dir": "docs/inbox"}` in `team-sync.json`.

```
team-sync msg send yulu "The pricing copy is done, see docs/pricing.md when you can"
team-sync msg send yulu,hanyu "..."      several recipients, comma-separated
team-sync msg list                       messages you sent and received, delivered or not
```

- The recipient is their short name, the `<PREFIX>_WHO` in their env file.
- One file per message: `<dir>/<recipient>/<time>_<sender>.md`, so two people writing at once never conflict.
- Delivery: at the top of the team summary when they open a new session, and checked again before every turn. The per-turn check only reads local files, with no network call, so it adds no wait; new messages arrive with the turn-end sync and the 5-minute background sync, so delivery can take a few minutes plus the recipient's next message.
- Once delivered, the message is marked as such; the mark syncs back and appears in the sender's next session summary.
- **Messages are relayed, never executed.** The injected text says it comes from a teammate, not from the user, and that the agent must tell the user first and act only once they agree. Otherwise anyone who can push to the repository could steer a teammate's agent, and the person would not know why it acted. This depends on the model following it; see the section above.

## Other commands

```
team-sync note "what / where / next"      append a line to the team journal
team-sync msg send <who> "..." | list     messages to teammates' agents
team-sync convo list | compact            conversation records
team-sync cursor show|pull|push           shared progress counters (for example, the last message a scraper fetched)
team-sync fingerprint [--check|--quick]   manifest for a data directory kept out of git
team-sync gate                            secret gate (installed as pre-commit automatically)
team-sync selftest                        simulates the whole flow with three clones in a temp directory, about 30 seconds
team-sync paths | status | version
```

## Design notes

- **Leases live in scripts.** Hooks differ between agents, need the user's trust, and can be bypassed by a command the agent writes itself. A script that is the only way to reach a resource cannot be skipped.
- **Members receive only the team passphrase.** They never get the database's service key. The anonymous key is public anyway; with row-level security on, only functions that check the passphrase can read or write.
- **"Who did what" comes from git.** Commits, the journal and conversation records are all in the repository, so any machine and any agent can read them, without relying on one vendor's memory feature.
- **Self-test.** `team-sync selftest` builds a local remote and three clones in a temp directory and covers syncing, conflicts, leases, the hooks of four agents, conversation records (filtering, redaction, compaction, opting out) concurrent journal appends, message delivery and receipts, and the commit identity, without touching a real remote.

## Background

The tool comes from daily use in a three-person team: Claude Code, Codex and Antigravity on macOS and Windows, two members who do not use git, and a shared question bank, website and shared drive.

## License

MIT. To try it, run `team-sync init` in a test repository, then `team-sync selftest` and check that every item passes.
