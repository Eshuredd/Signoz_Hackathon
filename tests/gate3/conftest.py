from __future__ import annotations

import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
GATE3_ROOT = REPO_ROOT / "gate3"
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(GATE3_ROOT) not in sys.path:
    sys.path.insert(0, str(GATE3_ROOT))
