# Changelog

## Unreleased

- Desktop notices for sync failures that last. Until now only a repeated pull conflict raised a notice; a push that kept failing (an expired GitHub login, lost access), a commit the secret gate kept blocking, or a pull failing for another reason only printed a line that nobody reads in the background sync, so a computer could stop syncing unnoticed. Each of these now raises one notice once it has lasted `sync.alert_after_min` (default 60) and one more when it clears. Being offline and a busy git lock are not counted; a push error that looks like the network is not counted either.

## 0.1.28 (2026-10-25)

- No more console windows flashing on Windows. The background sync runs under `pythonw` with no console, so every `git` it started (several per sync, every five minutes) opened a console window of its own. When team-sync has no console, it now starts its child processes with `CREATE_NO_WINDOW`, and their own children (git's helpers, the pre-commit hook) inherit the hidden console. Runs from a terminal are unchanged.

## 0.1.27 (2026-10-25)

Windows fixes from a first full run on Windows 10 (arm64 and x86-64, Python on both architectures).

- The launcher no longer imports itself. `bin/team_sync.py` has the package's name, and Python puts the script's folder first on the path, so `import team_sync` found the launcher instead of the package. Every hook and the pre-commit gate failed with "team-sync is not installed" on a machine that had the package from pip only; machines with `~/.team-sync/pkg` were saved by the fallback. The launcher now drops its own folder from the path and looks in `$TEAM_SYNC_PKG`, then `~/.team-sync/pkg` (the copy `upgrade` moves), then the installed package. The launcher lives in each team repository: update `bin/team_sync.py` there from `resources/shim.py`.
- `autosync on` works on Windows 10. The scheduled task asked for `RepetitionDuration [TimeSpan]::MaxValue`, which Windows 10 rejects as out of range; leaving the duration out repeats indefinitely.
- Windows notifications are native toasts. They show as team-sync with the configured icon instead of a tray balloon with the system warning icon. The app identity is a per-user AppUserModelId in the registry (no shortcut, no admin). Clicking a toast, on the popup or later in the Action Center, opens the agent app and removes that one toast. A per-user COM activator does this (`~/.team-sync/notify/toast_activator.pyw`, standard library ctypes): it answers Windows' activation call, then starts the app. Windows PowerShell cannot subscribe to the toast's own click event, and toasts do not launch non-system URL protocols. Where toasts fail, the tray balloon is used as before.
- The system proxy is handed to git. git reads only `http.proxy` and the `*_PROXY` variables, never the OS proxy setting, and hooks started by an app or by the autosync scheduler have no shell variables. When no proxy variable is set, team-sync now exports the system proxy for git and gh (what `urllib` reads: macOS network settings, the Windows registry). A git `http.proxy` you set still wins; PAC scripts are not read.
- The pre-commit gate is written with `python.exe` when autosync runs under `pythonw.exe`. Before, a refused commit in a terminal showed no reason, and the hook was rewritten back and forth between the two.
- The selftest leaves the real `~/.team-sync/notify` alone. Its hooks used to repoint which app a notification click opens. New checks: the launcher with only a pip install (11), the system proxy hand-over (12), and the real click target unchanged.

## 0.1.26 (2026-10-25)

- Rewinds are recorded. Claude Code keeps rewound messages in the transcript and hangs the next prompt off the point the user went back to; Codex writes a `thread_rolled_back` event. team-sync read both as ordinary history, so a record showed the taken-back turns and the redone ones one after the other with nothing between them. Now the taken-back part is folded under a "withdrawn" line, verbatim, and the conversation goes on below it. Claude Code: a prompt whose `parentUuid` is not the entry just before it is checked against the transcript, and recorded turns after that point are withdrawn (in normal sessions this never fires). Codex: the last `num_turns` turns are withdrawn. Withdrawn state moves with a resumed session.

## 0.1.25 (2026-10-24)

- A resumed Claude Code session goes on in the same record. Claude Code gives a session a new id each time it is resumed (app restart, reopening an idle session, after its own context compaction) and copies the whole conversation in; team-sync took every such copy for a fork, so one long conversation ended up cut into a file per resume. Now a copy that runs up to the last message the earlier session recorded is treated as the same conversation: the new id takes over the file, its month links, pause state and summaries, and the header lists the earlier ids (`earlier_sessions`). A copy that stops earlier is still a branch with its own file. If the earlier id is used again after the takeover, it writes a new `_2` file linked to the shared one, so two sessions never write the same file. Codex keeps its id on resume and is unaffected.
- Summaries no longer delete the verbatim text. When the text not yet summarized passes `compact_chars`, the agent is still asked for a summary of the older part, but it is added at the top and the conversation stays in full (`summarized` in the header marks how far the summaries go). Long-running sessions lost more and more of their own wording before; the monthly files from 0.1.23 already keep each file to a month. Parts summarized by earlier versions stay as they are.

## 0.1.24 (2026-10-24)

- Desktop notification for new messages. When a sync (turn end or the background autosync) pulls in a message for you that has not been delivered yet, the computer shows a notification with the sender and how the message starts; before, nothing happened until you happened to start a session or send a turn. Once per message, remembered locally; the notice does not mark the message delivered, so the agent still relays it and the sender still gets the receipt when it does.
- Clicking a notification opens your agent app: the one that last ran a team-sync hook on this computer (on macOS read from the __CFBundleIdentifier the app gives its processes; on Windows the first non-shell parent process). On macOS the notifications come from a small applet built with osacompile on first use (`~/.team-sync/notify/team-sync.app`), so they show as team-sync rather than Script Editor, and clicking one launches the applet, which opens the agent.
- `notify.icon` in the config: a square PNG or icns in the repository used as the notification icon (default: the system sync icon). Changing it rebuilds the applet under a new bundle id, because Notification Center caches icons per id; macOS then asks once more to allow notifications.
- Pull conflicts: a lock held by another git process (an agent committing while a sync runs) is retried after 3 seconds and never reported as a conflict. A real conflict notifies only when the next sync still conflicts, since most clear by themselves; every conflict is logged with its time in the local state dir, and one that cleared is reported once at the next session start, instead of leaving a notification that pointed to nothing.

## 0.1.23 (2026-10-20)

- One conversation record per session per month. A session that runs into a new month goes on in a new file under that month's folder, named after its first message there; the previous month's file gets a link forward and is no longer changed, the new one a link back. Before, a session kept its first file forever, so a long-lived session wrote months of conversation into the folder of the month it began, and its summaries grew without limit.

## 0.1.22 (2026-10-20)

- Claude Code pushes after every turn, like Codex: the turn-end hook writes the conversation record, then runs commit, pull and push in a detached process, so the user does not wait. Before, Claude Code pushed only at session end and through the 5-minute autosync, so teammates could be up to 5 minutes behind. The session-end journal line is unchanged.

## 0.1.21 (2026-10-19)

- The paused-recording notice also goes into the agent's context with a request to open its reply with that line. The Codex desktop app does not display `systemMessage` from UserPromptSubmit hooks, and Claude Code folds it under a notice, so the UI notice alone was easy to miss. Resuming gets the same treatment.

## 0.1.20 (2026-10-19)

- Pausing the conversation record no longer deletes it. 不记录这段对话 / 暂停记录 / `[no-record]` now pauses from that message on: the earlier part stays, and the record marks where the pause began. 恢复记录 / `[record]` resumes from that message. 恢复记录并补上 / `[record-all]` resumes and adds the paused part, which was held in the local cache (never in the repository), to put a false trigger right.
- While a session is paused, every turn shows the user a notice (`systemMessage` from the UserPromptSubmit hook, Claude Code and Codex), and the agent is told this turn is not recorded. The prompt that pauses or resumes gets its own notice.

## 0.1.19 (2026-10-19)

- The conversation-record opt-out no longer fires on a question or a passing mention. Any user message containing 不记录这段对话 or `[no-record]` used to delete the session's record, so asking "if I say 不记录这段对话, is it not recorded?" deleted the record of the conversation in which it was asked. Now the message has to open with the phrase, or be a very short message ending with it, and must not read as a question.

## 0.1.18 (2026-10-19)

- Network failures are no longer reported as pull conflicts. Sync used to run `git pull --rebase` and tell the two apart by matching error text; errors missing from the list (an HTTP/2 stream reset, for one) raised a false "conflict" notification and marker. Now `git fetch` runs first: if it fails, that is the network, whatever the wording. The rebase onto the fetched branch runs locally, and only a failure there can be a conflict.
- Fetch and push retry once. When the error points to a flaky HTTP/2 path (common through some networks and proxies), the repository switches to HTTP/1.1 first, unless `http.version` is already set.

## 0.1.17 (2026-10-19)

- `msg send` pushes right away in the background, as its confirmation says, instead of waiting for the next turn-end sync or autosync run.

## 0.1.16 (2026-10-19)

- Messages between teammates' agents: `team-sync msg send <who> "..."` and `team-sync msg list`, on when `inbox.dir` is set. One file per message under `<dir>/<recipient>/`. The recipient's agent gets unread messages at the top of the session summary and before every turn (new `UserPromptSubmit` hooks for Claude Code, Codex and CodeBuddy; Antigravity checks on each invocation). The per-turn check reads local files only. Delivered messages are marked, and the sender's next summary shows the receipt. The injected text tells the agent to relay the message to the user and wait for their agreement before acting on it.
- Commit identity: when no git e-mail is configured, session start sets this repository's `user.name` and `user.email` to the GitHub login and its noreply address (through `gh`, at most once a day). Before, git signed with the OS full name and `user@host.local`, which GitHub links to no account: no username, no avatar.
- Codex members must Trust the new `team-sync: messages` hook in Settings → Hooks.

## 0.1.15 (2026-10-19)

- `autosync on` works on Python 3.9 to 3.11 again. One f-string reused its own quote inside a replacement field, which only Python 3.12+ accepts, so importing the autosync module failed on the macOS system Python and the five-minute background sync could not be installed.
- HTTPS calls (leases, cursors, database setup) work on the python.org macOS build before its "Install Certificates" step. When Python's default context has no CA certificates, team-sync loads them from certifi, or from the system bundle.

## 0.1.14 (2026-10-17)

- Conversation records handle forked and continued sessions. Both start with a copy of the earlier transcript (same message ids), which used to be recorded again in a new file. Each message now belongs to the first session that recorded it, through a per-machine index of message ids; later sessions record only their own messages and link to the record they continue. On first run the index is built from every cached session, oldest first, and existing records are re-rendered without the copies.

## 0.1.13 (2026-10-17)

- Codex hooks ignore scripted `codex exec` runs (rollout `session_meta.source == "exec"`): no summary, no conversation record, no sync. Batch jobs that call Codex inside the repo used to pull and push on every call and leave one record each.

## 0.1.12 (2026-10-17)

- The session-start summary lists the shared drive's top-level folders under the Drive line, so the agent knows what lives there without browsing first.

## 0.1.11 (2026-10-17)

- selftest no longer shows desktop notifications. Its staged conflict in step 3 used to raise a real "pull conflicted" notification several times per run, which looked like a problem in the team repo. `TEAM_SYNC_NO_NOTIFY=1` turns notifications off.

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
