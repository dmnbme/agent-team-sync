#!/usr/bin/env python3
"""team-sync launcher committed in the team repo (bin/team_sync.py). Works with any python3 >= 3.9:
1. $TEAM_SYNC_PKG                         2. a checkout at ~/.team-sync/pkg (made by the setup script; `upgrade` moves it)
3. an installed package (pip)             Usage: python3 bin/team_sync.py start|stop|note|...
"""
import os
import sys

HERE = os.path.dirname(os.path.realpath(__file__))
sys.path[:] = [p for p in sys.path if os.path.realpath(p or os.curdir) != HERE]   # this file is named team_sync.py: never import itself
for cand in (os.environ.get('TEAM_SYNC_PKG'), os.path.join(os.path.expanduser('~'), '.team-sync', 'pkg')):
    if cand and os.path.isdir(os.path.join(cand, 'team_sync')):
        sys.path.insert(0, cand)
        break
try:
    import team_sync  # noqa: F401
except ImportError:
    sys.exit('team-sync is not installed: run the setup script, or: git clone https://github.com/dmnbme/agent-team-sync ~/.team-sync/pkg')

os.environ.setdefault('TEAM_SYNC_REPO', os.path.dirname(HERE))
from team_sync.cli import main  # noqa: E402

sys.exit(main())
