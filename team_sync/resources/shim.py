#!/usr/bin/env python3
"""team-sync launcher committed in the team repo (bin/team_sync.py). Works with any python3 >= 3.9:
1. an installed package (pip)            2. a checkout at ~/.team-sync/pkg (made by the setup script)
3. $TEAM_SYNC_PKG                         Usage: python3 bin/team_sync.py start|stop|note|...
"""
import os
import sys

try:
    import team_sync  # noqa: F401
except ImportError:
    for cand in (os.environ.get('TEAM_SYNC_PKG'), os.path.join(os.path.expanduser('~'), '.team-sync', 'pkg')):
        if cand and os.path.isdir(os.path.join(cand, 'team_sync')):
            sys.path.insert(0, cand)
            break
    else:
        sys.exit('team-sync is not installed: run the setup script, or: git clone https://github.com/dmnbme/agent-team-sync ~/.team-sync/pkg')

os.environ.setdefault('TEAM_SYNC_REPO', os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from team_sync.cli import main  # noqa: E402

sys.exit(main())
