# Team repo · agent rules

Several people share this repository, each with their own AI coding agent. A hook injects a "team sync" summary at the
start of every session: recent commits, latest journal lines, active leases, shared-drive path. If you do not see it, run
`python3 -m team_sync start`.

## Three layers

| layer | what | how |
|---|---|---|
| git (this repo) | code, docs, skills, specs, data fingerprints | hooks pull/commit/push automatically |
| shared drive | large files | resolve the path with `python3 -m team_sync paths`; never hard-code it |
| database | leases (mutual exclusion) and shared cursors | `python3 -m team_sync lease ...` / `cursor ...` |

Secrets live only in `.env.team` on each machine. A pre-commit gate refuses commits that contain credential-shaped strings.

## Rules

1. Take a lease before touching a shared resource, through the scripts, never by hand. If it is held, wait.
2. When you finish a piece of work: `python3 -m team_sync note "what / where / next"`.
3. Keep large files on the drive, not in git. If the fingerprinted data directory changed: `python3 -m team_sync fingerprint`.
4. A "pull conflict" notice means local work was kept and needs a human merge: send the text to the maintainer.
