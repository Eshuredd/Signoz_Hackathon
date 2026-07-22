from __future__ import annotations

import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
GATE2_ROOT = REPO_ROOT / "gate2"
if str(GATE2_ROOT) not in sys.path:
    sys.path.insert(0, str(GATE2_ROOT))
