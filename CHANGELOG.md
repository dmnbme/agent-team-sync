# Changelog

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
