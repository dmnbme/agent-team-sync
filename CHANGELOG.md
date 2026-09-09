# Changelog

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
