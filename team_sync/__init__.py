"""agent-team-sync: keep a small team of humans and their AI coding agents in sync.

Three layers, each doing one job:
  git       code, docs, skills, specs             (this tool automates pull/commit/push around agent sessions)
  file drive large files                           (only the path resolution lives here)
  Postgres  leases (mutual exclusion) and cursors  (Supabase or any Postgres; see resources/schema.sql)
"""
__version__ = '0.1.1'
